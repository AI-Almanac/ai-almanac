"""Benchmark, blend and forecast runs from the workspace.

Stateless and typed (D-R7): the whole spec is the tool argument, so what the user
approves is exactly what is validated, submitted and hashed.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import numpy as np
import sqlalchemy as sa
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import RunContext

from ai_almanac.server import tables
from ai_almanac.settings import settings

from .. import (
    approval_display,
    area_summaries,
    job_submission,
    ledger,
    proposed_run_facts,
    read_sets,
    run_summaries,
    workspace_settings,
    workspaces,
)
from ..approval_display import Fact
from ..blend_forecast import WEEKLY_PROBABILITIES_CSV
from ..chat_tools import BenchmarkAdvancedParams, PerModelRompParams
from ..focus_area import FocusArea
from ..seasons import SeasonWindow
from .registry import Refusal, json_params, primitive


class BenchmarkSpec(BaseModel):
    """A complete benchmark: one ROMP job per model, scored against one observation dataset."""

    dataset_id: str = Field(description="Observation dataset id from list_datasets.")
    model_ids: list[str] = Field(min_length=1, description="Model ids from list_models.")
    region_id: str | None = Field(
        default=None, description="Region id from list_regions; defaults to the dataset's region."
    )
    event_type: str = "monsoon_onset"
    forecast_window_days: int = Field(default=30, ge=30)
    intent: str = Field(default="", description="One sentence on what the user wants to learn.")
    advanced_params: BenchmarkAdvancedParams = Field(default_factory=BenchmarkAdvancedParams)


class BlendSpec(BaseModel):
    """A complete blend: forecast models combined with weights trained against observations."""

    name: str = Field(min_length=1)
    obs_dataset_id: str = Field(description="Observation data source id.")
    model_ids: list[str] = Field(
        min_length=1, description="Model source ids from list_blend_models."
    )
    training_years: str = Field(
        description=(
            'Years like "2000:2015" or "2001,2003". The first '
            f"{job_submission.MIN_ONSET_YEARS} observation years build the onset climatology, "
            "so no training or holdout year may come before them."
        )
    )
    cv_holdout_years: str
    forecast_years: str = ""
    true_holdout_years: str = ""
    formula_text: str = ""
    intent: str = ""
    focus_area: FocusArea | None = Field(
        default=None,
        description=(
            "Area of interest the blend is trained and scored on. Either a lat/lon box "
            '{"lat_min", "lat_max", "lon_min", "lon_max"} or named areas '
            '{"level": "adm1" or "adm2", "units": [names from list_areas]}, one level per '
            'area. Add "iso" (a three-letter country code from find_country) to score in '
            "that country whatever region the data is filed under; a whole country is "
            '{"level": "adm0", "units": [its name], "iso": its code}. Omit to use the '
            "whole region."
        ),
    )
    threshold_mm: float | None = Field(
        default=None,
        description="Rainfall in mm over the onset window that marks onset. Omit for "
        f"{job_submission.DEFAULT_THRESHOLD_MM:g} mm.",
    )
    cutoff_month_day: str = Field(
        default="",
        description="MM-DD from which onset is searched each season; also the first "
        f'forecast issue date. Empty for "{job_submission.DEFAULT_CUTOFF_MONTH_DAY}".',
    )
    ref_onset_month_day: str = Field(
        default="",
        description="MM-DD climatological onset date that the onset-before-this-date "
        f'probability is scored against. Empty for "{job_submission.DEFAULT_REF_ONSET_MONTH_DAY}".',
    )
    train_day_level_blend: bool | None = Field(
        default=None,
        description="Also train day-by-day onset probabilities. Omit to follow this "
        "deployment's setting; true is refused where they are turned off.",
    )


class RerunOverrides(PerModelRompParams):
    """ROMP parameters to change on the rerun; everything left out keeps the original value."""

    wet_threshold: float | None = None
    wet_init: float | None = None
    wet_spell: int | None = None
    dry_spell: int | None = None
    dry_extent: int | None = None
    onset_rule: str | None = None
    onset_rule_params: dict | None = None


def _approved_submission(ctx: RunContext, **params: object) -> ledger.Attribution:
    """The assistant's attribution for a submission the user approved as ``params``.

    Serialized by the same function the decorator uses for the approval card, so
    the approval's payload hash covers exactly what the user was shown (D8).
    """
    deps = ctx.deps
    attribution = ledger.Attribution.by_assistant(
        deps.user_id, deps.session_id, approved=True, turn=deps.turn
    )
    return replace(attribution, approved_payload=json_params(params))


async def _link_submitted(ctx: RunContext, payload: dict) -> dict:
    for job in payload.get("jobs") or []:
        await workspaces.link_job(ctx.deps.scope.key, job["id"])
    return payload


async def _linked_job(ctx: RunContext, job_id: str) -> dict | None:
    found = await workspaces.job_rows(
        sa.and_(tables.jobs.c.id == job_id, workspaces.in_workspace(ctx.deps.scope.key))
    )
    return found[0] if found else None


# --- what a run takes from the workspace ---------------------------------------


def with_season_dates(spec: BlendSpec, season: SeasonWindow | None) -> BlendSpec:
    """The spec with the season dates it leaves unset taken from the workspace season."""
    if season is None:
        return spec
    return spec.model_copy(
        update={
            "cutoff_month_day": spec.cutoff_month_day or season.onset_search_start,
            "ref_onset_month_day": spec.ref_onset_month_day or season.reference_onset,
        }
    )


async def _workspace_blend(ctx: RunContext, spec: BlendSpec) -> BlendSpec:
    """The spec with what it leaves unset taken from the workspace: season dates and area."""
    settings_ = await workspace_settings.get_settings(ctx.deps.scope.key)
    spec = with_season_dates(spec, settings_.season)
    return spec.model_copy(update={"focus_area": spec.focus_area or settings_.study_area})


async def _workspace_benchmark(ctx: RunContext, spec: BenchmarkSpec) -> BenchmarkSpec:
    """The spec scored in the workspace's area when it names none of its own."""
    settings_ = await workspace_settings.get_settings(ctx.deps.scope.key)
    advanced = spec.advanced_params
    if advanced.focus_area or settings_.study_area is None:
        return spec
    return spec.model_copy(
        update={"advanced_params": advanced.model_copy(update={"focus_area": settings_.study_area})}
    )


async def _benchmark_in_workspace(ctx: RunContext, spec: BenchmarkSpec) -> dict:
    return {"spec": await _workspace_benchmark(ctx, spec)}


async def _blend_in_workspace(ctx: RunContext, spec: BlendSpec) -> dict:
    return {"spec": await _workspace_blend(ctx, spec)}


async def _forecast_in_workspace_season(ctx: RunContext, **arguments: object) -> dict:
    """The forecast's arguments with a season it does not name taken from the workspace;
    with neither, the platform default applies."""
    if arguments.get("season") is not None:
        return arguments
    season = (await workspace_settings.get_settings(ctx.deps.scope.key)).season
    return {**arguments, "season": season}


# --- benchmarks ---------------------------------------------------------------


@primitive("benchmark.validate_benchmark", "1.0.0", resolve=_benchmark_in_workspace)
async def validate_benchmark(ctx: RunContext, spec: BenchmarkSpec) -> dict:
    """Check a complete benchmark spec against the catalog and the statistical guardrails
    without submitting anything. Returns the resolved spec and `benchmark_validation` with
    errors, warnings and whether it can run."""
    from .. import benchmark_domain

    return await benchmark_domain.validate_benchmark_spec(
        spec.model_dump(exclude_none=True), ctx.deps.user_id
    )


async def _describe_submit_benchmark(ctx: RunContext, spec: BenchmarkSpec) -> list[Fact]:
    sources = await approval_display.ready_sources(ctx.deps.user_id)
    dataset = sources.get(spec.dataset_id) or {}
    region_id = spec.region_id or dataset.get("region")
    spans = _evaluation_spans(spec, sources)
    return [
        *approval_display.benchmark_facts(
            spec, sources, await approval_display.region_label(region_id), spans
        ),
        *await proposed_run_facts.execution_facts(
            ctx.deps.user_id,
            proposed_run_facts.benchmark_jobs(
                region_id, spans.values(), spec.advanced_params.focus_area
            ),
        ),
    ]


def _evaluation_spans(
    spec: BenchmarkSpec, sources: approval_display.Sources
) -> dict[str, approval_display.YearSpan | None]:
    """The years each model is scored over, by model id."""
    per_model = spec.advanced_params.per_model_params or {}
    return {
        ref: approval_display.evaluation_span(sources.get(ref), per_model.get(ref))
        for ref in spec.model_ids
    }


@primitive(
    "benchmark.submit_benchmark",
    "1.0.0",
    needs_approval=True,
    describe=_describe_submit_benchmark,
    resolve=_benchmark_in_workspace,
)
async def submit_benchmark(ctx: RunContext, spec: BenchmarkSpec) -> dict:
    """Submit a complete benchmark spec: one job per model, linked into this workspace. The
    user approves the exact spec first. An invalid spec submits nothing and returns its
    validation errors; fix the spec and call again."""
    from .. import benchmark_domain

    payload = await benchmark_domain.submit_benchmark_spec(
        spec.model_dump(exclude_none=True), ctx.deps.user_id, _approved_submission(ctx, spec=spec)
    )
    return await _link_submitted(ctx, payload)


async def _describe_rerun_job(
    ctx: RunContext, job_id: str, overrides: RerunOverrides | None = None
) -> list[Fact]:
    config = await approval_display.readable_job_config(job_id, ctx.deps.user_id)
    return [
        *_rerun_facts(job_id, config, overrides),
        *await proposed_run_facts.execution_facts(
            ctx.deps.user_id, proposed_run_facts.rerun_jobs(config)
        ),
    ]


def _rerun_facts(job_id: str, config: dict | None, overrides: RerunOverrides | None) -> list[Fact]:
    changes = (overrides or RerunOverrides()).model_dump(mode="json", exclude_none=True)
    changed = approval_display.generic_facts(changes, {})
    return [
        *approval_display.facts(("Job", approval_display.job_label(config) or job_id)),
        *(changed or approval_display.facts(("Changes", "None; same settings as before"))),
    ]


@primitive("benchmark.rerun_job", "1.0.0", needs_approval=True, describe=_describe_rerun_job)
async def rerun_job(ctx: RunContext, job_id: str, overrides: RerunOverrides | None = None) -> dict:
    """Rerun a benchmark job linked to this workspace with changed ROMP parameters. The new
    job joins the original's run and is linked here. Use it only after the user asks to rerun
    or confirms the fix."""
    from fastapi import HTTPException

    from .. import benchmark_domain

    try:
        result = await benchmark_domain.rerun_job_in_scope(
            job_id,
            (overrides or RerunOverrides()).model_dump(exclude_none=True),
            ctx.deps.user_id,
            ctx.deps.scope,
            _approved_submission(ctx, job_id=job_id, overrides=overrides),
        )
    except HTTPException as exc:
        raise Refusal(str(exc.detail)) from exc
    if "job" in result:
        await workspaces.link_job(ctx.deps.scope.key, result["job"]["id"])
    return result


# --- blends -------------------------------------------------------------------


@primitive("blend.validate_blend", "1.0.0", resolve=_blend_in_workspace)
async def validate_blend(ctx: RunContext, spec: BlendSpec) -> dict:
    """Check a complete blend spec (sources, year splits, holdout leakage, member count)
    without submitting anything. Returns the spec and `blend_validation`."""
    from .. import blend_domain

    return await blend_domain.validate_blend_spec(spec.model_dump(), ctx.deps.user_id)


async def _describe_submit_blend(ctx: RunContext, spec: BlendSpec) -> list[Fact]:
    return approval_display.blend_facts(
        spec, await approval_display.ready_sources(ctx.deps.user_id)
    )


@primitive(
    "blend.submit_blend",
    "1.0.0",
    needs_approval=True,
    describe=_describe_submit_blend,
    resolve=_blend_in_workspace,
)
async def submit_blend(ctx: RunContext, spec: BlendSpec) -> dict:
    """Train a blend from a complete spec, linked into this workspace. The user approves the
    exact spec first. An invalid spec submits nothing and returns its validation errors."""
    from .. import blend_domain

    attribution = _approved_submission(ctx, spec=spec)
    payload = await blend_domain.submit_blend_spec(spec.model_dump(), ctx.deps.user_id, attribution)
    return await _link_submitted(ctx, payload)


# --- forecasts ----------------------------------------------------------------


def _forecast_request(
    blend_id: str | None = None,
    models: list[str] | None = None,
    season_year: int | None = None,
    season: SeasonWindow | None = None,
) -> job_submission.ForecastCreate:
    """The request a forecast call makes; raises ValidationError for one that cannot parse."""
    return job_submission.ForecastCreate(
        blend_id=blend_id, models=models, season_year=season_year, season=season
    )


def _validation_message(exc: ValidationError) -> str:
    return "; ".join(error["msg"].removeprefix("Value error, ") for error in exc.errors())


async def _forecast_refusal(**arguments: object) -> None:
    """Refuse a forecast request that can't parse before asking for approval."""
    if not settings.enable_forecasting:
        raise Refusal("Forecasting is turned off on this deployment")
    try:
        _forecast_request(**arguments)
    except ValidationError as exc:
        raise Refusal(_validation_message(exc)) from exc


async def _cost_check(ctx: RunContext, arguments: dict) -> dict | None:
    """What `check_forecast` would say about this request; None when it cannot be worked
    out, so the card still shows the request itself."""
    from fastapi import HTTPException

    from .. import forecast_checks

    try:
        return await forecast_checks.check_forecast(
            _forecast_request(**arguments), ctx.deps.user_id
        )
    except (HTTPException, ValidationError):
        return None


async def _describe_run_forecast(ctx: RunContext, **arguments: object) -> list[Fact]:
    """Reads whichever of ``blend_id``, ``models`` and the season were sent, by name."""
    user_id = ctx.deps.user_id
    blend_id = arguments.get("blend_id")
    blend = await approval_display.readable_job_config(blend_id, user_id) if blend_id else None
    sources = await approval_display.ready_sources(user_id) if arguments.get("models") else {}
    return approval_display.forecast_facts(
        arguments, blend, sources, check=await _cost_check(ctx, arguments)
    )


@primitive(
    "forecast.run_forecast",
    "2.0.0",
    needs_approval=True,
    precheck=_forecast_refusal,
    describe=_describe_run_forecast,
    resolve=_forecast_in_workspace_season,
)
async def run_forecast(
    ctx: RunContext,
    blend_id: str | None = None,
    models: list[str] | None = None,
    season_year: int | None = None,
    season: SeasonWindow | None = None,
) -> dict:
    """Run a forecast for the season from live initial conditions. Give exactly one of:

    - `blend_id`: a completed blend. Its models are rolled out and scored against the
      blend's trained weights, giving calibrated onset probabilities per window.
    - `models`: model data source ids from list_blend_models, forecast raw, with no blend.
      Each model's rainfall for the season is published as-is; there are no blended
      probabilities. Raw onset skill is often well below climatology, so read a raw forecast
      against that model's benchmark skill for the region, and say when it has none here.

    `season_year` defaults to the current season; a past year re-forecasts an ended season.
    `season` is the season window (name, onset search start, reference onset, last issue
    date); a blend keeps the onset dates it was trained on. Call `check_forecast` first: a
    cached season costs nothing, a cold one rolls out on a GPU and can take hours. The result
    says where the season stands. The job is linked into this workspace; when it completes,
    `get_forecast_results` summarizes it and each model's raw season forecast appears as a
    dataset artifact."""
    from fastapi import HTTPException

    body = _forecast_request(blend_id, models, season_year, season)
    try:
        job = await job_submission.build_forecast_job(body, ctx.deps.user_id)
        forecast = await job_submission.submit_forecast_job(
            job,
            ctx.deps.user_id,
            attribution=_approved_submission(
                ctx, blend_id=blend_id, models=models, season_year=season_year, season=season
            ),
        )
    except HTTPException as exc:
        raise Refusal(str(exc.detail)) from exc
    await workspaces.link_job(ctx.deps.scope.key, forecast.id)
    return {
        "job": forecast.model_dump(mode="json"),
        "blended": blend_id is not None,
        "season": job.season.model_dump(mode="json") if job.season else None,
    }


@primitive(
    "forecast.check_forecast",
    "1.0.0",
    precheck=_forecast_refusal,
    resolve=_forecast_in_workspace_season,
)
async def check_forecast(
    ctx: RunContext,
    blend_id: str | None = None,
    models: list[str] | None = None,
    season_year: int | None = None,
    season: SeasonWindow | None = None,
) -> dict:
    """Say what a forecast would cost before running it; submits nothing. Takes the same
    arguments as `run_forecast`. Returns where the season stands (`season.summary`), whether
    the forecast can run and why not, and per model: the live model it runs, whether its
    season is already cached (free) or which issue dates still need a GPU rollout, and a
    rough GPU time estimate (`gpu_minutes` of GPU time, `wall_minutes` best case if nothing
    else is queued). Tell the user the season state and cost before asking to run."""
    from fastapi import HTTPException

    from .. import forecast_checks

    try:
        return await forecast_checks.check_forecast(
            _forecast_request(blend_id, models, season_year, season), ctx.deps.user_id
        )
    except HTTPException as exc:
        raise Refusal(str(exc.detail)) from exc


async def _describe_update_forecast(ctx: RunContext, job_id: str) -> list[Fact]:
    forecast = await approval_display.readable_job_config(job_id, ctx.deps.user_id)
    return approval_display.facts(
        ("Forecast", approval_display.job_label(forecast) or job_id),
        ("Adds", "Issue dates that have come since it ran; stored ones are reused"),
    )


@primitive(
    "forecast.update_forecast", "1.0.0", needs_approval=True, describe=_describe_update_forecast
)
async def update_forecast(ctx: RunContext, job_id: str) -> dict:
    """Re-issue a forecast linked to this workspace with its original settings, adding the
    issue dates that have come since it ran. Cached dates are reused; new ones may need a
    GPU rollout. When nothing has changed it starts no run and says it is already up to
    date. The new run is linked into this workspace."""
    from fastapi import HTTPException

    job = await _linked_job(ctx, job_id)
    if job is None or job["job_type"] != "forecast":
        raise Refusal(f"Forecast {job_id} is not linked to this workspace")
    try:
        refreshed = await job_submission.refresh_forecast_for_user(
            job_id, ctx.deps.user_id, attribution=_approved_submission(ctx, job_id=job_id)
        )
    except HTTPException as exc:
        raise Refusal(str(exc.detail)) from exc
    if refreshed.up_to_date:
        return {
            "up_to_date": True,
            "job_id": job_id,
            "message": "This forecast is already up to date: no issue date has come since it ran.",
        }
    await workspaces.link_job(ctx.deps.scope.key, refreshed.forecast.id)
    return {"up_to_date": False, "job": refreshed.forecast.model_dump(mode="json")}


_RAW_FORECAST = (
    "This forecast ran its models raw, without a blend, so it has no blended "
    "probabilities. Each model's season forecast is a dataset artifact named "
    "<model>_season_forecast; read onset from it with `detect_onset`, map it with `plot_map`, "
    "and read it against that model's benchmark skill for the region."
)


@primitive("forecast.get_forecast_results", "1.0.0")
async def get_forecast_results(ctx: RunContext, job_id: str) -> dict:
    """Summarize a completed forecast linked to this workspace: for each issue date, the
    region-mean probability that onset falls in weeks 1-4 or later, and how many grid points
    have each window as their most likely. The full per-point probabilities and each model's
    raw season forecast are workspace artifacts; use `inspect_artifact`, `plot_map` or
    `run_code` on those for detail."""
    from ..storage import get_storage

    job = await _linked_job(ctx, job_id)
    if job is None:
        raise Refusal(f"Forecast {job_id} is not linked to this workspace")
    if job["job_type"] != "forecast":
        raise Refusal(f"Job {job_id} is a {job['job_type']}, not a forecast")
    if job["status"] != run_summaries.FINISHED:
        return {"job_id": job_id, "status": job["status"], "error": job.get("error")}
    if not job["config"].get("blend_id"):
        return {"job_id": job_id, "blended": False, "message": _RAW_FORECAST}
    text = await asyncio.to_thread(
        get_storage().read_result_text, job_id, "output", WEEKLY_PROBABILITIES_CSV
    )
    try:
        summary = summarize_forecast_csv(text or "")
    except (KeyError, ValueError) as exc:
        raise Refusal(f"Forecast {job_id} has no blended probabilities") from exc
    return await read_sets.with_job_sources(
        {"job_id": job_id, **summary},
        ctx.deps.scope,
        job_id,
        read_sets.is_file_named(WEEKLY_PROBABILITIES_CSV),
    )


def summarize_forecast_csv(text: str) -> dict:
    """Per-issue-date region means and most-likely-window counts from the blend CSV. A
    point with no probabilities for an issue date is left out of that date, never read as
    no chance of onset."""
    table = area_summaries.parse_forecast_table(text)
    windows = [window.name for window in table.windows]
    issue_dates = []
    for index, issue_date in enumerate(table.issue_dates):
        probs = table.probs[:, index, :]
        forecast = probs[np.isfinite(probs).any(axis=1)]
        if not len(forecast):
            continue
        likeliest = np.nanargmax(forecast, axis=1)
        issue_dates.append(
            {
                "issue_date": issue_date,
                "points": len(forecast),
                "mean_probability": {
                    window: _rounded_mean(forecast[:, i]) for i, window in enumerate(windows)
                },
                "most_likely_window_points": {
                    window: int((likeliest == i).sum()) for i, window in enumerate(windows)
                },
            }
        )
    return {"windows": windows, "issue_dates": issue_dates}


def _rounded_mean(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    return round(float(finite.mean()), 3) if finite.size else None
