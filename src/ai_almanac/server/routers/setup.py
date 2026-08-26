"""Setup wizard API — install state, probes, env preparation, and completion.

All endpoints live under `/api/setup/` and vanish (404) once setup is done.
Auth is handled by `require_setup_token` (bootstrap token in X-Setup-Token),
not the normal CurrentUser/AdminUser dependency.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ai_almanac.server.services import setup as setup_svc

router = APIRouter(prefix="/api/setup", tags=["setup"])


# ---------------------------------------------------------------------------
# Auth dependency
# ---------------------------------------------------------------------------


async def require_setup_token(request: Request) -> None:
    if not setup_svc.setup_required():
        raise HTTPException(status_code=404, detail="Setup already complete")
    token = request.headers.get("x-setup-token")
    if not setup_svc.verify_bootstrap_token(token):
        raise HTTPException(status_code=401, detail="Invalid or missing setup token")


_SetupAuth = Annotated[None, Depends(require_setup_token)]


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SetupState(BaseModel):
    platform: dict
    gpu: dict | None
    data_dir: str
    config_yaml_path: str
    dataset_mount_roots: list[str]
    llm: dict
    envs: dict
    prepare: dict


class StorageInput(BaseModel):
    output_dir: str | None = None
    dataset_mount_roots: list[str] | None = None


class LlmInput(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None
    test_only: bool = False


class LlmTestOut(BaseModel):
    ok: bool
    models_ok: bool
    completion_ok: bool
    models: list[str]
    error: str | None


class PrepareInput(BaseModel):
    include_forecast: bool = True


class PrepareStatus(BaseModel):
    status: str
    started: bool


class FinishOut(BaseModel):
    ok: bool


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/state", response_model=SetupState)
async def get_state(_: _SetupAuth):
    from ai_almanac.paths import config_yaml_path, data_root
    from ai_almanac.settings import settings

    return SetupState(
        platform=setup_svc.detect_platform(),
        gpu=setup_svc.probe_gpu(),
        data_dir=str(data_root()),
        config_yaml_path=str(config_yaml_path()),
        dataset_mount_roots=[
            r.strip() for r in settings.dataset_mount_roots.split(",") if r.strip()
        ],
        llm={
            "configured": bool(settings.llm_base_url),
            "base_url": settings.llm_base_url,
            "model": settings.llm_model,
        },
        envs=setup_svc.env_status(),
        prepare={
            "status": setup_svc.prepare_task.status,
            "last_seq": setup_svc.prepare_task._seq - 1,
        },
    )


@router.post("/storage", status_code=204)
async def post_storage(body: StorageInput, _: _SetupAuth):
    setup_svc.save_storage(body.output_dir, body.dataset_mount_roots)


@router.post("/llm", response_model=LlmTestOut)
async def post_llm(body: LlmInput, _: _SetupAuth):
    result = await setup_svc.test_llm_connection(body.base_url, body.model, body.api_key)
    if not body.test_only and result.ok:
        setup_svc.save_llm(body.base_url, body.model, body.api_key)
    return LlmTestOut(
        ok=result.ok,
        models_ok=result.models_ok,
        completion_ok=result.completion_ok,
        models=result.models,
        error=result.error,
    )


@router.post("/envs/prepare", response_model=PrepareStatus)
async def post_prepare(body: PrepareInput, _: _SetupAuth):
    started = setup_svc.prepare_task.start(include_forecast=body.include_forecast)
    return PrepareStatus(status=setup_svc.prepare_task.status, started=started)


@router.get("/envs/events")
async def get_envs_events(
    _: _SetupAuth,
    after: Annotated[int, Query()] = -1,
):
    task = setup_svc.prepare_task

    async def _generate():
        async for evt in task.subscribe(after=after):
            if evt.get("type") == "keepalive":
                yield ": keepalive\n\n"
            else:
                yield f"data: {json.dumps(evt)}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/finish", response_model=FinishOut)
async def post_finish(_: _SetupAuth):
    setup_svc.finish_setup()
    return FinishOut(ok=True)
