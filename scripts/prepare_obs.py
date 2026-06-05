"""
Fetch ERA5 daily precipitation from ARCO and write ROMP-format annual obs files.

Reads from the public ARCO ERA5 Zarr store on GCS, aggregates hourly → daily
totals for a bounding box, and writes annual NetCDF files with the ROMP obs
schema (dims: TIME × LATITUDE × LONGITUDE, variable: RAINFALL in mm).

The spatial bbox is read from backend/app/config/regions.yaml. A configurable
buffer (default 1°) is added around the bbox.

Usage:
  cd backend && uv run python ../scripts/prepare_obs.py \\
      --region bangladesh \\
      --years 2020 2021 2022 \\
      --output /path/to/obs/output \\
      [--buffer 1.0]

Requires GCS access (gcloud auth application-default login or a service account).
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import xarray as xr
import yaml

ARCO_URL = "gs://gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3"
PRECIP_VAR = "total_precipitation"
UNIT_CVT = 1000.0  # m → mm


def _load_region(regions_yaml: Path, region_id: str) -> dict:
    raw = yaml.safe_load(regions_yaml.read_text())
    for r in raw:
        if r["id"] == region_id:
            return r
    available = [r["id"] for r in raw]
    raise ValueError(f"Region '{region_id}' not found. Available: {available}")


def fetch_year(year: int, lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> xr.DataArray:
    """Fetch hourly ERA5 precip for one calendar year, aggregate to daily totals."""
    print(f"  Opening ARCO store for {year}...", flush=True)
    ds = xr.open_zarr(ARCO_URL, consolidated=True)

    da = ds[PRECIP_VAR].sel(
        time=slice(f"{year}-01-01", f"{year}-12-31T23:00"),
        latitude=slice(lat_max, lat_min),  # ARCO stores lat descending
        longitude=slice(lon_min, lon_max),
    )

    print(f"  Loading {year} ({dict(da.sizes)})...", flush=True)
    da = da.load()
    ds.close()
    del ds

    # Aggregate hourly → daily (mm/day = sum of hourly m/h values × 1000)
    daily = da.resample(time="1D").sum() * UNIT_CVT
    daily = daily.astype(np.float32)
    daily = daily.rename({"time": "TIME", "latitude": "LATITUDE", "longitude": "LONGITUDE"})
    daily.name = "RAINFALL"
    daily.attrs["units"] = "mm"
    daily.attrs["long_name"] = "Daily precipitation"
    return daily


def write_obs_file(daily: xr.DataArray, year: int, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{year}.nc"
    ds = daily.to_dataset()
    ds.to_netcdf(path, encoding={"RAINFALL": {"zlib": True, "complevel": 4}})
    size_kb = path.stat().st_size // 1024
    print(f"  Wrote {path} ({size_kb} KB, {daily.sizes['TIME']} days)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", required=True, help="Region id from regions.yaml")
    parser.add_argument("--years", nargs="+", type=int, required=True, help="Years to fetch (e.g. 2020 2021 2022)")
    parser.add_argument("--output", required=True, help="Output directory for obs files")
    parser.add_argument("--buffer", type=float, default=1.0, help="Degrees to add around bbox (default: 1.0)")
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    regions_yaml = repo_root / "backend" / "app" / "config" / "regions.yaml"
    if not regions_yaml.exists():
        print(f"ERROR: {regions_yaml} not found", file=sys.stderr)
        return 1

    region = _load_region(regions_yaml, args.region)
    lat_min = region["lat_min"] - args.buffer
    lat_max = region["lat_max"] + args.buffer
    lon_min = region["lon_min"] - args.buffer
    lon_max = region["lon_max"] + args.buffer

    output_dir = Path(args.output)
    print(f"Region: {region['display_name']} — lat=[{lat_min},{lat_max}] lon=[{lon_min},{lon_max}] (buffer={args.buffer}°)")
    print(f"Years:  {args.years}")
    print(f"Output: {output_dir}")
    print()

    for year in sorted(args.years):
        try:
            daily = fetch_year(year, lat_min, lat_max, lon_min, lon_max)
            write_obs_file(daily, year, output_dir)
            del daily
        except Exception as exc:
            print(f"ERROR processing year {year}: {exc}", file=sys.stderr)
            return 1

    print(f"\nDone. Wrote {len(args.years)} obs files → {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
