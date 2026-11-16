"""Human-facing names and prose for workspace artifacts, steps and jobs.

Pure functions over the plain dicts ``services/workspaces.py`` reads. Titles
come from the output filename families the benchmark and blend runners write;
anything unrecognised falls back to a prettified filename so a new output type
still reads sensibly without a change here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date
from pathlib import PurePosixPath

from .skill_files import SkillFileName

_ONSET_CLIMATOLOGY_RE = re.compile(r"^climatology_onset_doy_(?P<start>\d{4})-(?P<end>\d{4})$")
_SKILL_SCORES_RE = re.compile(
    r"^(?P<which>binned|overall)_skill_scores_(?P<model>.+)_(?P<window>\d+-\d+)$"
)
_FIGURE_RE = re.compile(r"^(?P<figure>portrait|panel_heatmap_skill)_(?P<model>.+)$")
_TRAILING_YEARS_RE = re.compile(r"[_-](?P<start>\d{4})[_-](?P<end>\d{4})$")
_FIGURE_NAMES = {"portrait": "Portrait plot", "panel_heatmap_skill": "Skill heatmap"}
_CLIMATOLOGY = "climatology"
_LOCATION_KEY_SUFFIXES = ("_dir", "_path", "_key")
_LOCATION_PREFIXES = ("/", "gs://")

GROUP_SPATIAL = "Spatial metrics"
GROUP_CLIMATOLOGY = "Climatology"
GROUP_FIGURES = "Figures"
GROUP_TABLES = "Tables"
GROUP_CODE = "Code"
GROUP_OTHER = "Other"


# --- artifacts ----------------------------------------------------------------


def artifact_stem(artifact: dict) -> str:
    """The original filename stem, which keeps the case the runner wrote."""
    return PurePosixPath(artifact["filename"]).stem


def artifact_title(artifact: dict, step: dict | None) -> str:
    stem = artifact_stem(artifact)
    params = (step or {}).get("params", {})
    if skill_file := SkillFileName.parse(stem):
        return _join(
            display_model(skill_file.model, params), "Spatial metrics", _window(skill_file.window)
        )
    if match := _ONSET_CLIMATOLOGY_RE.match(stem):
        return _join("Climatology", "onset day of year", _span(match["start"], match["end"]))
    if match := _SKILL_SCORES_RE.match(stem):
        return _join(
            display_model(match["model"], params),
            f"{match['which'].capitalize()} skill scores",
            _window(match["window"]),
        )
    if match := _FIGURE_RE.match(stem):
        return _join(display_model(match["model"], params), _FIGURE_NAMES[match["figure"]])
    return prettify_stem(stem)


def artifact_group(artifact: dict) -> str:
    stem = artifact_stem(artifact)
    if skill_file := SkillFileName.parse(stem):
        return GROUP_CLIMATOLOGY if skill_file.model.lower() == _CLIMATOLOGY else GROUP_SPATIAL
    if stem.startswith(f"{_CLIMATOLOGY}_"):
        return GROUP_CLIMATOLOGY
    if artifact["kind"] == "figure":
        return GROUP_FIGURES
    if artifact["kind"] == "table":
        return GROUP_TABLES
    if artifact["format"] == "python":
        return GROUP_CODE
    return GROUP_OTHER


def prettify_stem(stem: str) -> str:
    years = _TRAILING_YEARS_RE.search(stem)
    base = stem[: years.start()] if years else stem
    words = re.sub(r"[_\-]+", " ", base).strip() or stem
    text = words[:1].upper() + words[1:]
    return _join(text, _span(years["start"], years["end"])) if years else text


def display_model(token: str, config: Mapping) -> str:
    """A model named in a file the way the product names it, where the run's settings
    give a display name for it."""
    if token.lower() == _CLIMATOLOGY:
        return "Climatology"
    if token.lower() == str(config.get("model_name", "")).lower():
        return config.get("model_display_name") or token
    return token


def _window(window: str) -> str:
    if window == "all":
        return "all lead days"
    start, end = window.split("-")
    return f"days {start}–{end}"


def _span(start: object, end: object) -> str:
    return str(start) if start == end else f"{start}–{end}"


def _join(*parts: str | None) -> str:
    return " · ".join(part for part in parts if part)


# --- jobs ---------------------------------------------------------------------


def region_name(config: dict) -> str | None:
    name = config.get("region_name") or config.get("romp_region")
    if name:
        return str(name)
    region_id = config.get("region_id")
    return str(region_id).replace("_", " ").title() if region_id else None


YearRange = tuple[int, int]


def year_range(config: Mapping) -> YearRange | None:
    """The first and last year a run covers, as far as its settings say."""
    romp = config.get("romp_params") or {}
    if romp.get("start_date") and romp.get("end_date"):
        return int(str(romp["start_date"])[:4]), int(str(romp["end_date"])[:4])
    model = config.get("model_config") or {}
    if model.get("start_year") and model.get("end_year"):
        return int(model["start_year"]), int(model["end_year"])
    if years := config.get("forecast_years") or []:
        return int(min(years)), int(max(years))
    return years_range((config.get("blend_params") or {}).get("training_years"))


def years_range(years: object) -> YearRange | None:
    """A ``"2000:2015"`` year span as its first and last year; None for any other form."""
    if not isinstance(years, str) or ":" not in years:
        return None
    start, end = years.split(":", 1)
    return (int(start), int(end)) if start.strip().isdigit() and end.strip().isdigit() else None


def range_label(years: YearRange | None) -> str | None:
    """``(1998, 2024)`` as ``1998–2024``; one year as itself."""
    return _span(*years) if years else None


def year_span(config: Mapping) -> str | None:
    return range_label(year_range(config))


def lead_day_range(config: Mapping) -> tuple[int, int] | None:
    """The first and last lead day a benchmark scores."""
    max_day = (config.get("romp_params") or {}).get("max_forecast_day")
    return (1, int(max_day)) if max_day else None


def lead_days(config: dict) -> str | None:
    days = lead_day_range(config)
    return f"days {_span(*days)}" if days else None


def model_display_name(config: Mapping) -> str | None:
    """The model a benchmark scored, by the name the product gives it."""
    model_config = config.get("model_config") or {}
    return (
        config.get("model_display_name")
        or model_config.get("display_name")
        or config.get("model_name")
    )


def job_name(config: Mapping) -> str | None:
    """What the user calls a run: the name a blend was given, else the model it ran."""
    return config.get("blend_name") or config.get("name") or model_display_name(config)


def job_summary(config: dict) -> str:
    """One line a user can tell runs apart by, e.g. 'Ethiopia · 1998–2024 · days 1–30'."""
    return _join(region_name(config), year_span(config), lead_days(config))


def run_scope(config: dict) -> dict:
    """What tells a run from its neighbours, as parts a client words for itself: the
    place, the years and the lead days it covers."""
    return {
        "place": region_name(config),
        "years": _bounds(year_range(config)),
        "lead_days": _bounds(lead_day_range(config)),
    }


def _bounds(span: tuple[int, int] | None) -> dict | None:
    return {"start": span[0], "end": span[1]} if span else None


# --- provenance steps ---------------------------------------------------------


def describe_step(step: dict) -> str:
    """What a step did, in the user's terms; who decided it is shown beside it."""
    params = step.get("params") or {}
    describer = _STEP_DESCRIBERS.get(step["primitive"])
    if describer:
        return describer(params)
    return f"Ran the {step['primitive'].rsplit('.', 1)[-1].replace('_', ' ')} step."


def _describe_open_source(params: dict) -> str:
    area = params.get("area_name")
    subject = f"Opened {params.get('source_name') or 'a dataset'}"
    if area and area != "whole source":
        subject += f" for {area}"
    return _sentence(subject, _date_span(params.get("start"), params.get("end")))


def _describe_fetch(params: dict) -> str:
    request = params.get("request") or {}
    variables = _and_list([str(v) for v in request.get("variables") or []] or ["data"])
    subject = f"Fetched {variables} from {request.get('source', 'an external source')}"
    return _sentence(subject, _date_span(request.get("start"), request.get("end")))


def _describe_map(params: dict) -> str:
    if params.get("title"):
        return f"Drew the map “{params['title']}”."
    return f"Drew a map of {params.get('variable') or params.get('column') or 'the data'}."


def _describe_onset(params: dict) -> str:
    threshold = params.get("wet_threshold")
    wet_day = f"counting days of {threshold:g} mm or more as wet" if threshold else None
    return _sentence("Found the onset date in each forecast", wet_day)


def _describe_summary(params: dict) -> str:
    areas = params.get("areas") or []
    return _sentence("Summarized the forecast by area", _and_list(areas) if areas else None)


def _describe_benchmark(params: dict) -> str:
    model = model_display_name(params)
    dataset = (params.get("dataset_config") or {}).get("source_name")
    subject = f"Benchmark run of {model}" if model else "Benchmark run"
    if dataset:
        subject += f" against {dataset}"
    return _sentence(subject, year_span(params), lead_days(params))


def _describe_blend(params: dict) -> str:
    models = params.get("model_names") or []
    subject = f"Blend of {_and_list([str(m) for m in models])}" if models else "Blend"
    region = region_name(params)
    if region:
        subject += f" over {region}"
    return _sentence(subject, year_span(params))


def _sentence(*parts: str | None) -> str:
    return ", ".join(part for part in parts if part) + "."


def _date_span(start: object, end: object) -> str | None:
    if not start or not end:
        return None
    return f"{_iso_day(str(start))} to {_iso_day(str(end))}"


def day_label(day: date) -> str:
    """A date as a person writes it in a title: ``1 Jun 2026``."""
    return f"{day.day} {day:%b %Y}"


def _iso_day(iso: str) -> str:
    try:
        return day_label(date.fromisoformat(iso[:10]))
    except ValueError:
        return iso


def _and_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def _describe_forecast(params: dict) -> str:
    return _sentence("Forecast run", model_display_name(params), region_name(params))


def _describe_code(_params: dict) -> str:
    return "Ran code the assistant wrote. The platform does not check its results."


_STEP_DESCRIBERS = {
    "benchmark.job": _describe_benchmark,
    "blend.job": _describe_blend,
    "forecast.job": _describe_forecast,
    "acquire.open_source": _describe_open_source,
    "acquire.fetch": _describe_fetch,
    "figure.plot_map": _describe_map,
    "figure.plot_maps": _describe_map,
    "figure.plot_area_map": _describe_map,
    "forecast.detect_onset": _describe_onset,
    "forecast.summarize_by_area": _describe_summary,
    "code.run_code": _describe_code,
}


# --- params -------------------------------------------------------------------


def display_params(params: dict) -> dict:
    """Params without filesystem paths or storage keys; the full params stay for audit."""
    shown = {}
    for key, value in params.items():
        if _is_location_key(key) or _is_location_value(value):
            continue
        cleaned = _displayable(value)
        if value and not cleaned and isinstance(value, (dict, list)):
            continue
        shown[key] = cleaned
    return shown


def _displayable(value: object) -> object:
    if isinstance(value, dict):
        return display_params(value)
    if isinstance(value, list):
        return [_displayable(item) for item in value if not _is_location_value(item)]
    return value


def _is_location_key(key: str) -> bool:
    return key.endswith(_LOCATION_KEY_SUFFIXES)


def _is_location_value(value: object) -> bool:
    if isinstance(value, str):
        return value.startswith(_LOCATION_PREFIXES)
    if isinstance(value, list):
        return bool(value) and all(_is_location_value(item) for item in value)
    return False


# --- job output roles ----------------------------------------------------------

# What a blend run writes for its own use: fitted weights per fold, training tables,
# cross-validation predictions and the run's manifest. They are kept, but as files
# a result was made from rather than results.
_INTERNAL_SUFFIXES = (".pkl", ".rds", ".yml", ".yaml")
_INTERNAL_STEMS = ("manifest", "formula_sample_support", "training_spec")


def is_training_internal(filename: str) -> bool:
    path = PurePosixPath(filename)
    return path.suffix.lower() in _INTERNAL_SUFFIXES or path.stem.startswith(_INTERNAL_STEMS)
