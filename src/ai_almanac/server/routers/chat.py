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
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field
from pydantic_ai import ToolDenied
from sqlalchemy import text

from ai_almanac.server.auth import CurrentUser
from ai_almanac.server.db import get_db
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
from ..services.chat_state import (
    ChatScope,
    ChatTurn,
)
from ..services.chat_tools import (
    SubmitBenchmarkApproval,
    SubmitBlendApproval,
)
from ..services.chat_turns import (
    get_session_provider_scope,
    json_dict,
    json_list,
    resume_deferred_setup_tool,
    save_provider_state,
    stream_chat_turn,
    validate_scope,
)
from ..services.llm import llm_is_configured
from ..services.rulesets import selectable_ruleset
from ..services.llm_profiles import chat_available_for_user
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


class BenchmarkSubmitIn(BaseModel):
    approval: SubmitBenchmarkApproval | None = None


class BenchmarkApprovalIn(BaseModel):
    approval: SubmitBenchmarkApproval
    message: str = "The user declined to run the benchmark."


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


class BlendSubmitIn(BaseModel):
    approval: SubmitBlendApproval | None = None


class BlendApprovalIn(BaseModel):
    approval: SubmitBlendApproval
    message: str = "The user declined to train the blend."


class BlendConfigOut(BaseModel):
    blend_config: BlendRunSpec
    blend_validation: BlendValidation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _benchmark_config(value: object) -> BenchmarkRunSpec | None:
    parsed = json_dict(value)
    return BenchmarkRunSpec.model_validate(parsed) if parsed else None


def _benchmark_validation(value: object) -> BenchmarkValidation | None:
    parsed = json_dict(value)
    return BenchmarkValidation.model_validate(parsed) if parsed else None


def _benchmark_submit_out(payload: dict) -> BenchmarkSubmitOut:
    # ``submit`` returns benchmark_config/benchmark_validation; the resumed
    # approval path returns the generic config/validation keys.
    return BenchmarkSubmitOut(
        run_id=payload["run_id"],
        jobs=payload["jobs"],
        benchmark_config=BenchmarkRunSpec.model_validate(
            payload.get("benchmark_config") or payload["config"]
        ),
        benchmark_validation=BenchmarkValidation.model_validate(
            payload.get("benchmark_validation") or payload["validation"]
        ),
    )


def _blend_submit_out(payload: dict) -> BlendSubmitOut:
    return BlendSubmitOut(
        run_id=payload["run_id"],
        jobs=payload["jobs"],
        blend_config=BlendRunSpec.model_validate(payload.get("blend_config") or payload["config"]),
        blend_validation=BlendValidation.model_validate(
            payload.get("blend_validation") or payload["validation"]
        ),
    )


def _blend_config(value: object) -> BlendRunSpec | None:
    parsed = json_dict(value)
    return BlendRunSpec.model_validate(parsed) if parsed else None


def _blend_validation(value: object) -> BlendValidation | None:
    parsed = json_dict(value)
    return BlendValidation.model_validate(parsed) if parsed else None


def _session_out(row) -> SessionOut:
    transcript = json_list(row["transcript"])
    return SessionOut(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=len(transcript),
        scope=ChatScope.model_validate(json_dict(row["scope"])),
        benchmark_config=_benchmark_config(row.get("benchmark_config")),
        benchmark_validation=_benchmark_validation(row.get("benchmark_validation")),
        blend_config=_blend_config(row.get("blend_config")),
        blend_validation=_blend_validation(row.get("blend_validation")),
        run_id=row.get("run_id"),
        ruleset_id=row.get("ruleset_id"),
    )


def _session_detail(row, user_id: str) -> SessionDetail:
    transcript = json_list(row["transcript"])
    return SessionDetail(
        **_session_out(row).model_dump(),
        transcript=[
            hydrate_turn_artifact_urls(ChatTurn.model_validate(turn), user_id)
            for turn in transcript
        ],
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


async def _require_selectable_ruleset(ruleset_id: str) -> None:
    if await selectable_ruleset(ruleset_id) is None:
        raise HTTPException(status_code=400, detail=f"Ruleset not available: {ruleset_id}")


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(body: SessionCreate, user: CurrentUser):
    await require_chat_available(user.id)
    if body.ruleset_id is not None:
        await _require_selectable_ruleset(body.ruleset_id)

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

    return [_session_out(row) for row in rows]


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str, user: CurrentUser):
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

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    return _session_detail(row, user.id)


@router.patch("/sessions/{session_id}", response_model=SessionOut)
async def update_session(session_id: str, body: SessionUpdate, user: CurrentUser):
    updates = body.model_dump(exclude_unset=True)
    assignments = ["updated_at = :now"]
    params: dict[str, object] = {"id": session_id, "uid": user.id, "now": _now()}

    if "title" in updates:
        title = updates["title"].strip() if isinstance(updates["title"], str) else None
        params["title"] = title or None
        assignments.append("title = :title")
    if "ruleset_id" in updates:
        if updates["ruleset_id"] is not None:
            await _require_selectable_ruleset(updates["ruleset_id"])
        params["ruleset_id"] = updates["ruleset_id"]
        assignments.append("ruleset_id = :ruleset_id")

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(f"""
                UPDATE chat_sessions
                SET {", ".join(assignments)}
                WHERE id = :id AND user_id = :uid
                RETURNING *
            """),
                    params,
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    return _session_out(row)


@router.post("/sessions/{session_id}/benchmark/submit", response_model=BenchmarkSubmitOut)
async def submit_session_benchmark(
    session_id: str, user: CurrentUser, body: BenchmarkSubmitIn | None = None
):
    if body and body.approval:
        final_provider_state, payload = await resume_deferred_setup_tool(
            session_id,
            user.id,
            body.approval,
            True,
        )
        if payload is None:
            raise HTTPException(status_code=400, detail="Benchmark approval did not submit a run")

        await save_provider_state(session_id, user.id, final_provider_state)
        return _benchmark_submit_out(payload)

    row = await get_session_provider_scope(session_id, user.id)
    scope = ChatScope.model_validate(json_dict(row["scope"]))
    payload = await submit_benchmark_for_session(user.id, scope, session_id)
    if payload.get("error"):
        raise HTTPException(status_code=400, detail=payload["error"])
    if not isinstance(payload.get("run_id"), str) or not isinstance(payload.get("jobs"), list):
        raise HTTPException(status_code=400, detail="Benchmark config is not runnable")

    return _benchmark_submit_out(payload)


@router.post("/sessions/{session_id}/benchmark/approval", status_code=status.HTTP_204_NO_CONTENT)
async def deny_session_benchmark_approval(
    session_id: str, body: BenchmarkApprovalIn, user: CurrentUser
):
    final_provider_state, _ = await resume_deferred_setup_tool(
        session_id,
        user.id,
        body.approval,
        ToolDenied(body.message),
    )
    await save_provider_state(session_id, user.id, final_provider_state)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/sessions/{session_id}/benchmark/config", response_model=BenchmarkConfigOut)
async def update_session_benchmark_config(
    session_id: str, body: BenchmarkConfigPatchIn, user: CurrentUser
):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT scope FROM chat_sessions WHERE id = :id AND user_id = :uid"),
                    {"id": session_id, "uid": user.id},
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    scope = ChatScope.model_validate(json_dict(row["scope"]))
    payload = await update_benchmark_config(
        body.model_dump(exclude_none=True),
        user.id,
        scope,
        session_id,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("benchmark_config"), dict):
        raise HTTPException(
            status_code=500, detail="Benchmark config update returned invalid payload"
        )
    return BenchmarkConfigOut(
        benchmark_config=BenchmarkRunSpec.model_validate(payload["benchmark_config"]),
        benchmark_validation=BenchmarkValidation.model_validate(payload["benchmark_validation"]),
    )


@router.post("/sessions/{session_id}/blend/submit", response_model=BlendSubmitOut)
async def submit_session_blend(
    session_id: str, user: CurrentUser, body: BlendSubmitIn | None = None
):
    if body and body.approval:
        final_provider_state, payload = await resume_deferred_setup_tool(
            session_id,
            user.id,
            body.approval,
            True,
            config_event="blend_config",
        )
        if payload is None:
            raise HTTPException(status_code=400, detail="Blend approval did not submit a run")
        await save_provider_state(session_id, user.id, final_provider_state)
        return _blend_submit_out(payload)

    row = await get_session_provider_scope(session_id, user.id)
    scope = ChatScope.model_validate(json_dict(row["scope"]))
    payload = await submit_blend_for_session(user.id, scope, session_id)
    if payload.get("error"):
        raise HTTPException(status_code=400, detail=payload["error"])
    if not isinstance(payload.get("run_id"), str) or not isinstance(payload.get("jobs"), list):
        raise HTTPException(status_code=400, detail="Blend config is not runnable")
    return _blend_submit_out(payload)


@router.post("/sessions/{session_id}/blend/approval", status_code=status.HTTP_204_NO_CONTENT)
async def deny_session_blend_approval(session_id: str, body: BlendApprovalIn, user: CurrentUser):
    final_provider_state, _ = await resume_deferred_setup_tool(
        session_id,
        user.id,
        body.approval,
        ToolDenied(body.message),
        config_event="blend_config",
    )
    await save_provider_state(session_id, user.id, final_provider_state)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/sessions/{session_id}/blend/config", response_model=BlendConfigOut)
async def update_session_blend_config(session_id: str, body: BlendConfigPatchIn, user: CurrentUser):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT scope FROM chat_sessions WHERE id = :id AND user_id = :uid"),
                    {"id": session_id, "uid": user.id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    scope = ChatScope.model_validate(json_dict(row["scope"]))
    payload = await update_blend_config(
        body.model_dump(exclude_none=True),
        user.id,
        scope,
        session_id,
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("blend_config"), dict):
        raise HTTPException(status_code=500, detail="Blend config update returned invalid payload")
    return BlendConfigOut(
        blend_config=BlendRunSpec.model_validate(payload["blend_config"]),
        blend_validation=BlendValidation.model_validate(payload["blend_validation"]),
    )


@router.post("/sessions/{session_id}/message")
async def send_message(session_id: str, body: MessageIn, user: CurrentUser):
    await require_chat_available(user.id)

    requested_scope = None
    if body.scope is not None:
        requested_scope = await validate_scope(body.scope, user)

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT id, ruleset_id FROM chat_sessions WHERE id = :id AND user_id = :uid"
                    ),
                    {"id": session_id, "uid": user.id},
                )
            )
            .mappings()
            .fetchone()
        )

    if not row:
        raise HTTPException(status_code=404, detail="Session not found")

    # A pinned ruleset that has since been archived or deleted degrades to the
    # active one — same never-raise spirit as rulesets.active_ruleset().
    session_ruleset = await selectable_ruleset(row["ruleset_id"]) if row["ruleset_id"] else None

    return StreamingResponse(
        stream_chat_turn(
            session_id, user.id, body.content, requested_scope, active_ruleset=session_ruleset
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
        await conn.execute(text("DELETE FROM chat_sessions WHERE id = :id"), {"id": session_id})
