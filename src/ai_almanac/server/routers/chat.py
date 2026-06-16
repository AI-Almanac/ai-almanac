"""
Chat router — persistent LLM chat sessions with tool access to job data.

Endpoints:
  POST   /chat/sessions              create a new session
  GET    /chat/sessions              list user's sessions
  GET    /chat/sessions/{id}         get session with full message history
  POST   /chat/sessions/{id}/message send a message and stream the response
  DELETE /chat/sessions/{id}         delete a session
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel
from pydantic_ai import DeferredToolResults, ToolDenied
from pydantic_ai.messages import ModelRequest, ToolReturnPart
from sqlalchemy import bindparam, text

from ai_almanac.server.auth import CurrentUser
from ai_almanac.server.db import get_db

from ..services.benchmark_domain import (
    submit_benchmark_for_session,
    update_benchmark_config,
)
from ..services.benchmark_state import (
    BenchmarkRunSpec,
    BenchmarkValidation,
)
from ..services.chat_artifacts import (
    delete_chat_figure_artifact,
    hydrate_turn_artifact_urls,
    verify_chat_figure_signature,
)
from ..services.chat_state import (
    ChatArtifact,
    ChatScope,
    ChatToolCall,
    ChatTurn,
)
from ..services.chat_tools import (
    SubmitBenchmarkApproval,
)
from ..services.llm import (
    deserialize_model_messages,
    llm_is_configured,
    serialize_model_messages,
    stream_response,
)
from ..services.storage import get_storage, guess_chat_figure_media_type

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    title: str | None = None
    scope: ChatScope


class SessionOut(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime
    message_count: int
    scope: ChatScope
    benchmark_config: BenchmarkRunSpec | None = None
    benchmark_validation: BenchmarkValidation | None = None
    run_id: str | None = None


class SessionDetail(SessionOut):
    transcript: list[ChatTurn]


class MessageIn(BaseModel):
    content: str
    scope: ChatScope | None = None


class SessionUpdate(BaseModel):
    title: str | None = None


class BenchmarkSubmitOut(BaseModel):
    run_id: str
    jobs: list[dict]
    benchmark_config: BenchmarkRunSpec
    benchmark_validation: BenchmarkValidation


class BenchmarkConfigPatchIn(BaseModel):
    intent: str | None = None
    region_id: str | None = None
    dataset_id: str | None = None
    model_ids: list[str] | None = None
    event_type: str | None = None
    forecast_window_days: int | None = None
    advanced_params: dict | None = None


class BenchmarkSubmitIn(BaseModel):
    approval: SubmitBenchmarkApproval | None = None


class BenchmarkApprovalIn(BaseModel):
    approval: SubmitBenchmarkApproval
    message: str = "The user declined to run the benchmark."


class BenchmarkConfigOut(BaseModel):
    benchmark_config: BenchmarkRunSpec
    benchmark_validation: BenchmarkValidation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(UTC)


def _json_value[T](value: object, default: T) -> T:
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


def _json_list(value: object) -> list[dict]:
    return _json_value(value, [])


def _json_dict(value: object) -> dict:
    return _json_value(value, {})


def _benchmark_config(value: object) -> BenchmarkRunSpec | None:
    parsed = _json_dict(value)
    return BenchmarkRunSpec.model_validate(parsed) if parsed else None


def _benchmark_validation(value: object) -> BenchmarkValidation | None:
    parsed = _json_dict(value)
    return BenchmarkValidation.model_validate(parsed) if parsed else None


def _benchmark_submit_out(payload: dict) -> BenchmarkSubmitOut:
    return BenchmarkSubmitOut(
        run_id=payload["run_id"],
        jobs=payload["jobs"],
        benchmark_config=BenchmarkRunSpec.model_validate(payload["benchmark_config"]),
        benchmark_validation=BenchmarkValidation.model_validate(
            payload["benchmark_validation"]
        ),
    )


def _session_out(row) -> SessionOut:
    transcript = _json_list(row["transcript"])
    return SessionOut(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=len(transcript),
        scope=ChatScope.model_validate(_json_dict(row["scope"])),
        benchmark_config=_benchmark_config(row.get("benchmark_config")),
        benchmark_validation=_benchmark_validation(row.get("benchmark_validation")),
        run_id=row.get("run_id"),
    )


def _session_detail(row, user_id: str) -> SessionDetail:
    transcript = _json_list(row["transcript"])
    return SessionDetail(
        **_session_out(row).model_dump(),
        transcript=[
            hydrate_turn_artifact_urls(ChatTurn.model_validate(turn), user_id)
            for turn in transcript
        ],
    )


def _parse_llm_event(event: str) -> dict | None:
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
    return [
        turn_payload if existing.get("id") == turn.id else existing
        for existing in transcript
    ]


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


async def _validate_scope(scope: ChatScope, user_id: str) -> ChatScope:
    job_ids = list(dict.fromkeys(scope.job_ids))
    validated_scope = scope.model_copy(update={"job_ids": job_ids})
    if not job_ids:
        return validated_scope

    query = text("""
        SELECT id
        FROM jobs
        WHERE user_id = :uid AND id IN :job_ids
    """).bindparams(bindparam("job_ids", expanding=True))

    async with get_db() as conn:
        rows = (
            (await conn.execute(query, {"uid": user_id, "job_ids": job_ids}))
            .mappings()
            .fetchall()
        )

    valid_ids = {row["id"] for row in rows}
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
    if (
        "openai" in message
        or "api" in name
        or "rate limit" in message
        or "timeout" in message
    ):
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


async def _get_session_provider_scope(session_id: str, user_id: str):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("""
                        SELECT provider_state, scope
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
    return row


def _approval_metadata(approval: SubmitBenchmarkApproval) -> dict:
    return {
        "approved_config": approval.approved_config.model_dump(mode="json")
        if approval.approved_config
        else None
    }


async def _save_provider_state(
    session_id: str, user_id: str, provider_state: list[dict]
) -> None:
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


async def _resume_deferred_benchmark_tool(
    session_id: str,
    user_id: str,
    approval: SubmitBenchmarkApproval,
    approval_result: bool | ToolDenied,
) -> tuple[list[dict], dict | None]:
    row = await _get_session_provider_scope(session_id, user_id)
    scope = ChatScope.model_validate(_json_dict(row["scope"]))
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
        data = _parse_llm_event(event)
        if data and data.get("type") == "benchmark_config" and data.get("run_id"):
            payload = {
                "run_id": data["run_id"],
                "jobs": data.get("jobs"),
                "benchmark_config": data.get("config"),
                "benchmark_validation": data.get("validation"),
            }
        if data and data.get("type") == "done":
            final_provider_state = data.get("provider_state", final_provider_state)

    return final_provider_state, payload


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post(
    "/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED
)
async def create_session(body: SessionCreate, user: CurrentUser):
    if not llm_is_configured():
        raise HTTPException(status_code=503, detail="LLM is not configured")

    session_id = str(uuid.uuid4())
    now = _now()
    initial_messages: list[dict] = []

    async with get_db() as conn:
        await conn.execute(
            text("""
                INSERT INTO chat_sessions (id, user_id, title, provider_state, scope, transcript, created_at, updated_at)
                VALUES (:id, :uid, :title, :provider_state, :scope, '[]', :now, :now)
            """),
            {
                "id": session_id,
                "uid": user.id,
                "title": body.title,
                "provider_state": json.dumps(initial_messages),
                "scope": json.dumps(body.scope.model_dump(mode="json")),
                "now": now,
            },
        )

    return SessionOut(
        id=session_id,
        title=body.title,
        created_at=now,
        updated_at=now,
        message_count=0,
        scope=body.scope,
        benchmark_config=None,
        benchmark_validation=None,
        run_id=None,
    )


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    user: CurrentUser,
    scope_kind: str | None = Query(default=None),
    scope_key: str | None = Query(default=None),
):
    async with get_db() as conn:
        query = "SELECT * FROM chat_sessions WHERE user_id = :uid"
        params: dict[str, object] = {"uid": user.id}
        if scope_kind:
            query += " AND scope->>'kind' = :scope_kind"
            params["scope_kind"] = scope_kind
        if scope_key:
            query += " AND scope->>'key' = :scope_key"
            params["scope_key"] = scope_key
        query += " ORDER BY updated_at DESC"
        rows = (
            (
                await conn.execute(
                    text(query),
                    params,
                )
            )
            .mappings()
            .fetchall()
        )

    return [_session_out(row) for row in rows]


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, user: CurrentUser):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT * FROM chat_sessions WHERE id = :id AND user_id = :uid"
                    ),
                    {"id": session_id, "uid": user.id},
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    return _session_detail(row, user.id)


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def update_session(session_id: str, body: SessionUpdate, user: CurrentUser):
    title = body.title.strip() if isinstance(body.title, str) else None
    if title == "":
        title = None

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("""
                UPDATE chat_sessions
                SET title = :title, updated_at = :now
                WHERE id = :id AND user_id = :uid
                RETURNING *
            """),
                    {
                        "id": session_id,
                        "uid": user.id,
                        "title": title,
                        "now": _now(),
                    },
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    return _session_out(row)


@router.post(
    "/sessions/{session_id}/benchmark/submit", response_model=BenchmarkSubmitOut
)
async def submit_session_benchmark(
    session_id: str, user: CurrentUser, body: BenchmarkSubmitIn | None = None
):
    if body and body.approval:
        final_provider_state, payload = await _resume_deferred_benchmark_tool(
            session_id,
            user.id,
            body.approval,
            True,
        )
        if payload is None:
            raise HTTPException(
                status_code=400, detail="Benchmark approval did not submit a run"
            )

        await _save_provider_state(session_id, user.id, final_provider_state)
        return _benchmark_submit_out(payload)

    row = await _get_session_provider_scope(session_id, user.id)
    scope = ChatScope.model_validate(_json_dict(row["scope"]))
    payload = await submit_benchmark_for_session(user.id, scope, session_id)
    if payload.get("error"):
        raise HTTPException(status_code=400, detail=payload["error"])
    if not isinstance(payload.get("run_id"), str) or not isinstance(
        payload.get("jobs"), list
    ):
        raise HTTPException(status_code=400, detail="Benchmark config is not runnable")

    return _benchmark_submit_out(payload)


@router.post(
    "/sessions/{session_id}/benchmark/approval", status_code=status.HTTP_204_NO_CONTENT
)
async def deny_session_benchmark_approval(
    session_id: str, body: BenchmarkApprovalIn, user: CurrentUser
):
    final_provider_state, _ = await _resume_deferred_benchmark_tool(
        session_id,
        user.id,
        body.approval,
        ToolDenied(body.message),
    )
    await _save_provider_state(session_id, user.id, final_provider_state)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch(
    "/sessions/{session_id}/benchmark/config", response_model=BenchmarkConfigOut
)
async def update_session_benchmark_config(
    session_id: str, body: BenchmarkConfigPatchIn, user: CurrentUser
):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT scope FROM chat_sessions WHERE id = :id AND user_id = :uid"
                    ),
                    {"id": session_id, "uid": user.id},
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    scope = ChatScope.model_validate(_json_dict(row["scope"]))
    payload = await update_benchmark_config(
        body.model_dump(exclude_none=True),
        user.id,
        scope,
        session_id,
    )
    if not isinstance(payload, dict) or not isinstance(
        payload.get("benchmark_config"), dict
    ):
        raise HTTPException(
            status_code=500, detail="Benchmark config update returned invalid payload"
        )
    return BenchmarkConfigOut(
        benchmark_config=BenchmarkRunSpec.model_validate(payload["benchmark_config"]),
        benchmark_validation=BenchmarkValidation.model_validate(
            payload["benchmark_validation"]
        ),
    )


@router.post("/sessions/{session_id}/message")
async def send_message(session_id: str, body: MessageIn, user: CurrentUser):
    if not llm_is_configured():
        raise HTTPException(status_code=503, detail="LLM is not configured")

    requested_scope = None
    if body.scope is not None:
        requested_scope = await _validate_scope(body.scope, user.id)

    async with get_db() as conn:
        row = (
            await conn.execute(
                text("SELECT id FROM chat_sessions WHERE id = :id AND user_id = :uid"),
                {"id": session_id, "uid": user.id},
            )
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    async def _generate():
        # --- Transaction 1: read state with row lock, persist user + streaming assistant turn ---
        async with get_db() as conn:
            row = (
                (
                    await conn.execute(
                        text("""
                    SELECT provider_state, transcript, scope
                    FROM chat_sessions
                    WHERE id = :id AND user_id = :uid
                    FOR UPDATE
                """),
                        {"id": session_id, "uid": user.id},
                    )
                )
                .mappings()
                .fetchone()
            )
            if not row:
                yield _stream_event(
                    "error", error_type="internal_error", message="Session not found"
                )
                return

            provider_state = _deny_unprocessed_tool_calls(
                deserialize_model_messages(row["provider_state"])
            )
            transcript = _json_list(row["transcript"])
            stored_scope = ChatScope.model_validate(_json_dict(row["scope"]))
            if requested_scope is not None:
                if (
                    requested_scope.kind != stored_scope.kind
                    or requested_scope.key != stored_scope.key
                ):
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
                content=body.content,
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
            async for event in stream_response(
                provider_state,
                user.id,
                session_id,
                scope,
                latest_user_message=body.content,
            ):
                data = _parse_llm_event(event)
                if data is None:
                    continue
                if (
                    data.get("type") == "benchmark_config"
                    and isinstance(data.get("run_id"), str)
                    and isinstance(data.get("jobs"), list)
                ):
                    scope = ChatScope(
                        kind="benchmark_run_group",
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
                    final_provider_state = data.get(
                        "provider_state", pending_provider_state
                    )
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
                    hydrated_turn = hydrate_turn_artifact_urls(
                        completed_turn, user.id
                    )
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
                user.id,
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

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _serve_chat_figure(figure_id: str, user_id: str):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("""
                SELECT storage_key
                FROM chat_artifacts
                WHERE id = :id AND user_id = :uid AND kind = 'figure'
            """),
                    {"id": figure_id, "uid": user_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Figure not found")

    storage = get_storage()
    storage_key = row["storage_key"]
    local_path = storage.chat_figure_local_path(storage_key)
    if local_path is not None:
        if not local_path.exists():
            raise HTTPException(status_code=404, detail="Figure not found")
        return FileResponse(
            local_path, media_type=guess_chat_figure_media_type(local_path)
        )
    figure = await asyncio.to_thread(storage.read_chat_figure, storage_key)
    if figure is not None:
        data, media_type = figure
        return Response(content=data, media_type=media_type)
    raise HTTPException(status_code=404, detail="Figure not found")


@router.get("/figures/{figure_id}")
async def get_chat_figure(figure_id: str, user: CurrentUser):
    return await _serve_chat_figure(figure_id, user.id)


@router.get("/figures/{figure_id}/public")
async def get_chat_figure_public(figure_id: str, exp: int, sig: str):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT user_id FROM chat_artifacts WHERE id = :id AND kind = 'figure'"
                    ),
                    {"id": figure_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Figure not found")
    user_id = row["user_id"]
    if not verify_chat_figure_signature(figure_id, user_id, exp, sig):
        raise HTTPException(status_code=403, detail="Invalid figure signature")
    return await _serve_chat_figure(figure_id, user_id)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(session_id: str, user: CurrentUser):
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    text("""
                SELECT chat_sessions.id, chat_artifacts.storage_key
                FROM chat_sessions
                LEFT JOIN chat_artifacts ON chat_artifacts.session_id = chat_sessions.id
                WHERE chat_sessions.id = :id AND chat_sessions.user_id = :uid
            """),
                    {"id": session_id, "uid": user.id},
                )
            )
            .mappings()
            .fetchall()
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Session not found")
        storage_keys = [row["storage_key"] for row in rows if row.get("storage_key")]
        for storage_key in storage_keys:
            await delete_chat_figure_artifact(storage_key)
        await conn.execute(
            text("DELETE FROM chat_sessions WHERE id = :id"), {"id": session_id}
        )
