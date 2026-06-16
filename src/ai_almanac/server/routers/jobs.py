import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import text

from ai_almanac.server.attribution import CurrentUser
from ai_almanac.server.db import get_db
from ai_almanac.server.services import data_sources as data_source_service
from ai_almanac.server.services.job_manager import (
    ACTIVE_STATUSES,
    launch_job,
    request_cancel,
)
from ai_almanac.settings import (
    get_model_registry,
    get_region,
    get_regions,
)

from ..services.metrics import (
    JobCellResponse,
    JobGridResponse,
    JobMetrics,
    compute_job_cell,
    compute_job_grid,
    compute_job_metrics,
)
from ..services.storage import get_storage

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class RompParams(BaseModel):
    obs: str | None = None
    obs_file_pattern: str | None = None
    obs_var: str | None = None
    model_var: str | None = None
    file_pattern: str | None = None
    region: str | None = None
    event_type: str | None = None
    wet_threshold: float | None = None
    wet_init: float | None = None
    wet_spell: int | None = None
    dry_spell: int | None = None
    dry_extent: int | None = None
    start_date: str | None = None
    end_date: str | None = None
    start_year_clim: int | None = None
    end_year_clim: int | None = None
    max_forecast_day: int | None = None
    probabilistic: bool | None = None
    members: str | None = None
    parallel: bool | None = None
    ref_model: str | None = None
    init_days: str | None = None
    lat_min: float | None = None
    lat_max: float | None = None
    lon_min: float | None = None
    lon_max: float | None = None
    land_only: bool | None = None
    shp_only: bool | None = None
    nc_mask: str | None = None
    ref_model_dir: str | None = None
    thresh_file: str | None = None


class JobCreate(BaseModel):
    dataset_id: str
    model_name: str
    obs_dir: str | None = None
    params: RompParams = RompParams()
    run_id: str | None = None


class JobOut(BaseModel):
    id: str
    dataset_id: str
    status: str
    model_name: str
    model_display_name: str
    model_source_id: str | None = None
    model_dir: str | None = None
    obs_dir: str | None = None
    params: dict | None = None
    region_id: str | None = None
    region_name: str | None = None
    romp_region: str | None = None
    created_at: str
    started_at: str | None
    completed_at: str | None
    error: str | None
    is_owner: bool = True
    run_id: str | None = None


class ResultFile(BaseModel):
    name: str
    type: str  # "output" | "figure"
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


def _region_from_romp_name(romp_region: str | None) -> dict | None:
    if not romp_region:
        return None
    normalized = romp_region.lower()
    if normalized == "custom":
        return None
    for region in get_regions():
        if (region.get("romp_name") or "custom").lower() == normalized:
            return region
    return None


def _job_region_metadata(cfg: dict) -> dict[str, str | None]:
    if cfg.get("region_id") and cfg.get("region_name"):
        return {
            "region_id": cfg["region_id"],
            "region_name": cfg["region_name"],
            "romp_region": cfg.get("romp_region")
            or (cfg.get("romp_params") or {}).get("region"),
        }

    region = get_region(cfg.get("region_id") or "")
    if region is None:
        dataset_config = cfg.get("dataset_config") or {}
        region = get_region(dataset_config.get("region") or "")
    if region is None:
        region = _region_from_romp_name((cfg.get("romp_params") or {}).get("region"))

    if region:
        return {
            "region_id": region["id"],
            "region_name": region["display_name"],
            "romp_region": region.get("romp_name") or "custom",
        }

    romp_region = (cfg.get("romp_params") or {}).get("region")
    return {
        "region_id": None,
        "region_name": romp_region,
        "romp_region": romp_region,
    }


def _row_to_job_out(row: dict, current_user_id: str | None = None) -> JobOut:
    cfg = json.loads(row.get("config_json") or "{}")
    model_config = cfg.get("model_config") or {}
    model_name = cfg.get("model_name", "")
    is_owner = (current_user_id is None) or (row.get("user_id") == current_user_id)
    region_metadata = _job_region_metadata(cfg)
    return JobOut(
        id=row["id"],
        dataset_id=row["dataset_id"],
        status=row["status"],
        model_name=model_name,
        model_display_name=cfg.get("model_display_name")
        or model_config.get("display_name")
        or model_name,
        model_source_id=cfg.get("model_source_id") or model_config.get("id"),
        model_dir=cfg.get("model_dir"),
        obs_dir=cfg.get("obs_dir"),
        params=cfg.get("romp_params") or None,
        **region_metadata,
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        completed_at=row.get("completed_at"),
        error=row.get("error"),
        is_owner=is_owner,
        run_id=row.get("run_id"),
    )


def _apply_region_params(romp_params: dict) -> dict:
    params = dict(romp_params)
    region_id = params.pop("region", None)
    if not region_id:
        return params

    region_def = get_region(region_id)
    if not region_def:
        params["region"] = region_id
        return params

    if region_def.get("romp_name"):
        params["region"] = region_def["romp_name"]
        return params

    params["region"] = "custom"
    if region_def["id"] == "custom":
        params.setdefault("land_only", False)
        params.setdefault("shp_only", False)
        return params
    for key in ("lat_min", "lat_max", "lon_min", "lon_max"):
        params.setdefault(key, region_def[key])
    params.setdefault("land_only", region_def.get("land_only", False))
    params.setdefault("shp_only", region_def.get("shp_only", False))
    return params


def _apply_inferred_custom_bounds(
    params: dict,
    observation_metadata: dict,
    model_metadata: dict,
) -> dict:
    if params.get("region") != "custom":
        return params

    observation_bounds = observation_metadata.get("spatial_bounds")
    model_bounds = model_metadata.get("spatial_bounds")
    if not isinstance(observation_bounds, dict) or not isinstance(model_bounds, dict):
        raise HTTPException(
            status_code=409,
            detail="Custom coverage requires inferred spatial bounds for both data sources.",
        )

    overlap = {
        "lat_min": max(observation_bounds["lat_min"], model_bounds["lat_min"]),
        "lat_max": min(observation_bounds["lat_max"], model_bounds["lat_max"]),
        "lon_min": max(observation_bounds["lon_min"], model_bounds["lon_min"]),
        "lon_max": min(observation_bounds["lon_max"], model_bounds["lon_max"]),
    }
    if overlap["lat_min"] > overlap["lat_max"] or overlap["lon_min"] > overlap["lon_max"]:
        raise HTTPException(
            status_code=400,
            detail="The selected observation and forecast sources do not overlap geographically.",
        )

    bounded = dict(params)
    for key, value in overlap.items():
        bounded.setdefault(key, value)
    bounded.setdefault("land_only", False)
    bounded.setdefault("shp_only", False)
    return bounded


async def _resolve_obs_dir(dataset_id: str, obs_dir_override: str | None) -> str | None:
    """Resolve an observation source, with legacy upload compatibility."""
    if obs_dir_override:
        return obs_dir_override
    source = await data_source_service.get_source(dataset_id)
    if source:
        if source["kind"] != "obs":
            raise HTTPException(status_code=400, detail="Selected source is not observations")
        if source.get("status") != "ready":
            raise HTTPException(
                status_code=409,
                detail=source.get("validation_error") or "Observation source is not ready",
            )
        return source["path"]
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT storage_key FROM datasets WHERE id = :id"),
                    {"id": dataset_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row or not row["storage_key"]:
        raise HTTPException(status_code=400, detail="Dataset has no storage_key")
    return get_storage().resolve_obs_path(row["storage_key"])


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/models")
async def list_models(region: str | None = None):
    registry = get_model_registry()
    if region:
        registry = [
            m for m in registry if m.get("region", "").lower() == region.lower()
        ]
    return registry


@router.post("", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(body: JobCreate, user: CurrentUser):
    observation_source = await data_source_service.get_source(body.dataset_id)
    if observation_source:
        if observation_source["kind"] != "obs":
            raise HTTPException(status_code=400, detail="Selected source is not observations")
        if observation_source.get("status") != "ready":
            raise HTTPException(
                status_code=409,
                detail=observation_source.get("validation_error")
                or "Observation source is not ready",
            )
    else:
        async with get_db() as conn:
            ds = (
                (
                    await conn.execute(
                        text(
                            "SELECT * FROM datasets WHERE id = :id AND user_id = :uid"
                        ),
                        {"id": body.dataset_id, "uid": user["id"]},
                    )
                )
                .mappings()
                .fetchone()
            )
        if not ds:
            raise HTTPException(status_code=404, detail="Dataset not found")
        if ds["status"] != "ready":
            raise HTTPException(
                status_code=409, detail=f"Dataset is not ready (status: {ds['status']})"
            )

    region = (body.params.region or "").lower()
    model_source = await data_source_service.get_source(body.model_name)
    if not model_source or model_source["kind"] != "model":
        raise HTTPException(
            status_code=400, detail=f"Unknown model: {body.model_name!r}"
        )
    if model_source.get("status") != "ready":
        raise HTTPException(
            status_code=409,
            detail=model_source.get("validation_error") or "Model source is not ready",
        )
    model_cfg = {
        "id": model_source["id"],
        "display_name": model_source["name"],
        "region": model_source.get("region") or "",
        "model_dir": model_source["path"],
        **model_source["metadata"],
    }
    if region and model_cfg["region"].lower() != region:
        raise HTTPException(
            status_code=400,
            detail=f"Model is not configured for region {region!r}",
        )
    obs_dir = await _resolve_obs_dir(body.dataset_id, body.obs_dir)

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    romp_params = body.params.model_dump(exclude_none=True)
    for key in (
        "date_filter_year",
        "probabilistic",
        "members",
        "start_date",
        "end_date",
        "start_year_clim",
        "end_year_clim",
        "init_days",
        "max_forecast_day",
    ):
        if key not in romp_params and model_cfg.get(key) is not None:
            romp_params[key] = model_cfg[key]

    # Pull the app region id from params, or fall back to the model's configured region.
    region_id = romp_params.get("region") or model_cfg.get("region", "")
    region_def = get_region(region_id) if region_id else None
    romp_params = _apply_region_params({"region": region_id, **romp_params})

    source_metadata = observation_source["metadata"] if observation_source else {}
    if region_id == "custom":
        romp_params = _apply_inferred_custom_bounds(
            romp_params,
            source_metadata,
            model_source["metadata"],
        )
    if "obs_file_pattern" not in romp_params and source_metadata.get("obs_file_pattern"):
        romp_params["obs_file_pattern"] = source_metadata["obs_file_pattern"]
    if "obs_var" not in romp_params and source_metadata.get("obs_var"):
        romp_params["obs_var"] = source_metadata["obs_var"]
    dataset_config = {
        "provider": "local",
        "source_id": body.dataset_id,
        "source_name": observation_source["name"] if observation_source else None,
        "region": observation_source.get("region") if observation_source else None,
        **source_metadata,
    }

    compute_e2s_metrics = bool(
        model_cfg.get("compute_e2s_metrics")
        or dataset_config.get("compute_e2s_metrics")
    )

    config = {
        "model_name": model_source["name"],
        "model_display_name": model_source["name"],
        "model_source_id": body.model_name,
        "obs_dir": obs_dir,
        "model_dir": model_cfg["model_dir"],
        "model_config": model_cfg,
        "region_id": region_def["id"] if region_def else None,
        "region_name": region_def["display_name"] if region_def else None,
        "romp_region": region_def.get("romp_name") or "custom"
        if region_def
        else region_id,
        "dataset_config": dataset_config,
        "compute_e2s_metrics": compute_e2s_metrics,
        "romp_params": romp_params,
    }

    async with get_db() as conn:
        result = await conn.execute(
            text(
                "INSERT INTO jobs (id, user_id, dataset_id, status, config_json, run_id, created_at) "
                "VALUES (:id, :uid, :did, 'queued', :cfg, :run_id, :now) RETURNING *"
            ),
            {
                "id": job_id,
                "uid": user["id"],
                "did": body.dataset_id,
                "cfg": json.dumps(config),
                "run_id": body.run_id,
                "now": now,
            },
        )
        row = dict(result.mappings().fetchone())

    await launch_job(job_id)
    return _row_to_job_out(row, user["id"])


@router.get("", response_model=list[JobOut])
async def list_jobs(user: CurrentUser):
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT * FROM jobs "
                        "WHERE user_id = :uid AND run_id IS NOT NULL "
                        "ORDER BY created_at DESC"
                    ),
                    {"uid": user["id"]},
                )
            )
            .mappings()
            .fetchall()
        )
    return [_row_to_job_out(dict(r), user["id"]) for r in rows]


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str, user: CurrentUser):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM jobs WHERE id = :id AND user_id = :uid"),
                    {"id": job_id, "uid": user["id"]},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _row_to_job_out(dict(row), user["id"])


@router.websocket("/{job_id}/stream")
async def stream_job(ws: WebSocket, job_id: str) -> None:
    """Stream durable log additions and status changes."""
    await ws.accept()
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
                            text("SELECT status, exit_code FROM jobs WHERE id = :id"),
                            {"id": job_id},
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
async def cancel_job(job_id: str, user: CurrentUser):
    row = await request_cancel(job_id, user["id"])
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return _row_to_job_out(row, user["id"])


@router.get("/{job_id}/logs")
async def get_logs(job_id: str, user: CurrentUser) -> dict:
    async with get_db() as conn:
        found = (
            await conn.execute(
                text("SELECT id FROM jobs WHERE id = :id AND user_id = :uid"),
                {"id": job_id, "uid": user["id"]},
            )
        ).fetchone()
    if not found:
        raise HTTPException(status_code=404, detail="Job not found")

    storage = get_storage()
    logs = await asyncio.to_thread(storage.read_log, job_id)
    return {"logs": logs}


@router.get("/{job_id}/results", response_model=list[ResultFile])
async def get_results(job_id: str, user: CurrentUser):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT status FROM jobs WHERE id = :id AND user_id = :uid"),
                    {"id": job_id, "uid": user["id"]},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] != "complete":
        raise HTTPException(
            status_code=409, detail=f"Job is not complete (status: {row['status']})"
        )

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


@router.get("/{job_id}/results/{kind}/{filename}")
async def get_result_file(job_id: str, kind: str, filename: str, user: CurrentUser):
    """Serve a result file — FileResponse locally, signed URL redirect in production."""
    if kind not in ("output", "figure"):
        raise HTTPException(status_code=400, detail="kind must be 'output' or 'figure'")

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT status FROM jobs WHERE id = :id AND user_id = :uid"),
                    {"id": job_id, "uid": user["id"]},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] != "complete":
        raise HTTPException(
            status_code=409, detail=f"Job is not complete (status: {row['status']})"
        )

    storage = get_storage()
    local_path = storage.result_file_path(job_id, kind, filename)

    if local_path is not None:
        if not local_path.exists():
            raise HTTPException(status_code=404, detail="File not found")
        from fastapi.responses import FileResponse

        return FileResponse(local_path)
    else:
        signed_url = await asyncio.to_thread(
            storage.generate_result_url, job_id, kind, filename
        )
        return RedirectResponse(url=signed_url, status_code=302)


@router.get("/{job_id}/metrics", response_model=JobMetrics)
async def get_metrics(
    job_id: str,
    user: CurrentUser,
    lat_min: float | None = None,
    lat_max: float | None = None,
    lon_min: float | None = None,
    lon_max: float | None = None,
):
    has_bbox = any(v is not None for v in (lat_min, lat_max, lon_min, lon_max))

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT status, metrics_cache FROM jobs WHERE id = :id AND user_id = :uid"
                    ),
                    {"id": job_id, "uid": user["id"]},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] != "complete":
        raise HTTPException(
            status_code=409, detail=f"Job is not complete (status: {row['status']})"
        )

    # Return cached result when no bbox filter is applied.
    if not has_bbox and (cached_metrics := _parse_metrics_cache(row["metrics_cache"])):
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
                text("UPDATE jobs SET metrics_cache = :cache WHERE id = :id"),
                {"cache": result.model_dump_json(), "id": job_id},
            )

    return result


@router.get("/{job_id}/grid", response_model=JobGridResponse)
async def get_grid(
    job_id: str, user: CurrentUser, model: str, window: str, metric: str
):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT status FROM jobs WHERE id = :id AND user_id = :uid"),
                    {"id": job_id, "uid": user["id"]},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] != "complete":
        raise HTTPException(
            status_code=409, detail=f"Job is not complete (status: {row['status']})"
        )

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
    user: CurrentUser,
    model: str,
    window: str,
    lat: float,
    lon: float,
):
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT status FROM jobs WHERE id = :id AND user_id = :uid"),
                    {"id": job_id, "uid": user["id"]},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    if row["status"] != "complete":
        raise HTTPException(
            status_code=409, detail=f"Job is not complete (status: {row['status']})"
        )

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
async def delete_job(job_id: str, user: CurrentUser):
    async with get_db() as conn:
        row = (
            await conn.execute(
                text("SELECT id, status FROM jobs WHERE id = :id AND user_id = :uid"),
                {"id": job_id, "uid": user["id"]},
            )
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Job not found")
        if row.status in ACTIVE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail="Cancel the job before deleting it.",
            )
        await conn.execute(text("DELETE FROM jobs WHERE id = :id"), {"id": job_id})

    storage = get_storage()
    if storage.is_local:
        import shutil

        output_uri, _ = storage.job_output_uri(job_id)
        job_dir = Path(output_uri).parent
        if job_dir.exists():
            await asyncio.to_thread(shutil.rmtree, job_dir)
    # GCS: let lifecycle rules handle expiry; no immediate deletion needed
