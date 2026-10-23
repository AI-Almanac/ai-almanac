"""Find data sources under a directory tree and propose how to register them.

A candidate is any directory holding yearly NetCDF files. Everything the
registration form asks for is inferred from the files themselves:

- file pattern: the year in the filenames (`data_1901.nc` -> `data_{}.nc`)
- kind: a model variable has a lead-time dim beside its start-date dim; an
  observation variable has neither (the same rules the validator applies)
- variable: the gridded data variable, preferring familiar precipitation names
- region: a path segment naming a configured region, else the region whose
  bounding box best overlaps the data

Each candidate is then run through the registration validator, so what a scan
reports is what registering it would store. Nothing here writes; callers
register the candidates they choose through `data_sources.create_source`.
"""

from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from ai_almanac.server.services import data_sources as svc
from ai_almanac.server.services import region_catalog
from ai_almanac.server.services.forecast_models import live_forecast_compatibility
from ai_almanac.settings import get_packaged_forecast_models

# Bounds on one scan, so pointing it at a large tree stays cheap.
MAX_DEPTH = 6
MAX_DIRECTORIES = 5000

_YEAR = re.compile(r"(?:19|20)\d{2}")
# Path segments that only group sources, so they add nothing to a name.
_GROUPING_SEGMENTS = {"data", "dataset", "datasets", "forecast", "forecasts", "model", "models"}
# How closely a source's lat/lon box must match a region's (intersection over
# union) before the region is suggested from bounds alone. Any overlap is too
# loose: a country inside a neighbour's bounding box would take its name.
_MIN_REGION_OVERLAP = 0.5
# Tried in order when a file holds several gridded variables.
_PRECIPITATION_NAMES = (
    "tp",
    "rainfall",
    "precip",
    "precipitation",
    "pr",
    "total_precipitation",
    "total_precipitation_24hr",
)


@dataclass
class DiscoveredSource:
    kind: svc.Kind
    name: str
    path: str
    region: str | None
    metadata: dict
    status: svc.Status
    validation_error: str | None
    # Name of the registered source already pointing at this directory, if any.
    registered_as: str | None = None


@dataclass
class SkippedDirectory:
    path: str
    reason: str


@dataclass
class Discovery:
    roots: list[str]
    sources: list[DiscoveredSource] = field(default_factory=list)
    skipped: list[SkippedDirectory] = field(default_factory=list)
    # True when the scan stopped at MAX_DIRECTORIES before covering every root.
    truncated: bool = False


def infer_file_pattern(names: list[str]) -> str | None:
    """The `{}` pattern most of these filenames share, the year standing for `{}`.

    Uses the first 19xx/20xx run in each name, as the validator does when it
    reads coverage years, so the pattern and the detected years always agree.
    """
    patterns = Counter()
    for name in names:
        match = _YEAR.search(name)
        if match:
            patterns[name[: match.start()] + "{}" + name[match.end() :]] += 1
    if not patterns:
        return None
    # Most common first; ties break alphabetically so a scan is repeatable.
    return min(patterns, key=lambda pattern: (-patterns[pattern], pattern))


def _netcdf_names(directory: Path) -> list[str]:
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return []
    return sorted(
        entry.name
        for entry in entries
        if not entry.name.startswith(".") and entry.name.endswith(".nc") and entry.is_file()
    )


def _candidate_directories(roots: list[Path]) -> tuple[list[Path], bool]:
    """Directories under `roots` that hold NetCDF files, and whether the walk was cut short.

    Hidden directories are skipped and symlinked directories are not followed,
    so a scan can't loop or leave the roots it was given.
    """
    found: list[Path] = []
    visited = 0
    stack = [(root, 0) for root in reversed(roots)]
    while stack:
        directory, depth = stack.pop()
        visited += 1
        if visited > MAX_DIRECTORIES:
            return found, True
        if _netcdf_names(directory):
            found.append(directory)
        if depth >= MAX_DEPTH:
            continue
        try:
            children = sorted(
                (
                    Path(entry.path)
                    for entry in os.scandir(directory)
                    if _is_real_subdirectory(entry)
                ),
                reverse=True,
            )
        except OSError:
            continue
        stack.extend((child, depth + 1) for child in children)
    return found, False


def _is_real_subdirectory(entry: os.DirEntry) -> bool:
    try:
        return not entry.name.startswith(".") and entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _gridded_variable(dataset) -> str | None:
    """The data variable laid out on the lat/lon grid, by name preference when several are."""
    latitude = svc._coordinate_name(dataset, svc._LATITUDE_NAMES)
    longitude = svc._coordinate_name(dataset, svc._LONGITUDE_NAMES)
    gridded = sorted(
        str(name)
        for name, variable in dataset.data_vars.items()
        if latitude in variable.dims and longitude in variable.dims and variable.ndim >= 3
    )
    if len(gridded) <= 1:
        return gridded[0] if gridded else None
    by_lower = {name.lower(): name for name in gridded}
    return next((by_lower[name] for name in _PRECIPITATION_NAMES if name in by_lower), gridded[0])


def _is_forecast(dataset, variable: str) -> bool:
    """True when `variable` has a lead-time dim beside its start-date dim."""
    import numpy as np

    grid = {
        svc._coordinate_name(dataset, svc._LATITUDE_NAMES),
        svc._coordinate_name(dataset, svc._LONGITUDE_NAMES),
    }
    dims = [str(dim) for dim in dataset[variable].dims if dim not in grid]
    dates = svc._dims_where(dataset, dims, lambda c: np.issubdtype(c.dtype, np.datetime64))
    if not dates:
        return False
    try:
        svc._lead_time_dim(dataset, [dim for dim in dims if dim not in dates])
    except ValueError:
        return False
    return True


def _probe(directory: Path, pattern: str) -> tuple[svc.Kind, str] | str:
    """(kind, variable) read from the directory's first matching file, or why not."""
    import xarray as xr

    first = next(
        (path for path in sorted(directory.glob(svc._file_glob(pattern))) if path.is_file()),
        None,
    )
    if first is None:
        return f"no files match {pattern!r}"
    try:
        with xr.open_dataset(first) as dataset:
            variable = _gridded_variable(dataset)
            if variable is None:
                return f"{first.name} has no variable on a latitude/longitude grid"
            return ("model" if _is_forecast(dataset, variable) else "obs"), variable
    except Exception as exc:  # noqa: BLE001 — any unreadable file just isn't a candidate
        return f"could not open {first.name} as NetCDF: {type(exc).__name__}: {exc}"


def _region_slugs(regions: list[dict]) -> dict[str, str]:
    """Lower-cased region ids and display-name slugs, each mapped to its region id."""
    slugs: dict[str, str] = {}
    for region in regions:
        slugs[str(region["id"]).lower()] = region["id"]
        slugs[region_catalog.region_slug(str(region.get("display_name") or ""))] = region["id"]
    slugs.pop("", None)
    return slugs


def _overlap_fraction(bounds: dict, region: dict) -> float:
    """Intersection over union of two lat/lon boxes; 0 when they don't overlap."""
    try:
        boxes = [
            (bounds["lat_min"], bounds["lat_max"], bounds["lon_min"], bounds["lon_max"]),
            (region["lat_min"], region["lat_max"], region["lon_min"], region["lon_max"]),
        ]
        (alat0, alat1, alon0, alon1), (blat0, blat1, blon0, blon1) = (
            tuple(float(value) for value in box) for box in boxes
        )
    except (KeyError, TypeError, ValueError):
        return 0.0
    height = min(alat1, blat1) - max(alat0, blat0)
    width = min(alon1, blon1) - max(alon0, blon0)
    if height <= 0 or width <= 0:
        return 0.0
    intersection = height * width
    union = (alat1 - alat0) * (alon1 - alon0) + (blat1 - blat0) * (blon1 - blon0) - intersection
    return intersection / union if union > 0 else 0.0


def _infer_region(segments: list[str], bounds: dict | None, regions: list[dict]) -> str | None:
    slugs = _region_slugs(regions)
    # The deepest segment naming a region wins: `india/kerala/obs` is about Kerala.
    for segment in reversed(segments):
        region_id = slugs.get(segment.lower()) or slugs.get(region_catalog.region_slug(segment))
        if region_id:
            return region_id
    if not bounds:
        return None
    best = max(regions, key=lambda region: _overlap_fraction(bounds, region), default=None)
    if best is None or _overlap_fraction(bounds, best) < _MIN_REGION_OVERLAP:
        return None
    return best["id"]


def _source_name(segments: list[str], directory: Path, region_id: str | None, regions) -> str:
    """A readable name from the path: `india/models/graphcast` -> `India graphcast`."""
    display = {region["id"]: region.get("display_name") or region["id"] for region in regions}
    slugs = _region_slugs(regions)
    words = []
    for segment in segments:
        if segment.lower() in _GROUPING_SEGMENTS:
            continue
        matched = slugs.get(segment.lower()) or slugs.get(region_catalog.region_slug(segment))
        words.append(display[matched] if matched and matched == region_id else segment)
    return " ".join(words) or directory.name


def _name_tokens(text: str) -> frozenset[str]:
    """Words and numbers in a name, ignoring case, separators, and version `v`s.

    `AIFS_ENS_v2`, `aifs2ens`, and `AIFS2 ENS` all give {aifs, 2, ens}.
    """
    parts = re.findall(r"[a-z]+|\d+", text.lower())
    return frozenset(part for part in parts if part != "v")


def _suggest_forecast_model(folder: str, grid_step: object) -> str | None:
    """The one live forecast model this folder names and whose grid it is on, if exactly one.

    A wrong link quietly miscalibrates live forecasts, so anything short of an
    exact, unique name-and-grid match suggests nothing.
    """
    if not isinstance(grid_step, int | float):
        return None
    tokens = _name_tokens(folder)
    matches = [
        model["id"]
        for model in (get_packaged_forecast_models().get("models") or [])
        if tokens in {_name_tokens(model["id"]), _name_tokens(model.get("display_name") or "")}
        and live_forecast_compatibility(model["id"], float(grid_step)).status == "ready"
    ]
    return matches[0] if len(matches) == 1 else None


def _inspect_candidate(
    directory: Path, root: Path, regions: list[dict]
) -> DiscoveredSource | SkippedDirectory:
    pattern = infer_file_pattern(_netcdf_names(directory))
    if pattern is None:
        return SkippedDirectory(str(directory), "no year (19xx or 20xx) in any filename")
    probed = _probe(directory, pattern)
    if isinstance(probed, str):
        return SkippedDirectory(str(directory), probed)
    kind, variable = probed
    metadata = (
        {"obs_var": variable, "obs_file_pattern": pattern}
        if kind == "obs"
        else {"model_var": variable, "file_pattern": pattern}
    )
    status, error, metadata = svc._inspect_local_source(kind, str(directory), metadata)
    if kind == "model" and status == "ready":
        # Checked against the archive's grid already, so it needs no revalidation.
        suggestion = _suggest_forecast_model(directory.name, metadata.get("grid_step_deg"))
        if suggestion:
            metadata["forecast_model_id"] = suggestion
    segments = list(directory.relative_to(root).parts) if directory != root else [directory.name]
    region_id = _infer_region(segments, metadata.get("spatial_bounds"), regions)
    return DiscoveredSource(
        kind=kind,
        name=_source_name(segments, directory, region_id, regions),
        path=str(directory),
        region=region_id,
        metadata=metadata,
        status=status,
        validation_error=error,
    )


def _scan(roots: list[Path], regions: list[dict], registered: dict[str, str]) -> Discovery:
    discovery = Discovery(roots=[str(root) for root in roots])
    directories, discovery.truncated = _candidate_directories(roots)
    for directory in directories:
        root = next(root for root in roots if directory == root or directory.is_relative_to(root))
        result = _inspect_candidate(directory, root, regions)
        if isinstance(result, SkippedDirectory):
            discovery.skipped.append(result)
            continue
        result.registered_as = registered.get(result.path)
        discovery.sources.append(result)
    return discovery


async def discover_sources(roots: list[Path]) -> Discovery:
    """Scan `roots` (resolved, existing directories) for data sources."""
    regions = await region_catalog.list_regions()
    registered = {
        str(Path(source["path"]).resolve()): source["name"] for source in await svc.list_sources()
    }
    resolved = [root.resolve() for root in roots]
    return await asyncio.to_thread(_scan, resolved, regions, registered)
