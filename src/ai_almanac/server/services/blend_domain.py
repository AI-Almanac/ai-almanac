"""Pure-ish domain operations for the chat-driven blend setup.

Mirrors ``benchmark_domain`` for the blend workflow: load/patch/validate/submit
a single :class:`BlendRunSpec` attached to a chat session. Obs and forecast
models are ``data_sources`` rows (not the benchmark model registry), so blend
``model_ids`` / ``obs_dataset_id`` are data-source ids.
"""

from __future__ import annotations

import asyncio
import json
import uuid

import sqlalchemy as sa

from ai_almanac.server.services import data_sources as data_source_service
from ai_almanac.server.services import job_submission
from ai_almanac.server.services.benchmark_state import BenchmarkScope
from ai_almanac.server.services.blend_state import BlendRunSpec, BlendValidation
from ai_almanac.server.tables import jobs as _jobs

# Per-lead columns in the blend's pooled summary CSV, ordered week 1 → later.
# Mirrors AUC_COLUMNS and BRIER_COLUMNS in web/src/routes/blends/blend-summary.ts.
_SKILL_LEAD_COLUMNS = ["auc_week1", "auc_week2", "auc_week3", "auc_week4", "auc_later"]
_BRIER_LEAD_COLUMNS = [
    "brier_week1",
    "brier_week2",
    "brier_week3",
    "brier_week4",
    "brier_later",
]
_BLEND_MODEL = "blended_model"

# The blend package scores every skill column against this baseline
# (``summarize_models_pooled``, baseline_model). ``unc`` is *unconditional*, not
# uncalibrated: it comes from ``prob_clim_mr_unc``, the climatology that does not
# condition on onset having held off until the issue date.
_BASELINE_MODEL = "unc_clim_raw"

# Climatology needs this many observation years before the first forecast year.
# Mirrors ``min_onset_years`` in modal/blending_app.py and the frontend
# ``MIN_ONSET_YEARS`` in web/src/routes/blends/year-coverage.ts.
MIN_ONSET_YEARS = 10


# --------------------------------------------------------------------------
# Source candidates
# --------------------------------------------------------------------------


def _source_candidate(source: dict) -> dict:
    meta = source.get("metadata") or {}
    return {
        "id": source["id"],
        "name": source["name"],
        "region": source.get("region"),
        "start_year": meta.get("start_year"),
        "end_year": meta.get("end_year"),
    }


async def _ready_obs_candidates(user_id: str | None = None) -> list[dict]:
    sources = await data_source_service.get_obs_sources(user_id=user_id)
    return [_source_candidate(s) for s in sources if s.get("status") == "ready"]


async def _ready_model_candidates(
    region: str | None = None, user_id: str | None = None
) -> list[dict]:
    sources = await data_source_service.get_model_sources(region, user_id=user_id)
    return [_source_candidate(s) for s in sources if s.get("status") == "ready"]


# --------------------------------------------------------------------------
# Coverage + year validation (ported from web/.../year-coverage.ts)
# --------------------------------------------------------------------------


def _parse_year_spec(value: str) -> list[int] | None:
    """Parse '2005:2010' / '2011,2012' into sorted unique years, or None if
    any token is malformed (so callers report it instead of dropping years)."""
    try:
        years = job_submission._parse_year_spec(value)
    except ValueError:
        return None
    return years


def _coverage(obs: dict | None, models: list[dict]) -> dict | None:
    if obs is None or not models:
        return None
    obs_start, obs_end = obs.get("start_year"), obs.get("end_year")
    model_starts = [m.get("start_year") for m in models]
    model_ends = [m.get("end_year") for m in models]
    if obs_start is None or obs_end is None:
        return None
    if any(y is None for y in model_starts) or any(y is None for y in model_ends):
        return None
    return {
        "start": max(obs_start, *model_starts),
        "end": min(obs_end, *model_ends),
        "earliest_forecast": max(obs_start + MIN_ONSET_YEARS, *model_starts),
    }


def _year_errors(spec: BlendRunSpec, coverage: dict | None) -> list[str]:
    specs = {
        "training_years": spec.training_years,
        "cv_holdout_years": spec.cv_holdout_years,
        "forecast_years": spec.forecast_years,
        "true_holdout_years": spec.true_holdout_years,
    }
    parsed: dict[str, list[int]] = {}
    for field, text in specs.items():
        years = _parse_year_spec(text)
        if years is None:
            return [f'{field} must look like "2005:2010" or "2011,2012".']
        parsed[field] = years

    if coverage is None:
        return []

    explicit = parsed["forecast_years"]
    forecast_years = explicit or (
        parsed["training_years"] + parsed["cv_holdout_years"] + parsed["true_holdout_years"]
    )
    if not forecast_years:
        return []

    errors: list[str] = []
    low, high = min(forecast_years), max(forecast_years)
    if low < coverage["start"] or high > coverage["end"]:
        errors.append(f"Chosen sources only share data for {coverage['start']}-{coverage['end']}.")
    if low < coverage["earliest_forecast"]:
        errors.append(
            f"Climatology needs {MIN_ONSET_YEARS} years of observations before the "
            f"first forecast year — start at {coverage['earliest_forecast']} or later."
        )
    return errors


# --------------------------------------------------------------------------
# Finalize + validate
# --------------------------------------------------------------------------


def _finalize_blend_config(spec: BlendRunSpec) -> BlendRunSpec:
    missing = []
    if not spec.name.strip():
        missing.append("name")
    if not spec.obs_dataset_id:
        missing.append("observations")
    if not spec.model_ids:
        missing.append("models")
    if not spec.training_years.strip():
        missing.append("training_years")
    if not spec.cv_holdout_years.strip():
        missing.append("cv_holdout_years")
    status = "runnable" if not missing else "collecting"
    assumptions = [
        "Combine the selected forecast models into a single blended forecast.",
        "Use the selected observation dataset as ground truth for scoring.",
        f"Reserve {MIN_ONSET_YEARS} observation years before the first forecast "
        "year for the onset climatology baseline.",
    ]
    return spec.model_copy(
        update={"missing_fields": missing, "status": status, "assumptions": assumptions}
    )


async def _validation_for_config(spec: BlendRunSpec, user_id: str | None = None) -> BlendValidation:
    errors: list[str] = []
    warnings: list[str] = []
    missing = list(spec.missing_fields)

    obs_candidates = {c["id"]: c for c in await _ready_obs_candidates(user_id)}
    obs = obs_candidates.get(spec.obs_dataset_id) if spec.obs_dataset_id else None
    if spec.obs_dataset_id and obs is None:
        errors.append(f"Unknown or unavailable observation source: {spec.obs_dataset_id}")

    model_region = obs.get("region") if obs else None
    model_candidates = {c["id"]: c for c in await _ready_model_candidates(model_region, user_id)}
    selected_models = [model_candidates[mid] for mid in spec.model_ids if mid in model_candidates]
    bad_models = [mid for mid in spec.model_ids if mid not in model_candidates]
    if bad_models:
        errors.append("Models are not available for this region: " + ", ".join(bad_models))

    coverage = _coverage(obs, selected_models)
    errors.extend(_year_errors(spec, coverage))

    can_run = not missing and not errors
    return BlendValidation(
        can_run=can_run,
        status="runnable" if can_run else "collecting",
        missing_fields=missing,
        errors=errors,
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Session-attached state (chat_sessions.blend_config / blend_validation)
# --------------------------------------------------------------------------


async def _load_blend_config(session_id: str, user_id: str) -> BlendRunSpec:
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    sa.text(
                        "SELECT blend_config FROM chat_sessions WHERE id = :id AND user_id = :uid"
                    ),
                    {"id": session_id, "uid": user_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row or not row["blend_config"]:
        return BlendRunSpec()
    value = row["blend_config"]
    if isinstance(value, str):
        value = json.loads(value)
    return BlendRunSpec.model_validate(value)


async def _save_blend_state(
    session_id: str,
    user_id: str,
    spec: BlendRunSpec,
    validation: BlendValidation,
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
                SET blend_config = :config,
                    blend_validation = :validation
                    {run_sql}
                WHERE id = :id AND user_id = :uid
            """),
            values,
        )


def blend_payload(spec: BlendRunSpec, validation: BlendValidation, **extra: object) -> dict:
    return {
        "blend_config": spec.model_dump(mode="json"),
        "blend_validation": validation.model_dump(mode="json"),
        **extra,
    }


async def validation_for_config(spec: BlendRunSpec) -> BlendValidation:
    return await _validation_for_config(spec)


# --------------------------------------------------------------------------
# Tool entrypoints
# --------------------------------------------------------------------------


async def list_blend_models(region: str | None, user_id: str, scope: BenchmarkScope) -> dict:
    return {"models": await _ready_model_candidates(region, user_id)}


async def get_blend_config(user_id: str, scope: BenchmarkScope, session_id: str) -> dict:
    spec = _finalize_blend_config(await _load_blend_config(session_id, user_id))
    validation = await _validation_for_config(spec, user_id)
    await _save_blend_state(session_id, user_id, spec, validation)
    return blend_payload(spec, validation)


async def update_blend_config(
    patch: dict, user_id: str, scope: BenchmarkScope, session_id: str
) -> dict:
    spec = await _load_blend_config(session_id, user_id)

    obs_candidates = {c["id"]: c for c in await _ready_obs_candidates(user_id)}
    obs_id = patch.get("obs_dataset_id") if "obs_dataset_id" in patch else spec.obs_dataset_id
    obs = obs_candidates.get(obs_id) if isinstance(obs_id, str) else None

    # Models are region-scoped to the chosen observations, matching the UI.
    model_region = obs.get("region") if obs else None
    model_candidates = {c["id"]: c for c in await _ready_model_candidates(model_region, user_id)}
    raw_model_ids = (
        patch["model_ids"] if isinstance(patch.get("model_ids"), list) else spec.model_ids
    )
    models = [model_candidates[mid] for mid in raw_model_ids if mid in model_candidates]

    def text_field(key: str) -> str:
        value = patch.get(key)
        return value if isinstance(value, str) else getattr(spec, key)

    next_spec = spec.model_copy(
        update={
            "intent": text_field("intent"),
            "name": text_field("name"),
            "obs_dataset_id": obs["id"] if obs else None,
            "obs_dataset_name": obs["name"] if obs else None,
            "region_id": obs.get("region") if obs else None,
            "model_ids": [m["id"] for m in models],
            "model_names": [m["name"] for m in models],
            "training_years": text_field("training_years"),
            "cv_holdout_years": text_field("cv_holdout_years"),
            "forecast_years": text_field("forecast_years"),
            "true_holdout_years": text_field("true_holdout_years"),
            "formula_text": text_field("formula_text"),
        }
    )
    next_spec = _finalize_blend_config(next_spec)
    validation = await _validation_for_config(next_spec, user_id)
    await _save_blend_state(session_id, user_id, next_spec, validation)
    return blend_payload(next_spec, validation)


async def validate_blend_config(user_id: str, scope: BenchmarkScope, session_id: str) -> dict:
    spec = _finalize_blend_config(await _load_blend_config(session_id, user_id))
    validation = await _validation_for_config(spec, user_id)
    await _save_blend_state(session_id, user_id, spec, validation)
    return blend_payload(spec, validation)


async def propose_blend_submit(user_id: str, scope: BenchmarkScope, session_id: str) -> dict:
    spec = _finalize_blend_config(await _load_blend_config(session_id, user_id))
    validation = await _validation_for_config(spec, user_id)
    await _save_blend_state(session_id, user_id, spec, validation)
    payload = blend_payload(spec, validation)
    if not validation.can_run:
        payload["error"] = "Blend config is not runnable — cannot request approval"
        return payload
    payload["approval_required"] = True
    return payload


def _blend_create_body(spec: BlendRunSpec, run_id: str) -> job_submission.BlendCreate:
    def opt(value: str) -> str | None:
        return value.strip() or None

    return job_submission.BlendCreate(
        name=spec.name.strip(),
        obs_dataset_id=spec.obs_dataset_id or "",
        model_ids=list(spec.model_ids),
        params=job_submission.BlendParams(
            training_years=spec.training_years.strip(),
            cv_holdout_years=spec.cv_holdout_years.strip(),
            forecast_years=opt(spec.forecast_years),
            true_holdout_years=opt(spec.true_holdout_years),
            formula_text=opt(spec.formula_text),
        ),
        run_id=run_id,
    )


async def submit_blend_for_session(user_id: str, scope: BenchmarkScope, session_id: str) -> dict:
    spec = _finalize_blend_config(await _load_blend_config(session_id, user_id))
    validation = await _validation_for_config(spec, user_id)
    if not validation.can_run:
        await _save_blend_state(session_id, user_id, spec, validation)
        return blend_payload(spec, validation, error="Blend config is not runnable")

    run_id = str(uuid.uuid4())
    blend = await job_submission.create_blend_for_user(_blend_create_body(spec, run_id), user_id)
    job = blend.model_dump(mode="json")

    submitted = spec.model_copy(update={"status": "running"})
    submitted_validation = validation.model_copy(update={"status": "running"})
    await _save_blend_state(session_id, user_id, submitted, submitted_validation, run_id)

    from ai_almanac.server.db import get_db

    next_scope = BenchmarkScope(kind="job_set", key=run_id, title=scope.title, job_ids=[blend.id])
    async with get_db() as conn:
        await conn.execute(
            sa.text("UPDATE chat_sessions SET scope = :scope WHERE id = :id AND user_id = :uid"),
            {
                "scope": json.dumps(next_scope.model_dump(mode="json")),
                "id": session_id,
                "uid": user_id,
            },
        )
    return blend_payload(submitted, submitted_validation, run_id=run_id, jobs=[job])


async def get_current_blend_config(session_id: str, user_id: str) -> BlendRunSpec:
    return _finalize_blend_config(await _load_blend_config(session_id, user_id))


# --------------------------------------------------------------------------
# Results analysis
# --------------------------------------------------------------------------


def _brier_skill(value: float | None, baseline: float | None) -> float | None:
    """Skill of a Brier score against the baseline, in the lower-is-better form.

    A zero or missing baseline makes the ratio meaningless, so it yields None
    rather than raising or returning infinity.
    """
    if value is None or baseline is None or baseline == 0:
        return None
    return 1.0 - value / baseline


def _parse_pooled_summary(csv_text: str) -> list[dict]:
    """Parse the blend's pooled per-model summary CSV into skill rows.

    Mirrors ``parsePooledSummary`` in web/.../blend-summary.ts: one row per model
    with its pooled scores and its per-lead AUC and Brier series.

    The Ranked Probability Skill Score matters most here — the outcome is five
    ordinal bins (weeks 1-4, later), so a metric that credits being close beats
    one that only asks whether the right bin won. The blend reports it pooled
    only, so it has no per-lead series.
    """
    lines = [line for line in csv_text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return []
    header = lines[0].split(",")
    index = {name: position for position, name in enumerate(header)}
    if "model" not in index:
        return []

    def cell(cells: list[str], name: str) -> float | None:
        position = index.get(name)
        if position is None or position >= len(cells):
            return None
        try:
            return float(cells[position])
        except ValueError:
            # pandas writes NaN as an empty string.
            return None

    rows: list[dict] = []
    for line in lines[1:]:
        cells = line.split(",")
        model = cells[index["model"]] if index["model"] < len(cells) else ""
        if not model:
            continue
        observations = cell(cells, "n")
        rows.append(
            {
                "model": model,
                "is_blend": model == _BLEND_MODEL,
                "is_baseline": model == _BASELINE_MODEL,
                "auc": cell(cells, "auc"),
                "brier": cell(cells, "brier"),
                "rps": cell(cells, "rps"),
                "brier_skill": cell(cells, "brier_skill"),
                "rps_skill": cell(cells, "rps_skill"),
                "pietra": cell(cells, "pietra"),
                "observations": None if observations is None else int(observations),
                "auc_by_lead": [cell(cells, col) for col in _SKILL_LEAD_COLUMNS],
                "brier_by_lead": [cell(cells, col) for col in _BRIER_LEAD_COLUMNS],
                "brier_skill_by_lead": [None] * len(_BRIER_LEAD_COLUMNS),
            }
        )

    # The blend reports raw Brier per lead but skill only pooled, so derive the
    # per-lead skill from the baseline row that is already in this same file.
    baseline = next((row for row in rows if row["is_baseline"]), None)
    if baseline is not None:
        for row in rows:
            row["brier_skill_by_lead"] = [
                _brier_skill(value, base)
                for value, base in zip(row["brier_by_lead"], baseline["brier_by_lead"], strict=True)
            ]

    # Blend first so the model's own row leads any rendered comparison.
    rows.sort(key=lambda row: not row["is_blend"])
    return rows


async def _blend_job_status(job_id: str, user_id: str, scope: BenchmarkScope):
    from ai_almanac.server.db import get_db
    from ai_almanac.server.services import benchmark_domain

    query = (
        sa.select(_jobs.c.status, _jobs.c.job_type)
        .where(_jobs.c.id == sa.bindparam("id"))
        .where(_jobs.c.user_id == sa.bindparam("uid"))
    )
    for cond in benchmark_domain._scope_conditions(scope, _jobs):
        query = query.where(cond)
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    query,
                    {"id": job_id, "uid": user_id, **benchmark_domain._scope_params(scope)},
                )
            )
            .mappings()
            .fetchone()
        )
    return dict(row) if row else None


async def get_blend_results(job_id: str, user_id: str, scope: BenchmarkScope) -> dict:
    """Read a completed blend's pooled per-model skill summary and artifact list."""
    from ai_almanac.server.services.artifacts import list_job_artifacts
    from ai_almanac.server.services.storage import get_storage

    row = await _blend_job_status(job_id, user_id, scope)
    if not row:
        return {"error": f"Blend {job_id} not found"}
    if row["job_type"] != "blend":
        return {"error": f"Job {job_id} is not a blend"}
    if row["status"] != "complete":
        return {"error": f"Blend {job_id} is not complete (status: {row['status']})"}

    artifacts = await list_job_artifacts(job_id)
    summary = next(
        (a for a in artifacts if a["filename"].startswith("summary_models_pooled")),
        None,
    )
    artifact_list = [
        {"filename": a["filename"], "kind": a["kind"], "size_bytes": a["size_bytes"]}
        for a in artifacts
    ]
    if summary is None:
        return {
            "job_id": job_id,
            "skill": [],
            "artifacts": artifact_list,
            "note": "No pooled summary artifact found for this blend.",
        }

    text = await asyncio.to_thread(
        get_storage().read_result_text, job_id, summary["kind"], summary["filename"]
    )
    if text is None:
        return {"error": f"Could not read the summary file for blend {job_id}"}
    return {
        "job_id": job_id,
        "skill": _parse_pooled_summary(text),
        "spatial": await _cell_coverage(job_id, artifacts),
        "artifacts": artifact_list,
    }


async def _cell_coverage(job_id: str, artifacts: list[dict]) -> list[dict]:
    """Where the blend beats climatology, alongside whether it does on average.

    Pooled skill cannot distinguish a small gain everywhere from a large gain in
    one corner, and the per-point summary is the only artifact that can. Absent or
    unreadable, this is simply omitted: it enriches an answer about a blend rather
    than being the answer, so failing to read it must not fail the whole tool.
    """
    from ai_almanac.server.services import blend_cells
    from ai_almanac.server.services.storage import get_storage

    summary = next(
        (a for a in artifacts if blend_cells.is_per_cell_summary(a["filename"])),
        None,
    )
    if summary is None:
        return []
    text = await asyncio.to_thread(
        get_storage().read_result_text, job_id, summary["kind"], summary["filename"]
    )
    if text is None:
        return []
    metrics = blend_cells.build_cell_metrics(job_id, text)
    return [c.model_dump() for c in blend_cells.coverage_summary(metrics)]
