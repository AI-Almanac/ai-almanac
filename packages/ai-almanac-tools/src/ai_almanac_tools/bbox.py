"""Latitude/longitude boxes, independent of the longitude convention a dataset uses."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import xarray as xr

Bounds = Sequence[float] | None


@dataclass(frozen=True)
class BBox:
    """A box whose longitudes are in the caller's convention: -180..180 or 0..360.

    Selecting from a dataset in the other convention remaps its longitudes, so a box
    such as ``lon=(-10, 5)`` works on 0..360 data and comes back as -10..5.
    """

    lat: tuple[float, float] | None
    lon: tuple[float, float] | None

    @classmethod
    def parse(cls, lat_bounds: Bounds = None, lon_bounds: Bounds = None) -> BBox:
        """Accept bounds in either order; reject values off the globe."""
        lat = _ordered_pair(lat_bounds, "lat")
        lon = _ordered_pair(lon_bounds, "lon")
        if lat and not (lat[0] >= -90 and lat[1] <= 90):
            raise ValueError(f"lat bounds {lat_bounds} must lie within -90..90")
        if lon and not (lon[0] >= -180 and lon[1] <= (180 if lon[0] < 0 else 360)):
            raise ValueError(f"lon bounds {lon_bounds} must lie within -180..180 or within 0..360")
        return cls(lat, lon)

    def select(self, da: xr.DataArray) -> xr.DataArray:
        """The part of ``da`` inside the box; raises if no grid points fall inside."""
        if self.lat:
            da = da.sel(lat=_coord_slice(da["lat"].values, self.lat))
        if self.lon:
            da = self._select_lon(da)
        empty = [dim for dim in ("lat", "lon") if dim in da.dims and da.sizes[dim] == 0]
        if empty:
            raise ValueError(f"{self} selects no grid points along {', '.join(empty)}")
        return da

    def _select_lon(self, da: xr.DataArray) -> xr.DataArray:
        west, east = self.lon
        lon = np.asarray(da["lon"].values, dtype=float)
        lon = ((lon + 180) % 360) - 180 if west < 0 else lon % 360
        inside = np.flatnonzero((lon >= west) & (lon <= east))
        order = inside[np.argsort(lon[inside], kind="stable")]
        return da.isel(lon=order).assign_coords(lon=lon[order])


def _ordered_pair(bounds: Bounds, name: str) -> tuple[float, float] | None:
    if not bounds:
        return None
    if len(bounds) != 2:
        raise ValueError(f"{name} bounds must be [low, high], got {bounds}")
    low, high = sorted(float(value) for value in bounds)
    return low, high


def _coord_slice(values, bounds: tuple[float, float]) -> slice:
    low, high = bounds
    if len(values) >= 2 and float(values[0]) > float(values[-1]):
        return slice(high, low)
    return slice(low, high)
