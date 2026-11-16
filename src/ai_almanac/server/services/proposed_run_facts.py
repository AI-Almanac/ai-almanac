"""What an approval card says about a run that has not been submitted yet: where it
will execute and how long runs like it have taken this user.

A proposed run is described as the jobs it would queue, so the estimate comes from the
same estimator a queued job's does (``run_summaries.expected_seconds``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ai_almanac.settings import settings

from . import approval_display, job_history, run_summaries
from .approval_display import Fact, YearSpan
from .focus_area import FocusArea

_QUEUED = "queued"


def _proposed_job(job_type: str, config: Mapping[str, Any]) -> dict:
    return {"job_type": job_type, "status": _QUEUED, "config": dict(config)}


def benchmark_jobs(
    region_id: str | None,
    spans: Iterable[YearSpan | None],
    focus_area: FocusArea | None = None,
) -> list[dict]:
    """The jobs a benchmark would queue: one per model, each over that model's
    evaluation years and limited to the benchmark's area of interest, if it has one."""
    return [
        _proposed_job("benchmark", _benchmark_config(region_id, span, focus_area)) for span in spans
    ]


def _benchmark_config(
    region_id: str | None, span: YearSpan | None, focus_area: FocusArea | None
) -> dict:
    dates = {"start_date": f"{span[0]}-01-01", "end_date": f"{span[1]}-12-31"} if span else {}
    area = {"focus_area": focus_area.model_dump(mode="json")} if focus_area else {}
    return {"region_id": region_id, "romp_params": {**dates, **area}}


def rerun_jobs(config: Mapping[str, Any] | None) -> list[dict]:
    """The job a rerun would queue: the original over again. Empty when the original
    cannot be read, so nothing is estimated from a job the user may not see."""
    return [_proposed_job("benchmark", config)] if config else []


async def execution_facts(user_id: str, proposed: Sequence[Mapping[str, Any]]) -> list[Fact]:
    """Where the proposed jobs will run and how long they are expected to take: from
    past runs like them, else for a run over a new area no longer than the same run
    over its whole region took."""
    history = await job_history.finished_jobs_of((user_id, job["job_type"]) for job in proposed)
    past = [history.get((user_id, job["job_type"]), []) for job in proposed]
    expected = [
        run_summaries.expected_seconds(job, runs) for job, runs in zip(proposed, past, strict=True)
    ]
    ceilings = [
        seconds if seconds is not None else run_summaries.longest_likely_seconds(job, runs)
        for job, runs, seconds in zip(proposed, past, expected, strict=True)
    ]
    return approval_display.execution_facts(settings.job_runner, expected, at_most=ceilings)
