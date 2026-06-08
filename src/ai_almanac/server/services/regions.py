from __future__ import annotations

from ai_almanac.server.services.data_sources import get_obs_sources
from ai_almanac.settings import get_regions


def normalize_region_id(region: str | None) -> str | None:
    if region is None:
        return None
    normalized = region.strip().lower()
    return normalized or None


async def list_region_options() -> list[dict]:
    sources = await get_obs_sources()
    configured_regions = {
        region_id
        for source in sources
        if source.get("status") == "ready"
        if (region_id := normalize_region_id(source.get("region")))
    }
    return [
        {
            "id": region["id"],
            "display_name": region["display_name"],
            "romp_region": region.get("romp_name", "custom"),
            "description": region.get("description", ""),
            "has_data": region["id"] in configured_regions,
        }
        for region in get_regions()
    ]
