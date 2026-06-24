"""Benchmark job submission: validation, config assembly, quota, and dispatch.

Shared by the HTTP route (POST /jobs) and the chat benchmark tools, so the
chat path enforces exactly the same source checks, region resolution, and
per-user quota as direct submission. Raises HTTPException — both callers
surface the errors to an HTTP client.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from fastapi import HTTPException
from pydantic import BaseModel

from ai_almanac.server.db import get_db, lock_for_update
from ai_almanac.server.services import data_sources as data_source_service
from ai_almanac.server.services.data_catalog import year_uris
from ai_almanac.server.services.events import audit, usage
from ai_almanac.server.services.execution import ExecutionRequest, ResourceRequest
from ai_almanac.server.services.job_manager import ACTIVE_STATUSES
from ai_almanac.server.services.registry import CatalogSnapshot, load_catalog
from ai_almanac.server.services.runner_registry import get_job_runner
from ai_almanac.server.services.storage import get_storage
from ai_almanac.server.tables import datasets, jobs, users
from ai_almanac.settings import settings


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
    visibility: str = "private"
    run_id: str | None = None


def job_region_metadata(cfg: dict, catalog: CatalogSnapshot) -> dict[str, str | None]:
    if cfg.get("region_id") and cfg.get("region_name"):
        return {
            "region_id": cfg["region_id"],
            "region_name": cfg["region_name"],
            "romp_region": cfg.get("romp_region")
            or (cfg.get("romp_params") or {}).get("region"),
        }

    region = catalog.region(cfg.get("region_id"))
    if region is None:
        dataset_config = cfg.get("dataset_config") or {}
        region = catalog.region(dataset_config.get("region"))
    if region is None:
        region = catalog.region_by_romp_name(
            (cfg.get("romp_params") or {}).get("region")
        )

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


def row_to_job_out(
    row: dict, current_user_id: str | None, catalog: CatalogSnapshot
) -> JobOut:
    cfg = json.loads(row.get("config_json") or "{}")
    model_config = cfg.get("model_config") or {}
    model_name = cfg.get("model_name", "")
    is_owner = (current_user_id is None) or (row.get("user_id") == current_user_id)
    region_metadata = job_region_metadata(cfg, catalog)
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
        visibility=row.get("visibility") or "private",
        run_id=row.get("run_id"),
    )


def apply_region_params(romp_params: dict, catalog: CatalogSnapshot) -> dict:
    params = dict(romp_params)
    region_id = params.pop("region", None)
    if not region_id:
        return params

    region_def = catalog.region(region_id)
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


def apply_inferred_custom_bounds(
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
                    sa.select(datasets.c.storage_key).where(datasets.c.id == dataset_id)
                )
            )
            .mappings()
            .fetchone()
        )
    if not row or not row["storage_key"]:
        raise HTTPException(status_code=400, detail="Dataset has no storage_key")
    return get_storage().resolve_obs_path(row["storage_key"])


class BlendParams(BaseModel):
    """Blend preparation and training hyperparameters."""

    forecast_years: str | None = None
    obs_years: str | None = None
    training_years: str
    cv_holdout_years: str
    true_holdout_years: str | None = None
    formula_text: str | None = None
    threshold_mm: float | None = None
    cutoff_month_day: str | None = None
    mok_month_day: str | None = None


class BlendCreate(BaseModel):
    name: str
    obs_dataset_id: str
    model_ids: list[str]
    params: BlendParams
    run_id: str | None = None


class BlendOut(BaseModel):
    id: str
    name: str
    status: str
    model_names: list[str]
    region_id: str | None = None
    created_at: str
    completed_at: str | None = None
    error: str | None = None
    is_owner: bool = True
    visibility: str = "private"
    run_id: str | None = None


def _blend_model_key(name: str) -> str:
    """Filesystem/column-safe key used for a forecast model inside the blend."""
    return re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_")


def _parse_year_spec(value: str | None) -> list[int]:
    """Parse a year spec ('2019:2024', '2021,2022') into sorted unique years.

    Mirrors the Modal app's ``_parse_years`` so the staging years computed here
    match what the training step expects. Raises ValueError on a malformed token.
    """
    if not value:
        return []
    years: list[int] = []
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if ":" in token:
            start_text, end_text = token.split(":", 1)
            years.extend(range(int(start_text), int(end_text) + 1))
        else:
            years.append(int(token))
    return sorted(dict.fromkeys(years))


def _blend_forecast_years(params: BlendParams) -> list[int]:
    """Years to stage per forecast model: explicit ``forecast_years`` if given,
    else the union of the training / CV / true-holdout years.

    Resolved on the server so each forecast model stages exactly these years
    (one ``{year}.nc`` file each) instead of a whole multi-GB dataset dir, and so
    ``run_blend`` receives concrete file URIs rather than scanning a prefix.
    """
    explicit = _parse_year_spec(params.forecast_years)
    if explicit:
        return explicit
    return _parse_year_spec(
        ",".join(
            spec
            for spec in (
                params.training_years,
                params.cv_holdout_years,
                params.true_holdout_years,
            )
            if spec
        )
    )


def blend_row_to_out(row: dict, current_user_id: str | None) -> BlendOut:
    cfg = json.loads(row.get("config_json") or "{}")
    is_owner = (current_user_id is None) or (row.get("user_id") == current_user_id)
    return BlendOut(
        id=row["id"],
        name=cfg.get("blend_name") or "",
        status=row["status"],
        model_names=cfg.get("model_names") or [],
        region_id=cfg.get("region_id"),
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
        error=row.get("error"),
        is_owner=is_owner,
        visibility=row.get("visibility") or "private",
        run_id=row.get("run_id"),
    )


async def _resolve_model_source(model_id: str, user_id: str) -> dict:
    source = await data_source_service.get_source(model_id)
    if not source or source["kind"] != "model":
        raise HTTPException(status_code=400, detail=f"Unknown model: {model_id!r}")
    if (
        source.get("owner_id") not in (None, user_id)
        and source.get("visibility") != "shared"
    ):
        raise HTTPException(status_code=404, detail=f"Model not found: {model_id!r}")
    if source.get("status") != "ready":
        raise HTTPException(
            status_code=409,
            detail=source.get("validation_error")
            or f"Model source is not ready: {source['name']!r}",
        )
    return source


async def create_blend_for_user(body: BlendCreate, user_id: str) -> BlendOut:
    """Submit a blend-training job: prepare intermediates and train weights."""
    if not body.model_ids:
        raise HTTPException(status_code=400, detail="At least one model is required")

    obs_dir = await _resolve_obs_dir(body.obs_dataset_id, None)
    obs_source = await data_source_service.get_source(body.obs_dataset_id)

    try:
        forecast_years = _blend_forecast_years(body.params)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid year specification: {exc}"
        ) from exc
    if not forecast_years:
        raise HTTPException(
            status_code=400,
            detail="Blend requires training years or explicit forecast years",
        )

    model_names: list[str] = []
    # Per-model list of {year}.nc URIs to stage — resolved here so the staging
    # year filter and backend resolution live on the server, and run_blend just
    # downloads the files it is given.
    model_files: dict[str, list[str]] = {}
    for model_id in body.model_ids:
        source = await _resolve_model_source(model_id, user_id)
        key = _blend_model_key(source["name"])
        if key in model_files:
            raise HTTPException(
                status_code=400, detail=f"Duplicate model in blend: {source['name']!r}"
            )
        model_names.append(key)
        model_files[key] = year_uris(source["path"], forecast_years)

    job_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    region_id = obs_source.get("region") if obs_source else None
    config = {
        "job_type": "blend",
        "modal_app": settings.modal_blending_app_name,
        "modal_function": "run_blend",
        "blend_name": body.name,
        "obs_dir": obs_dir,
        "model_files": model_files,
        "model_names": model_names,
        "model_source_ids": list(body.model_ids),
        "forecast_years": forecast_years,
        "region_id": region_id,
        "dataset_config": {"provider": "local", "source_id": body.obs_dataset_id},
        "blend_params": body.params.model_dump(exclude_none=True),
    }

    async with get_db() as conn:
        await lock_for_update(conn)
        await conn.execute(
            sa.select(users.c.id).where(users.c.id == user_id).with_for_update()
        )
        active_count = (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(jobs)
                .where(
                    jobs.c.user_id == user_id,
                    jobs.c.status.in_(ACTIVE_STATUSES),
                )
            )
        ).scalar_one()
        if active_count >= settings.max_active_jobs_per_user:
            raise HTTPException(status_code=429, detail="active job quota exceeded")
        result = await conn.execute(
            sa.insert(jobs)
            .values(
                id=job_id,
                user_id=user_id,
                dataset_id=body.obs_dataset_id,
                job_type="blend",
                status="queued",
                config_json=json.dumps(config),
                run_id=body.run_id,
                created_at=now,
                runner_request={"job_id": job_id, "resources": {"gpus": 0}},
            )
            .returning(jobs)
        )
        row = dict(result.mappings().fetchone())
        await audit(
            conn,
            "blend.submitted",
            user_id=user_id,
            resource_type="job",
            resource_id=job_id,
            metadata={"model_source_ids": list(body.model_ids)},
        )
        await usage(
            conn,
            "blend.submitted",
            user_id=user_id,
            resource_type="job",
            resource_id=job_id,
            quantity=1,
        )

    try:
        handle = await get_job_runner().submit(ExecutionRequest(job_id=job_id))
    except Exception as exc:
        async with get_db() as conn:
            await conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    status="failed",
                    completed_at=datetime.now(UTC).isoformat(),
                    error=str(exc),
                )
            )
        raise HTTPException(
            status_code=400, detail=f"Blend submission failed: {exc}"
        ) from exc

    values: dict = {"runner": handle.runner, "runner_handle": handle.as_dict()}
    if handle.runner != "local":
        values["status"] = "running"
    async with get_db() as conn:
        await conn.execute(sa.update(jobs).where(jobs.c.id == job_id).values(**values))
    return blend_row_to_out(row, user_id)


async def create_job_for_user(body: JobCreate, user_id: str) -> JobOut:
    observation_source = await data_source_service.get_source(body.dataset_id)
    if observation_source:
        if (
            observation_source.get("owner_id") not in (None, user_id)
            and observation_source.get("visibility") != "shared"
        ):
            raise HTTPException(status_code=404, detail="Dataset not found")
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
                        sa.select(datasets).where(
                            datasets.c.id == body.dataset_id,
                            datasets.c.user_id == user_id,
                        )
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
    catalog = await load_catalog()
    region_id = romp_params.get("region") or model_cfg.get("region", "")
    region_def = catalog.region(region_id)
    romp_params = apply_region_params({"region": region_id, **romp_params}, catalog)

    source_metadata = observation_source["metadata"] if observation_source else {}
    if region_id == "custom":
        romp_params = apply_inferred_custom_bounds(
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
        # On SQLite this takes the write lock up front; on PostgreSQL the
        # FOR UPDATE below serializes concurrent submissions per user.
        await lock_for_update(conn)
        await conn.execute(
            sa.select(users.c.id).where(users.c.id == user_id).with_for_update()
        )
        active_count = (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(jobs)
                .where(
                    jobs.c.user_id == user_id,
                    jobs.c.status.in_(ACTIVE_STATUSES),
                )
            )
        ).scalar_one()
        if active_count >= settings.max_active_jobs_per_user:
            raise HTTPException(status_code=429, detail="active job quota exceeded")
        runner_request = {
            "job_id": job_id,
            "resources": {"gpus": 1},
            "timeout_seconds": None,
        }
        result = await conn.execute(
            sa.insert(jobs)
            .values(
                id=job_id,
                user_id=user_id,
                dataset_id=body.dataset_id,
                status="queued",
                config_json=json.dumps(config),
                run_id=body.run_id,
                created_at=now,
                runner_request=runner_request,
            )
            .returning(jobs)
        )
        row = dict(result.mappings().fetchone())
        await audit(
            conn,
            "job.submitted",
            user_id=user_id,
            resource_type="job",
            resource_id=job_id,
            metadata={"dataset_id": body.dataset_id, "model_source_id": body.model_name},
        )
        await usage(
            conn,
            "job.submitted",
            user_id=user_id,
            resource_type="job",
            resource_id=job_id,
            quantity=1,
        )

    storage = get_storage()
    # Remote runners (Modal) have no local workspace; only the local runner does.
    workspace = storage.job_dir(job_id) if storage.is_local else None
    try:
        handle = await get_job_runner().submit(
            ExecutionRequest(
                job_id=job_id,
                workspace=workspace,
                bundle_path=workspace,
                resources=ResourceRequest(),
            )
        )
    except Exception as exc:
        async with get_db() as conn:
            await conn.execute(
                sa.update(jobs)
                .where(jobs.c.id == job_id)
                .values(
                    status="failed",
                    completed_at=datetime.now(UTC).isoformat(),
                    error=str(exc),
                )
            )
        raise HTTPException(
            status_code=400, detail=f"Job submission failed: {exc}"
        ) from exc

    values: dict = {"runner": handle.runner, "runner_handle": handle.as_dict()}
    # The local runner's supervisor advances status itself; a remote runner is
    # already executing once spawned, and the reconciler tracks it from here.
    if handle.runner != "local":
        values["status"] = "running"
    async with get_db() as conn:
        await conn.execute(sa.update(jobs).where(jobs.c.id == job_id).values(**values))
    return row_to_job_out(row, user_id, catalog)
