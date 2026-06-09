from __future__ import annotations

from ai_almanac.server.services.data_sources import list_sources
from ai_almanac.server.services.region_catalog import (
    list_regions as list_catalog_regions,
)
from ai_almanac.server.services.region_catalog import (
    seed_packaged_regions,
)


def normalize_region_id(region: str | None) -> str | None:
    if region is None:
        return None
    normalized = region.strip().lower()
    return normalized or None


async def list_region_options() -> list[dict]:
    await seed_packaged_regions()
    sources = await list_sources()
    source_counts: dict[str, int] = {}
    ready_regions: set[str] = set()
    for source in sources:
        region_id = normalize_region_id(source.get("region"))
        if not region_id:
            continue
        source_counts[region_id] = source_counts.get(region_id, 0) + 1
        if source["kind"] == "obs" and source.get("status") == "ready":
            ready_regions.add(region_id)

    return [
        {
            **region,
            "romp_region": region.get("romp_name") or "custom",
            "has_data": region["id"] in ready_regions,
            "source_count": source_counts.get(region["id"], 0),
        }
        for region in await list_catalog_regions()
    ]
