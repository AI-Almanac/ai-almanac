from __future__ import annotations

import asyncio
import logging
from typing import Annotated

import sqlalchemy as sa
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ai_almanac.server.auth import CurrentUser, authenticate_websocket
from ai_almanac.server.db import get_db
from ai_almanac.server.services import job_access
from ai_almanac.server.services.artifact_store import get_artifact_store
from ai_almanac.server.services.artifacts import list_job_artifacts
from ai_almanac.server.services.events import audit
from ai_almanac.server.services.job_manager import (
    ACTIVE_STATUSES,
    signal_cancel,
)
from ai_almanac.server.services.job_submission import (
    JobCreate,
    JobOut,
    create_job_for_user,
    row_to_job_out,
)
from ai_almanac.server.services.registry import load_catalog, load_model_registry
from ai_almanac.server.tables import job_artifacts, jobs

from ..services.metrics import (
    JobCellResponse,
    JobGridResponse,
    JobMetrics,
    compute_job_cell,
    compute_job_grid,
    compute_job_metrics,
)
from ..services.storage import GCSStorage, get_storage

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ResultFile(BaseModel):
    name: str
    type: str  # "output" | "figure"
    url: str


class ArtifactOut(BaseModel):
    id: str
    kind: str
    filename: str
    media_type: str
    size_bytes: int
    checksum: str
    created_at: str
    url: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_metrics_cache(value: object) -> JobMetrics | None:
    if not value:
        return None
    try:
        if isinstance(value, (str, bytes, bytearray)):
            return JobMetrics.model_validate_json(value)
        return JobMetrics.model_validate(value)
    except (TypeError, ValueError):
        logger.warning("Ignoring invalid persisted metrics cache")
        return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/models")
async def list_models(user: CurrentUser, region: str | None = None):
    return await load_model_registry(region, user_id=user.id, is_admin=user.is_admin)


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(body: JobCreate, user: CurrentUser):
    return await create_job_for_user(body, user.id)


@router.get("", response_model=list[JobOut])
async def list_jobs(user: CurrentUser):
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.select(jobs)
                    .where(jobs.c.user_id == user.id, jobs.c.run_id.is_not(None))
                    .order_by(jobs.c.created_at.desc())
                )
            )
            .mappings()
            .fetchall()
        )
    catalog = await load_catalog()
    return [row_to_job_out(dict(r), user.id, catalog) for r in rows]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job: ReadableJob, user: CurrentUser):
    return row_to_job_out(job, user.id, await load_catalog())


async def readable_job(job_id: str, user: CurrentUser) -> dict:
    job = await job_access.fetch_job(job_id)
    if not job or not job_access.can_read(job, user):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def modifiable_job(job_id: str, user: CurrentUser) -> dict:
    job = await job_access.fetch_job(job_id)
    if not job or not job_access.can_modify(job, user):
        raise HTTPException(status_code=404, detail="Job not found")
    return job


ReadableJob = Annotated[dict, Depends(readable_job)]
ModifiableJob = Annotated[dict, Depends(modifiable_job)]


def _require_complete(job: dict) -> None:
    if job["status"] != "complete":
        raise HTTPException(
            status_code=409, detail=f"Job is not complete (status: {job['status']})"
        )


@router.websocket("/{job_id}/stream")
async def stream_job(ws: WebSocket, job_id: str) -> None:
    """Stream durable log additions and status changes to the job's owner."""
    user = await authenticate_websocket(ws)
    if user is None:
        return  # handshake already closed (missing identity in shared mode)
    await ws.accept()

    job = await job_access.fetch_job(job_id)
    if job is None or not job_access.can_read(job, user):
        # Don't leak existence of other users' jobs; reject uniformly.
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    storage = get_storage()
    sent_lines = 0
    previous_status: str | None = None
    try:
        while True:
            logs = await asyncio.to_thread(storage.read_log, job_id)
            lines = logs.splitlines()
            for line in lines[sent_lines:]:
                await ws.send_json({"type": "log", "payload": {"line": line}})
            sent_lines = len(lines)

            async with get_db() as conn:
                row = (
                    (
                        await conn.execute(
                            sa.select(jobs.c.status, jobs.c.exit_code).where(
                                jobs.c.id == job_id
                            )
                        )
                    )
                    .mappings()
                    .fetchone()
                )
            if not row:
                await ws.send_json(
                    {"type": "done", "payload": {"status": "deleted"}}
                )
                break
            if row["status"] != previous_status:
                previous_status = row["status"]
                await ws.send_json(
                    {"type": "status", "payload": {"status": row["status"]}}
                )
            if row["status"] in ("complete", "failed", "canceled"):
                await ws.send_json(
                    {
                        "type": "done",
                        "payload": {
                            "status": row["status"],
                            "exit_code": row["exit_code"],
                        },
                    }
                )
                break
            await asyncio.sleep(0.75)
    except WebSocketDisconnect:
        pass


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job: ModifiableJob, user: CurrentUser):
    row = await signal_cancel(job["id"]) or job
    return row_to_job_out(row, user.id, await load_catalog())


@router.get("/{job_id}/logs")
async def get_logs(job_id: str, job: ReadableJob) -> dict:
    logs = await asyncio.to_thread(get_storage().read_log, job_id)
    return {"logs": logs}


@router.get("/{job_id}/results", response_model=list[ResultFile])
async def get_results(job_id: str, job: ReadableJob):
    _require_complete(job)
    storage = get_storage()
    files = await asyncio.to_thread(storage.list_result_files, job_id)
    return [
        ResultFile(
            name=filename,
            type=kind,
            url=storage.generate_result_url(job_id, kind, filename),
        )
        for kind, filename in files
    ]


@router.get("/{job_id}/artifacts", response_model=list[ArtifactOut])
async def list_artifacts(job_id: str, job: ReadableJob):
    """Return the job's indexed artifacts (published on completion)."""
    storage = get_storage()
    return [
        ArtifactOut(
            **row,
            url=storage.generate_result_url(job_id, row["kind"], row["filename"]),
        )
        for row in await list_job_artifacts(job_id)
    ]


@router.get("/{job_id}/blend-summary")
async def get_blend_summary(job_id: str, job: ReadableJob) -> dict:
    """Return the blend's pooled summary CSV, read server-side.

    The browser parses this for the skill chart; serving it here keeps the
    outputs bucket off the client (mirroring how metrics read outputs).
    """
    _require_complete(job)
    summary = next(
        (
            a
            for a in await list_job_artifacts(job_id)
            if a["filename"].startswith("summary_models_pooled")
        ),
        None,
    )
    if summary is None:
        return {"csv": ""}
    text = await asyncio.to_thread(
        get_storage().read_result_text, job_id, summary["kind"], summary["filename"]
    )
    return {"csv": text or ""}


@router.get("/{job_id}/results/{kind}/{filename}")
async def get_result_file(job_id: str, kind: str, filename: str, job: ReadableJob):
    """Serve a result file from this origin — a local file or a proxied GCS stream."""
    if kind not in ("output", "figure"):
        raise HTTPException(status_code=400, detail="kind must be 'output' or 'figure'")
    _require_complete(job)
    storage = get_storage()
    local_path = storage.result_file_path(job_id, kind, filename)

    if local_path is not None:
        if not local_path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        from fastapi.responses import FileResponse

        return FileResponse(local_path)

    # Remote (GCS): proxy the bytes so the browser never reads the bucket
    # cross-origin, which has no CORS policy for the frontend origin.
    assert isinstance(storage, GCSStorage)
    stream = await asyncio.to_thread(
        storage.open_result_stream, job_id, kind, filename
    )
    if stream is None:
        raise HTTPException(status_code=404, detail="File not found")
    body, media_type, size = stream
    headers = {"Content-Length": str(size)} if size else {}
    return StreamingResponse(body, media_type=media_type, headers=headers)


@router.get("/{job_id}/metrics", response_model=JobMetrics)
async def get_metrics(
    job_id: str,
    job: ReadableJob,
    lat_min: float | None = None,
    lat_max: float | None = None,
    lon_min: float | None = None,
    lon_max: float | None = None,
):
    _require_complete(job)
    has_bbox = any(v is not None for v in (lat_min, lat_max, lon_min, lon_max))

    # Return cached result when no bbox filter is applied.
    if not has_bbox and (cached_metrics := _parse_metrics_cache(job["metrics_cache"])):
        return cached_metrics

    try:
        result = await asyncio.to_thread(
            compute_job_metrics,
            job_id,
            get_storage(),
            lat_min,
            lat_max,
            lon_min,
            lon_max,
        )
    except Exception as e:
        logger.exception("Error computing metrics for job %s", job_id)
        raise HTTPException(status_code=500, detail=str(e)) from e

    # Persist unfiltered result for future requests.
    if not has_bbox:
        async with get_db() as conn:
            await conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(metrics_cache=result.model_dump(mode="json"))
            )

    return result


@router.get("/{job_id}/grid", response_model=JobGridResponse)
async def get_grid(
    job_id: str, job: ReadableJob, model: str, window: str, metric: str
):
    _require_complete(job)
    try:
        return await asyncio.to_thread(
            compute_job_grid, job_id, get_storage(), model, window, metric
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception(
            "Error computing grid for job %s model=%s window=%s metric=%s",
            job_id,
            model,
            window,
            metric,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{job_id}/cell", response_model=JobCellResponse)
async def get_cell(
    job_id: str,
    job: ReadableJob,
    model: str,
    window: str,
    lat: float,
    lon: float,
):
    _require_complete(job)
    try:
        return await asyncio.to_thread(
            compute_job_cell, job_id, get_storage(), model, window, lat, lon
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.exception(
            "Error computing cell for job %s model=%s window=%s lat=%s lon=%s",
            job_id,
            model,
            window,
            lat,
            lon,
        )
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, job: ModifiableJob):
    if job["status"] in ACTIVE_STATUSES:
        raise HTTPException(
            status_code=409, detail="Cancel the job before deleting it."
        )
    async with get_db() as conn:
        # Remove indexed artifact records, then the job. (Explicit delete rather
        # than relying on SQLite FK cascade, which is off by default.)
        await conn.execute(
            sa.delete(job_artifacts).where(job_artifacts.c.job_id == job_id)
        )
        await conn.execute(sa.delete(jobs).where(jobs.c.id == job_id))
        await audit(
            conn,
            "job.deleted",
            user_id=job["user_id"],
            resource_type="job",
            resource_id=job_id,
        )

    # Remove the workspace and its files; datasets are untouched.
    await asyncio.to_thread(get_artifact_store().delete_job, job_id)


async def _set_job_visibility(job: dict, visibility: str, user) -> JobOut:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    sa.update(jobs)
                    .where(jobs.c.id == job["id"])
                    .values(visibility=visibility)
                    .returning(jobs)
                )
            )
            .mappings()
            .fetchone()
        )
        await audit(
            conn,
            f"job.{visibility}",
            user_id=user.id,
            resource_type="job",
            resource_id=job["id"],
        )
    return row_to_job_out(dict(row), user.id, await load_catalog())


@router.post("/{job_id}/share", response_model=JobOut)
async def share_job(job: ModifiableJob, user: CurrentUser):
    """Make a job readable by other authenticated users (read-only)."""
    return await _set_job_visibility(job, "shared", user)


@router.post("/{job_id}/unshare", response_model=JobOut)
async def unshare_job(job: ModifiableJob, user: CurrentUser):
    """Return a shared job to private (owner/admin only)."""
    return await _set_job_visibility(job, "private", user)
