"""Copy geoBoundaries outlines into a data bucket for deployments that cannot reach
geoBoundaries (see ``ai_almanac.server.services.boundary_copies``).

Run it from a machine that can reach geoBoundaries, with credentials that can
write to the bucket. It copies the country list, then every level of each named
country, or of every packaged region's country when none is named. Re-run it to
refresh the copies; deployments pick them up as their instances restart.

Usage:
    pixi run python scripts/copy_boundaries.py almanac-data-ai-almanac
    pixi run python scripts/copy_boundaries.py almanac-data-ai-almanac KEN UGA
"""

from __future__ import annotations

import asyncio
import sys

from ai_almanac.server.services import boundaries, boundary_copies
from ai_almanac.server.services.focus_area import parse_country_code
from ai_almanac.settings import get_packaged_regions


def region_countries() -> list[str]:
    return sorted({r["boundary_iso"] for r in get_packaged_regions() if r.get("boundary_iso")})


async def copy_country(root: str, iso: str) -> list[str]:
    """Copies every level of a country; returns the levels left uncopied."""
    skipped: list[str] = []
    for boundary_type in boundaries.BOUNDARY_LEVELS.values():
        try:
            boundary = await boundaries.fetch_boundary(iso, boundary_type)
        except boundaries.BoundaryUnreachable:
            # Every other level would wait out the same timeouts.
            raise
        except boundaries.BoundaryUnavailable as exc:
            print(f"{iso} {boundary_type}: not copied, {exc}")
            skipped.append(f"{iso} {boundary_type}")
            continue
        boundary_copies.write(boundary_copies.boundary_path(root, iso, boundary_type), boundary)
        print(f"{iso} {boundary_type}: {len(boundary['geojson']['features'])} areas copied")
    return skipped


async def main(bucket: str, wanted: list[str]) -> None:
    root = boundary_copies.root_in(bucket)
    countries = await boundaries.fetch_countries()
    boundary_copies.write(boundary_copies.countries_path(root), countries)
    print(f"{len(countries)} countries copied to {root}")
    # Not checked against that list: it leaves out some countries, India among them,
    # whose outlines geoBoundaries still has.
    skipped: list[str] = []
    for iso in [parse_country_code(code) for code in wanted] or region_countries():
        skipped += await copy_country(root, iso)
    # Not a failure: geoBoundaries publishes no subdistricts for some countries.
    print(f"Not copied: {', '.join(skipped)}" if skipped else "Every level was copied")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    try:
        asyncio.run(main(sys.argv[1], sys.argv[2:]))
    except boundaries.BoundaryUnreachable as exc:
        sys.exit(f"Stopped, nothing more was copied: {exc}")
