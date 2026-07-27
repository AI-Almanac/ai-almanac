"""
Generate synthetic Earth2Studio-style forecast and obs files for Bangladesh.

Produces ROMP-compatible NetCDF files that can be used for end-to-end testing
without requiring real model inference or ERA5 data downloads.

Model files (one per year):
  testdata/bangladesh/e2s-test/{year}.nc
  dims: (time=N_init, day=33, lat=8, lon=6)
  variable: tp (meters; unit_cvt=1000 → mm)

Obs files (one per year):
  testdata/bangladesh/obs/{year}.nc
  dims: (TIME=365_or_366, LATITUDE=8, LONGITUDE=6)
  variable: RAINFALL (mm)

Usage (from repo root):
  pixi run python scripts/generate_e2s_test_data.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

# Bangladesh bbox at 1° resolution
LAT = np.arange(20.0, 28.0, 1.0, dtype=np.float32)  # 20..27, 8 points
LON = np.arange(88.0, 94.0, 1.0, dtype=np.float32)  # 88..93, 6 points

YEARS = [2020, 2021, 2022]

# Monsoon season: May–September
SEASON_START_MONTH = 5
SEASON_END_MONTH = 9

# Mon (0) + Thu (3)
INIT_WEEKDAYS = {0, 3}
DATE_FILTER_YEAR = 2020

# Lead days 0..32. ROMP drops step=0 internally, leaving 32 usable lead days.
# The onset detector needs max_forecast_day + wet_spell - 1 steps; defaults are 30 + 3 - 1.
LEAD_DAYS = np.arange(0, 33, dtype=np.int64)

RNG = np.random.default_rng(42)


def _init_dates_for_year(year: int) -> list[pd.Timestamp]:
    start = pd.Timestamp(DATE_FILTER_YEAR, SEASON_START_MONTH, 1)
    end = pd.Timestamp(DATE_FILTER_YEAR, SEASON_END_MONTH, 30)
    dates = pd.date_range(start, end, freq="D")
    filtered = [d for d in dates if d.weekday() in INIT_WEEKDAYS]
    return [pd.Timestamp(year, d.month, d.day) for d in filtered]


def _synthetic_precip(shape: tuple, mean_mm: float = 8.0) -> np.ndarray:
    """Generate non-negative precip values (mm) with realistic spatial correlation."""
    raw = RNG.exponential(scale=mean_mm, size=shape).astype(np.float32)
    # Add day-of-lead decay so later lead days are slightly wetter (adds realism)
    return raw


def write_model_file(year: int, out_dir: Path) -> None:
    init_dates = _init_dates_for_year(year)
    n_init = len(init_dates)
    n_lead = len(LEAD_DAYS)
    n_lat = len(LAT)
    n_lon = len(LON)

    # tp in meters (ROMP unit_cvt=1000 converts to mm)
    tp_mm = _synthetic_precip((n_init, n_lead, n_lat, n_lon))
    tp_m = (tp_mm / 1000.0).astype(np.float32)

    time_coord = pd.DatetimeIndex([d.to_pydatetime() for d in init_dates])

    ds = xr.Dataset(
        {"tp": (["time", "day", "lat", "lon"], tp_m)},
        coords={
            "time": time_coord,
            "day": LEAD_DAYS,
            "lat": LAT,
            "lon": LON,
        },
    )
    ds["tp"].attrs["units"] = "m"
    ds["tp"].attrs["long_name"] = "Total precipitation"

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{year}.nc"
    ds.to_netcdf(path, encoding={"tp": {"zlib": True, "complevel": 4}})
    size_kb = path.stat().st_size // 1024
    print(f"  model  {path} ({size_kb} KB, {n_init} init dates)")


def write_obs_file(year: int, out_dir: Path) -> None:
    days = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    n_days = len(days)
    n_lat = len(LAT)
    n_lon = len(LON)

    rainfall = _synthetic_precip((n_days, n_lat, n_lon), mean_mm=6.0)

    ds = xr.Dataset(
        {"RAINFALL": (["TIME", "LATITUDE", "LONGITUDE"], rainfall)},
        coords={
            "TIME": days,
            "LATITUDE": LAT,
            "LONGITUDE": LON,
        },
    )
    ds["RAINFALL"].attrs["units"] = "mm"
    ds["RAINFALL"].attrs["long_name"] = "Daily rainfall"

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{year}.nc"
    ds.to_netcdf(path, encoding={"RAINFALL": {"zlib": True, "complevel": 4}})
    size_kb = path.stat().st_size // 1024
    print(f"  obs    {path} ({size_kb} KB, {n_days} days)")


def main() -> None:
    repo_root = Path(__file__).parent.parent
    model_dir = repo_root / "testdata" / "bangladesh" / "e2s-test"
    obs_dir = repo_root / "testdata" / "bangladesh" / "obs"

    print("Generating synthetic Bangladesh model files...")
    for yr in YEARS:
        write_model_file(yr, model_dir)

    print("Generating synthetic Bangladesh obs files...")
    for yr in YEARS:
        write_obs_file(yr, obs_dir)

    total_kb = sum(f.stat().st_size for f in repo_root.glob("testdata/bangladesh/**/*.nc")) // 1024
    print(f"\nDone. Total size: {total_kb} KB")
    print("\nAdd to backend/.env:")
    print(f"  BANGLADESH_OBS_DIR={obs_dir}")
    print(f"  BANGLADESH_E2S_TEST_MODEL_DIR={model_dir}")


if __name__ == "__main__":
    sys.exit(main())
