from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Annotated, Literal

import sqlalchemy as sa
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from ai_almanac.server.auth import AdminUser, CurrentUser, OptionalCurrentUser
from ai_almanac.server.db import get_db
from ai_almanac.server.services import blend_cells, derived_outputs, job_access
from ai_almanac.server.services.artifact_store import get_artifact_store
from ai_almanac.server.services.artifacts import list_job_artifacts
from ai_almanac.server.services.blend_cells import BlendCellMetrics
from ai_almanac.server.services.blend_forecast import (
    ForecastModel,
    ForecastResolution,
    ForecastView,
    UnsupportedForecastView,
    available_views,
    parse_blend_forecast,
    parse_forecast_view,
)
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
from ai_almanac.server.tables import job_artifacts, jobs, user_hidden_jobs

from ..services.metrics import (
    JobCellResponse,
    JobGrids,
    JobMetrics,
    build_job_grids,
    compute_job_cell,
    compute_job_metrics,
)
from ..services.skill_scores import JobSkillScores, compute_job_skill_scores
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
async def list_jobs(user: OptionalCurrentUser):
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.select(jobs)
                    .where(
                        job_access.listing_filter(user.id if user else None),
                        jobs.c.run_id.is_not(None),
                    )
                    .order_by(jobs.c.created_at.desc())
                )
            )
            .mappings()
            .fetchall()
        )
    catalog = await load_catalog()
    # Anonymous callers pass "" (never None — the converters treat None as owner).
    return [row_to_job_out(dict(r), user.id if user else "", catalog) for r in rows]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job: ReadableJob, user: OptionalCurrentUser):
    return row_to_job_out(job, user.id if user else "", await load_catalog())


async def readable_job(job_id: str, user: OptionalCurrentUser) -> dict:
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


def _job_region_id(job: dict) -> str | None:
    return json.loads(job.get("config_json") or "{}").get("region_id")


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


_EMPTY_BLEND_FORECAST = {"issue_dates": [], "points": [], "onset_threshold": None}


@router.get("/{job_id}/blend-forecast", response_model=dict)
async def get_blend_forecast(
    job_id: str,
    job: ReadableJob,
    model: ForecastModel = "weekly_model",
    resolution: ForecastResolution = "weekly",
) -> Response:
    """Return blended onset probabilities for all issue dates and grid points.

    `model` picks the week-level or day-level blend; `resolution` bins its
    probabilities by week or by day (only the day-level blend has days). The
    probabilities CSV is reshaped into per-point series once per view, stored
    beside the job's outputs, and served from there on later reads.
    """
    from ai_almanac.server.services.region_catalog import get_region

    try:
        view = parse_forecast_view(model, resolution)
    except UnsupportedForecastView as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    _require_complete(job)

    # The region defines what "onset" means (e.g. India → Modified Moron–Robertson,
    # Ethiopia → Kiremt); surface its name + definition so the UI can keep it visible.
    # Read per request rather than stored: region descriptions can be edited.
    region_id = _job_region_id(job)
    region = await get_region(region_id) if region_id else None
    region_context = {
        "region_id": region_id,
        "region_name": (region or {}).get("display_name"),
        "onset_definition": (region or {}).get("description"),
    }

    artifacts = await list_job_artifacts(job_id)
    # Per request, not stored: tells the viewer which views this forecast can offer
    # (forecasts from blends trained before the day-level blend have only one).
    views = {"available_views": available_views(a["filename"] for a in artifacts)}
    artifact = next((a for a in artifacts if a["filename"] == view.source_filename), None)
    payload = (
        await derived_outputs.load_or_build(
            job_id, view.payload_name, lambda: _build_blend_forecast(job_id, artifact, view)
        )
        if artifact
        else None
    )
    forecast = json.loads(payload) if payload else _EMPTY_BLEND_FORECAST
    # Serialized directly: FastAPI's encoder is slow on ~100k nested probabilities.
    return Response(
        json.dumps({**forecast, **region_context, **views}), media_type="application/json"
    )


def _build_blend_forecast(job_id: str, artifact: dict, view: ForecastView) -> bytes | None:
    text = get_storage().read_result_text(job_id, artifact["kind"], artifact["filename"])
    return json.dumps(parse_blend_forecast(text, view.columns)).encode() if text else None


_BLEND_SUMMARY_PREFIXES = {"pooled": "summary_models_pooled", "yearly": "yearly_metrics_global"}


@router.get("/{job_id}/blend-summary")
async def get_blend_summary(
    job_id: str, job: ReadableJob, table: Literal["pooled", "yearly"] = "pooled"
) -> dict:
    """Return one of the blend's small CV summary CSVs, read server-side.

    `pooled` is the per-model summary behind the skill chart; `yearly` is the
    per-holdout-year CV scores. Serving them here keeps the outputs bucket off
    the client (mirroring how metrics read outputs).
    """
    _require_complete(job)
    prefix = _BLEND_SUMMARY_PREFIXES[table]
    summary = next(
        (
            a
            for a in await list_job_artifacts(job_id)
            if a["filename"].startswith(prefix) and a["filename"].endswith(".csv")
        ),
        None,
    )
    if summary is None:
        return {"csv": ""}
    text = await asyncio.to_thread(
        get_storage().read_result_text, job_id, summary["kind"], summary["filename"]
    )
    return {"csv": text or ""}


@router.get("/{job_id}/blend-cell-metrics")
async def get_blend_cell_metrics(
    job_id: str,
    job: ReadableJob,
    model: blend_cells.BlendModel = blend_cells.DEFAULT_BLEND_MODEL,
) -> BlendCellMetrics:
    """Return one blend's per-grid-point skill, reshaped into grids for the map.

    Returns empty ``grids`` rather than 404 when the per-cell summary is absent or
    lacks the requested blend and baseline rows: the frontend's request wrapper
    throws on any non-OK status, so a 404 would paint an error state over a run
    that simply has nothing to map. ``available_models`` names the blends the
    summary does score, so a blend trained before the day-level blend existed
    offers no choice.
    """
    _require_complete(job)
    summary = next(
        (
            a
            for a in await list_job_artifacts(job_id)
            if blend_cells.is_per_cell_summary(a["filename"])
        ),
        None,
    )
    if summary is None:
        return blend_cells.build_cell_metrics(
            job_id, "", region_id=_job_region_id(job), model=model
        )
    text = await asyncio.to_thread(
        get_storage().read_result_text, job_id, summary["kind"], summary["filename"]
    )
    return await asyncio.to_thread(
        blend_cells.build_cell_metrics,
        job_id,
        text or "",
        region_id=_job_region_id(job),
        model=model,
    )


@router.get("/{job_id}/results/{kind}/{filename:path}")
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
    stream = await asyncio.to_thread(storage.open_result_stream, job_id, kind, filename)
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


@router.get("/{job_id}/skill-scores", response_model=JobSkillScores)
async def get_skill_scores(job_id: str, job: ReadableJob):
    """Probabilistic skill scores parsed from ROMP's skill-score CSVs.

    Deterministic jobs produce no skill CSVs, so an empty ``windows`` list is a
    normal response rather than a 404 — ROMP's deterministic and probabilistic
    output paths are mutually exclusive.
    """
    _require_complete(job)
    try:
        return await asyncio.to_thread(compute_job_skill_scores, job_id, get_storage())
    except Exception as e:
        logger.exception("Error reading skill scores for job %s", job_id)
        raise HTTPException(status_code=500, detail=str(e)) from e


_JOB_GRIDS_PAYLOAD = "map_grids.v1.json"


@router.get("/{job_id}/grids", response_model=JobGrids)
async def get_grids(job_id: str, job: ReadableJob) -> Response:
    """Every map grid for the job in one response, built once and then served as stored bytes."""
    _require_complete(job)
    try:
        payload = await derived_outputs.load_or_build(
            job_id,
            _JOB_GRIDS_PAYLOAD,
            lambda: build_job_grids(job_id, get_storage()).model_dump_json().encode(),
        )
    except Exception as e:
        logger.exception("Error building grids for job %s", job_id)
        raise HTTPException(status_code=500, detail=str(e)) from e
    return Response(content=payload, media_type="application/json")


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


async def _hide_example_job(job_id: str, user: CurrentUser) -> None:
    async with get_db() as conn:
        # Upsert so concurrent deletes of the same example stay idempotent
        # (valid on both SQLite and Postgres, like get_or_create_user).
        result = await conn.execute(
            sa.text(
                "INSERT INTO user_hidden_jobs (user_id, job_id, created_at) "
                "VALUES (:user_id, :job_id, :now) "
                "ON CONFLICT (user_id, job_id) DO NOTHING"
            ),
            {
                "user_id": user.id,
                "job_id": job_id,
                "now": datetime.now(UTC).isoformat(),
            },
        )
        if result.rowcount:
            await audit(
                conn,
                "job.hidden",
                user_id=user.id,
                resource_type="job",
                resource_id=job_id,
            )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(job_id: str, user: CurrentUser):
    job = await job_access.fetch_job(job_id)
    if not job or not job_access.can_read(job, user):
        raise HTTPException(status_code=404, detail="Job not found")
    if (job.get("visibility") or "private") == "example":
        # Examples are shared with everyone; deleting only hides the job from
        # the caller's view — even for the owner or an admin. Demote it first
        # (POST /jobs/{id}/unshare) to actually delete it.
        await _hide_example_job(job_id, user)
        return
    if not job_access.can_modify(job, user):
        raise HTTPException(status_code=404, detail="Job not found")
    if job["status"] in ACTIVE_STATUSES:
        raise HTTPException(status_code=409, detail="Cancel the job before deleting it.")
    async with get_db() as conn:
        # Remove indexed artifact records and hide markers, then the job.
        # (Explicit delete rather than relying on SQLite FK cascade, which is
        # off by default.)
        await conn.execute(sa.delete(job_artifacts).where(job_artifacts.c.job_id == job_id))
        await conn.execute(sa.delete(user_hidden_jobs).where(user_hidden_jobs.c.job_id == job_id))
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


async def _retain_outputs(job_id: str, keep: bool) -> None:
    await asyncio.to_thread(get_artifact_store().retain, job_id, keep)


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
        # Inside the transaction: if storage can't apply the hold, the
        # visibility change rolls back instead of leaving an unprotected example.
        await _retain_outputs(job["id"], visibility == "example")
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
    """Return a shared or example job to private (owner/admin only)."""
    return await _set_job_visibility(job, "private", user)


@router.post("/{job_id}/example", response_model=JobOut)
async def promote_job_to_example(job: ModifiableJob, user: AdminUser):
    """Feature a completed job as the shared example every user sees."""
    _require_complete(job)
    if job["job_type"] == "benchmark" and job["run_id"]:
        # A benchmark run is a group of sibling jobs (one per model); promote
        # the completed siblings together so the group renders whole.
        async with get_db() as conn:
            promoted = (
                (
                    await conn.execute(
                        sa.update(jobs)
                        .where(
                            jobs.c.run_id == job["run_id"],
                            # run_id is client-supplied, so scope to the promoted
                            # job's owner: a legitimate run's siblings share one.
                            jobs.c.user_id == job["user_id"],
                            jobs.c.job_type == "benchmark",
                            jobs.c.status == "complete",
                        )
                        .values(visibility="example")
                        .returning(jobs.c.id)
                    )
                )
                .scalars()
                .all()
            )
            for sibling_id in promoted:
                if sibling_id == job["id"]:
                    continue  # retained and audited below by _set_job_visibility
                await _retain_outputs(sibling_id, keep=True)
                await audit(
                    conn,
                    "job.example",
                    user_id=user.id,
                    resource_type="job",
                    resource_id=sibling_id,
                )
    return await _set_job_visibility(job, "example", user)
