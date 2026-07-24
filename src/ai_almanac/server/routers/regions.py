import json
import logging
import ssl
from typing import Any

import aiohttp
import certifi
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_almanac.server.auth import AdminUser, CurrentUser, require_data_management
from ai_almanac.server.services import region_catalog
from ai_almanac.server.services.regions import list_region_options

router = APIRouter(prefix="/regions", tags=["regions"])
logger = logging.getLogger(__name__)

# Verify the geoBoundaries TLS cert against certifi's CA bundle rather than the
# ambient system trust store, which some environments (e.g. a bare pixi Python)
# leave unpopulated — ssl.get_default_verify_paths() returns nothing there and
# every HTTPS fetch fails with CERTIFICATE_VERIFY_FAILED.
_BOUNDARY_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

BOUNDARY_LEVELS = {
    "adm1": "ADM1",
    "adm2": "ADM2",
    "adm3": "ADM3",
}

_BOUNDARY_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


class RegionWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, allow_inf_nan=False)

    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    lat_min: float = Field(ge=-90, le=90)
    lat_max: float = Field(ge=-90, le=90)
    lon_min: float = Field(ge=-180, le=180)
    lon_max: float = Field(ge=-180, le=180)
    land_only: bool = False

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.lat_min >= self.lat_max:
            raise ValueError("lat_min must be less than lat_max")
        if self.lon_min >= self.lon_max:
            raise ValueError("lon_min must be less than lon_max")
        return self


@router.get("")
async def list_regions(_user: CurrentUser) -> list[dict]:
    """Return benchmark regions annotated with locally configured data."""
    return await list_region_options()


@router.post("", status_code=201, dependencies=[Depends(require_data_management)])
async def create_region(body: RegionWrite, _admin: AdminUser) -> dict:
    await region_catalog.seed_packaged_regions()
    region = await region_catalog.create_region(**body.model_dump())
    return {
        **region,
        "romp_region": "custom",
        "has_data": False,
        "source_count": 0,
    }


@router.put("/{region_id}", dependencies=[Depends(require_data_management)])
async def update_region(region_id: str, body: RegionWrite, _admin: AdminUser) -> dict:
    await region_catalog.seed_packaged_regions()
    existing = await region_catalog.get_region(region_id)
    if not existing:
        raise HTTPException(status_code=404, detail="region not found")
    if existing["is_builtin"]:
        raise HTTPException(status_code=403, detail="built-in regions cannot be edited")
    region = await region_catalog.update_region(region_id, **body.model_dump())
    if not region:
        raise HTTPException(status_code=404, detail="region not found")
    source_count = await region_catalog.count_region_sources(region_id)
    return {
        **region,
        "romp_region": "custom",
        "has_data": source_count > 0,
        "source_count": source_count,
    }


@router.delete("/{region_id}", status_code=204, dependencies=[Depends(require_data_management)])
async def delete_region(region_id: str, _admin: AdminUser) -> None:
    await region_catalog.seed_packaged_regions()
    existing = await region_catalog.get_region(region_id)
    if not existing:
        raise HTTPException(status_code=404, detail="region not found")
    if existing["is_builtin"]:
        raise HTTPException(status_code=403, detail="built-in regions cannot be removed")
    source_count = await region_catalog.count_region_sources(region_id)
    if source_count:
        raise HTTPException(
            status_code=409,
            detail=f"region is used by {source_count} data source(s)",
        )
    if not await region_catalog.delete_region(region_id):
        raise HTTPException(status_code=404, detail="region not found")


@router.get("/{region}/boundaries/{level}")
async def get_boundary(region: str, level: str, _user: CurrentUser) -> dict[str, Any]:
    """
    Return simplified geoBoundaries gbOpen GeoJSON for a supported benchmark region.

    The frontend cannot reliably fetch the GitHub-hosted GeoJSON directly because
    of browser CORS restrictions, so the API fetches and caches it server-side.
    """
    region_def = await region_catalog.get_region(region.strip())
    if not region_def or not region_def.get("boundary_iso"):
        raise HTTPException(status_code=404, detail=f"No boundary mapping for region {region!r}")
    iso = region_def["boundary_iso"]

    boundary_type = BOUNDARY_LEVELS.get(level.strip().lower())
    if not boundary_type:
        raise HTTPException(status_code=404, detail=f"Unsupported boundary level {level!r}")

    cache_key = (iso, boundary_type)
    cached = _BOUNDARY_CACHE.get(cache_key)
    if cached:
        return cached

    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=_BOUNDARY_SSL_CONTEXT)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        metadata = await _fetch_json(session, _metadata_url(iso, boundary_type))
        geojson_url = metadata.get("simplifiedGeometryGeoJSON") or metadata.get("gjDownloadURL")
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
