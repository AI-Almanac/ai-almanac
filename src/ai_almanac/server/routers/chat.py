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
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from ai_almanac.server.auth import CurrentUser
from ai_almanac.server.db import get_db
from ai_almanac.server.tables import json_col
from ai_almanac.settings import settings

from ..services.benchmark_domain import (
    submit_benchmark_for_session,
    update_benchmark_config,
)
from ..services.benchmark_state import (
    BenchmarkRunSpec,
    BenchmarkValidation,
)
from ..services.blend_domain import (
    submit_blend_for_session,
    update_blend_config,
)
from ..services.blend_state import (
    BlendRunSpec,
    BlendValidation,
)
from ..services.chat_artifacts import (
    delete_chat_figure_artifact,
    hydrate_turn_artifact_urls,
    verify_chat_figure_signature,
)
from ..services.chat_job_events import ChatJobActivity, scope_job_activity
from ..services.chat_state import (
    ApprovalRequest,
    ChatScope,
    ChatTurn,
)
from ..services.chat_turns import (
    pending_approval,
    stream_approval_turn,
    stream_chat_turn,
    submitting_scope,
    validate_scope,
    with_interrupted_turns,
    workspace_scope_hidden,
)
from ..services.ledger import Attribution
from ..services.llm import deserialize_model_messages, llm_is_configured
from ..services.llm_profiles import chat_available_for_user
from ..services.rulesets import selectable_ruleset
from ..services.storage import get_storage, guess_chat_figure_media_type
from ..services.turn_log import rate_turn

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


async def require_chat_available(user_id: str) -> None:
    """Block chat when no LLM can be resolved, with an actionable message.

    Local installs use the env-configured model; shared deployments resolve a
    per-user (own or shared) profile, so the readiness check differs by mode.
    """
    if settings.deployment_mode == "shared":
        if not await chat_available_for_user(user_id):
            raise HTTPException(
                status_code=400,
                detail="No LLM is available. Enable a shared model or add your own in AI settings.",
            )
        return
    if not llm_is_configured():
        raise HTTPException(status_code=503, detail="LLM is not configured")


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class SessionCreate(BaseModel):
    title: str | None = None
    scope: ChatScope
    ruleset_id: str | None = None


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
    blend_config: BlendRunSpec | None = None
    blend_validation: BlendValidation | None = None
    run_id: str | None = None
    ruleset_id: str | None = None


class SessionDetail(SessionOut):
    transcript: list[ChatTurn]
    # The approval the assistant is still waiting on, in the shape it was streamed,
    # so a client that reloads can show the card again. Absent once it is answered.
    pending_approval: ApprovalRequest | None = None


class MessageIn(BaseModel):
    content: str
    scope: ChatScope | None = None


class TurnRatingIn(BaseModel):
    """A thumbs up/down on one assistant turn."""

    value: Literal[-1, 1]
    note: str | None = Field(default=None, max_length=2000)


class SessionUpdate(BaseModel):
    title: str | None = None
    ruleset_id: str | None = None


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


class BenchmarkConfigOut(BaseModel):
    benchmark_config: BenchmarkRunSpec
    benchmark_validation: BenchmarkValidation


class BlendSubmitOut(BaseModel):
    run_id: str
    jobs: list[dict]
    blend_config: BlendRunSpec
    blend_validation: BlendValidation


class BlendConfigPatchIn(BaseModel):
    intent: str | None = None
    name: str | None = None
    obs_dataset_id: str | None = None
    model_ids: list[str] | None = None
    training_years: str | None = None
    cv_holdout_years: str | None = None
    forecast_years: str | None = None
    true_holdout_years: str | None = None
    formula_text: str | None = None
    focus_area: dict | None = None
    threshold_mm: float | None = None
    cutoff_month_day: str | None = None
    ref_onset_month_day: str | None = None


class BlendConfigOut(BaseModel):
    blend_config: BlendRunSpec
    blend_validation: BlendValidation


class ToolApprovalIn(BaseModel):
    tool_call_id: str
    approved: bool
    message: str = "The user declined this step."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _benchmark_config(value: object) -> BenchmarkRunSpec | None:
    parsed = json_col(value, {})
    return BenchmarkRunSpec.model_validate(parsed) if parsed else None


def _benchmark_validation(value: object) -> BenchmarkValidation | None:
    parsed = json_col(value, {})
    return BenchmarkValidation.model_validate(parsed) if parsed else None


def _blend_config(value: object) -> BlendRunSpec | None:
    parsed = json_col(value, {})
    return BlendRunSpec.model_validate(parsed) if parsed else None


def _blend_validation(value: object) -> BlendValidation | None:
    parsed = json_col(value, {})
    return BlendValidation.model_validate(parsed) if parsed else None


def _session_out(row) -> SessionOut:
    transcript = json_col(row["transcript"], [])
    return SessionOut(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=len(transcript),
        scope=_scope(row),
        benchmark_config=_benchmark_config(row.get("benchmark_config")),
        benchmark_validation=_benchmark_validation(row.get("benchmark_validation")),
        blend_config=_blend_config(row.get("blend_config")),
        blend_validation=_blend_validation(row.get("blend_validation")),
        run_id=row.get("run_id"),
        ruleset_id=row.get("ruleset_id"),
    )


def _session_detail(row, user_id: str) -> SessionDetail:
    stored_transcript = json_col(row["transcript"], [])
    transcript = with_interrupted_turns(stored_transcript, row["updated_at"])
    return SessionDetail(
        **_session_out(row).model_dump(),
        transcript=[
            hydrate_turn_artifact_urls(ChatTurn.model_validate(turn), user_id)
            for turn in transcript
        ],
        pending_approval=pending_approval(
            stored_transcript,
            deserialize_model_messages(row["provider_state"]),
            row["updated_at"],
        ),
    )


def _scope(session: Any) -> ChatScope:
    return ChatScope.model_validate(json_col(session["scope"], {}))


def _hidden_from(session: Any, user: CurrentUser) -> bool:
    return workspace_scope_hidden(json_col(session["scope"], {}).get("kind"), user)


async def owned_session(session_id: str, user: CurrentUser) -> dict:
    """The caller's own chat session. A workspace conversation does not exist for anyone
    outside the workspaces audience, so it is as not found as someone else's."""
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM chat_sessions WHERE id = :id AND user_id = :uid"),
                    {"id": session_id, "uid": user.id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row or _hidden_from(row, user):
        raise HTTPException(status_code=404, detail="Session not found")
    return dict(row)


OwnedSession = Annotated[dict, Depends(owned_session)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def _require_selectable_ruleset(ruleset_id: str, *, for_admin: bool) -> None:
    if await selectable_ruleset(ruleset_id, for_admin=for_admin) is None:
        raise HTTPException(status_code=400, detail=f"Ruleset not available: {ruleset_id}")


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, user: CurrentUser):
    await require_chat_available(user.id)
    if body.ruleset_id is not None:
        await _require_selectable_ruleset(body.ruleset_id, for_admin=user.is_admin)
    await validate_scope(body.scope, user)

    session_id = str(uuid.uuid4())
    now = _now()
    initial_messages: list[dict] = []

    async with get_db() as conn:
        await conn.execute(
            text("""
                INSERT INTO chat_sessions (id, user_id, title, provider_state, scope, transcript, ruleset_id, created_at, updated_at)
                VALUES (:id, :uid, :title, :provider_state, :scope, '[]', :ruleset_id, :now, :now)
            """),
            {
                "id": session_id,
                "uid": user.id,
                "title": body.title,
                "provider_state": json.dumps(initial_messages),
                "scope": json.dumps(body.scope.model_dump(mode="json")),
                "ruleset_id": body.ruleset_id,
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
        ruleset_id=body.ruleset_id,
    )


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    user: CurrentUser,
    scope_kind: str | None = Query(default=None),
    scope_key: str | None = Query(default=None),
):
    async with get_db() as conn:
        # Scratch sessions from a ruleset comparison carry a comparison_id and are
        # not conversations the user started, so they stay out of the list.
        query = "SELECT * FROM chat_sessions WHERE user_id = :uid AND comparison_id IS NULL"
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

    return [_session_out(row) for row in rows if not _hidden_from(row, user)]


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session: OwnedSession, user: CurrentUser):
    return _session_detail(session, user.id)


@router.get("/sessions/{session_id}/job-events", response_model=ChatJobActivity)
async def get_session_job_events(session: OwnedSession, user: CurrentUser) -> ChatJobActivity:
    """Runs in the session's scope that finished since the conversation began."""
    return await scope_job_activity(user.id, _scope(session), session["created_at"])


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def update_session(session: OwnedSession, body: SessionUpdate, user: CurrentUser):
    updates = body.model_dump(exclude_unset=True)
    assignments = ["updated_at = :now"]
    params: dict[str, object] = {"id": session["id"], "now": _now()}

    if "title" in updates:
        title = updates["title"].strip() if isinstance(updates["title"], str) else None
        params["title"] = title or None
        assignments.append("title = :title")
    if "ruleset_id" in updates:
        if updates["ruleset_id"] is not None:
            await _require_selectable_ruleset(updates["ruleset_id"], for_admin=user.is_admin)
        params["ruleset_id"] = updates["ruleset_id"]
        assignments.append("ruleset_id = :ruleset_id")

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(f"""
                UPDATE chat_sessions
                SET {", ".join(assignments)}
                WHERE id = :id
                RETURNING *
            """),
                    params,
                )
            )
            .mappings()
            .one()
        )
    return _session_out(row)


@router.post("/sessions/{session_id}/benchmark/submit", response_model=BenchmarkSubmitOut)
async def submit_session_benchmark(session: OwnedSession, user: CurrentUser):
    """Submit the session's draft as the user, from the setup form's own control."""
    session_id = session["id"]
    payload = await submit_benchmark_for_session(
        user.id, submitting_scope(session), session_id, Attribution.by_user(user.id)
    )
    if payload.get("error"):
        raise HTTPException(status_code=400, detail=payload["error"])
    if not isinstance(payload.get("run_id"), str) or not isinstance(payload.get("jobs"), list):
        raise HTTPException(status_code=400, detail="Benchmark config is not runnable")
    return BenchmarkSubmitOut.model_validate(payload)


@router.patch("/sessions/{session_id}/benchmark/config", response_model=BenchmarkConfigOut)
async def update_session_benchmark_config(
    session: OwnedSession, body: BenchmarkConfigPatchIn, user: CurrentUser
):
    try:
        payload = await update_benchmark_config(
            body.model_dump(exclude_unset=True), user.id, _scope(session), session["id"]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("benchmark_config"), dict):
        raise HTTPException(
            status_code=500, detail="Benchmark config update returned invalid payload"
        )
    return BenchmarkConfigOut(
        benchmark_config=BenchmarkRunSpec.model_validate(payload["benchmark_config"]),
        benchmark_validation=BenchmarkValidation.model_validate(payload["benchmark_validation"]),
    )


@router.post("/sessions/{session_id}/blend/submit", response_model=BlendSubmitOut)
async def submit_session_blend(session: OwnedSession, user: CurrentUser):
    """Submit the session's draft as the user, from the setup form's own control."""
    session_id = session["id"]
    payload = await submit_blend_for_session(
        user.id, submitting_scope(session), session_id, Attribution.by_user(user.id)
    )
    if payload.get("error"):
        raise HTTPException(status_code=400, detail=payload["error"])
    if not isinstance(payload.get("run_id"), str) or not isinstance(payload.get("jobs"), list):
        raise HTTPException(status_code=400, detail="Blend config is not runnable")
    return BlendSubmitOut.model_validate(payload)


@router.post("/sessions/{session_id}/tool-approval")
async def decide_tool_approval(session: OwnedSession, body: ToolApprovalIn, user: CurrentUser):
    """Approve or decline a step the assistant paused for, a run's submission included;
    streams the rest of the turn as SSE, like a message."""
    return StreamingResponse(
        stream_approval_turn(
            session["id"],
            user.id,
            body.tool_call_id,
            body.approved,
            message=body.message,
            for_admin=user.is_admin,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.patch("/sessions/{session_id}/blend/config", response_model=BlendConfigOut)
async def update_session_blend_config(
    session: OwnedSession, body: BlendConfigPatchIn, user: CurrentUser
):
    try:
        payload = await update_blend_config(
            body.model_dump(exclude_unset=True), user.id, _scope(session), session["id"]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("blend_config"), dict):
        raise HTTPException(status_code=500, detail="Blend config update returned invalid payload")
    return BlendConfigOut(
        blend_config=BlendRunSpec.model_validate(payload["blend_config"]),
        blend_validation=BlendValidation.model_validate(payload["blend_validation"]),
    )


@router.post("/sessions/{session_id}/message")
async def send_message(session: OwnedSession, body: MessageIn, user: CurrentUser):
    await require_chat_available(user.id)
    requested_scope = await validate_scope(body.scope, user) if body.scope is not None else None
    return StreamingResponse(
        stream_chat_turn(
            session["id"], user.id, body.content, requested_scope, for_admin=user.is_admin
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/sessions/{session_id}/turns/{turn_id}/rating", status_code=status.HTTP_204_NO_CONTENT
)
async def rate_chat_turn(
    session_id: str, turn_id: str, body: TurnRatingIn, user: CurrentUser
) -> None:
    """Rate one assistant turn.

    Open to any authenticated user for their own conversation — rating your own
    turn is not a privileged action, and the write is scoped by user_id. The
    thumbs are only *rendered* for admins today, so opening the sample wider is a
    UI change rather than an API one.
    """
    rated = await rate_turn(session_id, turn_id, user.id, body.value, body.note)
    if not rated:
        raise HTTPException(status_code=404, detail="Turn not found")


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
        return FileResponse(local_path, media_type=guess_chat_figure_media_type(local_path))
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
                    text("SELECT user_id FROM chat_artifacts WHERE id = :id AND kind = 'figure'"),
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
async def delete_session(session: OwnedSession):
    async with get_db() as conn:
        storage_keys = (
            await conn.execute(
                text(
                    "SELECT storage_key FROM chat_artifacts"
                    " WHERE session_id = :id AND storage_key IS NOT NULL"
                ),
                {"id": session["id"]},
            )
        ).scalars()
        for storage_key in storage_keys.all():
            await delete_chat_figure_artifact(storage_key)
        await conn.execute(text("DELETE FROM chat_sessions WHERE id = :id"), {"id": session["id"]})
