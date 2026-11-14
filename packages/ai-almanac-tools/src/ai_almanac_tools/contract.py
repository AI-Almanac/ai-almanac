"""The dataset contract that fetcher outputs and transform inputs are normalised to."""

from __future__ import annotations

import xarray as xr

DIM_SYNONYMS = {
    "latitude": "lat",
    "longitude": "lon",
    "valid_time": "time",
    "step": "prediction_timedelta",
    "lead_time": "prediction_timedelta",
    "number": "member",
    "realization": "member",
}
CANONICAL_DIMS = frozenset(
    {"lat", "lon", "time", "init_time", "prediction_timedelta", "member", "vertical", "point_id"}
)


def canonical_name(name: str) -> str:
    lower = name.lower()
    if lower in CANONICAL_DIMS:
        return lower
    return DIM_SYNONYMS.get(lower, name)


def rename_to_canonical(obj: xr.DataArray | xr.Dataset) -> xr.DataArray | xr.Dataset:
    """Rename synonym dimensions and coordinates, e.g. ``latitude`` → ``lat``."""
    names = {str(name) for name in (*obj.dims, *obj.coords)}
    return obj.rename(
        {name: canonical_name(name) for name in names if canonical_name(name) != name}
    )
