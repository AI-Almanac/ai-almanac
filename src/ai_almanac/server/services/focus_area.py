"""A user-chosen area of interest that limits which grid cells a job scores.

Two shapes: a lat/lon box, or a set of named administrative units whose
outlines are attached at submission (see ``boundaries.attach_unit_outlines``)
so the run carries the exact geometry it was scored on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import numpy as np
import xarray as xr
from pydantic import BaseModel, TypeAdapter, field_validator, model_validator


class FocusBox(BaseModel):
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    @model_validator(mode="after")
    def _ordered_within_globe(self) -> FocusBox:
        if not -90 <= self.lat_min < self.lat_max <= 90:
            raise ValueError("Area of interest needs lat_min < lat_max within [-90, 90]")
        if not -180 <= self.lon_min < self.lon_max <= 360:
            raise ValueError("Area of interest needs lon_min < lon_max within [-180, 360]")
        return self


class FocusUnits(BaseModel):
    level: Literal["adm2"]
    units: list[str]
    # GeoJSON FeatureCollection of the units' outlines, filled in at submission.
    geometry: dict | None = None

    @field_validator("units")
    @classmethod
    def _named(cls, units: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(name.strip() for name in units if name.strip()))
        if not cleaned:
            raise ValueError("Area of interest needs at least one named area")
        return cleaned


FocusArea = FocusBox | FocusUnits
_FOCUS_AREA = TypeAdapter(FocusArea)


def parse_focus_area(value: object) -> FocusArea:
    return _FOCUS_AREA.validate_python(value)


def cells_inside(geometry: dict, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Boolean (lat, lon) grid of cell centres inside any polygon of a FeatureCollection.

    Even-odd ray casting over every ring, so holes cut out and disjoint parts add up.
    """
    lat2d, lon2d = np.meshgrid(lat, lon, indexing="ij")
    inside = np.zeros(lat2d.shape, dtype=bool)
    for feature in geometry.get("features") or []:
        shape = feature.get("geometry") or {}
        polygons = (
            [shape["coordinates"]]
            if shape.get("type") == "Polygon"
            else shape.get("coordinates", [])
            if shape.get("type") == "MultiPolygon"
            else []
        )
        for rings in polygons:
            in_polygon = np.zeros(lat2d.shape, dtype=bool)
            for ring in rings:
                in_polygon ^= _inside_ring(np.asarray(ring, dtype=float), lon2d, lat2d)
            inside |= in_polygon
    return inside


def _inside_ring(ring: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    inside = np.zeros(x.shape, dtype=bool)
    for (xa, ya), (xb, yb) in zip(ring[:-1], ring[1:], strict=True):
        if ya == yb:
            continue
        crosses = (ya > y) != (yb > y)
        x_at_y = (xb - xa) * (y - ya) / (yb - ya) + xa
        inside ^= crosses & (x < x_at_y)
    return inside


def write_focus_mask(area: FocusArea, path: Path, lat: np.ndarray, lon: np.ndarray) -> Path:
    """Write a 0/1 NetCDF mask on the given grid that is 1 inside the area.

    ROMP aligns the mask to the data with an exact label match, so the mask
    must sit on the data grid itself rather than on a grid of our choosing.
    """
    if isinstance(area, FocusBox):
        inside_lat = (lat >= area.lat_min) & (lat <= area.lat_max)
        inside_lon = (lon >= area.lon_min) & (lon <= area.lon_max)
        inside = inside_lat[:, None] & inside_lon[None, :]
    else:
        if not area.geometry:
            raise ValueError("Area of interest outlines were not attached at submission")
        inside = cells_inside(area.geometry, lat, lon)
    if not inside.any():
        raise ValueError("No grid cells fall inside the area of interest")
    mask = xr.DataArray(
        inside.astype("i1"),
        dims=("lat", "lon"),
        coords={"lat": lat, "lon": lon},
        name="mask",
    )
    mask.to_netcdf(path)
    return path


def data_grid(data_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read the lat/lon coordinates from the first NetCDF file in a directory."""
    sample = next(iter(sorted(data_dir.glob("*.nc"))), None)
    if sample is None:
        raise FileNotFoundError(f"No NetCDF files in {data_dir} to derive the mask grid")
    with xr.open_dataset(sample) as ds:
        return _coord(ds, "lat"), _coord(ds, "lon")


def _coord(ds: xr.Dataset, prefix: str) -> np.ndarray:
    name = next((c for c in ds.coords if c.lower().startswith(prefix)), None)
    if name is None:
        raise ValueError(f"{ds.encoding.get('source', 'dataset')} has no {prefix} coordinate")
    return ds[name].values.astype("f8")


def materialize_focus_mask(config: dict, mask_dir: Path) -> dict:
    """Turn a job's focus_area into an nc_mask file on the observation grid."""
    params = config.get("romp_params") or {}
    if not params.get("focus_area"):
        return config
    area = parse_focus_area(params["focus_area"])
    lat, lon = data_grid(Path(config["obs_dir"]))
    path = write_focus_mask(area, mask_dir / "focus-mask.nc", lat, lon)
    return {**config, "romp_params": {**params, "nc_mask": str(path)}}
