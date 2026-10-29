"""Download the shapes ROMP masks regions with, before ROMP starts.

ROMP's worker processes each ask cartopy for the Natural Earth countries
shapefile (momp/utils/land_mask.py:get_shp, its shp_only mask) and regionmask
for Natural Earth's land (mask_land, its land_only mask). Both download what
is missing on first use, writing and unpacking in place, so workers that start
together download over one another and read half-written files: GDAL then
fails to open one, and the run dies with a broken process pool. Fetching both
once here, into the caches every run shares, means the workers always find a
complete copy.

Runs in the benchmark environment, with CARTOPY_DATA_DIR and XDG_CACHE_HOME
(regionmask's cache) set as ROMP sees them.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import cartopy
from cartopy.io import shapereader

# The dataset get_shp loads.
RESOLUTION = "10m"
CATEGORY = "cultural"
NAME = "admin_0_countries"


def main() -> None:
    fetch_borders()
    fetch_land()


def fetch_borders() -> None:
    data_dir = Path(os.environ["CARTOPY_DATA_DIR"])
    target = data_dir / "shapefiles" / "natural_earth" / CATEGORY / f"ne_{RESOLUTION}_{NAME}.shp"
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as staging:
        # Download somewhere no reader looks, then move it into place.
        cartopy.config["pre_existing_data_dir"] = Path(staging)
        cartopy.config["data_dir"] = Path(staging)
        downloaded = Path(
            shapereader.natural_earth(resolution=RESOLUTION, category=CATEGORY, name=NAME)
        )
        # Fails here, rather than in every worker, if the download is incomplete.
        next(iter(shapereader.Reader(downloaded).records()))
        # Readers key on the .shp, so it goes in last, once its sidecars are there.
        parts = sorted(
            downloaded.parent.glob(f"{downloaded.stem}.*"), key=lambda p: p.suffix == ".shp"
        )
        for part in parts:
            os.replace(part, target.with_suffix(part.suffix))
    print(f"==> Downloaded country borders to {target}", flush=True)


def fetch_land() -> None:
    """regionmask's Natural Earth land, read once. A copy a race left half
    unpacked fails to read: it is removed and fetched again."""
    import regionmask
    from regionmask.defined_regions import _natural_earth

    folder = Path(_natural_earth._get_cache_dir()) / "natural_earth" / "v5.0.0"
    present = (folder / "ne_110m_land" / "ne_110m_land.shp").exists()
    try:
        _ = regionmask.defined_regions.natural_earth_v5_0_0.land_110.polygons
    except Exception as exc:  # noqa: BLE001 - any failure to read it is a bad copy
        print(f"==> Natural Earth land unreadable ({exc}); fetching it again", flush=True)
        shutil.rmtree(folder / "ne_110m_land", ignore_errors=True)
        (folder / "ne_110m_land.zip").unlink(missing_ok=True)
        _ = regionmask.defined_regions.natural_earth_v5_0_0.land_110.polygons
        present = False
    if not present:
        print(f"==> Downloaded Natural Earth land to {folder}", flush=True)


if __name__ == "__main__":
    main()
