"""The facts a forecaster wants before opening any file: where the season stands, what
the workspace is set up for, the latest forecast, and the headline skill behind it.

Pure functions build the summary from plain job rows and the small CSVs a run
publishes; the shell at the bottom reads those rows. A headline that cannot be read
(outputs gone, a run that wrote nothing) is left out rather than failing the page.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
from collections import Counter
from datetime import date
from typing import Literal

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict

from ai_almanac.server import tables
from ai_almanac.server.db import get_db

from . import blend_summaries, metrics, workspace_display, workspaces
from .area_summaries import parse_forecast_table
from .artifacts import list_job_artifacts
from .blend_forecast import WEEKLY_PROBABILITIES_CSV
from .focus_area import country_of
from .metrics import JobMetrics
from .region_catalog import get_region
from .run_summaries import FINISHED
from .seasons import SeasonPhase, default_season, resolve_season, spoken_date
from .skill_files import window_start
from .workspace_settings import WorkspaceSettings, get_settings

logger = logging.getLogger(__name__)

_WEEKLY_BLEND_MODEL = "blended_model"
_CLIMATOLOGY = "climatology"
_MEAN_MAE = "mean_mae"
_HEADLINE_WINDOW = "1-15"


class SeasonSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    year: int
    phase: SeasonPhase
    first_issue_date: date
    # What ``first_issue_date`` is, in words to show beside the season's name: the day
    # the search for onset opens, which is earlier than the rains themselves.
    starts_label: str
    last_issue_date: date
    issued_through: date | None
    days_until_start: int | None
    next_season_start: date | None
    # True when no season is set on the workspace and the platform defaults frame it.
    is_default: bool


class SetupSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    region: str | None
    observations: str | None
    models: list[str]


class ForecastSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: Literal["forecast"] = "forecast"
    job_id: str
    status: str
    blended: bool
    blend_name: str | None
    first_issue_date: date | None
    last_issue_date: date | None
    issue_dates: int
    created_at: str


class BlendSkill(BaseModel):
    """The newest trained blend's pooled skill against Traditional Climatology."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["blend"] = "blend"
    job_id: str
    name: str
    rps_skill: float
    brier_skill: float
    holdout_years: str | None


class BenchmarkSkill(BaseModel):
    """The best model of the newest benchmark run, by mean onset error."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["benchmark"] = "benchmark"
    job_id: str
    model: str
    mae_days: float
    climatology_mae_days: float | None
    window: str


JobHeadline = BlendSkill | BenchmarkSkill | ForecastSummary
"""What a result row leads with: a blend's skill, a benchmark run's best model (on
that model's job), or a forecast's issue dates."""


class WorkspaceSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    season: SeasonSummary
    setup: SetupSummary
    latest_forecast: ForecastSummary | None
    skill: BlendSkill | BenchmarkSkill | None


# --- functional core ----------------------------------------------------------


def season_summary(
    settings: WorkspaceSettings, today: date, country: str | None = None
) -> SeasonSummary:
    """Where the workspace's season stands. With no season of its own, the workspace
    works in the one usual in ``country``."""
    window = settings.season or default_season(country)
    state = resolve_season(window, today)
    return SeasonSummary(
        name=window.name,
        year=state.year,
        phase=state.phase,
        first_issue_date=state.first_issue_date,
        starts_label=f"onset search starts {spoken_date(window.onset_search_start)}",
        last_issue_date=state.last_issue_date,
        issued_through=state.issued_through,
        days_until_start=state.days_until_start,
        next_season_start=state.next_season_start,
        is_default=settings.season is None,
    )


def newest(jobs: list[dict], job_type: str, *, finished: bool = False) -> dict | None:
    """The most recently created job of one type, optionally only a finished one."""
    wanted = [
        job
        for job in jobs
        if job["job_type"] == job_type and (not finished or job["status"] == FINISHED)
    ]
    return max(wanted, key=lambda job: job["created_at"], default=None)


def most_common_region(jobs: list[dict]) -> str | None:
    regions = Counter(
        job["config"].get("region_id") for job in jobs if job["config"].get("region_id")
    )
    return regions.most_common(1)[0][0] if regions else None


def observations_name(jobs: list[dict]) -> str | None:
    for job in sorted(jobs, key=lambda job: job["created_at"], reverse=True):
        config = job["config"]
        name = config.get("obs_dataset_name") or (config.get("dataset_config") or {}).get(
            "source_name"
        )
        if name:
            return str(name)
    return None


def model_names(jobs: list[dict]) -> list[str]:
    """Every model the runs scored or blended, each once, display names winning."""
    by_key: dict[str, str] = {}
    for job in sorted(jobs, key=lambda job: job["created_at"]):
        config = job["config"]
        if job["job_type"] == "benchmark" and (
            name := workspace_display.model_display_name(config)
        ):
            by_key[name.lower()] = name
        for name in config.get("model_names") or []:
            by_key.setdefault(str(name).lower(), str(name))
    return sorted(by_key.values(), key=str.lower)


def setup_summary(jobs: list[dict], region_name: str | None) -> SetupSummary:
    return SetupSummary(
        region=region_name, observations=observations_name(jobs), models=model_names(jobs)
    )


def issue_date_span(csv_text: str) -> tuple[date | None, date | None, int]:
    """First and last issue date in a blended probabilities table, and how many."""
    try:
        dates = parse_forecast_table(csv_text).issue_dates
    except (KeyError, ValueError):  # no table yet, or not one of probabilities
        return None, None, 0
    return date.fromisoformat(dates[0]), date.fromisoformat(dates[-1]), len(dates)


def forecast_summary(
    job: dict, blend_name: str | None, probabilities_csv: str | None
) -> ForecastSummary:
    first, last, count = issue_date_span(probabilities_csv or "")
    return ForecastSummary(
        job_id=job["id"],
        status=job["status"],
        blended=bool(job["config"].get("blend_id")),
        blend_name=blend_name,
        first_issue_date=first,
        last_issue_date=last,
        issue_dates=count,
        created_at=job["created_at"],
    )


def holdout_years(config: dict) -> str | None:
    years = (config.get("blend_params") or {}).get("cv_holdout_years")
    return workspace_display.range_label(workspace_display.years_range(years))


def blend_skill(job: dict, pooled_csv: str) -> BlendSkill | None:
    """The week-level blend's pooled skill row; None when the run has no such row."""
    for row in csv.DictReader(io.StringIO(pooled_csv)):
        if row.get("model") != _WEEKLY_BLEND_MODEL:
            continue
        try:
            rps, brier = float(row["rps_skill"]), float(row["brier_skill"])
        except (KeyError, TypeError, ValueError):
            return None
        return BlendSkill(
            job_id=job["id"],
            name=workspace_display.job_name(job["config"]) or "Blend",
            rps_skill=rps,
            brier_skill=brier,
            holdout_years=holdout_years(job["config"]),
        )
    return None


def headline_window(metrics: list[JobMetrics]) -> str | None:
    windows = {w.window for m in metrics for w in m.windows}
    if _HEADLINE_WINDOW in windows:
        return _HEADLINE_WINDOW
    return min(windows, key=window_start) if windows else None


def benchmark_skill(metrics_by_job: dict[str, JobMetrics]) -> BenchmarkSkill | None:
    """The model with the lowest mean onset error over the shortest lead window."""
    window = headline_window(list(metrics_by_job.values()))
    if window is None:
        return None
    models: list[tuple[float, str, str]] = []
    climatology: float | None = None
    for job_id, job_metrics in metrics_by_job.items():
        for row in job_metrics.windows:
            if row.window != window or _MEAN_MAE not in row.metrics:
                continue
            if row.model.lower() == _CLIMATOLOGY:
                climatology = row.metrics[_MEAN_MAE].mean
            else:
                models.append((row.metrics[_MEAN_MAE].mean, row.model, job_id))
    if not models:
        return None
    mae, model, job_id = min(models)
    return BenchmarkSkill(
        job_id=job_id,
        model=model,
        mae_days=round(mae, 1),
        climatology_mae_days=None if climatology is None else round(climatology, 1),
        window=window,
    )


# --- imperative shell ---------------------------------------------------------


async def _linked_jobs(workspace_id: str) -> list[dict]:
    return await workspaces.job_rows(
        workspaces.in_workspace(workspace_id), extra=(tables.jobs.c.metrics_cache,)
    )


async def _job_config(job_id: str) -> dict:
    async with get_db() as conn:
        raw = await conn.execute(
            sa.select(tables.jobs.c.config_json).where(tables.jobs.c.id == job_id)
        )
    return tables.json_col(raw.scalar_one_or_none(), {})


async def _read_output(job_id: str, filename: str) -> str | None:
    from .storage import get_storage

    try:
        return await asyncio.to_thread(get_storage().read_result_text, job_id, "output", filename)
    except Exception:  # a headline is never worth failing the page over
        logger.warning("Could not read %s of job %s for the summary", filename, job_id)
        return None


async def _forecast_of(job: dict) -> ForecastSummary:
    blend_id = job["config"].get("blend_id")
    blend_name = workspace_display.job_name(await _job_config(blend_id)) if blend_id else None
    probabilities = None
    if blend_id and job["status"] == FINISHED:
        probabilities = await _read_output(job["id"], WEEKLY_PROBABILITIES_CSV)
    return forecast_summary(job, blend_name, probabilities)


async def _latest_forecast(jobs: list[dict]) -> ForecastSummary | None:
    job = newest(jobs, "forecast")
    return None if job is None else await _forecast_of(job)


async def _blend_skill_of(blend: dict) -> BlendSkill | None:
    try:
        pooled = await blend_summaries.read_summary(
            blend["id"], await list_job_artifacts(blend["id"]), "pooled"
        )
    except Exception:
        logger.warning("Could not read the pooled summary of blend %s", blend["id"])
        return None
    return blend_skill(blend, pooled)


async def _job_metrics(job: dict) -> JobMetrics | None:
    try:
        return await metrics.job_metrics(job["id"], job.get("metrics_cache"))
    except Exception:
        logger.warning("Could not compute metrics of job %s for the summary", job["id"])
        return None


def _run_key(job: dict) -> str:
    return job["run_id"] or job["id"]


def finished_benchmark_runs(jobs: list[dict]) -> dict[str, list[dict]]:
    """Finished benchmark jobs grouped by the run they were submitted in."""
    runs: dict[str, list[dict]] = {}
    for job in jobs:
        if job["job_type"] == "benchmark" and job["status"] == FINISHED:
            runs.setdefault(_run_key(job), []).append(job)
    return runs


async def _skill(jobs: list[dict]) -> BlendSkill | BenchmarkSkill | None:
    blend = newest(jobs, "blend", finished=True)
    if blend is not None and (skill := await _blend_skill_of(blend)):
        return skill
    benchmark = newest(jobs, "benchmark", finished=True)
    if benchmark is None:
        return None
    run = finished_benchmark_runs(jobs)[_run_key(benchmark)]
    by_job = {job["id"]: await _job_metrics(job) for job in run}
    return benchmark_skill({job_id: m for job_id, m in by_job.items() if m is not None})


async def job_headlines(workspace_id: str) -> dict[str, JobHeadline]:
    """One headline per result row, keyed by job id: every finished blend's skill,
    every forecast's issue dates, and each finished benchmark run's best model, on
    that model's job. Benchmarks use only metrics already computed, so listing a
    workspace never opens a NetCDF."""
    jobs = await _linked_jobs(workspace_id)
    headlines: dict[str, JobHeadline] = {}
    for job in jobs:
        if job["job_type"] == "blend" and job["status"] == FINISHED:
            if skill := await _blend_skill_of(job):
                headlines[job["id"]] = skill
        elif job["job_type"] == "forecast":
            headlines[job["id"]] = await _forecast_of(job)
    for run in finished_benchmark_runs(jobs).values():
        cached = {job["id"]: metrics.cached_job_metrics(job.get("metrics_cache")) for job in run}
        if best := benchmark_skill({job_id: m for job_id, m in cached.items() if m}):
            headlines[best.job_id] = best
    return headlines


async def summarize(workspace_id: str, today: date | None = None) -> WorkspaceSummary:
    jobs = await _linked_jobs(workspace_id)
    region_id = most_common_region(jobs)
    region = await get_region(region_id) if region_id else None
    region_name = (region or {}).get("display_name") or (
        workspace_display.region_name(next(iter(jobs))["config"]) if jobs else None
    )
    settings = await get_settings(workspace_id)
    # The workspace's own area says where it works; failing that, its runs' region does.
    country = country_of(settings.study_area, region)
    return WorkspaceSummary(
        season=season_summary(settings, today or date.today(), country),
        setup=setup_summary(jobs, region_name),
        latest_forecast=await _latest_forecast(jobs),
        skill=await _skill(jobs),
    )
