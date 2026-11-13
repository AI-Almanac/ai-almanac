"""What an approval card tells the user: a step's facts in their words, not its ids.

Each approval-gated primitive registers a describer (``@primitive(describe=...)``)
that turns its arguments into ordered :class:`Fact` s. A describer fetches the
names it needs with the lookups here, then hands them to pure formatters. The
facts travel beside the approved ``params`` and never change what is hashed.
Primitives without a describer get :func:`describe_generic`, which still names
every id it can resolve.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Protocol

from . import blend_domain, job_access, onset_rules, region_catalog, workspace_display
from .focus_area import area_label, parse_focus_area
from .seasons import SeasonWindow, spoken_date
from .workspace_display import day_label

if TYPE_CHECKING:
    from .primitives.runs import BenchmarkSpec, BlendSpec

_ID_KEY = re.compile(r"_ids?$")
_LABELS = {
    "dataset_id": "Observations",
    "obs_dataset_id": "Observations",
    "model_ids": "Models",
    "region_id": "Region",
    "job_id": "Job",
    "blend_id": "Blend",
    "focus_area": "Area of interest",
    "forecast_window_days": "Forecast days",
    "onset_rule": "Onset definition",
    "onset_rule_params": "Onset",
    "start_year_clim": "Climatology from",
    "end_year_clim": "Climatology to",
    "init_days": "Issue days",
    "members": "Ensemble members",
}
_CLOUD_RUNNERS = frozenset({"modal"})
# Past this many minutes a duration reads better in hours.
_HOURS_FROM_MINUTES = 90

YearSpan = tuple[int, int]


class EvaluationDates(Protocol):
    """The dates a model's scoring was narrowed to; unset ends keep the model's coverage."""

    start_date: date | None
    end_date: date | None


@dataclass(frozen=True)
class Fact:
    """One line of an approval card, e.g. ``Fact("Models", "AIFS, FuXi")``."""

    label: str
    value: str


def facts(*pairs: tuple[str, object]) -> list[Fact]:
    """Facts from ``(label, value)`` pairs, leaving out the ones with nothing to say."""
    return [Fact(label, str(value)) for label, value in pairs if value not in (None, "", [])]


def display_block(shown: Iterable[Fact]) -> dict:
    """The ``display`` an approval carries beside its ``params``."""
    return {"facts": [{"label": fact.label, "value": fact.value} for fact in shown]}


# --- lookups ------------------------------------------------------------------


Sources = Mapping[str, dict]


async def ready_sources(user_id: str) -> Sources:
    """Observation and model sources the user can pick, by id, as blend candidates."""
    candidates = [
        *await blend_domain._ready_obs_candidates(user_id),
        *await blend_domain._ready_model_candidates(user_id=user_id),
    ]
    return {candidate["id"]: candidate for candidate in candidates}


async def region_label(region_id: str | None) -> str | None:
    if not region_id:
        return None
    region = await region_catalog.get_region(region_id)
    if region and region.get("display_name"):
        return region["display_name"]
    return workspace_display.region_name({"region_id": region_id})


@dataclass(frozen=True)
class _Reader:
    id: str
    is_admin: bool = False


async def readable_job_config(job_id: str | None, user_id: str) -> dict | None:
    """A job's config when the user may read the job, so a card never names someone else's."""
    row = await job_access.fetch_job(job_id) if job_id else None
    if row is None or not job_access.can_read(row, _Reader(user_id)):
        return None
    return json.loads(row.get("config_json") or "{}")


async def resolve_names(refs: Iterable[str], user_id: str) -> dict[str, str]:
    """A readable name for each id that is a source, job or region the user can see."""
    sources = await ready_sources(user_id)
    names = {}
    for ref in set(refs):
        if ref in sources:
            names[ref] = source_label(sources[ref], ref)
        elif (config := await readable_job_config(ref, user_id)) is not None:
            names[ref] = job_label(config) or ref
        elif region := await region_catalog.get_region(ref):
            names[ref] = region.get("display_name") or ref
    return names


# --- formatters ---------------------------------------------------------------


def source_label(source: dict | None, ref: str) -> str:
    """A source's name with the years it covers, e.g. 'CHIRPS (1981–2024)'."""
    if source is None:
        return ref
    start, end = source.get("start_year"), source.get("end_year")
    return f"{source['name']} ({start}–{end})" if start and end else source["name"]


def source_names(refs: Iterable[str], sources: Sources) -> str:
    return ", ".join(sources[ref]["name"] if ref in sources else ref for ref in refs)


def job_label(config: dict | None) -> str | None:
    """A job as the workspace lists it, e.g. 'Kiremt blend · Ethiopia · 2000–2019'."""
    if not config:
        return None
    parts = [workspace_display.job_name(config), workspace_display.job_summary(config)]
    return " · ".join(part for part in parts if part) or None


def years_label(years: str) -> str:
    """'2000:2015,2018' as '2000–2015, 2018'."""
    tokens = [token.strip() for token in years.split(",") if token.strip()]
    return ", ".join(token.replace(":", "–") for token in tokens)


def forecast_days_label(days: int) -> str:
    return f"Days 1–{days}"


def onset_definition_label(rule: str | None) -> str:
    """An onset definition by the name the rest of the app gives it; unset is the default."""
    return onset_rules.display_name(rule)


def evaluation_span(source: dict | None, dates: EvaluationDates | None = None) -> YearSpan | None:
    """The first and last year a model is scored over: the years its data covers, narrowed
    by any dates set for it. None when either end is unknown."""
    covered = source or {}
    start = dates.start_date.year if dates and dates.start_date else covered.get("start_year")
    end = dates.end_date.year if dates and dates.end_date else covered.get("end_year")
    return (int(start), int(end)) if start and end else None


def evaluation_years_label(spans: Mapping[str, YearSpan | None]) -> str | None:
    """Evaluation years by model name: '2000–2023' when every model is scored over the
    same years, otherwise 'AIFS 2000–2023; FuXi 2004–2023'. Unknown spans are left out."""
    known = {name: f"{span[0]}–{span[1]}" for name, span in spans.items() if span}
    if len(known) == len(spans) and len(set(known.values())) == 1:
        return next(iter(known.values()))
    return "; ".join(f"{name} {years}" for name, years in known.items()) or None


def runs_on_label(runner: str) -> str:
    """Where the configured job runner executes a run, in the user's words."""
    return "The cloud" if runner in _CLOUD_RUNNERS else "This computer"


def duration_label(seconds: float) -> str:
    """A run time as a person would say it: 'About 3 minutes', 'About 2 hours'."""
    minutes = round(seconds / 60)
    if minutes < 1:
        return "Under a minute"
    if minutes == 1:
        return "About a minute"
    if minutes < _HOURS_FROM_MINUTES:
        return f"About {minutes} minutes"
    return f"About {round(minutes / 60)} hours"


def _longest(estimates: Sequence[int | None]) -> int | None:
    """The longest of the jobs' times; None unless every job has one, since a time for
    only part of a run would read as the time for all of it."""
    if not estimates or any(estimate is None for estimate in estimates):
        return None
    return max(estimates)


def expected_time_label(
    estimates: Sequence[int | None], at_most: Sequence[int | None] = ()
) -> str | None:
    """How long a proposed run is expected to take, from one estimate per job it starts.

    A run's jobs execute at the same time, so the run takes as long as its longest job:
    "About 8 minutes", not a time per model. Where a job has no past run like it,
    ``at_most`` gives each job's ceiling instead and the label says "Up to about 8
    minutes". None when some job has neither.
    """
    if (longest := _longest(estimates)) is not None:
        return duration_label(longest)
    if (ceiling := _longest(at_most)) is None:
        return None
    label = duration_label(ceiling)
    return label if label.startswith("Under") else f"Up to {_lower_first(label)}"


def execution_facts(
    runner: str, estimates: Sequence[int | None], *, at_most: Sequence[int | None] = ()
) -> list[Fact]:
    """Where a run executes and, when past runs allow an estimate, how long it takes."""
    return facts(
        ("Runs on", runs_on_label(runner)),
        ("Expected time", expected_time_label(estimates, at_most)),
    )


def date_range_label(start: date, end: date) -> str:
    """'1 Jun 2024 – 30 Sep 2024 (122 days)'."""
    days = (end - start).days + 1
    return f"{day_label(start)} – {day_label(end)} ({days} day{'s' if days != 1 else ''})"


def _lower_first(text: str) -> str:
    return f"{text[:1].lower()}{text[1:]}"


# --- step describers ----------------------------------------------------------


def benchmark_facts(
    spec: BenchmarkSpec,
    sources: Sources,
    region: str | None,
    spans: Mapping[str, YearSpan | None],
) -> list[Fact]:
    """A complete benchmark spec, by name; ``spans`` are each model's evaluation years."""
    advanced = spec.advanced_params
    area, rule = advanced.focus_area, advanced.onset_rule
    years = {source_names([ref], sources): span for ref, span in spans.items()}
    return facts(
        ("Observations", source_label(sources.get(spec.dataset_id), spec.dataset_id)),
        ("Models", source_names(spec.model_ids, sources)),
        ("Region", region),
        ("Area of interest", area and area_label(area)),
        ("Evaluation years", evaluation_years_label(years)),
        ("Forecast days", forecast_days_label(spec.forecast_window_days)),
        ("Onset definition", onset_definition_label(rule)),
        ("Goal", spec.intent),
    )


def blend_facts(spec: BlendSpec, sources: Sources) -> list[Fact]:
    return facts(
        ("Name", spec.name),
        ("Observations", source_label(sources.get(spec.obs_dataset_id), spec.obs_dataset_id)),
        ("Models", source_names(spec.model_ids, sources)),
        ("Area of interest", spec.focus_area and area_label(spec.focus_area)),
        ("Onset search starts", spec.cutoff_month_day and spoken_date(spec.cutoff_month_day)),
        ("Reference onset", spec.ref_onset_month_day and spoken_date(spec.ref_onset_month_day)),
        ("Training years", years_label(spec.training_years)),
        ("Cross-validation years", years_label(spec.cv_holdout_years)),
        ("Held-out years", years_label(spec.true_holdout_years)),
        ("Forecast years", years_label(spec.forecast_years)),
        ("Goal", spec.intent),
    )


def forecast_facts(
    arguments: Mapping[str, object],
    blend: dict | None,
    sources: Sources,
    *,
    check: dict | None = None,
) -> list[Fact]:
    """A forecast request, by name. ``blend`` is the config of the blend it names and
    ``check`` what ``check_forecast`` says about it, when either can be read."""
    blend_id = arguments.get("blend_id")
    season = (check or {}).get("season", {}).get("summary")
    return facts(
        ("Blend", blend_id and (job_label(blend) or blend_id)),
        ("Models", _forecast_models(arguments, blend, sources)),
        ("Season", season or _season_label(arguments.get("season"), arguments.get("season_year"))),
        ("Cannot run", (check or {}).get("reason")),
        ("GPU time", _gpu_time(check)),
    )


def _gpu_time(check: dict | None) -> str | None:
    if not check or not check.get("can_run"):
        return None
    if not check.get("needs_gpu"):
        return "None: every model's season is already stored"
    estimates = [model["gpu_estimate"] for model in check["models"]]
    total = sum(estimate["gpu_minutes"] for estimate in estimates)
    wait = max(estimate["wall_minutes"] for estimate in estimates)
    return (
        f"{duration_label(total * 60)} of GPU time; "
        f"results in {_lower_first(duration_label(wait * 60))} at best"
    )


def _forecast_models(
    arguments: Mapping[str, object], blend: dict | None, sources: Sources
) -> str | None:
    if models := arguments.get("models"):
        return source_names(models, sources)
    if names := arguments.get("model_names"):
        return ", ".join(names)
    if not arguments.get("blend_id"):
        return None
    in_blend = ", ".join((blend or {}).get("model_names") or [])
    return f"Every model in the blend ({in_blend})" if in_blend else "Every model in the blend"


def _season_label(season: object, year: object) -> str:
    name = season.name if isinstance(season, SeasonWindow) else ""
    return " ".join(part for part in (name, str(year or "")) if part) or "This season"


def _prettify(key: str) -> str:
    words = key.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


# --- generic fallback ---------------------------------------------------------


async def describe_generic(user_id: str, params: dict) -> list[Fact]:
    """Facts for a primitive with no describer: every param, ids replaced by names."""
    return generic_facts(params, await resolve_names(id_refs(params), user_id))


def id_refs(params: Mapping) -> set[str]:
    """Every value held under an ``*_id`` or ``*_ids`` key, nested specs included."""
    refs: set[str] = set()
    for key, value in params.items():
        if isinstance(value, Mapping):
            refs |= id_refs(value)
        elif _ID_KEY.search(key) and isinstance(value, str):
            refs.add(value)
        elif _ID_KEY.search(key) and isinstance(value, list):
            refs |= {item for item in value if isinstance(item, str)}
    return refs


def generic_facts(params: Mapping, names: Mapping[str, str], path: str = "") -> list[Fact]:
    """One fact per value, labels from the keys, ids shown by name where known.

    A top-level argument such as ``spec`` only wraps the payload, so its name is
    not part of the labels inside it.
    """
    shown = workspace_display.display_params(dict(params))
    return [
        fact
        for key, value in shown.items()
        for fact in _key_facts(key, value, names, path, top=path == "" and key not in _LABELS)
    ]


def _key_facts(key: str, value: object, names: Mapping[str, str], path: str, *, top: bool):
    label = _join_label(path, _LABELS.get(key) or _prettify(_ID_KEY.sub("", key)))
    if key == "focus_area" and value:
        return facts((label, area_label(parse_focus_area(value))))
    if isinstance(value, Mapping):
        return generic_facts(value, names, "" if top else label)
    return facts((label, _value_text(key, value, names)))


def _join_label(path: str, label: str) -> str:
    return f"{path} {label[:1].lower()}{label[1:]}" if path else label


def _value_text(key: str, value: object, names: Mapping[str, str]) -> str | None:
    if value is None or value == "":
        return None
    if key == "onset_rule":
        return onset_definition_label(str(value))
    if key.endswith("_years") and isinstance(value, str):
        return years_label(value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(names.get(str(item), str(item)) for item in value) or None
    return names.get(str(value), str(value))
