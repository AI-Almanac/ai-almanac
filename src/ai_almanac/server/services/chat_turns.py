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
from collections.abc import AsyncIterator
from contextlib import aclosing, suppress
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic_ai import DeferredToolResults, ToolDenied
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy import text

from ai_almanac.server.db import get_db, lock_for_update
from ai_almanac.server.services import job_access
from ai_almanac.server.services.chat_artifacts import hydrate_turn_artifact_urls
from ai_almanac.server.services.chat_job_events import platform_note, scope_job_activity
from ai_almanac.server.services.chat_state import ChatScope, ChatTurn
from ai_almanac.server.services.chat_tools import (
    SubmitBenchmarkApproval,
    SubmitBlendApproval,
)
from ai_almanac.server.services.llm import (
    ModelRepeated,
    deserialize_model_messages,
    serialize_model_messages,
    stream_response,
)
from ai_almanac.server.services.local_llm import AssistantUnavailable
from ai_almanac.server.services.rulesets import Ruleset, selectable_ruleset
from ai_almanac.server.services.turn_events import apply_stream_event

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def json_value[T](value: object, default: T) -> T:
    if isinstance(value, type(default)):
        return value
    if value is None:
        return default
    if not isinstance(value, str):
        return default
    stripped = value.strip()
    if not stripped:
        return default
    return json.loads(stripped)


def json_list(value: object) -> list[dict]:
    return json_value(value, [])


def json_dict(value: object) -> dict:
    return json_value(value, {})


def parse_llm_event(event: str) -> dict | None:
    stripped = event.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LLM stream emitted a malformed event") from exc
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM stream emitted a non-object event")
    return parsed


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


def _stream_event(event_type: str, **payload: object) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


async def _update_session_state(
    conn,
    session_id: str,
    *,
    provider_state: list[dict],
    transcript: list[dict],
    updated_at: datetime,
    scope: ChatScope | None = None,
) -> None:
    params = {
        "provider_state": json.dumps(provider_state),
        "transcript": json.dumps(transcript),
        "now": updated_at,
        "id": session_id,
    }
    if scope is None:
        await conn.execute(
            text("""
                UPDATE chat_sessions
                SET provider_state = :provider_state, transcript = :transcript, updated_at = :now
                WHERE id = :id
            """),
            params,
        )
        return
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
            **params,
            "scope": json.dumps(scope.model_dump(mode="json")),
        },
    )


async def validate_scope(scope: ChatScope, user: job_access.JobUser) -> ChatScope:
    job_ids = list(dict.fromkeys(scope.job_ids))
    validated_scope = scope.model_copy(update={"job_ids": job_ids})
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
    # Before the tool check: the overflow is often caused by a tool's result.
    if "maximum context length" in message or "context_length_exceeded" in message:
        return "context_overflow"
    # Before the tool check too: a loop is often cut off inside a tool call.
    if isinstance(exc, ModelRepeated):
        return "repetition"
    if (
        "tool" in message
        or "tool" in name
        or any(tool.status == "failed" for tool in assistant_turn.tool_calls)
    ):
        return "tool_error"
    if "openai" in message or "api" in name or "rate limit" in message or "timeout" in message:
        return "provider_error"
    return "internal_error"


def _deny_unprocessed_tool_calls(provider_state: list) -> list:
    if not provider_state:
        return provider_state

    tool_calls = getattr(provider_state[-1], "tool_calls", None)
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


async def _persist_failed_turn(
    conn,
    session_id: str,
    pending_provider_state: list[dict],
    pending_transcript: list[dict],
    assistant_turn: ChatTurn,
    exc: BaseException,
    scope: ChatScope | None = None,
) -> None:
    failed_turn = assistant_turn.model_copy(
        update={
            "status": "failed",
            "error": str(exc) or exc.__class__.__name__,
        }
    )
    await _update_session_state(
        conn,
        session_id,
        provider_state=pending_provider_state,
        transcript=_replace_turn(pending_transcript, failed_turn),
        updated_at=_now(),
        scope=scope,
    )


async def get_session_provider_scope(session_id: str, user_id: str):
    """Read a session's provider state for a submission or a resumed approval.

    Refuses a comparison's scratch session. Withholding the submit *tools* from a
    comparison keeps the model from launching anything, but the session id is
    visible to whoever ran the comparison, and these endpoints submit a session's
    configuration without involving the model at all. The playground compares what
    the assistant proposes; a run belongs to a conversation the user owns.
    """
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("""
                        SELECT provider_state, scope, comparison_id, ruleset_id
                        FROM chat_sessions
                        WHERE id = :id AND user_id = :uid
                    """),
                    {"id": session_id, "uid": user_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")
    if row["comparison_id"]:
        raise HTTPException(
            status_code=400,
            detail="This session belongs to a ruleset comparison and cannot submit a run.",
        )
    return row


def _approval_metadata(
    approval: SubmitBenchmarkApproval | SubmitBlendApproval,
) -> dict:
    return {
        "approved_config": approval.approved_config.model_dump(mode="json")
        if approval.approved_config
        else None
    }


async def save_provider_state(session_id: str, user_id: str, provider_state: list[dict]) -> None:
    async with get_db() as conn:
        await conn.execute(
            text("""
                UPDATE chat_sessions
                SET provider_state = :provider_state, updated_at = :now
                WHERE id = :id AND user_id = :uid
            """),
            {
                "provider_state": json.dumps(provider_state),
                "now": _now(),
                "id": session_id,
                "uid": user_id,
            },
        )


async def resume_deferred_setup_tool(
    session_id: str,
    user_id: str,
    approval: SubmitBenchmarkApproval | SubmitBlendApproval,
    approval_result: bool | ToolDenied,
    *,
    config_event: str = "benchmark_config",
    for_admin: bool = False,
) -> tuple[list[dict], dict | None]:
    """Resume a human-approved deferred setup tool (benchmark or blend submit).

    ``config_event`` selects which terminal config event carries the submitted
    run; the returned payload uses generic ``config`` / ``validation`` keys.
    """
    row = await get_session_provider_scope(session_id, user_id)
    scope = ChatScope.model_validate(json_dict(row["scope"]))
    provider_state = deserialize_model_messages(row["provider_state"])
    # The approval continues a conversation, so it runs under the same pinned
    # ruleset as the turns around it — falling back to the active one exactly
    # as send_message does when the pin has since been archived.
    session_ruleset = (
        await selectable_ruleset(row["ruleset_id"], for_admin=for_admin)
        if row["ruleset_id"]
        else None
    )
    deferred_results = DeferredToolResults(
        approvals={approval.tool_call_id: approval_result},
        metadata={approval.tool_call_id: _approval_metadata(approval)},
    )
    final_provider_state = serialize_model_messages(provider_state)
    payload: dict | None = None

    async for event in stream_response(
        provider_state,
        user_id,
        session_id,
        scope,
        deferred_tool_results=deferred_results,
        active_ruleset=session_ruleset,
    ):
        data = parse_llm_event(event)
        if data and data.get("type") == config_event and data.get("run_id"):
            payload = {
                "run_id": data["run_id"],
                "jobs": data.get("jobs"),
                "config": data.get("config"),
                "validation": data.get("validation"),
            }
        if data and data.get("type") == "done":
            final_provider_state = data.get("provider_state", final_provider_state)

    return final_provider_state, payload


class TurnStopped(Exception):
    """The user asked to stop the reply being streamed."""


# Replies this process is streaming, by session: (assistant turn id, stop signal).
# A stop reaches the reply through this, so it works from any page, not only
# the one holding the stream.
_running_turns: dict[str, tuple[str, asyncio.Event]] = {}


def request_stop(session_id: str) -> bool:
    """Ask the reply streaming in this session to stop. False if none is, here."""
    running = _running_turns.get(session_id)
    if running is None:
        return False
    running[1].set()
    return True


async def _next_event_or_stop(events: AsyncIterator[str], stop: asyncio.Event) -> str:
    """The stream's next event, or TurnStopped if a stop is asked for first.

    The pending read is cancelled on a stop, which closes the request to the
    model, so it stops generating at once rather than after its next token.
    """
    if stop.is_set():
        raise TurnStopped
    reading = asyncio.ensure_future(anext(events))
    stopping = asyncio.ensure_future(stop.wait())
    try:
        done, _ = await asyncio.wait({reading, stopping}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        reading.cancel()
        raise
    finally:
        stopping.cancel()
    if reading in done:
        return reading.result()
    reading.cancel()
    with suppress(BaseException):
        await reading
    raise TurnStopped


def _stopped_turn(turn: ChatTurn) -> ChatTurn:
    """The turn as saved after a stop: what it said, and tool calls it cut short."""
    calls = [
        call.model_copy(update={"status": "failed"})
        if call.status in ("pending", "running")
        else call
        for call in turn.tool_calls
    ]
    return turn.model_copy(
        update={"status": "completed", "stopped": True, "error": None, "tool_calls": calls}
    )


def _stopped_turn_history(provider_state: list, prompt: str, turn: ChatTurn) -> list[dict]:
    """The model's history after a stopped reply: the question, and a reply saying
    what it had said and done.

    A failed turn leaves the history as it was before the question, but a
    stopped one may already have acted (started a rerun, say); without a record
    of that the next turn would not know, and might do it again.
    """
    done = [call for call in turn.tool_calls if call.status in ("completed", "failed")]
    cut_short = [call for call in turn.tool_calls if call.status in ("pending", "running")]
    note = ["The user stopped this reply before it finished."]
    if done:
        note.append(
            "Already done in it: "
            + ", ".join(f"{call.name} ({call.status})" for call in done)
            + "."
        )
    if cut_short:
        note.append(
            "Stopped mid-call, so possibly done or not: "
            + ", ".join(call.name for call in cut_short)
            + "."
        )
    said = turn.content.strip()
    reply = (f"{said}\n\n" if said else "") + f"[{' '.join(note)}]"
    messages = [
        *provider_state,
        ModelRequest(parts=[UserPromptPart(content=prompt)]),
        ModelResponse(parts=[TextPart(content=reply)]),
    ]
    return serialize_model_messages(messages)


async def stream_chat_turn(
    session_id: str,
    user_id: str,
    content: str,
    requested_scope: ChatScope | None,
    *,
    active_ruleset: Ruleset | None = None,
    comparison_id: str | None = None,
) -> AsyncIterator[str]:
    """Persist the user turn, relay the LLM stream as SSE, persist the outcome.

    ``active_ruleset`` overrides the platform's active policy for this turn, and
    ``comparison_id`` tags the turn log; both are set by a side-by-side
    comparison and are None for an ordinary conversation.
    """
    # --- Transaction 1: read state with row lock, persist user + streaming assistant turn ---
    async with get_db() as conn:
        lock_clause = await lock_for_update(conn)
        row = (
            (
                await conn.execute(
                    text(
                        """
                SELECT provider_state, transcript, scope
                FROM chat_sessions
                WHERE id = :id AND user_id = :uid
            """
                        + lock_clause
                    ),
                    {"id": session_id, "uid": user_id},
                )
            )
            .mappings()
            .fetchone()
        )
        if not row:
            yield _stream_event("error", error_type="internal_error", message="Session not found")
            return

        provider_state = _deny_unprocessed_tool_calls(
            deserialize_model_messages(row["provider_state"])
        )
        transcript = json_list(row["transcript"])
        stored_scope = ChatScope.model_validate(json_dict(row["scope"]))
        if requested_scope is not None:
            if requested_scope.kind != stored_scope.kind or requested_scope.key != stored_scope.key:
                yield _stream_event(
                    "error",
                    error_type="scope_mismatch",
                    message="Session scope does not match the current view",
                )
                return
            scope = requested_scope
        else:
            scope = stored_scope

        created_at = _now()
        user_turn = ChatTurn(
            id=str(uuid.uuid4()),
            role="user",
            content=content,
            created_at=created_at,
        )
        assistant_turn = ChatTurn(
            id=str(uuid.uuid4()),
            role="assistant",
            created_at=created_at,
            status="streaming",
        )

        pending_provider_state = serialize_model_messages(provider_state)
        pending_transcript = transcript + [
            user_turn.model_dump(mode="json"),
            assistant_turn.model_dump(mode="json"),
        ]
        await _update_session_state(
            conn,
            session_id,
            provider_state=pending_provider_state,
            transcript=pending_transcript,
            updated_at=created_at,
            scope=scope,
        )
    # --- Transaction 1 committed, lock released ---
    job_update_note = await _job_update_note(user_id, scope, _previous_user_turn_at(transcript))

    # --- Stream without holding a DB connection ---
    terminal_event: str | None = None
    stop = asyncio.Event()
    _running_turns[session_id] = (assistant_turn.id, stop)
    try:
        # `aclosing` because the loop below breaks on the terminal event, leaving
        # the LLM generator suspended. Its `finally` writes the turn log, so
        # without an explicit close that write would happen whenever the
        # generator was garbage-collected — which is soon enough to look correct
        # and late enough to lose a race, as a comparison reading both variants'
        # logs immediately afterwards found.
        async with aclosing(
            stream_response(
                provider_state,
                user_id,
                session_id,
                scope,
                latest_user_message=content,
                platform_note=job_update_note,
                active_ruleset=active_ruleset,
                comparison_id=comparison_id,
                turn_id=assistant_turn.id,
            )
        ) as llm_events:
            while True:
                try:
                    event = await _next_event_or_stop(llm_events, stop)
                except StopAsyncIteration:
                    raise RuntimeError("Chat stream ended without a terminal event") from None
                data = parse_llm_event(event)
                if data is None:
                    continue
                if (
                    data.get("type") in ("benchmark_config", "blend_config")
                    and isinstance(data.get("run_id"), str)
                    and isinstance(data.get("jobs"), list)
                ):
                    scope = ChatScope(
                        kind="benchmark_run_group"
                        if data["type"] == "benchmark_config"
                        else "job_set",
                        key=data["run_id"],
                        title=scope.title,
                        job_ids=[
                            job["id"]
                            for job in data["jobs"]
                            if isinstance(job, dict) and isinstance(job.get("id"), str)
                        ],
                    )
                if data.get("type") == "done":
                    completed_turn = ChatTurn.model_validate(
                        {
                            **assistant_turn.model_dump(mode="json"),
                            **data["turn"],
                            "id": assistant_turn.id,
                            "status": "completed",
                            "error": None,
                        }
                    )
                    final_provider_state = data.get("provider_state", pending_provider_state)
                    final_transcript = _replace_turn(pending_transcript, completed_turn)
                    # --- Transaction 2: persist completed state ---
                    async with get_db() as conn:
                        await _update_session_state(
                            conn,
                            session_id,
                            provider_state=final_provider_state,
                            transcript=final_transcript,
                            updated_at=_now(),
                            scope=scope,
                        )
                    hydrated_turn = hydrate_turn_artifact_urls(completed_turn, user_id)
                    terminal_event = _stream_event(
                        "done", turn=hydrated_turn.model_dump(mode="json")
                    )
                    break

                apply_stream_event(assistant_turn, data)
                yield f"data: {event}\n\n"
    except TurnStopped:
        stopped_turn = _stopped_turn(assistant_turn)
        try:
            async with get_db() as conn:
                await _update_session_state(
                    conn,
                    session_id,
                    provider_state=_stopped_turn_history(provider_state, content, assistant_turn),
                    transcript=_replace_turn(pending_transcript, stopped_turn),
                    updated_at=_now(),
                    scope=scope,
                )
            hydrated = hydrate_turn_artifact_urls(stopped_turn, user_id)
            terminal_event = _stream_event("stopped", turn=hydrated.model_dump(mode="json"))
        except Exception:
            logger.exception("Could not save the stopped turn for session %s", session_id)
            terminal_event = _stream_event(
                "error",
                error_type="internal_persistence_error",
                message="The reply was stopped but could not be saved",
            )
    except asyncio.CancelledError as exc:
        # Shielded, or a second cancel (shutdown cancelling what is left) would
        # skip the write and leave the turn marked streaming for good.
        async def persist_cancelled(exc: BaseException) -> None:
            async with get_db() as conn:
                await _persist_failed_turn(
                    conn,
                    session_id,
                    pending_provider_state,
                    pending_transcript,
                    assistant_turn,
                    exc,
                    scope,
                )

        with suppress(Exception):
            await asyncio.shield(persist_cancelled(exc))
        return
    except Exception as exc:
        if not isinstance(exc, AssistantUnavailable):
            logger.exception(
                "Chat response stream failed for session %s and user %s",
                session_id,
                user_id,
            )
        try:
            async with get_db() as conn:
                await _persist_failed_turn(
                    conn,
                    session_id,
                    pending_provider_state,
                    pending_transcript,
                    assistant_turn,
                    exc,
                    scope,
                )
            terminal_event = (
                _stream_event("error", error_type="assistant_unavailable", message=str(exc))
                if isinstance(exc, AssistantUnavailable)
                else _stream_event(
                    "error",
                    error_type=_classify_stream_error(exc, assistant_turn),
                    message="Chat response failed",
                )
            )
        except Exception:
            terminal_event = _stream_event(
                "error",
                error_type="internal_persistence_error",
                message="Chat response failed and could not be persisted",
            )
    finally:
        # Only this turn's entry: a newer turn in the session may have replaced it.
        if _running_turns.get(session_id, (None,))[0] == assistant_turn.id:
            del _running_turns[session_id]
    if terminal_event is not None:
        yield terminal_event


# Replies running in the background, by session. A reply runs apart from the
# request that asked for it, so reloading the page does not end it: the page
# that comes back finds the turn still streaming and follows it to the end.
_reply_tasks: dict[str, asyncio.Task[None]] = {}


def reply_running(session_id: str) -> bool:
    """Whether a reply in this session is still being written, here."""
    return session_id in _reply_tasks


def start_chat_turn(
    session_id: str,
    user_id: str,
    content: str,
    requested_scope: ChatScope | None,
    *,
    active_ruleset: Ruleset | None = None,
) -> AsyncIterator[str]:
    """Start ``stream_chat_turn`` in the background and return its events.

    The task starts here, not on first iteration, so a second send to the same
    session sees it at once (see ``reply_running``). The returned stream only
    relays: dropping it leaves the reply running and saving its outcome.
    """
    events: asyncio.Queue[str | None] = asyncio.Queue()

    async def run() -> None:
        try:
            async with aclosing(
                stream_chat_turn(
                    session_id, user_id, content, requested_scope, active_ruleset=active_ruleset
                )
            ) as stream:
                async for event in stream:
                    events.put_nowait(event)
        finally:
            events.put_nowait(None)

    task = asyncio.create_task(run())
    _reply_tasks[session_id] = task

    def finished(done: asyncio.Task[None]) -> None:
        if _reply_tasks.get(session_id) is done:
            del _reply_tasks[session_id]
        if not done.cancelled() and done.exception() is not None:
            logger.error("Chat reply for session %s failed", session_id, exc_info=done.exception())

    task.add_done_callback(finished)

    async def relay() -> AsyncIterator[str]:
        while (event := await events.get()) is not None:
            yield event

    return relay()


async def cancel_running_replies() -> None:
    """End the replies still running, saving each as failed. For shutdown."""
    tasks = list(_reply_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


INTERRUPTED_REPLY = "The reply stopped when the app stopped or restarted."


def _writing_reply(session_id: str) -> bool:
    """Whether this process is writing a reply in the session: sent from a
    chat (from the send until it is saved) or a comparison's."""
    return session_id in _reply_tasks or session_id in _running_turns


async def fail_orphaned_replies(session_id: str | None = None) -> int:
    """Save as failed the replies still marked streaming that nothing is
    writing: every session's at startup, or one session's when a stop finds
    no reply running. Returns how many sessions changed.

    Only where this process is the one writing replies (a personal
    deployment); elsewhere another instance may be writing them.
    """
    query = "SELECT id FROM chat_sessions WHERE CAST(transcript AS TEXT) LIKE :streaming"
    params: dict[str, str] = {"streaming": '%"streaming"%'}
    if session_id is not None:
        query += " AND id = :id"
        params["id"] = session_id
    async with get_db() as conn:
        candidates = (await conn.execute(text(query), params)).scalars().all()
    changed = 0
    for candidate in candidates:
        async with get_db() as conn:
            # Locked as a send locks it, so a reply starting now can't be lost.
            lock_clause = await lock_for_update(conn)
            row = (
                (
                    await conn.execute(
                        text("SELECT transcript FROM chat_sessions WHERE id = :id" + lock_clause),
                        {"id": candidate},
                    )
                )
                .mappings()
                .fetchone()
            )
            if row is None or _writing_reply(candidate):
                continue
            transcript = json_list(row["transcript"])
            settled = [
                {**turn, "status": "failed", "error": INTERRUPTED_REPLY}
                if turn.get("role") == "assistant" and turn.get("status") == "streaming"
                else turn
                for turn in transcript
            ]
            if settled == transcript:
                continue
            await conn.execute(
                text("UPDATE chat_sessions SET transcript = :transcript WHERE id = :id"),
                {"transcript": json.dumps(settled), "id": candidate},
            )
            changed += 1
    return changed
