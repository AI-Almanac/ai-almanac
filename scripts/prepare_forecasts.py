"""
Clip annual model forecast files to a regional bounding box.

Reads ROMP-format annual NetCDF files (dims: time, day, lat, lon) and writes
spatially clipped copies to an output directory. The clip region is looked up
from backend/app/config/regions.yaml. A configurable buffer (default 1°) is
added around the bbox before clipping so boundary grid cells are retained.

Usage:
  cd backend && uv run python ../scripts/prepare_forecasts.py \\
      --region bangladesh \\
      --input /path/to/global/model/forecasts \\
      --output /path/to/regional/output \\
      [--buffer 1.0]

The input directory should contain annual files named {year}.nc.
"""

import argparse
import sys
from pathlib import Path

import xarray as xr
import yaml


def _load_regions(regions_yaml: Path) -> dict[str, dict]:
    raw = yaml.safe_load(regions_yaml.read_text())
    return {r["id"]: r for r in raw}


def clip_file(src: Path, dst: Path, lat_min: float, lat_max: float, lon_min: float, lon_max: float) -> None:
    ds = xr.open_dataset(src)

    lat_dim = next((d for d in ds.dims if d in ("lat", "latitude", "Latitude")), None)
    lon_dim = next((d for d in ds.dims if d in ("lon", "longitude", "Longitude")), None)
    if lat_dim is None or lon_dim is None:
        raise ValueError(f"Could not identify lat/lon dims in {src}. Found: {list(ds.dims)}")

    ds_clipped = ds.sel(
        {lat_dim: slice(lat_min, lat_max), lon_dim: slice(lon_min, lon_max)},
    )

    if ds_clipped.sizes[lat_dim] == 0 or ds_clipped.sizes[lon_dim] == 0:
        raise ValueError(
            f"Clip result is empty for bbox lat=[{lat_min},{lat_max}] lon=[{lon_min},{lon_max}]. "
            f"Source lat range: {float(ds[lat_dim].min()):.2f}–{float(ds[lat_dim].max()):.2f}, "
            f"lon range: {float(ds[lon_dim].min()):.2f}–{float(ds[lon_dim].max()):.2f}"
        )

    dst.parent.mkdir(parents=True, exist_ok=True)
    encoding = {v: {"zlib": True, "complevel": 4} for v in ds_clipped.data_vars}
    ds_clipped.to_netcdf(dst, encoding=encoding)
    ds.close()

    src_kb = src.stat().st_size // 1024
    dst_kb = dst.stat().st_size // 1024
    lat_n = ds_clipped.sizes[lat_dim]
    lon_n = ds_clipped.sizes[lon_dim]
    print(f"  {src.name}: {src_kb} KB → {dst_kb} KB  ({lat_n}×{lon_n} grid)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--region", required=True, help="Region id from regions.yaml (e.g. bangladesh)")
    parser.add_argument("--input", required=True, help="Directory containing annual {year}.nc forecast files")
    parser.add_argument("--output", required=True, help="Output directory for clipped files")
    parser.add_argument("--buffer", type=float, default=1.0, help="Degrees to add around bbox (default: 1.0)")
    args = parser.parse_args()

    repo_root = Path(__file__).parent.parent
    regions_yaml = repo_root / "backend" / "app" / "config" / "regions.yaml"
    if not regions_yaml.exists():
        print(f"ERROR: {regions_yaml} not found", file=sys.stderr)
        return 1

    regions = _load_regions(regions_yaml)
    if args.region not in regions:
        print(f"ERROR: region '{args.region}' not found in regions.yaml", file=sys.stderr)
        print(f"Available: {', '.join(regions)}", file=sys.stderr)
        return 1

    region = regions[args.region]
    lat_min = region["lat_min"] - args.buffer
    lat_max = region["lat_max"] + args.buffer
    lon_min = region["lon_min"] - args.buffer
    lon_max = region["lon_max"] + args.buffer

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    nc_files = sorted(input_dir.glob("*.nc"))
    if not nc_files:
        print(f"ERROR: no .nc files found in {input_dir}", file=sys.stderr)
        return 1

    print(f"Region: {region['display_name']} — lat=[{lat_min},{lat_max}] lon=[{lon_min},{lon_max}] (buffer={args.buffer}°)")
    print(f"Input:  {input_dir}  ({len(nc_files)} files)")
    print(f"Output: {output_dir}")

    for src in nc_files:
        dst = output_dir / src.name
        clip_file(src, dst, lat_min, lat_max, lon_min, lon_max)

    print(f"\nDone. Clipped {len(nc_files)} files → {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
