"""Places a run can be scored in: countries, and the administrative units inside them.

Places are reference data from geoBoundaries. They are looked up, never created.
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ai_almanac.server.auth import OptionalCurrentUser
from ai_almanac.server.services import boundaries
from ai_almanac.server.services.focus_area import parse_country_code

router = APIRouter(prefix="/places", tags=["places"])


class CountryOut(BaseModel):
    iso: str
    name: str


@router.get("/countries", response_model=list[CountryOut])
async def list_countries(_user: OptionalCurrentUser) -> list[dict[str, str]]:
    try:
        return await boundaries.list_countries()
    except boundaries.BoundaryUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/{iso}/boundaries/{level}")
async def get_boundary(iso: str, level: str, _user: OptionalCurrentUser) -> dict[str, Any]:
    """Simplified outlines of a country (adm0) or of the units inside it, fetched and
    cached here because a browser cannot read them from their host directly."""
    try:
        country = parse_country_code(iso)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        return await boundaries.load_boundary(country, level)
    except boundaries.BoundaryUnavailable as exc:
        status = 404 if "Unsupported boundary level" in str(exc) else 502
        raise HTTPException(status_code=status, detail=str(exc)) from exc
