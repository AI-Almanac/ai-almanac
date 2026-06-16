"""Benchmark, job, and weather-data domain operations."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel, Field

from .benchmark_state import BenchmarkRunSpec, BenchmarkScope, BenchmarkValidation

logger = logging.getLogger(__name__)


class SpatialMetricRequest(BaseModel):
    job_id: str
    model: str
    window: str
    metric: str


class CodeSandboxRequest(BaseModel):
    code: str


class JobCodeRequest(BaseModel):
    job_id: str
    code: str


class RerunJobRequest(BaseModel):
    job_id: str
    params_override: dict[str, Any] = Field(default_factory=dict)


def tool_unavailable_reason(name: str) -> str | None:
    from ai_almanac.settings import settings

    if name == "run_code_sandbox":
        if not settings.enable_run_code_sandbox:
            return "run_code_sandbox is disabled by configuration"
        return "run_code_sandbox is not available in local builds (requires a remote execution backend)"

    if name == "run_code":
        if not settings.enable_run_code:
            return "run_code is disabled by configuration"
        return "run_code is not available in local builds (requires a remote execution backend)"

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


SHARED_ROMP_PARAM_KEYS = {
    "obs",
    "obs_file_pattern",
    "obs_var",
    "wet_threshold",
    "wet_init",
    "wet_spell",
    "dry_spell",
    "dry_extent",
    "nc_mask",
    "thresh_file",
    "ref_model",
    "ref_model_dir",
}

PER_MODEL_ROMP_PARAM_KEYS = {
    "start_date",
    "end_date",
    "start_year_clim",
    "end_year_clim",
    "init_days",
    "date_filter_year",
    "parallel",
    "probabilistic",
    "members",
    "model_var",
    "file_pattern",
}


def _non_empty_params(params: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {
        key: value
        for key, value in params.items()
        if key in allowed and value is not None and value != ""
    }


def _clean_advanced_params(
    params: dict[str, Any], model_ids: list[str]
) -> dict[str, Any]:
    cleaned = _non_empty_params(params, SHARED_ROMP_PARAM_KEYS)
    raw_per_model = params.get("per_model_params")
    if isinstance(raw_per_model, dict):
        selected = set(model_ids)
        per_model = {
            model_id: _non_empty_params(model_params, PER_MODEL_ROMP_PARAM_KEYS)
            for model_id, model_params in raw_per_model.items()
            if model_id in selected and isinstance(model_params, dict)
        }
        per_model = {
            model_id: values for model_id, values in per_model.items() if values
        }
        if per_model:
            cleaned["per_model_params"] = per_model
    return cleaned


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
    from ai_almanac.settings import get_region

    return get_region(region_id)


async def _dataset_candidates(user_id: str) -> list[dict]:
    from ai_almanac.server.services.data_sources import get_obs_sources

    sources = await get_obs_sources()
    return [
        {
            "id": source["id"],
            "name": source["name"],
            "region": source.get("region"),
            "is_demo": False,
            "obs_file_pattern": source["metadata"].get("obs_file_pattern"),
            "obs_year_start": source["metadata"].get("start_year"),
            "obs_year_end": source["metadata"].get("end_year"),
        }
        for source in sources
        if source.get("status") == "ready"
    ]


def _region_models(region_id: str | None) -> list[dict]:
    from ai_almanac.settings import get_model_registry

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
        "Clip observation data to the selected benchmark coverage.",
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
    model_map = {model["id"]: model for model in models}
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
    per_model_params = spec.advanced_params.get("per_model_params")
    if isinstance(per_model_params, dict):
        for model_id, params in per_model_params.items():
            if not isinstance(params, dict):
                errors.append(f"per_model_params.{model_id} must be an object")
                continue
            start_date = params.get("start_date")
            end_date = params.get("end_date")
            if (
                isinstance(start_date, str)
                and isinstance(end_date, str)
                and start_date > end_date
            ):
                errors.append(f"{model_id}: start_date must be before end_date")
            model = model_map.get(model_id)
            if (
                model
                and isinstance(start_date, str)
                and start_date < model["start_date"]
            ):
                errors.append(
                    f"{model_id}: start_date is before available coverage ({model['start_date']})"
                )
            if model and isinstance(end_date, str) and end_date > model["end_date"]:
                errors.append(
                    f"{model_id}: end_date is after available coverage ({model['end_date']})"
                )
            start_year_clim = params.get("start_year_clim")
            end_year_clim = params.get("end_year_clim")
            if (
                isinstance(start_year_clim, int)
                and isinstance(end_year_clim, int)
                and start_year_clim > end_year_clim
            ):
                errors.append(
                    f"{model_id}: start_year_clim must be before end_year_clim"
                )
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
    from ai_almanac.server.db import get_db

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
    from ai_almanac.server.db import get_db

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
    from ai_almanac.server.services.regions import list_region_options

    return json.dumps(await list_region_options())


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
                    "model_var",
                    "file_pattern",
                    "probabilistic",
                    "members",
                    "init_days",
                    "date_filter_year",
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

    datasets = await _dataset_candidates(user_id)
    dataset = None
    if isinstance(patch.get("dataset_id"), str):
        dataset = next((d for d in datasets if d["id"] == patch["dataset_id"]), None)
    if dataset is None and spec.dataset_id:
        dataset = next((d for d in datasets if d["id"] == spec.dataset_id), None)

    if "region_id" in patch:
        region = _region_by_id(patch.get("region_id"))
    elif "dataset_id" in patch and dataset:
        region = _region_by_id(dataset.get("region"))
    else:
        region = _region_by_id(spec.region_id)

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
    advanced_params = _clean_advanced_params(
        advanced_params, [model["id"] for model in models]
    )

    next_spec = spec.model_copy(
        update={
            "intent": patch.get("intent")
            if isinstance(patch.get("intent"), str)
            else spec.intent,
            "region_id": region["id"] if region else None,
            "region_name": region["display_name"] if region else None,
            "romp_region": (region.get("romp_name") or "custom") if region else None,
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
    params.update(_non_empty_params(spec.advanced_params, SHARED_ROMP_PARAM_KEYS))
    per_model_params = spec.advanced_params.get("per_model_params")
    if isinstance(per_model_params, dict):
        model_params = per_model_params.get(model["id"])
        if isinstance(model_params, dict):
            params.update(_non_empty_params(model_params, PER_MODEL_ROMP_PARAM_KEYS))
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
        "region": spec.region_id,
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
    from ai_almanac.server.db import get_db

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
    from ai_almanac.server.db import get_db

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
        "queued",
        "starting",
        "running",
        "canceling",
        "canceled",
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
        model_config = cfg.get("model_config") or {}
        jobs.append(
            {
                "job_id": r["id"],
                "model_name": cfg.get("model_display_name")
                or model_config.get("display_name")
                or cfg.get("model_name"),
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
    from ai_almanac.server.db import get_db

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
    model_config = cfg.get("model_config") or {}
    return json.dumps(
        {
            "job_id": job_id,
            "status": row["status"],
            "dataset_id": row["dataset_id"],
            "run_id": row["run_id"],
            "error": row["error"],
            "created_at": row["created_at"],
            "completed_at": row["completed_at"],
            "model_name": cfg.get("model_display_name")
            or model_config.get("display_name")
            or cfg.get("model_name"),
            "model_source_id": cfg.get("model_source_id") or model_config.get("id"),
            "model_dir": cfg.get("model_dir"),
            "obs_dir": cfg.get("obs_dir"),
            "romp_params": cfg.get("romp_params", {}),
        }
    )


async def _exec_get_job_logs(args: dict, user_id: str, scope: BenchmarkScope) -> str:
    from ai_almanac.server.db import get_db

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
    logs = await asyncio.to_thread(storage.read_log, job_id)
    truncated = len(logs) > max_chars
    if truncated:
        logs = logs[-max_chars:]
    return json.dumps({"job_id": job_id, "logs": logs, "truncated": truncated})


async def _exec_rerun_job(args: dict, user_id: str, scope: BenchmarkScope) -> dict:
    from ai_almanac.server.db import get_db

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
            model_name=cfg.get("model_source_id")
            or (cfg.get("model_config") or {}).get("id")
            or cfg.get("model_name", ""),
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
    from ai_almanac.server.db import get_db

    from ..services.metrics import compute_job_metrics
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
        storage = get_storage()
        try:
            metrics = compute_job_metrics(job_id, storage)
        except Exception as exc:
            logger.exception("Could not load metrics for job %s", job_id)
            return {
                "error": f"Could not read metric outputs for job {job_id}: {exc}",
                "job_id": job_id,
            }
        if not metrics.windows:
            return {"error": f"No metric output files found for job {job_id}"}
        return metrics.model_dump()

    return json.dumps(await asyncio.to_thread(_load))


async def _exec_get_spatial_summary(
    args: dict, user_id: str, scope: BenchmarkScope
) -> str:
    import numpy as np

    from ai_almanac.server.db import get_db

    from ..services.metrics import UNIT_MAP
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
        storage = get_storage()
        match = storage.find_nc_output_file(job_id, model, window)

        if not match:
            return {"error": f"No grid file found for {model}/{window}"}

        try:
            ds = storage.open_nc_dataset(match)
            if metric not in ds.data_vars:
                return {"error": f"Metric {metric!r} not in dataset"}

            arr = ds[metric].values.astype(float)
            valid = arr[~np.isnan(arr)]
            lats = ds.lat.values.tolist()
            lons = ds.lon.values.tolist()
        except Exception as exc:
            logger.exception("Could not load grid file %s for job %s", match, job_id)
            return {"error": f"Could not read grid output {match}: {exc}"}
        finally:
            if "ds" in locals():
                ds.close()
                del ds

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
                "unit": UNIT_MAP.get(metric, "days"),
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
    from ai_almanac.server.db import get_db

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
