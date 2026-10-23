"""Download the country borders ROMP masks regions with, before ROMP starts.

ROMP's worker processes each ask cartopy for the Natural Earth countries
shapefile (momp/utils/land_mask.py:get_shp). Cartopy downloads it when it is
missing, writing each file in place, and treats the .shp existing as the
download being done. Workers that start together therefore download over one
another and read half-written files. Fetching it once here, into the
CARTOPY_DATA_DIR every run shares, means the workers always find a complete
copy.

Runs in the benchmark environment, with CARTOPY_DATA_DIR set to the shared
directory ROMP reads from.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import cartopy
from cartopy.io import shapereader

# The dataset get_shp loads.
RESOLUTION = "10m"
CATEGORY = "cultural"
NAME = "admin_0_countries"


def main() -> None:
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


if __name__ == "__main__":
    main()
