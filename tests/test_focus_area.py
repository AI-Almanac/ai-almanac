from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from pydantic import ValidationError

from ai_almanac.server.services.focus_area import FocusArea, materialize_focus_mask

BOX = {"lat_min": 8.13, "lat_max": 12.4, "lon_min": 38.0, "lon_max": 40.7}


def _write_obs(path: Path) -> None:
    lat = np.arange(3.0, 15.01, 0.25)
    lon = np.arange(33.0, 48.01, 0.25)
    xr.DataArray(
        np.zeros((lat.size, lon.size), dtype="f4"),
        dims=("LATITUDE", "LONGITUDE"),
        coords={"LATITUDE": lat, "LONGITUDE": lon},
        name="RAINFALL",
    ).to_netcdf(path)


def _romp_apply(mask_path: str, ds: xr.Dataset) -> xr.DataArray:
    """Mirror ROMP's apply_nc_mask: nearest-select, then align exactly."""
    mask = xr.open_dataset(mask_path)["mask"].sel(lat=ds.lat, lon=ds.lon, method="nearest")
    mask_bool, _ = xr.align(mask == 1, ds, join="exact")
    return mask_bool


def test_focus_area_rejects_reversed_bounds() -> None:
    with pytest.raises(ValidationError):
        FocusArea(lat_min=12, lat_max=8, lon_min=38, lon_max=40)


def test_mask_sits_on_the_obs_grid_and_keeps_only_the_box(tmp_path: Path) -> None:
    obs_dir = tmp_path / "obs"
    obs_dir.mkdir()
    _write_obs(obs_dir / "1998.nc")
    out = materialize_focus_mask(
        {"obs_dir": str(obs_dir), "romp_params": {"focus_area": BOX}}, tmp_path
    )
    with xr.open_dataset(obs_dir / "1998.nc") as obs:
        region = obs.rename(LATITUDE="lat", LONGITUDE="lon").sel(
            lat=slice(5, 14), lon=slice(35, 45)
        )
        kept = _romp_apply(out["romp_params"]["nc_mask"], region)
    assert bool(kept.sel(lat=10.0, lon=39.0))
    assert not bool(kept.sel(lat=8.0, lon=39.0))
    assert not bool(kept.sel(lat=10.0, lon=41.0))


def test_materialize_is_a_no_op_without_a_focus_area(tmp_path: Path) -> None:
    assert materialize_focus_mask({"romp_params": {}}, tmp_path) == {"romp_params": {}}
