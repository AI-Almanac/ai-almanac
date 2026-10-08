"""Data sources catalog service.

A data source is a pointer to a directory on disk, a gs:// prefix, or a
remote provider URL (e.g. ARCO ERA5) that the app can use as ground-truth
observations or model forecasts in a benchmark. Rows are registered through
the data-sources API/UI and validated at registration; there is no seeding.
"""

from __future__ import annotations

import asyncio
import json
import math
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.server.services.forecast_models import live_forecast_compatibility

Kind = Literal["obs", "model"]
Status = Literal["ready", "invalid"]
_LATITUDE_NAMES = ("lat", "latitude")
_LONGITUDE_NAMES = ("lon", "longitude")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _decode_metadata(value) -> dict:
    """SQLite returns JSON columns as strings; deserialize for callers."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}
    return value or {}


def _row_dict(row) -> dict:
    d = dict(row)
    d["metadata"] = _decode_metadata(d.get("metadata"))
    if isinstance(d.get("region"), str):
        d["region"] = d["region"].strip().lower() or None
    return d


def _file_glob(pattern: str) -> str:
    return pattern.replace("{}", "*")


def _coverage_years(files: list[Path]) -> list[int]:
    matches = (re.search(r"(?:19|20)\d{2}", file.name) for file in files)
    return sorted({int(match.group(0)) for match in matches if match})


def _missing_years(years: list[int]) -> list[int]:
    if not years:
        return []
    return sorted(set(range(years[0], years[-1] + 1)) - set(years))


def _coordinate_name(dataset, candidates: tuple[str, ...]) -> str | None:
    names = {str(name).lower(): str(name) for name in dataset.coords}
    return next((names[name] for name in candidates if name in names), None)


def _spatial_bounds(dataset) -> dict[str, float]:
    latitude = _coordinate_name(dataset, _LATITUDE_NAMES)
    longitude = _coordinate_name(dataset, _LONGITUDE_NAMES)
    if latitude is None or longitude is None:
        raise ValueError(
            "Could not identify latitude and longitude coordinates. "
            "Supported names are lat/lon and latitude/longitude, in any letter case."
        )
    if dataset[latitude].ndim != 1 or dataset[longitude].ndim != 1:
        raise ValueError("Latitude and longitude coordinates must be one-dimensional.")

    bounds = {
        "lat_min": float(dataset[latitude].min().item()),
        "lat_max": float(dataset[latitude].max().item()),
        "lon_min": float(dataset[longitude].min().item()),
        "lon_max": float(dataset[longitude].max().item()),
    }
    if not all(math.isfinite(value) for value in bounds.values()):
        raise ValueError("Latitude and longitude coordinates contain no finite extent.")
    return bounds


def _grid_step_deg(dataset) -> float | None:
    """Regular latitude spacing in degrees, or None when the grid is irregular.

    Live forecast models are matched against this: a blend's coefficients only
    transfer to a live season rolled out on the same grid the archive uses.
    """
    # ponytail: latitude spacing stands in for the whole grid; every archive so
    # far is a regular lat/lon grid with equal steps in both axes.
    latitude = _coordinate_name(dataset, _LATITUDE_NAMES)
    values = [float(value) for value in dataset[latitude].values]
    if len(values) < 2:
        return None
    steps = [abs(after - before) for before, after in zip(values[:-1], values[1:], strict=True)]
    step = round(steps[0], 6)
    if not all(math.isclose(candidate, step, abs_tol=1e-6) for candidate in steps):
        return None
    return step


def _initialization_days(dataset, coordinate: str) -> tuple[str, str, int] | None:
    values = dataset[coordinate]
    if values.size < 2:
        return None
    try:
        weekdays = sorted({int(day) for day in values.dt.weekday.values.tolist()})
    except (AttributeError, TypeError, ValueError):
        return None
    if not weekdays or any(day < 0 or day > 6 for day in weekdays):
        return None
    return ",".join(str(day) for day in weekdays), coordinate, int(values.size)


def _initialization_schedule(dataset, coordinate: str) -> list[str] | None:
    """The archive's fixed-calendar issue-date schedule as sorted ``MM-DD``.

    Archives pin issue dates to fixed calendar dates (e.g. Apr 1, 4, 8), not
    weekdays — the weekday of an issue date drifts year to year, so a weekday
    grid is both unstable across registrations and misaligned with training.
    Recording the month-days lets a live forecast reproduce the archive's
    calendar cadence (see forecast_pipeline.season_issue_dates). The month-days
    are stable across years, so the first file is representative.
    """
    values = dataset[coordinate]
    if values.size < 2:
        return None
    try:
        months = [int(month) for month in values.dt.month.values.tolist()]
        days = [int(day) for day in values.dt.day.values.tolist()]
    except (AttributeError, TypeError, ValueError):
        return None
    schedule = sorted({f"{month:02d}-{day:02d}" for month, day in zip(months, days, strict=True)})
    return schedule or None


def _season_window(schedule: list[str] | None) -> tuple[str, str]:
    """Season ``MM-DD`` span (start, end) from the init-time schedule.

    ROMP reads the month-day of start_date/end_date as the initialization
    season. A full calendar year both mis-states the season and drags Feb 29
    into the init-date candidates, which crashes non-leap target years. The
    schedule is sorted, so its first and last entries bound the season.
    Falls back to the full year when no schedule is available.
    """
    # ponytail: assumes a season within one calendar year (true for these
    # May-Aug onset archives); a year-wrapping season would need min/max by date.
    if schedule:
        return schedule[0], schedule[-1]
    return "01-01", "12-31"


def _normalized_initialization_days(value: object) -> str:
    raw_days = [day.strip() for day in str(value).split(",")]
    if not raw_days or any(not day for day in raw_days):
        raise ValueError("Initialization days must be comma-separated weekday numbers from 0 to 6.")
    try:
        days = sorted({int(day) for day in raw_days})
    except ValueError as exc:
        raise ValueError(
            "Initialization days must be comma-separated weekday numbers from 0 to 6."
        ) from exc
    if any(day < 0 or day > 6 for day in days):
        raise ValueError("Initialization days must use weekday numbers from 0 to 6.")
    return ",".join(str(day) for day in days)


def _normalized_metadata(kind: Kind, metadata: dict, files: list[Path]) -> dict:
    normalized = dict(metadata)
    variable_key = "obs_var" if kind == "obs" else "model_var"
    pattern_key = "obs_file_pattern" if kind == "obs" else "file_pattern"
    default_variable = "RAINFALL" if kind == "obs" else "tp"
    normalized[variable_key] = str(normalized.get(variable_key) or default_variable).strip()
    normalized[pattern_key] = str(normalized.get(pattern_key) or "{}.nc").strip()

    years = _coverage_years(files)
    start_year, end_year = (years[0], years[-1]) if years else (None, None)
    if years:
        normalized["start_year"] = start_year
        normalized["end_year"] = end_year
        # A gap is reported, not rejected: sources with a missing season are
        # still usable for the years they do cover.
        normalized["years"] = years
        normalized["missing_years"] = _missing_years(years)

    if kind == "model":
        forecast_model_id = str(normalized.pop("forecast_model_id", None) or "").strip()
        if forecast_model_id:
            normalized["forecast_model_id"] = forecast_model_id
        normalized.setdefault("model_type", "AIWP")
        normalized.setdefault("unit_cvt", 1.0)
        # probabilistic is defaulted in _finalize_inspection from the file's
        # dims (an ensemble member dim forces the probabilistic ROMP path).
        normalized.setdefault("members", None)
        if start_year is not None and end_year is not None:
            # start_date/end_date default from the init-time season window in
            # _finalize_inspection, which has the open dataset; the clim years
            # only need the coverage span.
            normalized.setdefault("start_year_clim", start_year)
            normalized.setdefault("end_year_clim", end_year)
    return normalized


def _source_file_pattern(kind: Kind, metadata: dict) -> str:
    pattern_key = "obs_file_pattern" if kind == "obs" else "file_pattern"
    return str(metadata.get(pattern_key) or "{}.nc").strip()


def _inspect_local_source(kind: Kind, path: str, metadata: dict) -> tuple[Status, str | None, dict]:
    directory = Path(path)
    if not directory.exists():
        return "invalid", "Directory does not exist.", metadata
    if not directory.is_dir():
        return "invalid", "Path is not a directory.", metadata
    try:
        next(directory.iterdir(), None)
    except PermissionError:
        return "invalid", "Directory is not readable.", metadata

    pattern = _source_file_pattern(kind, metadata)
    files = sorted(file for file in directory.glob(_file_glob(pattern)) if file.is_file())
    if not files:
        return "invalid", f"No files match {pattern!r}.", metadata

    import xarray as xr

    return _finalize_inspection(kind, metadata, files, lambda: xr.open_dataset(files[0]))


def _inspect_gcs_source(kind: Kind, path: str, metadata: dict) -> tuple[Status, str | None, dict]:
    from ai_almanac.server.services.storage import get_storage

    storage = get_storage()
    pattern = _source_file_pattern(kind, metadata)
    try:
        identifiers = storage.list_dataset_files(path, _file_glob(pattern))
    except Exception as exc:
        return (
            "invalid",
            f"Cannot read {path}: {type(exc).__name__}: {exc}. "
            "Check that the path exists and is readable by the service account.",
            metadata,
        )
    if not identifiers:
        return "invalid", f"No files match {pattern!r} under {path}.", metadata

    files = [Path(identifier) for identifier in identifiers]
    return _finalize_inspection(
        kind, metadata, files, lambda: storage.open_nc_dataset(identifiers[0])
    )


# Integer lead-time dims carry no timedelta dtype, so they are known by name.
_INTEGER_LEAD_TIME_KEYWORDS = ("day", "step", "lead")


def _dims_where(dataset, dims: list[str], predicate) -> list[str]:
    return [dim for dim in dims if dim in dataset.coords and predicate(dataset[dim])]


def _only(candidates: list[str], label: str) -> str:
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"No {label} dimension was found.")
    raise ValueError(f"Found several possible {label} dimensions: {', '.join(candidates)}.")


def _lead_time_dim(dataset, dims: list[str]) -> str:
    import numpy as np

    timedeltas = _dims_where(dataset, dims, lambda c: np.issubdtype(c.dtype, np.timedelta64))
    if timedeltas:
        return _only(timedeltas, "lead time")
    named_integers = _dims_where(
        dataset,
        dims,
        lambda c: (
            np.issubdtype(c.dtype, np.integer)
            and any(word in str(c.name).lower() for word in _INTEGER_LEAD_TIME_KEYWORDS)
        ),
    )
    return _only(named_integers, "lead time")


def _forecast_dims(dataset, variable: str) -> dict[str, str]:
    """Which of `variable`'s dims plays each role ROMP needs, keyed by ROMP's name.

    Roles come from the coordinates' types, not their names: the datetime dim
    is the start date and the timedelta dim the lead time, whatever they are
    called, and a dim left over with more than one value is the ensemble member.
    """
    import numpy as np

    dims = [str(dim) for dim in dataset[variable].dims]
    roles = {
        "lat": _coordinate_name(dataset, _LATITUDE_NAMES),
        "lon": _coordinate_name(dataset, _LONGITUDE_NAMES),
        "init_time": _only(
            _dims_where(dataset, dims, lambda c: np.issubdtype(c.dtype, np.datetime64)),
            "forecast start date",
        ),
    }
    roles["step"] = _lead_time_dim(dataset, [dim for dim in dims if dim not in roles.values()])
    member = _ensemble_member_dim(dataset, [dim for dim in dims if dim not in roles.values()])
    if member:
        roles["member"] = member
    return _romp_safe_dims(roles)


def _ensemble_member_dim(dataset, remaining: list[str]) -> str | None:
    """The one dim left after the other roles, which must span several members."""
    if len(remaining) > 1:
        raise ValueError(f"Expected at most one ensemble member dimension, found {remaining}.")
    if not remaining:
        return None
    member = remaining[0]
    # A single-valued extra axis (e.g. a size-1 height) is not an ensemble; taking
    # it as one would silently make the source probabilistic.
    if dataset.sizes[member] < 2:
        raise ValueError(
            f"Dimension {member!r} has a single value, so it is not an ensemble member. "
            "Remove it, or give the forecast start date, lead time, lat and lon only."
        )
    return member


def _romp_safe_dims(roles: dict[str, str]) -> dict[str, str]:
    """Reject dim names ROMP's config cannot carry now, not when a job is submitted."""
    from pydantic import ValidationError

    from ai_almanac.server.services.romp import parse_model_dims

    try:
        return parse_model_dims(roles)
    except ValidationError:
        names = ", ".join(sorted(set(roles.values())))
        raise ValueError(
            "Dimension names may only use letters, digits, '_', '.' and '-' "
            f"(and must not start with '.' or '-'). Found: {names}."
        ) from None


# Multipliers from a precipitation total's CF `units` to the millimetres ROMP
# thresholds assume.
_MILLIMETRES_PER_UNIT = {
    "m": 1000.0,
    "metres": 1000.0,
    "meters": 1000.0,
    "m/day": 1000.0,
    "mm": 1.0,
    "mm/day": 1.0,
    "mm day-1": 1.0,
    "mm d-1": 1.0,
    "kg m-2": 1.0,
    "kg m**-2": 1.0,
    "kg/m2": 1.0,
}


def _precipitation_unit_cvt(units: object) -> float:
    try:
        return _MILLIMETRES_PER_UNIT[str(units).strip().lower()]
    except KeyError:
        supported = ", ".join(repr(name) for name in _MILLIMETRES_PER_UNIT)
        raise ValueError(
            f"Unrecognized precipitation units {units!r}. Supported units: {supported}."
        ) from None


def _finalize_inspection(
    kind: Kind, metadata: dict, files: list[Path], open_first
) -> tuple[Status, str | None, dict]:
    """Infer and validate metadata from the matched source files.

    `files` supplies basenames for coverage-year inference; `open_first()` opens
    the first file (a local path or a gs:// URI) as an xarray Dataset. Shared by
    the local and GCS inspectors so both backends validate sources identically.
    """
    normalized = _normalized_metadata(kind, metadata, files)
    variable_key = "obs_var" if kind == "obs" else "model_var"
    variable = normalized[variable_key]
    try:
        with open_first() as dataset:
            available = sorted(dataset.data_vars)
            spatial_bounds = _spatial_bounds(dataset)
            grid_step_deg = _grid_step_deg(dataset)
            units = dataset[variable].attrs.get("units") if variable in dataset.data_vars else None
            forecast_dims, layout_error = None, None
            if kind == "model" and variable in dataset.data_vars:
                try:
                    forecast_dims = _forecast_dims(dataset, variable)
                except ValueError as exc:
                    layout_error = str(exc)
            init_coordinate = (forecast_dims or {}).get("init_time")
            initialization_days = (
                _initialization_days(dataset, init_coordinate) if init_coordinate else None
            )
            initialization_schedule = (
                _initialization_schedule(dataset, init_coordinate) if init_coordinate else None
            )
    except Exception as exc:
        return (
            "invalid",
            f"Could not open {files[0].name} as NetCDF: {type(exc).__name__}: {exc}",
            normalized,
        )
    normalized["spatial_bounds"] = spatial_bounds
    normalized["grid_step_deg"] = grid_step_deg
    if kind == "model":
        # An ensemble member dim can only be evaluated by ROMP's probabilistic
        # path; the deterministic path crashes on the extra dim. The file's
        # shape decides the mode, so this overrides any stored flag.
        normalized["probabilistic"] = "member" in (forecast_dims or {}) or bool(
            normalized.get("probabilistic")
        )
        # ROMP renames these to its own names before scoring, so model files
        # keep whatever dim names their producer chose.
        normalized["forecast_dims"] = forecast_dims
        existing_source = normalized.get("init_days_source")
        configured_init_days = str(normalized.get("init_days") or "").strip()
        has_configured_days = bool(configured_init_days) and existing_source not in {
            "inferred",
            "default",
        }
        if has_configured_days:
            try:
                normalized["init_days"] = _normalized_initialization_days(configured_init_days)
            except ValueError as exc:
                return "invalid", str(exc), normalized
            normalized["init_days_source"] = "configured"
            normalized.pop("init_time_coordinate", None)
            normalized.pop("init_time_sample_count", None)
        elif initialization_days is not None:
            init_days, coordinate, sample_count = initialization_days
            normalized["init_days"] = init_days
            normalized["init_days_source"] = "inferred"
            normalized["init_time_coordinate"] = coordinate
            normalized["init_time_sample_count"] = sample_count
        else:
            normalized["init_days"] = "0"
            normalized["init_days_source"] = "default"
            normalized.pop("init_time_coordinate", None)
            normalized.pop("init_time_sample_count", None)
        # The calendar schedule drives live forecast issue dates; init_days
        # weekdays remain for ROMP and as the pre-schedule fallback.
        normalized["init_month_days"] = initialization_schedule
        start_year = normalized.get("start_year")
        end_year = normalized.get("end_year")
        if start_year is not None and end_year is not None:
            season_start, season_end = _season_window(initialization_schedule)
            if initialization_schedule:
                normalized["start_date"] = f"{start_year}-{season_start}"
                normalized["end_date"] = f"{end_year}-{season_end}"
            else:
                normalized.setdefault("start_date", f"{start_year}-01-01")
                normalized.setdefault("end_date", f"{end_year}-12-31")
    if variable not in available:
        names = ", ".join(available[:8]) or "none"
        return (
            "invalid",
            f"Variable {variable!r} was not found in {files[0].name}. Available variables: {names}.",
            normalized,
        )
    if kind == "model" and layout_error:
        return "invalid", f"{files[0].name}: {layout_error}", normalized
    if kind == "model" and units is not None:
        # Declared units decide the conversion, like the ensemble dim decides
        # probabilistic; files without a units attribute keep the stored value.
        try:
            normalized["unit_cvt"] = _precipitation_unit_cvt(units)
        except ValueError as exc:
            return "invalid", f"{variable!r} in {files[0].name}: {exc}", normalized
    if kind == "model" and normalized.get("start_year") is None:
        return (
            "invalid",
            "Could not infer model coverage years from matching filenames.",
            normalized,
        )
    if kind == "model" and (link_error := _forecast_model_link_error(normalized)):
        return "invalid", link_error, normalized
    return "ready", None, normalized


def _forecast_model_link_error(metadata: dict) -> str | None:
    """Why the live forecast model chosen for this archive can't run it, if so.

    Unlinked archives are valid (history only); a link must name a registry
    model on the archive's grid, or live forecasts would be scored with weights
    that don't apply to them.
    """
    model_id = metadata.get("forecast_model_id")
    if model_id is None:
        return None
    verdict = live_forecast_compatibility(model_id, metadata.get("grid_step_deg"))
    return None if verdict.status == "ready" else verdict.detail


async def validate_source(kind: Kind, path: str, metadata: dict) -> tuple[Status, str | None, dict]:
    # Remote-provider sources (e.g. era5_arco) have no file tree to inspect;
    # their metadata (arco_url, variable, bounds) is the contract.
    if (metadata or {}).get("provider") not in (None, "local"):
        return "ready", None, dict(metadata)
    inspect = _inspect_gcs_source if str(path).startswith("gs://") else _inspect_local_source
    return await asyncio.to_thread(inspect, kind, path, metadata)


async def list_sources(
    kind: Kind | None = None,
    *,
    user_id: str | None = None,
    is_admin: bool = False,
) -> list[dict]:
    """Return all data sources, optionally filtered by kind."""
    async with get_db() as conn:
        clauses: list[str] = []
        params: dict[str, str] = {}
        if kind is not None:
            clauses.append("kind = :kind")
            params["kind"] = kind
        if user_id is not None and not is_admin:
            clauses.append("(owner_id = :uid OR visibility = 'shared')")
            params["uid"] = user_id
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        result = await conn.execute(
            text(f"SELECT * FROM data_sources{where} ORDER BY kind, name"), params
        )
        return [_row_dict(row) for row in result.mappings().fetchall()]


async def create_source(
    kind: Kind,
    name: str,
    path: str,
    region: str | None = None,
    metadata: dict | None = None,
    owner_id: str | None = None,
    visibility: str = "shared",
) -> dict:
    """Insert a new data source. Returns the created row."""
    normalized_region = region.strip().lower() if region else None
    source_id = str(uuid.uuid4())
    status, validation_error, normalized_metadata = await validate_source(
        kind, path, metadata or {}
    )
    now = _now()
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, kind, name, path, region, metadata, location_type, status, "
                "validation_error, owner_id, visibility, created_at, updated_at) "
                "VALUES (:id, :kind, :name, :path, :region, :metadata, "
                ":location_type, :status, :validation_error, :owner_id, :visibility, "
                ":now, :now)"
            ),
            {
                "id": source_id,
                "kind": kind,
                "name": name,
                "path": path,
                "region": normalized_region,
                # SQLite's JSON column doesn't auto-serialize Python dicts; emit a string.
                "metadata": json.dumps(normalized_metadata),
                "location_type": "gcs" if path.startswith("gs://") else "local_directory",
                "status": status,
                "validation_error": validation_error,
                "owner_id": owner_id,
                "visibility": visibility,
                "now": now,
            },
        )
        result = await conn.execute(
            text("SELECT * FROM data_sources WHERE id = :id"), {"id": source_id}
        )
        return _row_dict(result.mappings().fetchone())


async def update_source(
    source_id: str,
    *,
    name: str,
    path: str,
    region: str | None,
    metadata: dict,
) -> dict | None:
    normalized_region = region.strip().lower() if region else None
    async with get_db() as conn:
        existing = (
            (
                await conn.execute(
                    text("SELECT kind FROM data_sources WHERE id = :id"),
                    {"id": source_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not existing:
        return None

    kind: Kind = existing["kind"]
    status, validation_error, normalized_metadata = await validate_source(kind, path, metadata)
    async with get_db() as conn:
        result = await conn.execute(
            text(
                "UPDATE data_sources SET name = :name, path = :path, region = :region, "
                "metadata = :metadata, status = :status, validation_error = :error, "
                "updated_at = :now WHERE id = :id RETURNING *"
            ),
            {
                "id": source_id,
                "name": name,
                "path": path,
                "region": normalized_region,
                "metadata": json.dumps(normalized_metadata),
                "status": status,
                "error": validation_error,
                "now": _now(),
            },
        )
        row = result.mappings().fetchone()
        return _row_dict(row) if row else None


async def revalidate_source(source_id: str) -> dict | None:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM data_sources WHERE id = :id"),
                    {"id": source_id},
                )
            )
            .mappings()
            .fetchone()
        )
    if not row:
        return None
    source = _row_dict(row)
    return await update_source(
        source_id,
        name=source["name"],
        path=source["path"],
        region=source.get("region"),
        metadata=source["metadata"],
    )


async def get_source(source_id: str) -> dict | None:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM data_sources WHERE id = :id"),
                    {"id": source_id},
                )
            )
            .mappings()
            .fetchone()
        )
    return _row_dict(row) if row else None


async def delete_source(source_id: str) -> bool:
    async with get_db() as conn:
        result = await conn.execute(
            text("DELETE FROM data_sources WHERE id = :id"), {"id": source_id}
        )
        return result.rowcount > 0


async def get_obs_sources(*, user_id: str | None = None, is_admin: bool = False) -> list[dict]:
    """Return obs data sources visible to the given user (all when unscoped)."""
    return await list_sources(kind="obs", user_id=user_id, is_admin=is_admin)


async def get_model_sources(
    region: str | None = None, *, user_id: str | None = None, is_admin: bool = False
) -> list[dict]:
    """Return model data sources visible to the given user, optionally per region."""
    sources = await list_sources(kind="model", user_id=user_id, is_admin=is_admin)
    if region:
        sources = [s for s in sources if (s.get("region") or "").lower() == region.lower()]
    return sources
