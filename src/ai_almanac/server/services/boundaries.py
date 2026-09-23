"""Administrative boundaries from geoBoundaries, cached per country and level.

The frontend cannot fetch the GitHub-hosted GeoJSON directly because of browser
CORS restrictions, so the API fetches and caches it here. The same loader
attaches selected outlines to an area of interest at submission time, so a job
carries the exact shapes it was scored on.
"""

from __future__ import annotations

import json
import re
import ssl
import unicodedata
from typing import Any

import aiohttp
import certifi

from ai_almanac.server.services.focus_area import FocusArea, FocusUnits

# aiohttp uses the system CA store, which some container images leave empty;
# certifi's bundle makes the geoBoundaries fetch work everywhere.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

BOUNDARY_LEVELS = {
    "adm0": "ADM0",
    "adm1": "ADM1",
    "adm2": "ADM2",
    "adm3": "ADM3",
}

# The humanitarian release mirrors OCHA's Common Operational Datasets, which the
# blend pipeline's administrative units are drawn from (Ethiopia: 1082 woredas,
# towns included), so its names line up with blend outputs. gbOpen covers the
# countries the COD set does not.
_RELEASES = ("gbHumanitarian", "gbOpen")

_NAME_FIELDS = ("shapeName", "ADM2_EN", "ADM3_EN", "adm3_name", "name", "NAME")

_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


class BoundaryUnavailable(RuntimeError):
    """geoBoundaries could not supply this country and level."""


async def load_boundary(iso: str, level: str) -> dict[str, Any]:
    """Return ``{"metadata": ..., "geojson": ...}`` for a country and admin level."""
    boundary_type = BOUNDARY_LEVELS.get(level.strip().lower())
    if not boundary_type:
        raise BoundaryUnavailable(f"Unsupported boundary level {level!r}")
    cache_key = (iso, boundary_type)
    cached = _CACHE.get(cache_key)
    if cached:
        return cached
    timeout = aiohttp.ClientTimeout(total=30)
    connector = aiohttp.TCPConnector(ssl=_SSL_CONTEXT)
    async with aiohttp.ClientSession(timeout=timeout, connector=connector) as session:
        metadata = await _fetch_metadata(session, iso, boundary_type)
        geojson = await _fetch_json(session, _geojson_url(metadata) or "")
    result = {
        "metadata": {
            key: metadata.get(key)
            for key in (
                "boundaryID",
                "boundaryName",
                "boundaryType",
                "boundarySource",
                "boundaryLicense",
                "licenseSource",
            )
        },
        "geojson": geojson,
    }
    _CACHE[cache_key] = result
    return result


def normalize_area_name(value: str) -> str:
    """Match the frontend's normalisation so a name picked on the map resolves here."""
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = re.sub(r"['’`]", "", text)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def unit_name(feature: dict) -> str | None:
    properties = feature.get("properties") or {}
    for field in _NAME_FIELDS:
        value = properties.get(field)
        if isinstance(value, str) and value.strip():
            return value
    return None


def select_units(geojson: dict, names: list[str]) -> dict:
    """The FeatureCollection of just the named units; unknown names are an error."""
    wanted = {normalize_area_name(name): name for name in names}
    features = []
    for feature in geojson.get("features") or []:
        name = unit_name(feature)
        if name is not None and normalize_area_name(name) in wanted:
            features.append(
                {"type": "Feature", "properties": {"name": name}, "geometry": feature["geometry"]}
            )
            wanted.pop(normalize_area_name(name))
    if wanted:
        raise ValueError(f"Unknown areas: {', '.join(sorted(wanted.values()))}")
    return {"type": "FeatureCollection", "features": features}


async def attach_unit_outlines(area: FocusArea, region_def: dict | None) -> FocusArea:
    """Freeze the picked units' outlines into the area so the run is reproducible."""
    if not isinstance(area, FocusUnits):
        return area
    iso = (region_def or {}).get("boundary_iso")
    if not iso:
        raise ValueError("This region has no administrative boundaries to pick areas from")
    boundary = await load_boundary(iso, area.level)
    return area.model_copy(update={"geometry": select_units(boundary["geojson"], area.units)})


def _metadata_url(iso: str, boundary_type: str, release: str) -> str:
    return f"https://www.geoboundaries.org/api/current/{release}/{iso}/{boundary_type}/"


def _geojson_url(metadata: Any) -> str | None:
    if not isinstance(metadata, dict):
        return None
    return metadata.get("simplifiedGeometryGeoJSON") or metadata.get("gjDownloadURL")


async def _fetch_metadata(
    session: aiohttp.ClientSession, iso: str, boundary_type: str
) -> dict[str, Any]:
    failures: list[str] = []
    for release in _RELEASES:
        try:
            metadata = await _fetch_json(session, _metadata_url(iso, boundary_type, release))
        except BoundaryUnavailable as exc:
            failures.append(str(exc))
            continue
        if _geojson_url(metadata):
            return metadata
        failures.append(f"{release} has no GeoJSON for {iso} {boundary_type}")
    raise BoundaryUnavailable("; ".join(failures))


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Any:
    async with session.get(url) as response:
        body = await response.text()
        if response.status >= 400:
            raise BoundaryUnavailable(
                f"Boundary upstream request failed ({response.status}): {body[:300]}"
            )
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise BoundaryUnavailable(
                f"Boundary upstream response was not JSON: {body[:300]}"
            ) from exc
