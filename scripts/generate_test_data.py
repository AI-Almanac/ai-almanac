"""
Subset real ROMP data files into small test fixtures for local development.

Reads from ~/code/ROMP/data/ethiopia and writes spatially-subsetted copies
(central Ethiopian highlands, 5x5 grid) to testdata/ at the repo root.

Output:
  testdata/ethiopia/obs/{1998,1999,2000}.nc   ~3 KB each
  testdata/ethiopia/fuxi/{1998,1999,2000}.nc  ~10 KB each

Usage (from repo root):
  pixi run python scripts/generate_test_data.py
"""

from pathlib import Path

import xarray as xr

ROMP_DATA = Path.home() / "code" / "ROMP" / "data" / "ethiopia"
YEARS = [1998, 1999, 2000]

# Central Ethiopian highlands — inside Ethiopia's shapefile boundary and on land.
LAT_SLICE = slice(8.0, 9.0)
LON_SLICE = slice(38.0, 39.0)


def subset_obs(year: int, out_dir: Path) -> None:
    src = ROMP_DATA / "obs" / f"{year}.nc"
    ds = xr.open_dataset(src)
    # dim_fmt in ROMP renames LATITUDE/LONGITUDE to lat/lon before region_select,
    # so we subset using the original coord names here.
    ds_sub = ds.sel(LATITUDE=LAT_SLICE, LONGITUDE=LON_SLICE)
    out_dir.mkdir(parents=True, exist_ok=True)
    ds_sub.to_netcdf(out_dir / f"{year}.nc")
    size = (out_dir / f"{year}.nc").stat().st_size // 1024
    print(f"  wrote {out_dir / f'{year}.nc'} ({size} KB)")


def subset_model(year: int, out_dir: Path) -> None:
    src = ROMP_DATA / "fuxi" / f"{year}.nc"
    ds = xr.open_dataset(src)
    ds_sub = ds.sel(lat=LAT_SLICE, lon=LON_SLICE)
    out_dir.mkdir(parents=True, exist_ok=True)
    ds_sub.to_netcdf(out_dir / f"{year}.nc")
    size = (out_dir / f"{year}.nc").stat().st_size // 1024
    print(f"  wrote {out_dir / f'{year}.nc'} ({size} KB)")


def main() -> None:
    if not ROMP_DATA.exists():
        print(f"ERROR: {ROMP_DATA} not found.")
        print("This script requires access to the real ROMP data.")
        print("Ask a team member for the data or check the project Box/Drive folder.")
        raise SystemExit(1)

    repo_root = Path(__file__).parent.parent
    obs_dir = repo_root / "testdata" / "ethiopia" / "obs"
    fuxi_dir = repo_root / "testdata" / "ethiopia" / "fuxi"

    print("Subsetting obs files...")
    for yr in YEARS:
        subset_obs(yr, obs_dir)

    print("Subsetting FuXi model files...")
    for yr in YEARS:
        subset_model(yr, fuxi_dir)

    total_kb = sum(f.stat().st_size for f in repo_root.glob("testdata/**/*.nc")) // 1024
    print(f"\nDone. Total size: {total_kb} KB")
    print("\nAdd to backend/.env:")
    print(f"  TEST_ETHIOPIA_OBS_DIR={obs_dir}")
    print(f"  TEST_FUXI_TEST_MODEL_DIR={fuxi_dir}")


if __name__ == "__main__":
    main()
