import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

from ai_almanac.server.auth import OptionalCurrentUser
from ai_almanac.server.compressed_json import gzipped_json_response
from ai_almanac.server.services import boundaries, region_catalog
from ai_almanac.server.services.regions import list_region_options

router = APIRouter(prefix="/regions", tags=["regions"])
logger = logging.getLogger(__name__)


@router.get("")
async def list_regions(_user: OptionalCurrentUser) -> list[dict]:
    """Return benchmark regions annotated with locally configured data."""
    return await list_region_options()


@router.get("/{region}/boundaries/{level}", response_model=dict[str, Any])
async def get_boundary(
    region: str, level: str, request: Request, _user: OptionalCurrentUser
) -> Response:
    """
    Return simplified geoBoundaries GeoJSON (gbHumanitarian, else gbOpen) for a region.

    The frontend cannot reliably fetch the GitHub-hosted GeoJSON directly because
    of browser CORS restrictions, so the API fetches and caches it server-side.
    """
    region_def = await region_catalog.get_region(region.strip())
    if not region_def or not region_def.get("boundary_iso"):
        raise HTTPException(status_code=404, detail=f"No boundary mapping for region {region!r}")
    try:
        body = await boundaries.load_encoded_boundary(region_def["boundary_iso"], level)
    except boundaries.BoundaryUnavailable as exc:
        status = 404 if "Unsupported boundary level" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    return gzipped_json_response(body, request)
