"""Small files of the kinds a workspace holds."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr


def write_nc(path: Path, seed: float = 1.0) -> Path:
    ds = xr.Dataset(
        {"far": (("lat", "lon"), np.full((3, 4), seed), {"units": "1"})},
        coords={"lat": [1.0, 2.0, 3.0], "lon": [10.0, 11.0, 12.0, 13.0]},
    )
    ds.to_netcdf(path, engine="h5netcdf")
    return path


def write_png(path: Path) -> Path:
    from PIL import Image

    Image.new("RGB", (4, 4), (10, 20, 30)).save(path, format="PNG")
    return path


def write_season_nc(path: Path) -> Path:
    times = np.array(["2026-07-01", "2026-07-08"], dtype="datetime64[ns]")
    tp = np.stack([np.full((3, 4), 1.0), np.full((3, 4), 5.0)])
    xr.Dataset(
        {"tp": (("time", "lat", "lon"), tp)},
        coords={"time": times, "lat": [1.0, 2.0, 3.0], "lon": [10.0, 11.0, 12.0, 13.0]},
    ).to_netcdf(path, engine="h5netcdf")
    return path
