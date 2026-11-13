"""A user-chosen area of interest that limits which grid cells a job scores.

Two shapes: a lat/lon box, or named places (a country, or administrative units
inside one) whose outlines are attached at submission (see
``boundaries.attach_unit_outlines``) so the run carries the exact geometry it was
scored on. Places that name their own country need no region to be found in.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
import xarray as xr
from pydantic import AfterValidator, BaseModel, TypeAdapter, field_validator, model_validator

from ai_almanac.server.services import netcdf


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


# A country, its states or regions, and the districts or zones inside those.
FocusUnitLevel = Literal["adm0", "adm1", "adm2"]


def parse_country_code(value: str) -> str:
    code = value.strip().upper()
    if len(code) != 3 or not code.isalpha():
        raise ValueError(f"A country is named by its three-letter code, e.g. ETH; got {value!r}")
    return code


CountryCode = Annotated[str, AfterValidator(parse_country_code)]


class FocusUnits(BaseModel):
    level: FocusUnitLevel
    units: list[str]
    # The country the units are in. Without it the run's region supplies the country.
    iso: CountryCode | None = None
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


def area_label(area: FocusArea) -> str:
    """The area in the user's words: its place names, or its box as '5°N–15°N, 33°E–48°E'."""
    if isinstance(area, FocusBox):
        return lat_lon_label((area.lat_min, area.lat_max), (area.lon_min, area.lon_max))
    return ", ".join(area.units)


def lat_lon_label(lat: tuple[float, float], lon: tuple[float, float]) -> str:
    """A box as '5°N–15°N, 33°E–48°E'."""
    return (
        f"{_degrees(lat[0], 'N', 'S')}–{_degrees(lat[1], 'N', 'S')}, "
        f"{_degrees(lon[0], 'E', 'W')}–{_degrees(lon[1], 'E', 'W')}"
    )


def _degrees(value: float, positive: str, negative: str) -> str:
    hemisphere = positive if value >= 0 else negative
    return f"{abs(value):g}°{hemisphere}"


def country_of(area: FocusArea | None, region: Mapping | None = None) -> str | None:
    """The country a run or workspace is in, by its three-letter code: the one its places
    name, else the one its region is; None when nothing says."""
    if isinstance(area, FocusUnits) and area.iso:
        return area.iso
    return (region or {}).get("boundary_iso") or None


def outline_features(area: FocusUnits) -> list[dict]:
    """The GeoJSON features of the units' outlines attached at submission; none before."""
    return (area.geometry or {}).get("features") or []


def area_bounds(area: FocusArea) -> dict[str, float]:
    """The lat/lon box that encloses the area."""
    if isinstance(area, FocusBox):
        return area.model_dump()
    rings = [
        np.asarray(ring, dtype=float)[:, :2]
        for feature in outline_features(area)
        for polygon in polygons_of(feature.get("geometry") or {})
        for ring in polygon
    ]
    if not rings:
        raise ValueError("Area of interest outlines were not attached at submission")
    lon, lat = np.concatenate(rings).T
    return {
        "lat_min": float(lat.min()),
        "lat_max": float(lat.max()),
        "lon_min": float(lon.min()),
        "lon_max": float(lon.max()),
    }


def cells_inside(geometry: dict, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Boolean (lat, lon) grid of cell centres inside any polygon of a FeatureCollection."""
    inside = np.zeros((lat.size, lon.size), dtype=bool)
    for feature in geometry.get("features") or []:
        inside |= cells_inside_shape(feature.get("geometry") or {}, lat, lon)
    return inside


def cells_inside_shape(shape: dict, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Boolean (lat, lon) grid of cell centres inside a GeoJSON geometry.

    Even-odd ray casting, so holes cut out and disjoint parts add up. Only cells within
    the bounding box of a polygon's outer ring are tested against its edges: a zone
    covers a small corner of a country-wide grid, and testing every cell against every
    edge is what made summaries over many areas slow."""
    inside = np.zeros((lat.size, lon.size), dtype=bool)
    for rings in polygons_of(shape):
        if not rings or len(rings[0]) == 0:
            continue
        outer = np.asarray(rings[0], dtype=float)
        rows = np.flatnonzero((lat >= outer[:, 1].min()) & (lat <= outer[:, 1].max()))
        cols = np.flatnonzero((lon >= outer[:, 0].min()) & (lon <= outer[:, 0].max()))
        if not rows.size or not cols.size:
            continue
        lat2d, lon2d = np.meshgrid(lat[rows], lon[cols], indexing="ij")
        inside[np.ix_(rows, cols)] |= points_inside(
            {"type": "Polygon", "coordinates": rings}, lon2d, lat2d
        )
    return inside


def polygons_of(shape: dict) -> list:
    """The rings of each polygon in a GeoJSON Polygon or MultiPolygon geometry."""
    if shape.get("type") == "Polygon":
        return [shape["coordinates"]]
    if shape.get("type") == "MultiPolygon":
        return shape.get("coordinates", [])
    return []


def points_inside(shape: dict, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
    """Which of the points fall inside a GeoJSON geometry, by even-odd ray casting."""
    inside = np.zeros(lon.shape, dtype=bool)
    for rings in polygons_of(shape):
        in_polygon = np.zeros(lon.shape, dtype=bool)
        for ring in rings:
            in_polygon ^= _inside_ring(np.asarray(ring, dtype=float), lon, lat)
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
    netcdf.write(mask, path)
    return path


def data_grid(data_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read the lat/lon coordinates from the first NetCDF file in a directory."""
    sample = next(iter(sorted(data_dir.glob("*.nc"))), None)
    if sample is None:
        raise FileNotFoundError(f"No NetCDF files in {data_dir} to derive the mask grid")
    with netcdf.open_dataset(sample) as ds:
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
