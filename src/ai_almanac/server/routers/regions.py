import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_almanac.server.auth import AdminUser, OptionalCurrentUser, require_data_management
from ai_almanac.server.services import boundaries, region_catalog
from ai_almanac.server.services.regions import list_region_options

router = APIRouter(prefix="/regions", tags=["regions"])
logger = logging.getLogger(__name__)


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
async def list_regions(_user: OptionalCurrentUser) -> list[dict]:
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
async def get_boundary(region: str, level: str, _user: OptionalCurrentUser) -> dict[str, Any]:
    """
    Return simplified geoBoundaries gbOpen GeoJSON for a supported benchmark region.

    The frontend cannot reliably fetch the GitHub-hosted GeoJSON directly because
    of browser CORS restrictions, so the API fetches and caches it server-side.
    """
    region_def = await region_catalog.get_region(region.strip())
    if not region_def or not region_def.get("boundary_iso"):
        raise HTTPException(status_code=404, detail=f"No boundary mapping for region {region!r}")
    try:
        return await boundaries.load_boundary(region_def["boundary_iso"], level)
    except boundaries.BoundaryUnavailable as exc:
        status = 404 if "Unsupported boundary level" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
