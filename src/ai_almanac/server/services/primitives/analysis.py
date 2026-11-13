"""Reading results by place and by onset: forecasts and benchmark skill summarized by
administrative area, and onset read from a raw season forecast."""

from __future__ import annotations

import asyncio
import hashlib
import json
from importlib.resources import files
from pathlib import Path

from pydantic_ai import RunContext

from ai_almanac.settings import (
    forecast_model_by_id,
    get_packaged_forecast_models,
    get_romp_defaults,
)

from .. import (
    area_summaries,
    boundaries,
    job_access,
    ledger,
    onset_rules,
    provenance,
    skill_area_report,
    skill_area_summaries,
    skill_by_area,
    workspaces,
)
from ..chat_tools import load_region_areas
from .code_harness import code_execution_available, run_in_harness
from .registry import (
    Refusal,
    artifact_text,
    output_dir,
    primitive,
    stage_into,
    work_dir,
    workspace_artifact,
)

# --- forecasts by area --------------------------------------------------------

_SINGLE_RUN_VALUES = (
    "These come from one model run, so each p_ column is the share of the area where that "
    "run puts onset in the window, not a probability. Report them as such, with the "
    "model's benchmark error for the region."
)

# Rows of the latest issue date returned inline; the table artifact holds all of them.
_INLINE_AREA_ROWS = 40


@primitive("forecast.summarize_by_area", "1.0.0", label_args=("forecast",))
async def summarize_by_area(
    ctx: RunContext,
    forecast: str,
    region_id: str,
    level: boundaries.AreaLevel = "adm2",
    areas: list[str] | None = None,
    within: str | None = None,
    within_days: list[int] | None = None,
    name: str | None = None,
) -> dict:
    """Summarize a forecast's onset probabilities by administrative area. For each area and
    issue date: the mean chance onset falls in each window, the chance of onset within each
    of `within_days` (default 7, 14 and 28 days), and the window most of the area expects.

    `forecast` is a probabilities table artifact (blended_forecast_probabilities, or
    daily_onset_probabilities for day-level windows). Name areas as `list_areas` does, or
    pass `within` to take every area inside one larger area. Grid cells count by how much of
    each area they cover. Writes the table and the outlines it used as workspace artifacts;
    map it with `plot_area_map`. adm3 areas (woredas) are often smaller than a forecast
    cell, so neighbouring woredas share their cell's values: say so when reporting them.
    The `later` window means onset after lead day 28 or not within the forecast. It never
    means onset has already happened: these forecasts do not read this season's
    observations, so do not say an area's onset is past. `name` is what to call the result, in a few words the user would use."""
    row = await workspace_artifact(ctx, forecast)
    if row["format"] != "csv":
        raise Refusal(f"{row['label']} is {row['format']}, not a probabilities table")
    try:
        found = await load_region_areas(region_id, level, within)
        picked = area_summaries.picked_areas(found, areas)
        table = area_summaries.parse_forecast_table(await artifact_text(row))
    except ValueError as exc:
        raise Refusal(str(exc)) from exc
    days = within_days or list(area_summaries.DEFAULT_WITHIN_DAYS)
    if errors := area_summaries.within_days_errors(table, days):
        raise Refusal(errors[0])
    weighted = await asyncio.to_thread(area_summaries.area_weights, table, picked)
    rows = area_summaries.summarize_areas(table, weighted, days)
    if not rows:
        raise Refusal(
            f"{row['label']} has no forecast points in these areas. Check that "
            f"{found.region['display_name']} is the forecast's region."
        )
    with output_dir("summarize_by_area") as out_dir:
        stem = workspaces.safe_label(f"{row['label']}_by_{level}")
        table_path = out_dir / f"{stem}.csv"
        table_path.write_text(area_summaries.table_csv(rows))
        outlines_path = out_dir / f"{found.region['id']}_{level}_areas.geojson"
        outlines_path.write_text(area_summaries.boundary_document(picked, found.source))
    return {
        **_latest_area_rows(rows),
        "not_covered": area_summaries.unsummarized(weighted),
        **({"values": _SINGLE_RUN_VALUES} if table.members == 1 else {}),
        "_outputs": [
            ledger.OutputFile(
                path=table_path, label=stem, kind="table", title=name or "Onset by area"
            ),
            ledger.OutputFile(
                path=outlines_path,
                label=outlines_path.stem,
                kind="document",
                title=f"{found.region['display_name']} area outlines",
                supporting=True,
            ),
        ],
        "_inputs": [("forecast", row)],
        # single_run tells readers the p_ columns are shares of each area, not chances.
        "_params": {
            "boundaries": found.source.get("boundaryID"),
            "single_run": table.members == 1,
        },
    }


def _latest_area_rows(rows: list[dict]) -> dict:
    latest = max(row["issue_date"] for row in rows)
    shown = sorted(
        (
            {
                key: value
                for key, value in row.items()
                if key in ("area", "parent", "coverage", "most_likely", "most_likely_share")
                or key.startswith("p_within_")
            }
            for row in rows
            if row["issue_date"] == latest
        ),
        key=lambda row: row["area"],
    )
    return {
        "issue_dates": len({row["issue_date"] for row in rows}),
        "latest_issue_date": latest,
        "areas": len(shown),
        "latest": shown[:_INLINE_AREA_ROWS],
        **({"truncated": True} if len(shown) > _INLINE_AREA_ROWS else {}),
    }


# --- benchmark skill by area --------------------------------------------------

_RUN_NOT_IN_WORKSPACE = (
    "That is not a benchmark run in this workspace. Find the run in the workspace's "
    "run list, or add it to the workspace first."
)
_NO_AREA_COVERED = (
    "None of these areas holds a scored grid cell of this run, so there is no skill "
    "to report for them."
)


async def _run_key(job_id: str) -> str:
    """What a job's run goes by in its workspace: the run its jobs share, else the job."""
    job = await job_access.fetch_job(job_id)
    return (job or {}).get("run_id") or job_id


@primitive("skill.summarize_skill_by_area", "1.0.0")
async def summarize_skill_by_area(
    ctx: RunContext,
    job_id: str,
    level: boundaries.AreaLevel | None = None,
    within: str | None = None,
    areas: list[str] | None = None,
    name: str | None = None,
) -> dict:
    """A finished benchmark run's skill area by area: for each area, model and lead
    window, the mean onset error in days, false alarm rate and miss rate over the grid
    cells centred in the area, the number of those cells, and the climatology baseline
    over the same cells. This is the only source of a benchmark's per-area numbers.

    `job_id` is any job of the run, from the workspace's run list, and is for tool
    calls only. With no `level`, `within` or `areas` the areas are the run's own area
    of interest, exactly as it was scored (states or regions for a run over its whole
    region). Otherwise name a `level` (adm1 states or regions, adm2 districts or zones),
    and narrow it with `within` (every area inside one larger area, e.g. the zones in
    Amhara) or `areas` (names as `list_areas` gives them).

    `rows` follow `columns`. Report `cells` with any value that rests on one or two of
    them, and list `not_covered` areas as having no value rather than leaving them out.
    The whole table is saved to the workspace as a file the user can open; `name` is
    what to call it, in words that say what it shows and where, e.g. "Onset skill by
    zone, Amhara, 1998-2024"."""
    try:
        table = await skill_by_area.summarize_skill_by_area(
            ctx.deps.scope.key, await _run_key(job_id), level=level, within=within, areas=areas
        )
    except skill_by_area.RunNotFound as exc:
        raise Refusal(_RUN_NOT_IN_WORKSPACE) from exc
    except skill_area_summaries.SkillByAreaError as exc:
        raise Refusal(str(exc)) from exc
    except skill_area_summaries.SkillMapsUnreadable as exc:
        raise Refusal(skill_area_summaries.SKILL_MAPS_UNREADABLE) from exc
    if not table.rows:
        raise Refusal(
            _NO_AREA_COVERED,
            not_covered=table.not_covered,
            not_shown=[missing.model_dump() for missing in table.not_shown],
        )
    with output_dir("summarize_skill_by_area") as out_dir:
        path = out_dir / f"skill_by_{table.level}.csv"
        path.write_text(skill_area_report.table_csv(table))
    table_file = ledger.OutputFile(
        path=path,
        label=workspaces.safe_label(path.stem),
        kind="table",
        title=name or "Onset skill by area",
    )
    return {**skill_area_report.quotable(table), "_outputs": [table_file]}


# --- onset from a raw season forecast -----------------------------------------

# Lead days onset is read over: the 28 the blend's windows and day-level layout cover.
ONSET_FORECAST_DAYS = 28
_SEASON_FILE_SUFFIX = "_season_forecast"
# A forecast run's season file names its issue dates `time`; an opened archive, `init_time`.
_ISSUE_DIMS = ("time", "init_time")
_ONSET_FILES = ("onset_days.nc", "onset_windows.csv")

_ENSEMBLE_MEAN_NOTE = (
    "This model is an ensemble, but its season forecast holds the ensemble mean. Averaging "
    "members spreads light rain and hides dry spells, so onset read from the mean tends to "
    "come early. Say so, and do not read it as the members' consensus."
)


def detect_onset_script() -> str:
    """The server-written script `detect_onset` runs in the code sandbox, beside ROMP."""
    return files("ai_almanac.server").joinpath("sandbox_scripts", "detect_onset.py").read_text()


@primitive(
    "forecast.detect_onset",
    "1.0.0",
    label_args=("forecast",),
    available=code_execution_available,
)
async def detect_onset(
    ctx: RunContext,
    forecast: str,
    onset_rule: str | None = None,
    onset_rule_params: dict | None = None,
    wet_threshold: float | None = None,
    name: str | None = None,
) -> dict:
    """Read onset from one model's raw season forecast (a <model>_season_forecast dataset)
    by an onset definition: for each issue date and grid cell, the lead day within 28 days
    on which the definition first finds onset, or none. It applies ROMP's onset rules, the
    same ones benchmarks score with. `onset_rule` takes a definition's `internal_value`
    from `list_onset_rules`, a value for tool calls that is never written to the user;
    leave it out for the default definition with a 20 mm onset threshold. Writes an onset-day dataset (map it with `plot_map`)
    and a table `summarize_by_area` reads. A single model run gives one date per cell, not
    a probability. `name` is what to call the result, in a few words the user would use."""
    row = await workspace_artifact(ctx, forecast)
    model = _forecast_model_name(row)
    _check_season_file(row)
    defaults = get_romp_defaults()
    onset_rule = onset_rule or onset_rules.DEFAULT_ONSET_RULE
    params = onset_rules.resolved_rule_params(onset_rule, onset_rule_params, defaults)
    if errors := _onset_definition_errors(row, model, onset_rule, onset_rule_params, params):
        raise Refusal(" ".join(errors))
    definition = {
        "onset_rule": onset_rule,
        "onset_rule_params": params,
        "wet_threshold": float(
            defaults["wet_threshold"] if wet_threshold is None else wet_threshold
        ),
        "max_forecast_day": ONSET_FORECAST_DAYS,
        "variable": "tp",
        "issue_dim": _issue_dim(row),
        "members": 1,
    }
    with work_dir("detect_onset_inputs") as downloads, output_dir("detect_onset") as out_dir:
        definition_path = downloads / "definition.json"
        definition_path.write_text(json.dumps(definition))
        inputs = {"forecast": await stage_into(row, downloads), "definition": definition_path}
        ran = await run_in_harness(detect_onset_script(), inputs, out_dir)
        if not ran.outcome.get("ok"):
            raise Refusal(f"Onset detection failed: {ran.outcome.get('error', 'no output')}")
        outputs = _onset_outputs(ran.outputs, model, name)
    entry = forecast_model_by_id(get_packaged_forecast_models(), model.lower())
    return {
        "model": model,
        "onset": ran.outcome.get("result"),
        "single_run": True,
        **({"ensemble_mean": _ENSEMBLE_MEAN_NOTE} if entry and entry.get("ensemble") else {}),
        "_outputs": outputs,
        "_inputs": [("forecast", row)],
        "_params": {
            key: definition[key]
            for key in ("onset_rule_params", "wet_threshold", "max_forecast_day")
        },
        "_code_hash": hashlib.sha256(detect_onset_script().encode()).hexdigest(),
        "_runtime": ran.runtime,
    }


def _issue_dim(row: dict) -> str | None:
    dims = row["summary"].get("dims", {})
    return next((dim for dim in _ISSUE_DIMS if dim in dims), None)


def _forecast_model_name(row: dict) -> str:
    """The model a forecast dataset came from, safe to name files by: the source an opened
    archive records, else the <model>_season_forecast filename a forecast run publishes."""
    source = (row["summary"].get("attrs") or {}).get("source")
    name = str(source) if source else Path(row["filename"]).stem.removesuffix(_SEASON_FILE_SUFFIX)
    return workspaces.safe_label(name)


def _check_season_file(row: dict) -> None:
    summary = row["summary"]
    if row["format"] != "netcdf" or "tp" not in summary.get("variables", {}):
        raise Refusal(f"{row['label']} is not a season forecast of daily rainfall (tp)")
    dims = set(summary.get("dims", {}))
    if _issue_dim(row) is None or not {"day", "lat", "lon"} <= dims:
        raise Refusal(f"{row['label']} needs issue date, day, lat and lon dimensions")
    # Forecast runs write mm/day with no units attribute; a model's native file says metres.
    if summary["variables"]["tp"].get("units") in ("m", "metre", "meter", "metres", "meters"):
        raise Refusal(
            f"{row['label']} holds rainfall in metres, a model's native units. "
            "Onset is read from a forecast run's season forecast, which is in mm per day."
        )


def _onset_definition_errors(
    row: dict, model: str, rule: str, given: dict | None, params: dict
) -> list[str]:
    if errors := onset_rules.onset_rule_errors(rule, given or {}):
        return errors
    return onset_rules.onset_fit_errors(
        {"onset_rule": rule, "onset_rule_params": params},
        forecast_days=ONSET_FORECAST_DAYS,
        forecast_steps=int(float(row["summary"]["coords"]["day"]["max"])),
        model_name=model,
    )


def _onset_outputs(produced: list[Path], model: str, title: str | None) -> list[ledger.OutputFile]:
    """The script's two files, renamed for the model they were read from."""
    by_name = {path.name: path for path in produced}
    if not set(_ONSET_FILES) <= set(by_name):
        raise Refusal("Onset detection wrote no onset days")
    described = (
        ("onset_days.nc", "dataset", title or f"{model} onset days", False),
        ("onset_windows.csv", "table", f"{model} onset by window", True),
    )
    outputs = []
    for filename, kind, file_title, supporting in described:
        target = by_name[filename].with_name(f"{model}_{filename}")
        by_name[filename].rename(target)
        provenance.strip_history(target)
        outputs.append(
            ledger.OutputFile(
                path=target, label=target.stem, kind=kind, title=file_title, supporting=supporting
            )
        )
    return outputs
