"""Chat turn streaming: scope validation, SSE event application, and durable
session-state persistence around the LLM stream.

The router owns HTTP shapes; this module owns the turn lifecycle — persist the
user turn and a streaming assistant turn up front, relay provider events,
then persist the completed or failed turn. Raises HTTPException for
authorization and lookup failures the same way the route handlers do.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import HTTPException
from pydantic import TypeAdapter
from pydantic_ai import DeferredToolResults, ToolDenied
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from sqlalchemy import text

from ai_almanac.server.db import get_db, lock_for_update
from ai_almanac.server.services import job_access
from ai_almanac.server.services.chat_artifacts import hydrate_turn_artifact_urls
from ai_almanac.server.services.chat_job_events import platform_note, scope_job_activity
from ai_almanac.server.services.chat_state import (
    TURN_HEARTBEAT_SECONDS,
    ApprovalRequest,
    ChatScope,
    ChatTurn,
    parse_llm_event,
    turn_in_flight,
    unprocessed_tool_calls,
)
from ai_almanac.server.services.llm import (
    deserialize_model_messages,
    serialize_model_messages,
    stream_response,
)
from ai_almanac.server.services.local_llm import AssistantUnavailable
from ai_almanac.server.services.rulesets import Ruleset, selectable_ruleset
from ai_almanac.server.services.turn_events import apply_stream_event
from ai_almanac.server.tables import json_col
from ai_almanac.settings import settings

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


INTERRUPTED = "The reply was interrupted before it finished."


def with_interrupted_turns(
    transcript: list[dict], last_active: object, now: datetime | None = None
) -> list[dict]:
    """The transcript as the user should see it: a turn left streaming by a relay
    that died reads as failed, not as still replying or as a reply with nothing in it."""
    live = turn_in_flight(transcript, last_active, now)
    return [
        {**turn, "status": "failed", "error": turn.get("error") or INTERRUPTED}
        if turn.get("status") == "streaming" and not (live and i == len(transcript) - 1)
        else turn
        for i, turn in enumerate(transcript)
    ]


@asynccontextmanager
async def _keeping_session_alive(session_id: str) -> AsyncIterator[None]:
    """Touch the session while a turn streams, so other writers know it is live."""
    heartbeat = asyncio.create_task(_touch_session_every(session_id, TURN_HEARTBEAT_SECONDS))
    try:
        yield
    finally:
        heartbeat.cancel()


async def _touch_session_every(session_id: str, seconds: float) -> None:
    while True:
        await asyncio.sleep(seconds)
        try:
            async with get_db() as conn:
                await conn.execute(
                    text("UPDATE chat_sessions SET updated_at = :now WHERE id = :id"),
                    {"now": _now(), "id": session_id},
                )
        except Exception:
            logger.warning("Could not touch chat session %s while it streams", session_id)


def _previous_user_turn_at(transcript: list[dict]) -> str | None:
    return next(
        (turn.get("created_at") for turn in reversed(transcript) if turn.get("role") == "user"),
        None,
    )


async def _job_update_note(user_id: str, scope: ChatScope, since: str | None) -> str | None:
    """Runs that finished since the user last wrote, for the assistant to hear about."""
    if since is None:
        return None
    return platform_note((await scope_job_activity(user_id, scope, since)).events)


def _replace_turn(transcript: list[dict], turn: ChatTurn) -> list[dict]:
    turn_payload = turn.model_dump(mode="json")
    return [turn_payload if existing.get("id") == turn.id else existing for existing in transcript]


def with_tool_result(
    transcript: list[dict], tool_call_id: str, status: str, result: object
) -> list[dict]:
    """The transcript with one tool call's outcome filled in."""

    def settle(call: dict) -> dict:
        return (
            {**call, "status": status, "result": result} if call.get("id") == tool_call_id else call
        )

    return [
        {**turn, "tool_calls": [settle(call) for call in turn.get("tool_calls") or []]}
        for turn in transcript
    ]


NOT_APPROVED = {"error": "Not approved"}
REPLY_FAILED_AFTER_STEP = (
    "The step you approved ran, but the assistant's reply about it failed. "
    "It does not need approving again."
)
_UNSETTLED = ("pending", "running")


def with_unanswered_declined(transcript: list[dict], tool_call_ids: set[str]) -> list[dict]:
    """The transcript with each of these steps that never got an answer marked as not
    approved, exactly as an explicit decline marks it. A step that already has an
    outcome keeps it."""

    def settle(call: dict) -> dict:
        unanswered = call.get("id") in tool_call_ids and call.get("status") in _UNSETTLED
        return {**call, "status": "failed", "result": NOT_APPROVED} if unanswered else call

    return [
        {**turn, "tool_calls": [settle(call) for call in turn.get("tool_calls") or []]}
        for turn in transcript
    ]


def _stream_event(event_type: str, **payload: object) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


async def _update_session_state(
    conn,
    session_id: str,
    *,
    provider_state: list[dict],
    transcript: list[dict],
    updated_at: datetime,
    scope: ChatScope,
) -> None:
    await conn.execute(
        text("""
            UPDATE chat_sessions
            SET provider_state = :provider_state,
                transcript = :transcript,
                scope = :scope,
                updated_at = :now
            WHERE id = :id
        """),
        {
            "provider_state": json.dumps(provider_state),
            "transcript": json.dumps(transcript),
            "scope": json.dumps(scope.model_dump(mode="json")),
            "now": updated_at,
            "id": session_id,
        },
    )


def workspace_scope_hidden(scope_kind: object, user: job_access.JobUser) -> bool:
    """A workspace conversation does not exist for anyone outside the workspaces audience."""
    return scope_kind == "workspace" and not settings.workspaces_allowed(user.is_admin)


async def validate_scope(scope: ChatScope, user: job_access.JobUser) -> ChatScope:
    job_ids = list(dict.fromkeys(scope.job_ids))
    validated_scope = scope.model_copy(update={"job_ids": job_ids})
    if scope.kind == "workspace":
        from .workspaces import get_workspace

        hidden = workspace_scope_hidden(scope.kind, user)
        if hidden or await get_workspace(scope.key, user.id) is None:
            raise HTTPException(status_code=404, detail="Workspace not found")
    if not job_ids:
        return validated_scope

    valid_ids = await job_access.readable_job_ids(job_ids, user)
    invalid_ids = [job_id for job_id in job_ids if job_id not in valid_ids]
    if invalid_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid job IDs for session scope: {', '.join(invalid_ids)}",
        )
    return validated_scope


def _classify_stream_error(exc: Exception, assistant_turn: ChatTurn) -> str:
    message = str(exc).lower()
    name = exc.__class__.__name__.lower()
    # A stalled or dropped read from the model's API, raised mid-stream by httpx
    # with no message, whatever the turn's tools did.
    if isinstance(exc, httpx.TimeoutException | httpx.NetworkError):
        return "provider_error"
    if (
        "tool" in message
        or "tool" in name
        or any(tool.status == "failed" for tool in assistant_turn.tool_calls)
    ):
        return "tool_error"
    if "openai" in message or "api" in name or "rate limit" in message or "timeout" in message:
        return "provider_error"
    return "internal_error"


def with_step_answered(provider_state: list, tool_call_id: str, result: object) -> list:
    """The model's history with a step's outcome recorded against its call.

    Written the moment a step the user decided on reports back, before the reply
    about it exists: whatever then happens to that reply, the step is no longer
    waiting for an answer and can be neither offered nor approved a second time.
    """
    call = next(
        (c for c in unprocessed_tool_calls(provider_state) if c.tool_call_id == tool_call_id),
        None,
    )
    if call is None:
        return provider_state
    answer = ToolReturnPart(tool_name=call.tool_name, content=result, tool_call_id=tool_call_id)
    return [*provider_state, ModelRequest(parts=[answer])]


_APPROVAL_REQUEST = TypeAdapter(ApprovalRequest)


def pending_approval(
    transcript: list[dict], provider_state: list, last_active: object
) -> ApprovalRequest | None:
    """The approval the conversation is still waiting on, for a client that reloaded.

    The model history decides whether a step is unanswered: approving, declining or
    writing a new message each give its call a result. The transcript only supplies
    what the card showed. Nothing is pending while a reply streams, which is how an
    approval being acted on looks until its turn lands.
    """
    waiting = {call.tool_call_id for call in unprocessed_tool_calls(provider_state)}
    if not waiting or turn_in_flight(transcript, last_active):
        return None
    requests = (
        request
        for turn in reversed(transcript)
        for request in reversed(turn.get("approval_requests") or [])
    )
    unanswered = next((r for r in requests if r.get("tool_call_id") in waiting), None)
    return _APPROVAL_REQUEST.validate_python(unanswered) if unanswered else None


def approved_step(transcript: list[dict], tool_call_id: str) -> dict:
    """What the step being decided was shown to the user as, for the step to check
    itself against when it runs. Empty when the card kept nothing to check against."""
    requests = (
        request
        for turn in reversed(transcript)
        for request in reversed(turn.get("approval_requests") or [])
    )
    shown = next((r for r in requests if r.get("tool_call_id") == tool_call_id), None)
    params = _shown_params(shown or {})
    return {"approved_params": params} if isinstance(params, dict) else {}


def _shown_params(request: dict) -> object:
    """What a card asked the user to approve: a draft's config as its form showed it, or
    a step's params."""
    if request.get("type") in ("benchmark_approval_request", "blend_approval_request"):
        return request.get("config")
    return (request.get("metadata") or {}).get("params")


def _deny_unprocessed_tool_calls(provider_state: list) -> list:
    tool_calls = unprocessed_tool_calls(provider_state)
    if not tool_calls:
        return provider_state

    denied_returns = [
        ToolReturnPart(
            tool_name=call.tool_name,
            content="The user continued the conversation without approving this action.",
            tool_call_id=call.tool_call_id,
            outcome="denied",
        )
        for call in tool_calls
        if getattr(call, "tool_call_id", None)
    ]
    if not denied_returns:
        return provider_state

    return [*provider_state, ModelRequest(parts=denied_returns)]


async def _save_session(
    session_id: str, provider_state: list[dict], transcript: list[dict], scope: ChatScope
) -> None:
    async with get_db() as conn:
        await _update_session_state(
            conn,
            session_id,
            provider_state=provider_state,
            transcript=transcript,
            updated_at=_now(),
            scope=scope,
        )


def _failed_turn(assistant_turn: ChatTurn, exc: BaseException, error: str | None) -> ChatTurn:
    return assistant_turn.model_copy(
        update={"status": "failed", "error": error or str(exc) or exc.__class__.__name__}
    )


def submitting_scope(session: Mapping[str, Any]) -> ChatScope:
    """The scope a session submits a run in. Refuses a comparison's scratch session.

    Withholding the submit *tools* from a comparison keeps the model from launching
    anything, but the session id is visible to whoever ran the comparison, and the
    submit endpoints submit a session's configuration without involving the model at
    all. The playground compares what the assistant proposes; a run belongs to a
    conversation the user owns.
    """
    if session["comparison_id"]:
        raise HTTPException(
            status_code=400,
            detail="This session belongs to a ruleset comparison and cannot submit a run.",
        )
    return ChatScope.model_validate(json_col(session["scope"], {}))


async def _pinned_ruleset(ruleset_id: str | None, *, for_admin: bool) -> Ruleset | None:
    """The ruleset a session is pinned to, or None for the active one.

    A pin that has since been archived or withdrawn degrades to the active ruleset,
    in the same never-raise spirit as ``rulesets.active_ruleset()``, so every turn of
    a conversation, approvals included, runs under the same policy.
    """
    return await selectable_ruleset(ruleset_id, for_admin=for_admin) if ruleset_id else None


@dataclass(frozen=True)
class StoredSession:
    """A session as read under its row lock, before a turn starts on it."""

    transcript: list[dict]
    provider_state: list
    scope: ChatScope
    ruleset_id: str | None
    comparison_id: str | None


@dataclass(frozen=True)
class PendingTurn:
    """What a turn starts from: the model history it continues, and the transcript with
    the assistant turn it streams into already appended."""

    provider_state: list
    transcript: list[dict]
    assistant_turn: ChatTurn
    scope: ChatScope


@dataclass(frozen=True)
class TurnRefused:
    error_type: str
    message: str

    def event(self) -> str:
        return _stream_event("error", error_type=self.error_type, message=self.message)


SESSION_NOT_FOUND = TurnRefused("internal_error", "Session not found")
SESSION_BUSY = TurnRefused("session_busy", "The assistant is still replying in this chat")
COMPARISON_RUNS_NOTHING = TurnRefused(
    "comparison", "This session belongs to a ruleset comparison and cannot run a step."
)


async def _open_turn(
    session_id: str, user_id: str, begin: Callable[[StoredSession], PendingTurn | TurnRefused]
) -> tuple[StoredSession, PendingTurn] | TurnRefused:
    """Start a turn on a session. One writer at a time (P8): the session is read under
    its row lock, and the streaming turn ``begin`` makes of it is saved before the lock
    is released."""
    async with get_db() as conn:
        lock_clause = await lock_for_update(conn)
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT provider_state, transcript, scope, ruleset_id, comparison_id,"
                        " updated_at"
                        " FROM chat_sessions WHERE id = :id AND user_id = :uid" + lock_clause
                    ),
                    {"id": session_id, "uid": user_id},
                )
            )
            .mappings()
            .fetchone()
        )
        if not row:
            return SESSION_NOT_FOUND
        session = StoredSession(
            transcript=json_col(row["transcript"], []),
            provider_state=deserialize_model_messages(row["provider_state"]),
            scope=ChatScope.model_validate(json_col(row["scope"], {})),
            ruleset_id=row["ruleset_id"],
            comparison_id=row["comparison_id"],
        )
        if turn_in_flight(session.transcript, row["updated_at"]):
            return SESSION_BUSY
        pending = begin(session)
        if isinstance(pending, TurnRefused):
            return pending
        await _update_session_state(
            conn,
            session_id,
            provider_state=serialize_model_messages(pending.provider_state),
            transcript=pending.transcript,
            updated_at=pending.assistant_turn.created_at,
            scope=pending.scope,
        )
    return session, pending


def _streaming_assistant_turn(created_at: datetime, trigger: str = "user") -> ChatTurn:
    return ChatTurn(
        id=str(uuid.uuid4()),
        role="assistant",
        trigger=trigger,
        created_at=created_at,
        status="streaming",
    )


def _begin_message_turn(
    session: StoredSession, content: str, requested_scope: ChatScope | None
) -> PendingTurn | TurnRefused:
    """A user message and the reply to it. Writing a new message instead of answering an
    approval card declines it, so the stored step must not go on reading as submitted."""
    stored = session.scope
    if requested_scope is not None and (
        requested_scope.kind != stored.kind or requested_scope.key != stored.key
    ):
        return TurnRefused("scope_mismatch", "Session scope does not match the current view")
    waiting = {call.tool_call_id for call in unprocessed_tool_calls(session.provider_state)}
    created_at = _now()
    user_turn = ChatTurn(id=str(uuid.uuid4()), role="user", content=content, created_at=created_at)
    assistant_turn = _streaming_assistant_turn(created_at)
    return PendingTurn(
        provider_state=_deny_unprocessed_tool_calls(session.provider_state),
        transcript=[
            *with_unanswered_declined(session.transcript, waiting),
            user_turn.model_dump(mode="json"),
            assistant_turn.model_dump(mode="json"),
        ],
        assistant_turn=assistant_turn,
        scope=requested_scope or stored,
    )


def _begin_approval_turn(
    session: StoredSession, tool_call_id: str, approved: bool
) -> PendingTurn | TurnRefused:
    """The reply to a decision on a step the assistant paused on. A comparison's scratch
    session proposes steps for comparing, never for running; see ``submitting_scope``."""
    if session.comparison_id:
        return COMPARISON_RUNS_NOTHING
    waiting = {call.tool_call_id for call in unprocessed_tool_calls(session.provider_state)}
    if tool_call_id not in waiting:
        return TurnRefused("approval_expired", "This step is no longer waiting for approval")
    transcript = (
        session.transcript
        if approved
        else with_tool_result(session.transcript, tool_call_id, "failed", NOT_APPROVED)
    )
    assistant_turn = _streaming_assistant_turn(_now(), trigger=f"approval:{tool_call_id}")
    return PendingTurn(
        provider_state=session.provider_state,
        transcript=[*transcript, assistant_turn.model_dump(mode="json")],
        assistant_turn=assistant_turn,
        scope=session.scope,
    )


async def stream_chat_turn(
    session_id: str,
    user_id: str,
    content: str,
    requested_scope: ChatScope | None,
    *,
    for_admin: bool = False,
    active_ruleset: Ruleset | None = None,
    comparison_id: str | None = None,
) -> AsyncIterator[str]:
    """Persist the user turn, relay the LLM stream as SSE, persist the outcome.

    The turn runs under the session's pinned ruleset, as ``for_admin`` may see it.
    ``active_ruleset`` overrides that, and ``comparison_id`` tags the turn log; both
    are set by a side-by-side comparison and are None for an ordinary conversation.
    """
    opened = await _open_turn(
        session_id,
        user_id,
        lambda session: _begin_message_turn(session, content, requested_scope),
    )
    if isinstance(opened, TurnRefused):
        yield opened.event()
        return
    session, pending = opened
    ruleset = active_ruleset or await _pinned_ruleset(session.ruleset_id, for_admin=for_admin)
    since = _previous_user_turn_at(session.transcript)
    job_update_note = await _job_update_note(user_id, pending.scope, since)
    async with _keeping_session_alive(session_id):
        async for chunk in _relay_turn(
            session_id,
            user_id,
            pending,
            latest_user_message=content,
            platform_note=job_update_note,
            active_ruleset=ruleset,
            comparison_id=comparison_id,
            turn_id=pending.assistant_turn.id,
        ):
            yield chunk


async def stream_approval_turn(
    session_id: str,
    user_id: str,
    tool_call_id: str,
    approved: bool,
    *,
    message: str | None = None,
    for_admin: bool,
) -> AsyncIterator[str]:
    """Continue past a workspace step the assistant paused on, as an assistant turn of its own.

    The continuation is a turn like any other: it waits its turn behind a streaming
    reply, streams to the client, and is persisted, so whatever the assistant says or
    asks next, including another approval, reaches the user. The decided step's
    outcome is filled in on the turn that proposed it.
    """
    opened = await _open_turn(
        session_id, user_id, lambda session: _begin_approval_turn(session, tool_call_id, approved)
    )
    if isinstance(opened, TurnRefused):
        yield opened.event()
        return
    session, pending = opened
    decision = True if approved else ToolDenied(message or "The user declined this step.")
    async with _keeping_session_alive(session_id):
        async for chunk in _relay_turn(
            session_id,
            user_id,
            pending,
            settles=tool_call_id,
            approved=approved,
            deferred_tool_results=DeferredToolResults(
                approvals={tool_call_id: decision},
                metadata={tool_call_id: approved_step(session.transcript, tool_call_id)},
            ),
            active_ruleset=await _pinned_ruleset(session.ruleset_id, for_admin=for_admin),
            turn_id=pending.assistant_turn.id,
        ):
            yield chunk


def _without_step(blocks: list, tool_call_id: str | None) -> list:
    """A turn's blocks without the step an earlier turn already shows. Text and thinking
    name no step, so they always stay."""
    if tool_call_id is None:
        return blocks
    return [block for block in blocks if getattr(block, "tool_call_id", None) != tool_call_id]


def _concerns(data: dict, tool_call_id: str | None) -> bool:
    """Whether a stream event is the call or result of ``tool_call_id``."""
    if tool_call_id is None or data.get("type") not in ("tool_call", "tool_result"):
        return False
    call = data.get("tool_call")
    return data.get("tool_call_id") == tool_call_id or (
        isinstance(call, dict) and call.get("id") == tool_call_id
    )


def _scope_after_submission(scope: ChatScope, data: dict) -> ChatScope:
    """The scope a conversation moves to once it submits a run: that run's jobs.
    A workspace conversation stays in its workspace."""
    if (
        scope.kind == "workspace"
        or data.get("type") not in ("benchmark_config", "blend_config")
        or not isinstance(data.get("run_id"), str)
        or not isinstance(data.get("jobs"), list)
    ):
        return scope
    return ChatScope(
        kind="benchmark_run_group" if data["type"] == "benchmark_config" else "job_set",
        key=data["run_id"],
        title=scope.title,
        job_ids=[
            job["id"]
            for job in data["jobs"]
            if isinstance(job, dict) and isinstance(job.get("id"), str)
        ],
    )


def _completed_turn(assistant_turn: ChatTurn, done: dict, settles: str | None) -> ChatTurn:
    """The turn a ``done`` event describes, without the settled step an earlier turn shows."""
    completed = ChatTurn.model_validate(
        {
            **assistant_turn.model_dump(mode="json"),
            **done["turn"],
            "id": assistant_turn.id,
            "trigger": assistant_turn.trigger,
            "status": "completed",
            "error": None,
        }
    )
    return completed.model_copy(
        update={
            "tool_calls": [call for call in completed.tool_calls if call.id != settles],
            "blocks": _without_step(completed.blocks, settles),
        }
    )


def _failure_event(exc: Exception, assistant_turn: ChatTurn, step_ran: bool) -> str:
    """What the client is told when a reply fails. Once an approved step has run, the
    user hears that it did, whatever broke afterwards."""
    if isinstance(exc, AssistantUnavailable) and not step_ran:
        return _stream_event("error", error_type="assistant_unavailable", message=str(exc))
    return _stream_event(
        "error",
        error_type=_classify_stream_error(exc, assistant_turn),
        message=REPLY_FAILED_AFTER_STEP if step_ran else "Chat response failed",
    )


async def _relay_turn(
    session_id: str,
    user_id: str,
    pending: PendingTurn,
    *,
    settles: str | None = None,
    approved: bool = True,
    **stream_kwargs: object,
) -> AsyncIterator[str]:
    """Stream one assistant turn to the client, then persist it completed or failed.

    ``settles`` names an earlier turn's tool call the user has decided on, whose
    outcome arrives in this stream. An approved step's result is filled in where that
    call lives, and marked failed if it never runs. A declined step already reads as
    not approved there, so its echo in this stream is dropped: the transcript keeps
    one entry per step. Either way the step is recorded as answered the moment its
    outcome arrives, so a reply that then fails cannot bring its approval back.
    """
    waiting_state = list(pending.provider_state)
    assistant_turn = pending.assistant_turn
    scope = pending.scope
    pending_provider_state = serialize_model_messages(waiting_state)
    pending_transcript = pending.transcript
    settled = settles is None
    # --- Stream without holding a DB connection ---
    terminal_event: str | None = None
    try:
        # `aclosing` because the loop below breaks on the terminal event, leaving
        # the LLM generator suspended. Its `finally` writes the turn log, so
        # without an explicit close that write would happen whenever the
        # generator was garbage-collected — which is soon enough to look correct
        # and late enough to lose a race, as a comparison reading both variants'
        # logs immediately afterwards found.
        async with aclosing(
            stream_response(waiting_state, user_id, session_id, scope, **stream_kwargs)
        ) as llm_events:
            async for event in llm_events:
                data = parse_llm_event(event)
                if data is None:
                    continue
                scope = _scope_after_submission(scope, data)
                if _concerns(data, settles):
                    if data.get("type") == "tool_result":
                        if approved:
                            pending_transcript = with_tool_result(
                                pending_transcript,
                                settles,
                                data.get("status") or "completed",
                                data.get("result"),
                            )
                        pending_provider_state = serialize_model_messages(
                            with_step_answered(waiting_state, settles, data.get("result"))
                        )
                        await _save_session(
                            session_id, pending_provider_state, pending_transcript, scope
                        )
                        settled = True
                    if approved:
                        yield f"data: {event}\n\n"
                    continue
                if data.get("type") == "done":
                    if not settled and approved:
                        pending_transcript = with_tool_result(
                            pending_transcript,
                            settles,
                            "failed",
                            {"error": "The approved step did not run"},
                        )
                    completed_turn = _completed_turn(assistant_turn, data, settles)
                    # --- Transaction 2: persist completed state ---
                    await _save_session(
                        session_id,
                        data.get("provider_state", pending_provider_state),
                        _replace_turn(pending_transcript, completed_turn),
                        scope,
                    )
                    hydrated_turn = hydrate_turn_artifact_urls(completed_turn, user_id)
                    terminal_event = _stream_event(
                        "done", turn=hydrated_turn.model_dump(mode="json")
                    )
                    break

                apply_stream_event(assistant_turn, data)
                yield f"data: {event}\n\n"
            else:
                raise RuntimeError("Chat stream ended without a terminal event")
    except asyncio.CancelledError as exc:
        try:
            failed = _failed_turn(assistant_turn, exc, None)
            await _save_session(
                session_id, pending_provider_state, _replace_turn(pending_transcript, failed), scope
            )
        except Exception:
            pass
        return
    except Exception as exc:
        if not isinstance(exc, AssistantUnavailable):
            logger.exception(
                "Chat response stream failed for session %s and user %s",
                session_id,
                user_id,
            )
        step_ran = settles is not None and settled and approved
        try:
            failed = _failed_turn(
                assistant_turn, exc, REPLY_FAILED_AFTER_STEP if step_ran else None
            )
            await _save_session(
                session_id, pending_provider_state, _replace_turn(pending_transcript, failed), scope
            )
            terminal_event = _failure_event(exc, assistant_turn, step_ran)
        except Exception:
            terminal_event = _stream_event(
                "error",
                error_type="internal_persistence_error",
                message="Chat response failed and could not be persisted",
            )
    if terminal_event is not None:
        yield terminal_event
