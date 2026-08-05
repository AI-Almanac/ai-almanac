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
from contextlib import aclosing
from datetime import UTC, datetime

from fastapi import HTTPException
from pydantic_ai import DeferredToolResults, ToolDenied
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from sqlalchemy import text

from ai_almanac.server.db import get_db, lock_for_update
from ai_almanac.server.services import job_access
from ai_almanac.server.services.chat_artifacts import hydrate_turn_artifact_urls
from ai_almanac.server.services.chat_state import (
    ChatArtifact,
    ChatScope,
    ChatToolCall,
    ChatTurn,
    GuardrailNotice,
)
from ai_almanac.server.services.chat_tools import (
    SubmitBenchmarkApproval,
    SubmitBlendApproval,
)
from ai_almanac.server.services.llm import (
    deserialize_model_messages,
    serialize_model_messages,
    stream_response,
)
from ai_almanac.server.services.rulesets import Ruleset

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


def _replace_turn(transcript: list[dict], turn: ChatTurn) -> list[dict]:
    turn_payload = turn.model_dump(mode="json")
    return [turn_payload if existing.get("id") == turn.id else existing for existing in transcript]


def _stream_event(event_type: str, **payload: object) -> str:
    return f"data: {json.dumps({'type': event_type, **payload})}\n\n"


def _append_tool_call(turn: ChatTurn, payload: dict) -> None:
    tool_call = ChatToolCall.model_validate(payload)
    if any(existing.id == tool_call.id for existing in turn.tool_calls):
        return
    turn.tool_calls.append(tool_call)


def _append_artifact(turn: ChatTurn, tool_call_id: str | None, payload: dict) -> None:
    artifact = ChatArtifact.model_validate(payload)
    if not any(existing.id == artifact.id for existing in turn.artifacts):
        turn.artifacts.append(artifact)
    if not tool_call_id:
        return
    for tool_call in turn.tool_calls:
        if tool_call.id != tool_call_id:
            continue
        if any(existing.id == artifact.id for existing in tool_call.artifacts):
            return
        tool_call.artifacts.append(artifact)
        return


def _apply_stream_event(turn: ChatTurn, data: dict) -> None:
    event_type = data.get("type")
    if event_type == "text_delta":
        turn.content += data.get("content", "")
        return
    if event_type == "tool_call":
        tool_payload = data.get("tool_call")
        if isinstance(tool_payload, dict):
            _append_tool_call(turn, tool_payload)
        return
    if event_type == "artifact":
        artifact_payload = data.get("artifact")
        if isinstance(artifact_payload, dict):
            _append_artifact(turn, data.get("tool_call_id"), artifact_payload)
        return
    if event_type == "guardrail":
        turn.guardrails.append(
            GuardrailNotice(
                tool_call_id=data.get("tool_call_id"),
                errors=[item for item in data.get("errors") or [] if isinstance(item, str)],
                warnings=[item for item in data.get("warnings") or [] if isinstance(item, str)],
                finding_keys=[
                    item for item in data.get("finding_keys") or [] if isinstance(item, str)
                ],
            )
        )
        return
    if event_type == "tool_result":
        tool_call_id = data.get("tool_call_id")
        for tool_call in turn.tool_calls:
            if tool_call.id == tool_call_id:
                tool_call.status = data.get("status", tool_call.status)
                tool_call.result = data.get("result")
                return


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
                        SELECT provider_state, scope, comparison_id
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
) -> tuple[list[dict], dict | None]:
    """Resume a human-approved deferred setup tool (benchmark or blend submit).

    ``config_event`` selects which terminal config event carries the submitted
    run; the returned payload uses generic ``config`` / ``validation`` keys.
    """
    row = await get_session_provider_scope(session_id, user_id)
    scope = ChatScope.model_validate(json_dict(row["scope"]))
    provider_state = deserialize_model_messages(row["provider_state"])
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
            stream_response(
                provider_state,
                user_id,
                session_id,
                scope,
                latest_user_message=content,
                active_ruleset=active_ruleset,
                comparison_id=comparison_id,
                turn_id=assistant_turn.id,
            )
        ) as llm_events:
            async for event in llm_events:
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

                _apply_stream_event(assistant_turn, data)
                yield f"data: {event}\n\n"
            else:
                raise RuntimeError("Chat stream ended without a terminal event")
    except asyncio.CancelledError as exc:
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
        except Exception:
            pass
        return
    except Exception as exc:
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
            terminal_event = _stream_event(
                "error",
                error_type=_classify_stream_error(exc, assistant_turn),
                message="Chat response failed",
            )
        except Exception:
            terminal_event = _stream_event(
                "error",
                error_type="internal_persistence_error",
                message="Chat response failed and could not be persisted",
            )
    if terminal_event is not None:
        yield terminal_event
