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

Kind = Literal["obs", "model"]
Status = Literal["ready", "invalid"]
_LATITUDE_NAMES = ("lat", "latitude")
_LONGITUDE_NAMES = ("lon", "longitude")
_INITIALIZATION_TIME_NAMES = (
    "time",
    "init_time",
    "initialization_time",
    "forecast_reference_time",
)


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


def _coverage_years(files: list[Path]) -> tuple[int | None, int | None]:
    years = []
    for file in files:
        match = re.search(r"(?:19|20)\d{2}", file.name)
        if match:
            years.append(int(match.group(0)))
    if not years:
        return None, None
    return min(years), max(years)


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


def _initialization_days(dataset) -> tuple[str, str, int] | None:
    coordinate = _coordinate_name(dataset, _INITIALIZATION_TIME_NAMES)
    if coordinate is None or dataset[coordinate].ndim != 1:
        return None

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


def _initialization_schedule(dataset) -> list[str] | None:
    """The archive's fixed-calendar issue-date schedule as sorted ``MM-DD``.

    Archives pin issue dates to fixed calendar dates (e.g. Apr 1, 4, 8), not
    weekdays — the weekday of an issue date drifts year to year, so a weekday
    grid is both unstable across registrations and misaligned with training.
    Recording the month-days lets a live forecast reproduce the archive's
    calendar cadence (see forecast_pipeline.season_issue_dates). The month-days
    are stable across years, so the first file is representative.
    """
    coordinate = _coordinate_name(dataset, _INITIALIZATION_TIME_NAMES)
    if coordinate is None or dataset[coordinate].ndim != 1:
        return None
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

    start_year, end_year = _coverage_years(files)
    if start_year is not None:
        normalized["start_year"] = start_year
        normalized["end_year"] = end_year

    if kind == "model":
        normalized.setdefault("model_type", "AIWP")
        normalized.setdefault("unit_cvt", 1.0)
        # probabilistic is defaulted in _finalize_inspection from the file's
        # dims (an ensemble member dim forces the probabilistic ROMP path).
        normalized.setdefault("members", None)
        if start_year is not None and end_year is not None:
            normalized.setdefault("start_date", f"{start_year}-01-01")
            normalized.setdefault("end_date", f"{end_year}-12-31")
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


# Ensemble member dim names ROMP recognises (see momp dim_fmt_model_ensemble).
_ENSEMBLE_DIM_KEYWORDS = ("number", "sample", "member")


def _has_ensemble_dim(dataset) -> bool:
    return any(
        keyword in str(name).lower() for name in dataset.dims for keyword in _ENSEMBLE_DIM_KEYWORDS
    )


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
            has_ensemble = _has_ensemble_dim(dataset) if kind == "model" else False
            initialization_days = _initialization_days(dataset) if kind == "model" else None
            initialization_schedule = _initialization_schedule(dataset) if kind == "model" else None
    except Exception as exc:
        return (
            "invalid",
            f"Could not open {files[0].name} as NetCDF: {type(exc).__name__}: {exc}",
            normalized,
        )
    normalized["spatial_bounds"] = spatial_bounds
    if kind == "model":
        # An ensemble member dim can only be evaluated by ROMP's probabilistic
        # path; the deterministic path crashes on the extra dim. The file's
        # shape decides the mode, so this overrides any stored flag.
        normalized["probabilistic"] = has_ensemble or bool(normalized.get("probabilistic"))
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
    if variable not in available:
        names = ", ".join(available[:8]) or "none"
        return (
            "invalid",
            f"Variable {variable!r} was not found in {files[0].name}. Available variables: {names}.",
            normalized,
        )
    if kind == "model" and normalized.get("start_year") is None:
        return (
            "invalid",
            "Could not infer model coverage years from matching filenames.",
            normalized,
        )
    return "ready", None, normalized


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
