"""A date range and area of a registered data source, staged as one NetCDF file.

``Subset`` and the selection functions are pure over opened datasets;
``stage_subset`` is the shell that opens each file (a local path or a gs://
object), totals the selected size before reading any data, then reads and writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import xarray as xr
from ai_almanac_tools.bbox import BBox
from ai_almanac_tools.contract import rename_to_canonical

from . import data_sources, netcdf
from .focus_area import FocusArea, FocusBox, area_label, cells_inside

# What storage still loads eagerly (storage._MAX_EAGER_NC_BYTES): a staged dataset
# larger than this could not be inspected or plotted in the workspace.
MAX_OPEN_SOURCE_BYTES = 256 * 2**20

_YEAR = re.compile(r"(?:19|20)\d{2}")
# Registration names a model archive's dims by role; these take the contract's names.
_CONTRACT_ROLES = ("init_time", "member")


@dataclass(frozen=True)
class Subset:
    start: date
    end: date
    box: BBox | None = None
    # FeatureCollection of named areas; cells outside it are blanked.
    outline: dict | None = None

    @classmethod
    def parse(cls, start: date, end: date, area: FocusArea | None) -> Subset:
        if end < start:
            raise ValueError(f"The end date {end} is before the start date {start}.")
        if area is None:
            return cls(start, end)
        if isinstance(area, FocusBox):
            return cls(
                start, end, BBox.parse((area.lat_min, area.lat_max), (area.lon_min, area.lon_max))
            )
        if area.geometry is None:
            raise ValueError("Named areas need their outlines attached before subsetting.")
        return cls(start, end, _outline_box(area.geometry), area.geometry)


def _outline_box(outline: dict) -> BBox:
    points = [
        point
        for feature in outline.get("features") or []
        for point in _coordinates(feature.get("geometry") or {})
    ]
    lons, lats = zip(*points, strict=True)
    return BBox.parse((min(lats), max(lats)), (min(lons), max(lons)))


def _coordinates(shape: dict) -> list[tuple[float, float]]:
    nested = shape.get("coordinates") or []
    while nested and not isinstance(nested[0][0], (int, float)):
        nested = [item for part in nested for item in part]
    return [(float(lon), float(lat)) for lon, lat, *_ in nested]


WHOLE_SOURCE = "whole source"


def subset_area_label(area: FocusArea | None) -> str:
    return area_label(area) if area is not None else WHOLE_SOURCE


def files_in_range(files: list[str], start: date, end: date) -> list[str]:
    """Files named for a year in the range; a file named for no year may hold any, so it stays."""

    def in_range(name: str) -> bool:
        match = _YEAR.search(Path(name).name)
        return match is None or start.year <= int(match.group(0)) <= end.year

    return [file for file in files if in_range(file)]


def source_variable(dataset: xr.Dataset, source: dict) -> xr.DataArray:
    """The source's variable with its dims under the dataset contract's names."""
    metadata = source.get("metadata") or {}
    name = metadata.get("obs_var" if source["kind"] == "obs" else "model_var")
    if name not in dataset.data_vars:
        raise ValueError(f"{source['name']} has no variable {name!r}.")
    roles = metadata.get("forecast_dims") or {}
    variable = dataset[name]
    renames = {roles[role]: role for role in _CONTRACT_ROLES if roles.get(role) in variable.dims}
    return rename_to_canonical(variable.rename({k: v for k, v in renames.items() if k != v}))


def time_dim(variable: xr.DataArray) -> str:
    """Model archives are selected by forecast start date, observations by day."""
    for dim in ("init_time", "time"):
        if dim in variable.dims:
            return dim
    raise ValueError(f"{variable.name} has no time or forecast start date dimension.")


def select(variable: xr.DataArray, subset: Subset) -> xr.DataArray:
    """The lazy slice of one file's variable inside the subset's dates and box."""
    selected = variable.sel(
        {time_dim(variable): slice(subset.start.isoformat(), subset.end.isoformat())}
    )
    return subset.box.select(selected) if subset.box else selected


def blank_outside(variable: xr.DataArray, outline: dict) -> xr.DataArray:
    inside = cells_inside(outline, variable["lat"].values, variable["lon"].values)
    mask = xr.DataArray(
        inside, dims=("lat", "lon"), coords={"lat": variable["lat"], "lon": variable["lon"]}
    )
    return variable.where(mask)


def megabytes(size: int) -> str:
    return f"{size / 1_000_000:,.0f} MB"


def _selected(identifier: str, source: dict, subset: Subset, use):
    with data_sources.open_source_file(identifier) as dataset:
        return use(select(source_variable(dataset, source), subset))


def stage_subset(source: dict, subset: Subset, out_path: Path) -> dict:
    """Write the subset to ``out_path`` and describe it; refuse before reading if too large."""
    files = files_in_range(data_sources.list_source_files(source), subset.start, subset.end)
    sizes = [_selected(file, source, subset, lambda variable: variable.nbytes) for file in files]
    if sum(sizes) > MAX_OPEN_SOURCE_BYTES:
        raise ValueError(
            f"This subset is about {megabytes(sum(sizes))}, over the "
            f"{megabytes(MAX_OPEN_SOURCE_BYTES)} limit. Narrow the dates or the area."
        )
    parts = [
        _selected(file, source, subset, lambda variable: variable.load())
        for file, size in zip(files, sizes, strict=True)
        if size
    ]
    if not parts:
        raise ValueError(f"{source['name']} has no data from {subset.start} to {subset.end}.")
    variable = xr.concat(parts, dim=time_dim(parts[0]))
    if subset.outline:
        variable = blank_outside(variable, subset.outline)
    netcdf.write(variable.to_dataset().assign_attrs(source=source["name"]), out_path)
    times = variable[time_dim(variable)].values
    return {
        "variable": str(variable.name),
        "units": variable.attrs.get("units"),
        "dims": {str(dim): int(size) for dim, size in variable.sizes.items()},
        "first": str(times[0])[:10],
        "last": str(times[-1])[:10],
        "size": megabytes(variable.nbytes),
    }
