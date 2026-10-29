"""ROMP integration: the value types ROMP accepts and the config file it reads.

ROMP reads its config by exec()ing Python source, and the Modal runner hands
these values to ROMP through environment variables. Every value that reaches
ROMP is therefore parsed into one of the types below first, and serialized
back in one canonical form, so no free-form text can reach that source.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    Field,
    PlainSerializer,
    StringConstraints,
    TypeAdapter,
    WithJsonSchema,
    model_validator,
)

_NAME_CHARS = r"A-Za-z0-9_.\-"
_MODEL_NAME_PART = re.compile(f"[{_NAME_CHARS}]+")


def romp_safe_model_name(name: str) -> str:
    """ROMP model names allow only name characters; collapse anything else to underscores."""
    return "_".join(_MODEL_NAME_PART.findall(str(name))) or "model"


def _comma_separated(value: object) -> object:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, int) and not isinstance(value, bool):
        return (value,)
    return value


def _joined(values: tuple[int, ...]) -> str:
    return ",".join(str(value) for value in values)


def _all_members_or_indices(value: object) -> object:
    if value is None or (isinstance(value, str) and value.strip().lower() == "all"):
        return "All"
    return _comma_separated(value)


def _members_text(value: str | tuple[int, ...]) -> str:
    return value if isinstance(value, str) else _joined(value)


def _without_parent_segments(value: str) -> str:
    if ".." in value.split("/"):
        raise ValueError("must not contain '..' path segments")
    return value


Weekday = Annotated[int, Field(ge=0, le=6)]
EnsembleMember = Annotated[int, Field(ge=0, le=9999)]
Year = Annotated[int, Field(ge=1800, le=2300)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=360)]

InitDays = Annotated[
    tuple[Weekday, ...],
    BeforeValidator(_comma_separated),
    Field(min_length=1, max_length=7),
    PlainSerializer(_joined),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^\s*[0-6](\s*,\s*[0-6])*\s*$",
            "description": "Comma-separated weekday numbers, Monday = 0 (e.g. '0,3').",
        }
    ),
]
"""Forecast initialization weekdays; accepts '0,3' or [0, 3], serializes as '0,3'."""

Members = Annotated[
    Literal["All"] | Annotated[tuple[EnsembleMember, ...], Field(min_length=1)],
    BeforeValidator(_all_members_or_indices),
    PlainSerializer(_members_text),
    WithJsonSchema(
        {
            "type": "string",
            "pattern": r"^\s*(All|all|\d{1,4}(\s*,\s*\d{1,4})*)\s*$",
            "description": "'All' or comma-separated ensemble member indices (e.g. '0,1,2').",
        }
    ),
]
"""Ensemble members to score; accepts 'All', '0,1' or [0, 1], serializes as 'All' or '0,1'."""

RompName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=f"^[A-Za-z0-9_][{_NAME_CHARS}]*$", max_length=128
    ),
]
"""Variable, dataset, model, region, or event names: letters, digits, '_', '.', '-'."""

FilePattern = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, pattern=r"^[A-Za-z0-9_.\-{}*?]+(/[A-Za-z0-9_.\-{}*?]+)*$"
    ),
    Field(max_length=256),
    AfterValidator(_without_parent_segments),
]
"""Relative file-name pattern where '{}' stands for the year, e.g. 'chirps_{}.nc'."""

DataPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^(gs://)?[A-Za-z0-9_.\-/ ]+$"),
    Field(max_length=1024),
    AfterValidator(_without_parent_segments),
]
"""A gs:// URI or filesystem path to a NetCDF file or directory."""

ModelDims = dict[Literal["init_time", "step", "member", "lat", "lon"], RompName]
"""ROMP's forecast dim names mapped to the names used in the model files."""

LeadDay = Annotated[int, Field(ge=1, le=366)]

# ROMP reads this many lead days when a job does not set max_forecast_day.
DEFAULT_MAX_FORECAST_DAY = 30


class VerificationWindow(BaseModel):
    """A lead-time range ROMP scores separately, with the onset tolerance FAR uses in it."""

    start: LeadDay = Field(description="First lead day in the window (1 = day after init).")
    end: LeadDay = Field(description="Last lead day in the window, inclusive.")
    tolerance_days: Annotated[int, Field(ge=0, le=30)] = Field(
        description=(
            "A predicted onset further than this many days from the observed onset "
            "counts as a false alarm in this window."
        )
    )

    @model_validator(mode="after")
    def _end_not_before_start(self) -> VerificationWindow:
        if self.end < self.start:
            raise ValueError("end must not be before start")
        return self


def _sorted_distinct_windows(
    windows: tuple[VerificationWindow, ...],
) -> tuple[VerificationWindow, ...]:
    # ROMP looks a window's tolerance up by its (start, end), so two windows
    # with the same range would both get the first one's tolerance.
    if len({(window.start, window.end) for window in windows}) != len(windows):
        raise ValueError("verification windows must not repeat a lead-day range")
    return tuple(sorted(windows, key=lambda window: (window.start, window.end)))


VerificationWindows = Annotated[
    tuple[VerificationWindow, ...],
    Field(min_length=1, max_length=8),
    AfterValidator(_sorted_distinct_windows),
]
"""The lead-time windows ROMP scores; each run yields one set of results per window."""

DEFAULT_VERIFICATION_WINDOWS: tuple[VerificationWindow, ...] = (
    VerificationWindow(start=1, end=15, tolerance_days=3),
    VerificationWindow(start=16, end=30, tolerance_days=5),
)

# Width of the lead-day bins probabilistic skill scores are reported in.
DAY_BIN_WIDTH = 5

_INIT_DAYS = TypeAdapter(InitDays)
_MEMBERS = TypeAdapter(Members)
_VERIFICATION_WINDOWS = TypeAdapter(VerificationWindows)
_MODEL_DIMS = TypeAdapter(ModelDims | None)
_DATE = TypeAdapter(date)


def _date_tuple(value: object) -> tuple[int, int, int]:
    parsed = _DATE.validate_python(value)
    return parsed.year, parsed.month, parsed.day


def _members(value: object) -> str | tuple[int, ...]:
    return _MEMBERS.validate_python(value)


def parse_model_dims(value: object) -> dict[str, str] | None:
    """Parse a forecast dims mapping; raises pydantic.ValidationError for unsafe names."""
    return _MODEL_DIMS.validate_python(value or None)


def _init_days(value: object) -> tuple[int, ...]:
    return _INIT_DAYS.validate_python(value or "0,3")


def _year_list(value: object) -> tuple[int, ...] | None:
    """Explicit years for ROMP to evaluate; None makes it use the full date range."""
    if not isinstance(value, list | tuple) or not value:
        return None
    return tuple(int(year) for year in value)


def parse_verification_windows(value: object) -> tuple[VerificationWindow, ...]:
    """The windows a job asked for, or ROMP's 1-15 / 16-30 defaults when it set none."""
    if value is None:
        return DEFAULT_VERIFICATION_WINDOWS
    return _VERIFICATION_WINDOWS.validate_python(value)


def day_bins(windows: tuple[VerificationWindow, ...]) -> tuple[tuple[int, int], ...]:
    """Lead-day bins covering day 1 through the last window's end, without overlap.

    Probabilistic scoring treats the bins as the categories of one forecast
    distribution, and scores each window over the bins wholly inside it
    (momp/lib/control.py:filter_bins_in_window). Breaking at every window edge
    gives each window an exact cover; between edges bins are DAY_BIN_WIDTH
    days, the last one possibly shorter.
    """
    edges = sorted(
        {0} | {window.start - 1 for window in windows} | {window.end for window in windows}
    )
    return tuple(
        (start, min(start + DAY_BIN_WIDTH - 1, high))
        for low, high in zip(edges, edges[1:], strict=False)
        for start in range(low + 1, high + 1, DAY_BIN_WIDTH)
    )


def verification_settings(value: object) -> dict:
    """ROMP's verification_window_list, tolerance_days_list, and day_bins for a job's windows.

    ROMP pairs tolerance_days_list with verification_window_list by position.
    """
    windows = parse_verification_windows(value)
    return {
        "verification_window_list": tuple((window.start, window.end) for window in windows),
        "tolerance_days_list": tuple(window.tolerance_days for window in windows),
        "day_bins": day_bins(windows),
    }


def render_romp_config(config: dict, output_dir: Path, figure_dir: Path) -> str:
    params = config.get("romp_params") or {}
    model = config.get("model_config") or {}
    dataset = config.get("dataset_config") or {}

    obs_dir = str(config["obs_dir"])
    model_dir = str(config["model_dir"])
    model_name = str(config["model_name"])
    obs_pattern = str(params.get("obs_file_pattern") or dataset.get("obs_file_pattern") or "{}.nc")
    obs_var = str(params.get("obs_var") or dataset.get("obs_var") or "RAINFALL")
    model_pattern = str(params.get("file_pattern") or model.get("file_pattern") or "{}.nc")
    model_var = str(params.get("model_var") or model.get("model_var") or "tp")
    start_date = _date_tuple(params.get("start_date") or model.get("start_date"))
    end_date = _date_tuple(params.get("end_date") or model.get("end_date"))
    ref_model_dir = str(params.get("ref_model_dir") or obs_dir)

    values = {
        "project_name": f"AI Almanac job {config.get('job_id', '')}".strip(),
        "work_dir": str(output_dir),
        "pkg_dir": str(output_dir),
        "layout": ("model", "verification_window"),
        "model_list": (model_name,),
        "obs": str(params.get("obs") or dataset.get("source_name") or "observations"),
        "obs_dir": obs_dir,
        "obs_file_pattern": (obs_pattern,),
        "obs_var": obs_var,
        "obs_unit_cvt": dataset.get("unit_cvt"),
        "ref_model": str(params.get("ref_model") or "climatology"),
        "ref_model_dir": ref_model_dir,
        "ref_model_file_pattern": obs_pattern,
        "ref_model_var": obs_var,
        "ref_model_unit_cvt": dataset.get("unit_cvt"),
        "model_dir_list": (model_dir,),
        "model_var_list": (model_var,),
        "unit_cvt_list": (model.get("unit_cvt"),),
        "file_pattern_list": (model_pattern,),
        "model_dims_list": (parse_model_dims(model.get("forecast_dims")),),
        "region": str(params.get("region") or config.get("romp_region") or "Ethiopia"),
        "lat_min": params.get("lat_min"),
        "lat_max": params.get("lat_max"),
        "lon_min": params.get("lon_min"),
        "lon_max": params.get("lon_max"),
        "nc_mask": params.get("nc_mask"),
        "land_only": bool(params.get("land_only", True)),
        "shp_only": bool(params.get("shp_only", True)),
        "shpfile_dir": None,
        "polygon": False,
        "wet_init": params.get("wet_init", 1),
        "wet_threshold": params.get("wet_threshold", 20),
        "wet_spell": params.get("wet_spell", 3),
        "dry_threshold": 1,
        # The dry-spell check is off (dry_extent 0) unless dry_spell_check turns
        # it on: then no dry run of 7+ days within the onset day and the 21 after it.
        "dry_spell": params.get("dry_spell", 7),
        "dry_extent": params.get("dry_extent", 22 if params.get("dry_spell_check") else 0),
        "thresh_file": params.get("thresh_file"),
        "thresh_var": None,
        "onset_percentage_threshold": 0.5,
        "start_date": start_date,
        "end_date": end_date,
        "start_year_clim": int(params.get("start_year_clim") or start_date[0]),
        "end_year_clim": int(params.get("end_year_clim") or end_date[0]),
        "init_days": _init_days(params.get("init_days")),
        "years": _year_list(params.get("years")),
        "years_clim": _year_list(params.get("years_clim")),
        "date_filter_year": int(params.get("date_filter_year") or start_date[0]),
        **verification_settings(params.get("verification_windows")),
        "max_forecast_day": int(params.get("max_forecast_day") or DEFAULT_MAX_FORECAST_DAY),
        "FAR": True,
        "MAE": True,
        "MR": True,
        "probabilistic": bool(params.get("probabilistic", False)),
        "members": _members(params.get("members")),
        "BS": True,
        "RPS": True,
        "AUC": True,
        "Reliability": True,
        "skill_score": True,
        "dir_out": str(output_dir),
        "dir_fig": str(figure_dir),
        "save_fig": True,
        "save_nc_spatial_far_mr_mae": True,
        "save_csv_score": True,
        "save_nc_climatology": True,
        "plot_spatial_far_mr_mae": False,
        "plot_heatmap_bss_auc": False,
        "plot_reliability": False,
        "plot_climatology_onset": False,
        "plot_panel_heatmap_error": False,
        "plot_panel_heatmap_skill": False,
        "plot_bar_bss_rpss_auc": False,
        "show_plot": False,
        "show_panel": False,
        "parallel": bool(params.get("parallel", True)),
        "debug": False,
    }
    return "\n".join(f"{key} = {value!r}" for key, value in values.items()) + "\n"


def write_romp_config(job_id: str, config: dict, output_dir: Path, figure_dir: Path) -> Path:
    job_config = {**config, "job_id": job_id}
    path = output_dir.parent / "romp-config.in"
    path.write_text(render_romp_config(job_config, output_dir, figure_dir))
    return path
