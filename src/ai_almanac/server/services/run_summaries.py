"""What tells one run from another in a list: the area it covered, the onset definition
it used, and how long a run like it is expected to take.

Pure functions over a job's stored config and plain job rows.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Any

from pydantic import BaseModel, ValidationError

from . import onset_rules, workspace_display
from .focus_area import FocusArea, FocusUnits, area_label, parse_focus_area

_SETTINGS_KEYS = ("romp_params", "blend_params")
FINISHED = "complete"
WAITING_STATUSES = ("queued", "starting", "running")
# Past runs within this factor of a run's year count are of comparable size.
_SIMILAR_YEARS_FACTOR = 1.5
# Only the most recent comparable runs count, so a change of machine or model shows soon.
_RECENT_RUNS = 10


class OnsetParameter(BaseModel):
    label: str
    # A few words for a run's name, where the full label is too long.
    short_label: str
    value: bool | int | float | None
    unit: str | None


class OnsetDefinition(BaseModel):
    """An onset definition in words: its name and whichever settings are not its defaults."""

    name: str
    description: str | None
    is_default: bool
    changes: list[OnsetParameter]


def _run_settings(config: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The benchmark or blend settings a job ran with; None for a job with neither."""
    for key in _SETTINGS_KEYS:
        if isinstance(config.get(key), Mapping):
            return config[key]
    return None


def focus_area_of(config: Mapping[str, Any]) -> FocusArea | None:
    """The area a benchmark or blend was limited to, as its stored settings give it;
    None when it covered its whole region or the area cannot be read."""
    area = (_run_settings(config) or {}).get("focus_area")
    if not area:
        return None
    try:
        return parse_focus_area(area)
    except ValidationError:
        return None


def area_of_interest(config: Mapping[str, Any]) -> list[str] | None:
    """The names of the areas a run was limited to, or one lat/lon box in words; None
    when it covered its whole region."""
    area = focus_area_of(config)
    if area is None:
        return None
    return list(area.units) if isinstance(area, FocusUnits) else [area_label(area)]


def scored_area(config: Mapping[str, Any]) -> tuple | None:
    """What identifies the area a run scored, whatever order its areas were named in;
    None for a whole-region run."""
    area = focus_area_of(config)
    if area is None:
        return None
    if isinstance(area, FocusUnits):
        return (area.level, frozenset(area.units))
    return (area.lat_min, area.lat_max, area.lon_min, area.lon_max)


def onset_definition(config: Mapping[str, Any]) -> OnsetDefinition | None:
    """The onset definition a run scored against; None for a job that uses none."""
    settings = _run_settings(config)
    if settings is None:
        return None
    key = settings.get("onset_rule") or onset_rules.DEFAULT_ONSET_RULE
    rule = onset_rules.onset_rules().get(key)
    if rule is None:
        return OnsetDefinition(
            name=onset_rules.display_name(key), description=None, is_default=False, changes=[]
        )
    given = settings.get("onset_rule_params")
    changes = onset_rules.changed_params(
        key, given if isinstance(given, Mapping) else None, settings
    )
    return OnsetDefinition(
        name=rule["display_name"],
        description=rule["description"],
        is_default=key == onset_rules.DEFAULT_ONSET_RULE and not changes,
        changes=[OnsetParameter(**change) for change in changes],
    )


@dataclass(frozen=True)
class RunSize:
    """What a run's duration mostly depends on, as far as its config says."""

    job_type: str
    region_id: str | None
    models: int
    years: int | None
    area: tuple | None = None

    def comparable(self, other: RunSize) -> bool:
        same_shape = (self.job_type, self.region_id, self.models, self.area) == (
            other.job_type,
            other.region_id,
            other.models,
            other.area,
        )
        return same_shape and _similar_years(self.years, other.years)


def _similar_years(a: int | None, b: int | None) -> bool:
    if a is None or b is None:
        return a is b
    return max(a, b) <= _SIMILAR_YEARS_FACTOR * min(a, b)


def _year_count(config: Mapping[str, Any]) -> int | None:
    years = workspace_display.year_range(config)
    return years[1] - years[0] + 1 if years else None


def run_size(job_type: str, config: Mapping[str, Any]) -> RunSize:
    models = config.get("model_names") or config.get("forecast_models") or []
    return RunSize(
        job_type,
        config.get("region_id"),
        len(models) or 1,
        _year_count(config),
        scored_area(config),
    )


def _seconds_between(start: object, end: object) -> float | None:
    try:
        elapsed = datetime.fromisoformat(str(end)) - datetime.fromisoformat(str(start))
    except (TypeError, ValueError):  # unparsable, or one timestamp has a zone and one not
        return None
    return elapsed.total_seconds() if elapsed.total_seconds() > 0 else None


def run_seconds(job: Mapping[str, Any]) -> float | None:
    """How long a finished job ran, from when it started rather than when it was queued."""
    if job.get("status") != FINISHED:
        return None
    return _seconds_between(job.get("started_at") or job.get("created_at"), job.get("completed_at"))


def expected_seconds(job: Mapping[str, Any], history: Sequence[Mapping[str, Any]]) -> int | None:
    """How long a queued or running job is expected to run, start to finish, in seconds.

    The estimate is the median run time of the same user's most recent finished jobs
    (up to ten) of comparable size: the same kind of job, on the same region and the
    same area of interest, with the same number of models, over a year span within a
    factor of 1.5 of this one's. It is the whole run, not what remains, so a caller
    showing progress subtracts the time since the job started. None for a job that is
    not waiting or running, and when the user has no comparable finished job: no
    estimate is better than a guess from runs of a different shape.

    The area must match exactly, a whole-region run only ever standing for another
    whole-region run. A run scores only the cells inside its area, so its time follows
    the area's size: runs limited to a dozen zones finished in about two minutes where
    the same models and years over the whole region took about eight, and counting the
    two as alike made the estimate three times too long. A config says which areas a
    run covered but not how many cells they hold, so areas are not compared by size:
    a different set of areas is simply no guide.

    ``job`` and each of ``history`` carry ``job_type``, ``status``, ``config``,
    ``created_at``, ``started_at`` and ``completed_at``; ``history`` is newest first.
    """
    if job.get("status") not in WAITING_STATUSES:
        return None
    size = run_size(job["job_type"], job["config"])
    durations = [
        seconds
        for past in history
        if size.comparable(run_size(past["job_type"], past["config"]))
        and (seconds := run_seconds(past)) is not None
    ]
    return round(median(durations[:_RECENT_RUNS])) if durations else None


def longest_likely_seconds(
    job: Mapping[str, Any], history: Sequence[Mapping[str, Any]]
) -> int | None:
    """The longest a run limited to an area is likely to take when no past run covered
    that area: what the same run over its whole region has taken, in seconds.

    A run scores only the cells inside its area, so the whole region is the largest it
    could have been asked to score. It is a ceiling to quote as "up to", never an
    expectation: runs over a dozen zones have finished in a quarter of it. None for a
    whole-region run, which has no larger run to bound it, and when the user has no
    comparable whole-region run either. Runs over other areas are no guide, since they
    may be smaller than this one.
    """
    if scored_area(job["config"]) is None:
        return None
    settings_key = next((key for key in _SETTINGS_KEYS if key in job["config"]), None)
    if settings_key is None:
        return None
    settings = {k: v for k, v in job["config"][settings_key].items() if k != "focus_area"}
    return expected_seconds({**job, "config": {**job["config"], settings_key: settings}}, history)
