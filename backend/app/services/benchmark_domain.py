"""Benchmark, job, and weather-data domain operations."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
import sqlalchemy as sa

from .benchmark_state import BenchmarkRunSpec, BenchmarkScope, BenchmarkValidation

logger = logging.getLogger(__name__)


class SpatialMetricRequest(BaseModel):
    job_id: str
    model: str
    window: str
    metric: Literal["false_alarm_rate", "miss_rate", "mean_mae"]


class CodeSandboxRequest(BaseModel):
    code: str


class JobCodeRequest(BaseModel):
    job_id: str
    code: str


class RerunJobRequest(BaseModel):
    job_id: str
    params_override: dict[str, Any] = Field(default_factory=dict)


def tool_unavailable_reason(name: str) -> str | None:
    from ..config import settings

    if name == "run_code_sandbox":
        if not settings.enable_run_code_sandbox:
            return "run_code_sandbox is disabled by configuration"
        if not settings.modal_token_id or not settings.modal_token_secret:
            return "run_code_sandbox requires Modal credentials and is not available in local dev by default"
        return None

    if name == "run_code":
        if not settings.enable_run_code:
            return "run_code is disabled by configuration"
        if settings.storage_backend.lower() != "gcs":
            return (
                "run_code requires GCS storage and is not available in local dev mode"
            )
        if not settings.gcs_outputs_bucket:
            return "run_code requires a configured GCS outputs bucket"
        if not settings.modal_token_id or not settings.modal_token_secret:
            return "run_code requires Modal credentials"
        return None

    return None


def is_tool_available(name: str) -> bool:
    return tool_unavailable_reason(name) is None


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------


def _scope_conditions(scope: BenchmarkScope, jobs_table: sa.Table) -> list:
    """Return a list of SQLAlchemy WHERE-clause expressions for the given scope."""
    if scope.kind == "benchmark_run_group":
        if scope.job_ids:
            return [
                sa.or_(
                    jobs_table.c.run_id == sa.bindparam("scope_key"),
                    jobs_table.c.id.in_(sa.bindparam("job_ids", expanding=True)),
                )
            ]
        return [jobs_table.c.run_id == sa.bindparam("scope_key")]
    if scope.job_ids:
        return [jobs_table.c.id.in_(sa.bindparam("job_ids", expanding=True))]
    return []


def _scope_params(scope: BenchmarkScope) -> dict:
    """Return bind-parameter values that correspond to _scope_conditions."""
    params: dict = {}
    if scope.kind == "benchmark_run_group":
        params["scope_key"] = scope.key
    if scope.job_ids:
        params["job_ids"] = scope.job_ids
    return params


# Lightweight table reference for building typed WHERE clauses.
_jobs = sa.table(
    "jobs",
    sa.column("id"),
    sa.column("user_id"),
    sa.column("dataset_id"),
    sa.column("status"),
    sa.column("run_id"),
    sa.column("config_json"),
    sa.column("completed_at"),
    sa.column("created_at"),
    sa.column("error"),
)


def _env_key(*parts: str) -> str:
    return "_".join(p for p in parts if p).upper().replace("-", "_")


def _obs_year_range(obs_dir: str) -> tuple[int | None, int | None]:
    from pathlib import Path

    path = Path(obs_dir)
    if not path.is_dir():
        return None, None
    years = []
    for file in path.iterdir():
        if file.suffix != ".nc":
            continue
        match = re.search(r"(?:^|_)(\d{4})$", file.stem)
        if match:
            years.append(int(match.group(1)))
    return (min(years), max(years)) if years else (None, None)


def _region_by_id(region_id: object) -> dict | None:
    if not isinstance(region_id, str):
        return None
    from ..routers.regions import KNOWN_REGIONS

    return next((region for region in KNOWN_REGIONS if region["id"] == region_id), None)


async def _dataset_candidates(user_id: str) -> list[dict]:
    from ..config import get_demo_datasets
    from ..database import get_db

    demo = []
    for dataset in get_demo_datasets():
        start, end = _obs_year_range(dataset["obs_dir"])
        demo.append(
            {
                "id": dataset["id"],
                "name": dataset["name"],
                "region": dataset.get("region"),
                "is_demo": True,
                "obs_file_pattern": dataset.get("obs_file_pattern"),
                "obs_year_start": start,
                "obs_year_end": end,
            }
        )

    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    sa.text("""
                    SELECT id, name
                    FROM datasets
                    WHERE user_id = :uid AND status = 'ready'
                    ORDER BY created_at DESC
                    """),
                    {"uid": user_id},
                )
            )
            .mappings()
            .fetchall()
        )
    user_datasets = [
        {
            "id": row["id"],
            "name": row["name"],
            "region": None,
            "is_demo": False,
            "obs_file_pattern": None,
            "obs_year_start": None,
            "obs_year_end": None,
        }
        for row in rows
    ]
    return demo + user_datasets


def _region_models(region_id: str | None) -> list[dict]:
    from ..config import get_model_registry

    registry = get_model_registry()
    if not region_id:
        return registry
    return [
        model
        for model in registry
        if model.get("region", "").lower() == region_id.lower()
    ]


def _finalize_benchmark_config(spec: BenchmarkRunSpec) -> BenchmarkRunSpec:
    missing = []
    if not spec.region_id:
        missing.append("region")
    if not spec.dataset_id:
        missing.append("ground_truth_dataset")
    if not spec.model_ids:
        missing.append("models")
    status = "runnable" if not missing else "collecting"
    assumptions = [
        "Use the selected observation dataset as ground truth.",
        "Use the configured monsoon onset definition for the selected region.",
        "Clamp each model evaluation range to available observation coverage.",
    ]
    return spec.model_copy(
        update={
            "missing_fields": missing,
            "status": status,
            "assumptions": assumptions,
        }
    )


def _validation_for_config(spec: BenchmarkRunSpec) -> BenchmarkValidation:
    errors = []
    warnings = []
    missing = list(spec.missing_fields)
    region = _region_by_id(spec.region_id)
    if spec.region_id and not region:
        errors.append(f"Unknown region_id: {spec.region_id}")
    models = _region_models(spec.region_id)
    valid_model_ids = {model["id"] for model in models}
    bad_models = [
        model_id for model_id in spec.model_ids if model_id not in valid_model_ids
    ]
    if bad_models:
        errors.append(
            f"Models are not available for {spec.region_id}: {', '.join(bad_models)}"
        )
    if spec.forecast_window_days is not None and spec.forecast_window_days <= 0:
        errors.append("forecast_window_days must be positive")
    if spec.forecast_window_days is not None and spec.forecast_window_days < 30:
        errors.append(
            "forecast_window_days must be at least 30 because ROMP's default verification window extends to day 30"
        )
    start_date = spec.advanced_params.get("start_date")
    end_date = spec.advanced_params.get("end_date")
    if (
        isinstance(start_date, str)
        and isinstance(end_date, str)
        and start_date > end_date
    ):
        errors.append("start_date must be before end_date")
    can_run = not missing and not errors
    return BenchmarkValidation(
        can_run=can_run,
        status="runnable" if can_run else "collecting",
        missing_fields=missing,
        errors=errors,
        warnings=warnings,
    )


def validation_for_config(spec: BenchmarkRunSpec) -> BenchmarkValidation:
    return _validation_for_config(spec)


async def _load_benchmark_config(session_id: str, user_id: str) -> BenchmarkRunSpec:
    from ..database import get_db

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    sa.text(
                        "SELECT benchmark_config FROM chat_sessions WHERE id = :id AND user_id = :uid"
                    ),
                    {"id": session_id, "uid": user_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row or not row["benchmark_config"]:
        return BenchmarkRunSpec()
    value = row["benchmark_config"]
    if isinstance(value, str):
        value = json.loads(value)
    return BenchmarkRunSpec.model_validate(value)


async def _save_benchmark_state(
    session_id: str,
    user_id: str,
    spec: BenchmarkRunSpec,
    validation: BenchmarkValidation,
    run_id: str | None = None,
) -> None:
    from ..database import get_db

    values = {
        "config": json.dumps(spec.model_dump(mode="json")),
        "validation": json.dumps(validation.model_dump(mode="json")),
        "id": session_id,
        "uid": user_id,
        "run_id": run_id,
    }
    run_sql = ", run_id = COALESCE(:run_id, run_id)" if run_id is not None else ""
    async with get_db() as conn:
        await conn.execute(
            sa.text(f"""
                UPDATE chat_sessions
                SET benchmark_config = :config,
                    benchmark_validation = :validation
                    {run_sql}
                WHERE id = :id AND user_id = :uid
            """),
            values,
        )


def benchmark_payload(
    spec: BenchmarkRunSpec, validation: BenchmarkValidation, **extra: object
) -> dict:
    return {
        "benchmark_config": spec.model_dump(mode="json"),
        "benchmark_validation": validation.model_dump(mode="json"),
        **extra,
    }


async def _exec_list_regions(args: dict, user_id: str, scope: BenchmarkScope) -> str:
    from ..config import get_demo_datasets
    from ..routers.regions import KNOWN_REGIONS

    demo_names = [d["name"].lower() for d in get_demo_datasets()]
    result = []
    for region in KNOWN_REGIONS:
        name = region["display_name"].lower()
        result.append({**region, "has_data": any(name in dn for dn in demo_names)})
    return json.dumps(result)


async def _exec_list_datasets(args: dict, user_id: str, scope: BenchmarkScope) -> str:
    region = args.get("region")
    datasets = await _dataset_candidates(user_id)
    if isinstance(region, str) and region:
        datasets = [
            dataset
            for dataset in datasets
            if (dataset.get("region") or "").lower() == region.lower()
        ]
    return json.dumps(datasets)


async def _exec_list_models(args: dict, user_id: str, scope: BenchmarkScope) -> str:
    region = args.get("region")
    models = _region_models(region if isinstance(region, str) else None)
    return json.dumps(
        [
            {
                key: model.get(key)
                for key in [
                    "id",
                    "display_name",
                    "region",
                    "model_type",
                    "probabilistic",
                    "init_days",
                    "start_date",
                    "end_date",
                    "start_year_clim",
                    "end_year_clim",
                ]
            }
            for model in models
        ]
    )


async def _exec_get_benchmark_config(
    args: dict, user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    spec = _finalize_benchmark_config(await _load_benchmark_config(session_id, user_id))
    validation = _validation_for_config(spec)
    await _save_benchmark_state(session_id, user_id, spec, validation)
    return benchmark_payload(spec, validation)


async def _exec_update_benchmark_config(
    args: dict, user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    spec = await _load_benchmark_config(session_id, user_id)
    patch = dict(args)
    region = _region_by_id(patch.get("region_id")) or _region_by_id(spec.region_id)

    datasets = await _dataset_candidates(user_id)
    dataset = None
    if isinstance(patch.get("dataset_id"), str):
        dataset = next((d for d in datasets if d["id"] == patch["dataset_id"]), None)
    if dataset is None and spec.dataset_id:
        dataset = next((d for d in datasets if d["id"] == spec.dataset_id), None)
    if (
        dataset
        and region
        and dataset.get("region")
        and dataset["region"] != region["id"]
    ):
        dataset = None

    model_ids = (
        patch.get("model_ids")
        if isinstance(patch.get("model_ids"), list)
        else spec.model_ids
    )
    models = [
        model
        for model in _region_models(region["id"] if region else None)
        if model["id"] in {mid for mid in model_ids if isinstance(mid, str)}
    ]
    advanced_params = dict(spec.advanced_params)
    if isinstance(patch.get("advanced_params"), dict):
        advanced_params.update(patch["advanced_params"])

    next_spec = spec.model_copy(
        update={
            "intent": patch.get("intent")
            if isinstance(patch.get("intent"), str)
            else spec.intent,
            "region_id": region["id"] if region else None,
            "region_name": region["display_name"] if region else None,
            "romp_region": region["romp_region"] if region else None,
            "event_type": patch.get("event_type")
            if isinstance(patch.get("event_type"), str)
            else spec.event_type,
            "dataset_id": dataset["id"] if dataset else None,
            "dataset_name": dataset["name"] if dataset else None,
            "model_ids": [model["id"] for model in models],
            "model_names": [model.get("display_name", model["id"]) for model in models],
            "forecast_window_days": patch.get("forecast_window_days")
            if isinstance(patch.get("forecast_window_days"), int)
            else spec.forecast_window_days,
            "advanced_params": advanced_params,
        }
    )
    next_spec = _finalize_benchmark_config(next_spec)
    validation = _validation_for_config(next_spec)
    await _save_benchmark_state(session_id, user_id, next_spec, validation)
    return benchmark_payload(next_spec, validation)


async def _exec_validate_benchmark_config(
    args: dict, user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    spec = _finalize_benchmark_config(await _load_benchmark_config(session_id, user_id))
    validation = _validation_for_config(spec)
    await _save_benchmark_state(session_id, user_id, spec, validation)
    return benchmark_payload(spec, validation)


def _clamp_model_params(model: dict, spec: BenchmarkRunSpec) -> dict:
    params = {
        "start_date": model["start_date"],
        "end_date": model["end_date"],
        "start_year_clim": model["start_year_clim"],
        "end_year_clim": model["end_year_clim"],
        "init_days": model["init_days"],
        "parallel": not bool(model.get("probabilistic")),
        "probabilistic": bool(model.get("probabilistic")),
    }
    if model.get("members"):
        params["members"] = model["members"]
    if model.get("model_var") and model["model_var"] != "tp":
        params["model_var"] = model["model_var"]
    if model.get("file_pattern") and model["file_pattern"] != "{}.nc":
        params["file_pattern"] = model["file_pattern"]
    params.update(spec.advanced_params)
    return params


async def _exec_propose_benchmark_submit(
    args: dict, user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    """Validate config and request user approval before submitting."""
    spec = _finalize_benchmark_config(await _load_benchmark_config(session_id, user_id))
    validation = _validation_for_config(spec)
    await _save_benchmark_state(session_id, user_id, spec, validation)
    payload = benchmark_payload(spec, validation)
    if not validation.can_run:
        payload["error"] = "Benchmark config is not runnable — cannot request approval"
        return payload
    payload["approval_required"] = True
    return payload


async def _exec_submit_benchmark(
    args: dict, user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    from ..routers.jobs import JobCreate, RompParams, create_job

    spec = _finalize_benchmark_config(await _load_benchmark_config(session_id, user_id))
    validation = _validation_for_config(spec)
    if not validation.can_run:
        await _save_benchmark_state(session_id, user_id, spec, validation)
        return benchmark_payload(
            spec, validation, error="Benchmark config is not runnable"
        )

    models = [
        model
        for model in _region_models(spec.region_id)
        if model["id"] in set(spec.model_ids)
    ]
    run_id = str(uuid.uuid4())
    jobs = []
    shared_params = {
        "event_type": spec.event_type,
        "region": spec.romp_region,
        "max_forecast_day": spec.forecast_window_days,
    }
    for model in models:
        params = {**shared_params, **_clamp_model_params(model, spec)}
        job = await create_job(
            JobCreate(
                dataset_id=spec.dataset_id or "",
                model_name=model["id"],
                params=RompParams(**params),
                run_id=run_id,
            ),
            {"id": user_id},
        )
        jobs.append(job.model_dump(mode="json"))
    submitted = spec.model_copy(update={"status": "running"})
    submitted_validation = validation.model_copy(update={"status": "running"})
    await _save_benchmark_state(
        session_id, user_id, submitted, submitted_validation, run_id
    )
    from ..database import get_db

    next_scope = BenchmarkScope(
        kind="benchmark_run_group",
        key=run_id,
        title=scope.title,
        job_ids=[job["id"] for job in jobs],
    )
    async with get_db() as conn:
        await conn.execute(
            sa.text("""
                UPDATE chat_sessions
                SET scope = :scope
                WHERE id = :id AND user_id = :uid
            """),
            {
                "scope": json.dumps(next_scope.model_dump(mode="json")),
                "id": session_id,
                "uid": user_id,
            },
        )
    return benchmark_payload(submitted, submitted_validation, run_id=run_id, jobs=jobs)


async def _exec_list_jobs(args: dict, user_id: str, scope: BenchmarkScope) -> str:
    from ..database import get_db

    status_filter = args.get("status")
    query = sa.select(
        _jobs.c.id,
        _jobs.c.dataset_id,
        _jobs.c.config_json,
        _jobs.c.status,
        _jobs.c.error,
        _jobs.c.run_id,
        _jobs.c.completed_at,
        _jobs.c.created_at,
    ).where(_jobs.c.user_id == sa.bindparam("uid"))
    if isinstance(status_filter, str) and status_filter in {
        "running",
        "complete",
        "failed",
    }:
        query = query.where(_jobs.c.status == sa.bindparam("status"))
    for cond in _scope_conditions(scope, _jobs):
        query = query.where(cond)
    query = query.order_by(_jobs.c.completed_at.desc())

    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    query,
                    {
                        "uid": user_id,
                        "status": status_filter,
                        **_scope_params(scope),
                    },
                )
            )
            .mappings()
            .fetchall()
        )
        rows = [dict(r) for r in rows]
    jobs = []
    for r in rows:
        cfg = json.loads(r.get("config_json") or "{}")
        jobs.append(
            {
                "job_id": r["id"],
                "model_name": cfg.get("model_name"),
                "region": cfg.get("romp_params", {}).get("region"),
                "dataset_id": r.get("dataset_id"),
                "status": r["status"],
                "run_id": r.get("run_id"),
                "error": r.get("error"),
                "completed_at": r["completed_at"],
                "created_at": r["created_at"],
            }
        )
    return json.dumps(jobs)


async def _exec_get_job_info(args: dict, user_id: str, scope: BenchmarkScope) -> str:
    from ..database import get_db

    job_id = args["job_id"]
    query = (
        sa.select(
            _jobs.c.config_json,
            _jobs.c.status,
            _jobs.c.dataset_id,
            _jobs.c.run_id,
            _jobs.c.error,
            _jobs.c.created_at,
            _jobs.c.completed_at,
        )
        .where(_jobs.c.id == sa.bindparam("id"))
        .where(_jobs.c.user_id == sa.bindparam("uid"))
    )
    for cond in _scope_conditions(scope, _jobs):
        query = query.where(cond)

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    query, {"id": job_id, "uid": user_id, **_scope_params(scope)}
                )
            )
            .mappings()
            .fetchone()
        )
        row = dict(row) if row else None
    if not row:
        return json.dumps({"error": f"Job {job_id} not found"})
    cfg = json.loads(row.get("config_json") or "{}")
    return json.dumps(
        {
            "job_id": job_id,
            "status": row["status"],
            "dataset_id": row["dataset_id"],
            "run_id": row["run_id"],
            "error": row["error"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
            "model_name": cfg.get("model_name"),
            "model_dir": cfg.get("model_dir"),
            "obs_dir": cfg.get("obs_dir"),
            "romp_params": cfg.get("romp_params", {}),
        }
    )


async def _exec_get_job_logs(args: dict, user_id: str, scope: BenchmarkScope) -> str:
    from ..database import get_db
    from ..services.logging import fetch_cloud_logs
    from ..services.storage import get_storage

    job_id = args["job_id"]
    max_chars = args.get("max_chars")
    if not isinstance(max_chars, int) or max_chars <= 0:
        max_chars = 12000
    query = (
        sa.select(_jobs.c.id)
        .where(_jobs.c.id == sa.bindparam("id"))
        .where(_jobs.c.user_id == sa.bindparam("uid"))
    )
    for cond in _scope_conditions(scope, _jobs):
        query = query.where(cond)
    async with get_db() as conn:
        row = (
            await conn.execute(
                query, {"id": job_id, "uid": user_id, **_scope_params(scope)}
            )
        ).fetchone()
    if not row:
        return json.dumps({"error": f"Job {job_id} not found"})

    storage = get_storage()
    if storage.is_local:
        logs = await asyncio.to_thread(storage.read_log, job_id)
    else:
        job_prefix = job_id.replace("_", "-")[:36]
        filter_expr = (
            f'resource.type="cloud_run_job" '
            f'AND labels."run.googleapis.com/execution_name"=~"romp-{job_prefix}"'
        )
        logs = await asyncio.to_thread(fetch_cloud_logs, filter_expr)
    truncated = len(logs) > max_chars
    if truncated:
        logs = logs[-max_chars:]
    return json.dumps({"job_id": job_id, "logs": logs, "truncated": truncated})


async def _exec_rerun_job(args: dict, user_id: str, scope: BenchmarkScope) -> dict:
    from ..database import get_db
    from ..routers.jobs import JobCreate, RompParams, create_job

    job_id = args["job_id"]
    params_override = args.get("params_override")
    if not isinstance(params_override, dict):
        params_override = {}
    query = (
        sa.select(
            _jobs.c.dataset_id,
            _jobs.c.config_json,
            _jobs.c.status,
            _jobs.c.run_id,
        )
        .where(_jobs.c.id == sa.bindparam("id"))
        .where(_jobs.c.user_id == sa.bindparam("uid"))
    )
    for cond in _scope_conditions(scope, _jobs):
        query = query.where(cond)
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    query,
                    {"id": job_id, "uid": user_id, **_scope_params(scope)},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        return {"error": f"Job {job_id} not found"}
    cfg = json.loads(row["config_json"] or "{}")
    params = {**(cfg.get("romp_params") or {}), **params_override}
    rerun = await create_job(
        JobCreate(
            dataset_id=row["dataset_id"],
            model_name=cfg.get("model_name", ""),
            params=RompParams(**params),
            run_id=row["run_id"],
        ),
        {"id": user_id},
    )
    return {
        "source_job_id": job_id,
        "job": rerun.model_dump(mode="json"),
    }


def _job_status_query(scope: BenchmarkScope):
    """Build a SELECT status query for a single job filtered by scope."""
    query = (
        sa.select(_jobs.c.status)
        .where(_jobs.c.id == sa.bindparam("id"))
        .where(_jobs.c.user_id == sa.bindparam("uid"))
    )
    for cond in _scope_conditions(scope, _jobs):
        query = query.where(cond)
    return query


async def _exec_get_job_metrics(args: dict, user_id: str, scope: BenchmarkScope) -> str:
    import numpy as np
    from ..database import get_db
    from ..services.storage import get_storage

    job_id = args["job_id"]

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    _job_status_query(scope),
                    {"id": job_id, "uid": user_id, **_scope_params(scope)},
                )
            )
            .mappings()
            .fetchone()
        )
        row = dict(row) if row else None
    if not row:
        return json.dumps({"error": f"Job {job_id} not found"})
    if row["status"] != "complete":
        return json.dumps({"error": f"Job {job_id} is not complete"})

    def _load():
        import xarray as xr

        storage = get_storage()
        UNIT_MAP = {"false_alarm_rate": "fraction", "miss_rate": "fraction"}

        if storage.is_local:
            output_dir = storage._outputs_dir / job_id / "output"
            nc_files = (
                sorted(output_dir.glob("spatial_metrics_*.nc"))
                if output_dir.exists()
                else []
            )
        else:
            import gcsfs

            fs = gcsfs.GCSFileSystem()
            prefix = f"{storage._outputs_bucket}/{job_id}/output/spatial_metrics_"
            nc_files = [f"gs://{f}" for f in sorted(fs.glob(f"{prefix}*.nc"))]

        def _open(path):
            if storage.is_local:
                return xr.open_dataset(path)
            with fs.open(str(path).removeprefix("gs://"), "rb") as f:
                return xr.load_dataset(f, engine="h5netcdf")

        windows = []
        for nc in nc_files:
            ds = _open(nc)
            model = str(ds.attrs.get("model", ""))
            window = str(ds.attrs.get("verification_window", "")).replace(",", "-")
            metrics = {}
            for var in ds.data_vars:
                arr = ds[var].values.astype(float)
                valid = arr[~np.isnan(arr)]
                if len(valid) == 0:
                    continue
                var_str = str(var)
                metrics[var_str] = {
                    "mean": round(float(np.mean(valid)), 4),
                    "p50": round(float(np.percentile(valid, 50)), 4),
                    "p90": round(float(np.percentile(valid, 90)), 4),
                    "min": round(float(np.min(valid)), 4),
                    "max": round(float(np.max(valid)), 4),
                    "unit": UNIT_MAP.get(var_str, "days"),
                }
            ds.close()
            windows.append({"model": model, "window": window, "metrics": metrics})
        windows.sort(key=lambda w: (w["model"] == "climatology", w["window"]))
        return windows

    windows = await asyncio.to_thread(_load)
    return json.dumps({"job_id": job_id, "windows": windows})


async def _exec_get_spatial_summary(
    args: dict, user_id: str, scope: BenchmarkScope
) -> str:
    import numpy as np
    from ..database import get_db
    from ..services.storage import get_storage

    job_id = args["job_id"]
    model = args["model"]
    window = args["window"]
    metric = args["metric"]

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    _job_status_query(scope),
                    {"id": job_id, "uid": user_id, **_scope_params(scope)},
                )
            )
            .mappings()
            .fetchone()
        )
        row = dict(row) if row else None
    if not row:
        return json.dumps({"error": f"Job {job_id} not found"})
    if row["status"] != "complete":
        return json.dumps({"error": f"Job {job_id} is not complete"})

    def _load():
        import xarray as xr

        storage = get_storage()
        w_alt = window.replace("-", ",")

        if storage.is_local:
            output_dir = storage._outputs_dir / job_id / "output"
            matches = list(output_dir.glob(f"spatial_metrics_{model}_{window}.nc"))
            if not matches:
                matches = list(output_dir.glob(f"spatial_metrics_{model}_{w_alt}.nc"))
        else:
            import gcsfs

            fs = gcsfs.GCSFileSystem()
            base = f"{storage._outputs_bucket}/{job_id}/output"
            matches = fs.glob(f"{base}/spatial_metrics_{model}_{window}.nc")
            if not matches:
                matches = fs.glob(f"{base}/spatial_metrics_{model}_{w_alt}.nc")
            matches = [f"gs://{f}" for f in matches]

        if not matches:
            return {"error": f"No grid file found for {model}/{window}"}

        if storage.is_local:
            ds = xr.open_dataset(matches[0])
        else:
            with fs.open(str(matches[0]).removeprefix("gs://"), "rb") as f:
                ds = xr.load_dataset(f, engine="h5netcdf")

        if metric not in ds.data_vars:
            ds.close()
            return {"error": f"Metric {metric!r} not in dataset"}

        arr = ds[metric].values.astype(float)
        valid = arr[~np.isnan(arr)]
        lats = ds.lat.values.tolist()
        lons = ds.lon.values.tolist()
        ds.close()

        if len(valid) == 0:
            return {"error": "No valid data points"}

        return {
            "job_id": job_id,
            "model": model,
            "window": window,
            "metric": metric,
            "grid_shape": {"lats": len(lats), "lons": len(lons)},
            "lat_range": [round(min(lats), 2), round(max(lats), 2)],
            "lon_range": [round(min(lons), 2), round(max(lons), 2)],
            "valid_points": int(len(valid)),
            "stats": {
                "mean": round(float(np.mean(valid)), 4),
                "p25": round(float(np.percentile(valid, 25)), 4),
                "p50": round(float(np.percentile(valid, 50)), 4),
                "p75": round(float(np.percentile(valid, 75)), 4),
                "p90": round(float(np.percentile(valid, 90)), 4),
                "min": round(float(np.min(valid)), 4),
                "max": round(float(np.max(valid)), 4),
            },
        }

    result = await asyncio.to_thread(_load)
    return json.dumps(result)


async def _exec_run_code_sandbox(
    args: dict, user_id: str, scope: BenchmarkScope
) -> dict | str:
    reason = tool_unavailable_reason("run_code_sandbox")
    if reason:
        return json.dumps({"error": reason})

    code = args["code"]

    def _run():
        import modal

        fn = modal.Function.from_name("almanac-romp", "run_code_sandbox")
        return fn.remote(code)

    try:
        result = await asyncio.to_thread(_run)
        return result
    except Exception as exc:
        logger.exception("run_code_sandbox failed")
        return json.dumps({"ok": False, "error": str(exc)})


async def _exec_run_code(args: dict, user_id: str, scope: BenchmarkScope) -> dict | str:
    from ..database import get_db
    from ..services.storage import get_storage

    reason = tool_unavailable_reason("run_code")
    if reason:
        return json.dumps({"error": reason})

    job_id = args["job_id"]
    code = args["code"]

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    _job_status_query(scope),
                    {"id": job_id, "uid": user_id, **_scope_params(scope)},
                )
            )
            .mappings()
            .fetchone()
        )
        row = dict(row) if row else None
    if not row:
        return json.dumps({"error": f"Job {job_id} not found"})
    if row["status"] != "complete":
        return json.dumps({"error": f"Job {job_id} is not complete"})

    storage = get_storage()

    def _run():
        import modal

        fn = modal.Function.from_name("almanac-romp", "run_code")
        return fn.remote(job_id, storage._outputs_bucket, code)

    try:
        result = await asyncio.to_thread(_run)
        return result
    except Exception as exc:
        logger.exception("run_code failed")
        return json.dumps({"ok": False, "error": str(exc)})


def _domain_payload(raw_result: object) -> dict:
    if isinstance(raw_result, str):
        try:
            parsed = json.loads(raw_result)
        except json.JSONDecodeError:
            return {"raw": raw_result}
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    if isinstance(raw_result, dict):
        return raw_result
    return {"value": raw_result}


async def list_regions(user_id: str, scope: BenchmarkScope) -> dict:
    return _domain_payload(await _exec_list_regions({}, user_id, scope))


async def list_datasets(
    region: str | None, user_id: str, scope: BenchmarkScope
) -> dict:
    args = {"region": region} if region else {}
    return _domain_payload(await _exec_list_datasets(args, user_id, scope))


async def list_models(region: str | None, user_id: str, scope: BenchmarkScope) -> dict:
    args = {"region": region} if region else {}
    return _domain_payload(await _exec_list_models(args, user_id, scope))


async def get_benchmark_config(
    user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    return _domain_payload(
        await _exec_get_benchmark_config({}, user_id, scope, session_id)
    )


async def update_benchmark_config(
    patch: dict, user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    return _domain_payload(
        await _exec_update_benchmark_config(patch, user_id, scope, session_id)
    )


async def validate_benchmark_config(
    user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    return _domain_payload(
        await _exec_validate_benchmark_config({}, user_id, scope, session_id)
    )


async def propose_benchmark_submit(
    user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    return _domain_payload(
        await _exec_propose_benchmark_submit({}, user_id, scope, session_id)
    )


async def submit_benchmark_for_session(
    user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    return _domain_payload(await _exec_submit_benchmark({}, user_id, scope, session_id))


async def list_jobs(
    user_id: str, scope: BenchmarkScope, status: str | None = None
) -> dict:
    args = {"status": status} if status else {}
    return _domain_payload(await _exec_list_jobs(args, user_id, scope))


async def get_job_info(job_id: str, user_id: str, scope: BenchmarkScope) -> dict:
    return _domain_payload(await _exec_get_job_info({"job_id": job_id}, user_id, scope))


async def get_job_logs(
    job_id: str, max_chars: int, user_id: str, scope: BenchmarkScope
) -> dict:
    return _domain_payload(
        await _exec_get_job_logs(
            {"job_id": job_id, "max_chars": max_chars}, user_id, scope
        )
    )


async def rerun_job(
    request: RerunJobRequest, user_id: str, scope: BenchmarkScope
) -> dict:
    return _domain_payload(await _exec_rerun_job(request.model_dump(), user_id, scope))


async def get_job_metrics(job_id: str, user_id: str, scope: BenchmarkScope) -> dict:
    return _domain_payload(
        await _exec_get_job_metrics({"job_id": job_id}, user_id, scope)
    )


async def get_spatial_summary(
    request: SpatialMetricRequest, user_id: str, scope: BenchmarkScope
) -> dict:
    return _domain_payload(
        await _exec_get_spatial_summary(request.model_dump(), user_id, scope)
    )


async def run_code_sandbox(
    request: CodeSandboxRequest, user_id: str, scope: BenchmarkScope
) -> dict:
    return _domain_payload(
        await _exec_run_code_sandbox(request.model_dump(), user_id, scope)
    )


async def run_code(
    request: JobCodeRequest, user_id: str, scope: BenchmarkScope
) -> dict:
    return _domain_payload(await _exec_run_code(request.model_dump(), user_id, scope))


async def get_current_benchmark_config(
    session_id: str, user_id: str
) -> BenchmarkRunSpec:
    return _finalize_benchmark_config(await _load_benchmark_config(session_id, user_id))
