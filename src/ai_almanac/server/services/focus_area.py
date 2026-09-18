"""A user-drawn lat/lon box that limits which grid cells a job scores."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr
from pydantic import BaseModel, model_validator


class FocusArea(BaseModel):
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    @model_validator(mode="after")
    def _ordered_within_globe(self) -> FocusArea:
        if not -90 <= self.lat_min < self.lat_max <= 90:
            raise ValueError("Focus area needs lat_min < lat_max within [-90, 90]")
        if not -180 <= self.lon_min < self.lon_max <= 360:
            raise ValueError("Focus area needs lon_min < lon_max within [-180, 360]")
        return self


def write_focus_mask(area: FocusArea, path: Path, lat: np.ndarray, lon: np.ndarray) -> Path:
    """Write a 0/1 NetCDF mask on the given grid that is 1 inside the box.

    ROMP aligns the mask to the data with an exact label match, so the mask
    must sit on the data grid itself rather than on a grid of our choosing.
    """
    inside_lat = (lat >= area.lat_min) & (lat <= area.lat_max)
    inside_lon = (lon >= area.lon_min) & (lon <= area.lon_max)
    mask = xr.DataArray(
        (inside_lat[:, None] & inside_lon[None, :]).astype("i1"),
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
        raise FileNotFoundError(f"No NetCDF files in {data_dir} to derive the focus mask grid")
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
    area = FocusArea.model_validate(params["focus_area"])
    lat, lon = data_grid(Path(config["obs_dir"]))
    path = write_focus_mask(area, mask_dir / "focus-mask.nc", lat, lon)
    return {**config, "romp_params": {**params, "nc_mask": str(path)}}
