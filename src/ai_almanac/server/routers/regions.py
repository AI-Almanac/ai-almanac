import json
import logging
from typing import Any

import aiohttp
from fastapi import APIRouter, HTTPException

from ai_almanac.server.services.regions import list_region_options
from ai_almanac.settings import get_regions

router = APIRouter(prefix="/regions", tags=["regions"])
logger = logging.getLogger(__name__)

BOUNDARY_LEVELS = {
    "adm1": "ADM1",
    "adm2": "ADM2",
}

_BOUNDARY_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


@router.get("")
async def list_regions() -> list[dict]:
    """Return benchmark regions annotated with locally configured data."""
    return await list_region_options()


@router.get("/{region}/boundaries/{level}")
async def get_boundary(region: str, level: str) -> dict[str, Any]:
    """
    Return simplified geoBoundaries gbOpen GeoJSON for a supported benchmark region.

    The frontend cannot reliably fetch the GitHub-hosted GeoJSON directly because
    of browser CORS restrictions, so the API fetches and caches it server-side.
    """
    region_lower = region.strip().lower()
    region_def = next((r for r in get_regions() if r["id"] == region_lower), None)
    if not region_def or not region_def.get("boundary_iso"):
        raise HTTPException(
            status_code=404, detail=f"No boundary mapping for region {region!r}"
        )
    iso = region_def["boundary_iso"]

    boundary_type = BOUNDARY_LEVELS.get(level.strip().lower())
    if not boundary_type:
        raise HTTPException(
            status_code=404, detail=f"Unsupported boundary level {level!r}"
        )

    cache_key = (iso, boundary_type)
    cached = _BOUNDARY_CACHE.get(cache_key)
    if cached:
        return cached

    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        metadata = await _fetch_json(session, _metadata_url(iso, boundary_type))
        geojson_url = metadata.get("simplifiedGeometryGeoJSON") or metadata.get(
            "gjDownloadURL"
        )
        if not geojson_url:
            raise HTTPException(
                status_code=502,
                detail="geoBoundaries metadata did not include a GeoJSON URL",
            )
        geojson = await _fetch_json(session, geojson_url)

    result = {
        "metadata": {
            "boundaryID": metadata.get("boundaryID"),
            "boundaryName": metadata.get("boundaryName"),
            "boundaryType": metadata.get("boundaryType"),
            "boundarySource": metadata.get("boundarySource"),
            "boundaryLicense": metadata.get("boundaryLicense"),
            "licenseSource": metadata.get("licenseSource"),
        },
        "geojson": geojson,
    }
    _BOUNDARY_CACHE[cache_key] = result
    return result


def _metadata_url(iso: str, boundary_type: str) -> str:
    return f"https://www.geoboundaries.org/api/current/gbOpen/{iso}/{boundary_type}/"


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> dict[str, Any]:
    async with session.get(url) as response:
        body = await response.text()
        if response.status >= 400:
            raise HTTPException(
                status_code=502,
                detail=f"Boundary upstream request failed ({response.status}): {body[:300]}",
            )
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Boundary upstream response was not JSON: {body[:300]}",
            ) from exc
