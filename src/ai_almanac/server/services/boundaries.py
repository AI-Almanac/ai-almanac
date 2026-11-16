"""Administrative boundaries from geoBoundaries, cached per country and level.

The frontend cannot fetch the GitHub-hosted GeoJSON directly because of browser
CORS restrictions, so the API fetches and caches it here. The same loader
attaches selected outlines to an area of interest at submission time, so a job
carries the exact shapes it was scored on. A deployment reads the copies kept in
its data bucket (``boundary_copies``) before asking geoBoundaries.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import ssl
import unicodedata
from typing import Any, Literal
from urllib.parse import urlsplit

import aiohttp
import certifi
import numpy as np

from ai_almanac.server.compressed_json import gzipped_json
from ai_almanac.server.services import boundary_copies
from ai_almanac.server.services.focus_area import (
    FocusArea,
    FocusUnits,
    points_inside,
    polygons_of,
)
from ai_almanac.settings import get_packaged_regions

logger = logging.getLogger(__name__)

# aiohttp uses the system CA store, which some container images leave empty;
# certifi's bundle makes the geoBoundaries fetch work everywhere.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

BOUNDARY_LEVELS = {
    "adm0": "ADM0",
    "adm1": "ADM1",
    "adm2": "ADM2",
    "adm3": "ADM3",
}

# States or regions, their districts or zones, and the subdistricts or woredas inside those.
AreaLevel = Literal["adm1", "adm2", "adm3"]
PARENT_LEVEL: dict[str, str] = {"adm2": "adm1", "adm3": "adm2"}

# The humanitarian release mirrors OCHA's Common Operational Datasets, which the
# blend pipeline's administrative units are drawn from (Ethiopia: 1082 woredas,
# towns included), so its names line up with blend outputs. gbOpen covers the
# countries the COD set does not.
_RELEASES = ("gbHumanitarian", "gbOpen")

_NAME_FIELDS = ("shapeName", "ADM2_EN", "ADM3_EN", "adm3_name", "name", "NAME")

_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_ENCODED: dict[tuple[str, str], bytes] = {}
_ENCODING: dict[tuple[str, str], asyncio.Lock] = {}


_COUNTRIES: list[dict[str, str]] = []


class BoundaryUnavailable(RuntimeError):
    """geoBoundaries could not supply this country and level."""


class BoundaryUnreachable(BoundaryUnavailable):
    """geoBoundaries could not be reached at all, so asking it anything else would wait
    just as long."""


def countries_from(listing: object) -> list[dict[str, str]]:
    """Each country in a geoBoundaries listing once, as its code and name, by name."""
    entries = listing if isinstance(listing, list) else []
    named = {
        str(entry["boundaryISO"]).upper(): str(entry["boundaryName"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("boundaryISO") and entry.get("boundaryName")
    }
    return sorted(
        ({"iso": iso, "name": name} for iso, name in named.items()), key=lambda c: c["name"]
    )


def match_countries(countries: list[dict[str, str]], wanted: str) -> list[dict[str, str]]:
    """The countries a name or three-letter code points at: the one it names exactly,
    else every country whose name contains it."""
    needle = wanted.strip().casefold()
    if not needle:
        return []
    exact = [
        country
        for country in countries
        if needle in (country["iso"].casefold(), country["name"].casefold())
    ]
    return exact or [country for country in countries if needle in country["name"].casefold()]


def copied_countries(copy: object) -> list[dict[str, str]]:
    """The countries in a copy of the list that ``list_countries`` returns."""
    entries = copy if isinstance(copy, list) else []
    return [
        {"iso": str(entry["iso"]), "name": str(entry["name"])}
        for entry in entries
        if isinstance(entry, dict) and entry.get("iso") and entry.get("name")
    ]


def copied_boundary(copy: object) -> dict[str, Any] | None:
    """A copy of what ``load_boundary`` returns, or None if it is not one."""
    if not isinstance(copy, dict):
        return None
    metadata, geojson = copy.get("metadata"), copy.get("geojson")
    if not isinstance(metadata, dict) or not isinstance(geojson, dict):
        return None
    if not isinstance(geojson.get("features"), list):
        return None
    return {"metadata": metadata, "geojson": geojson}


def with_region_countries(
    countries: list[dict[str, str]], regions: list[dict]
) -> list[dict[str, str]]:
    """The listed countries and each region's country, by name. geoBoundaries' listing
    of every country leaves some out, India among them, whose outlines it still has."""
    named = {
        str(region["boundary_iso"]): str(region.get("display_name") or region["boundary_iso"])
        for region in regions
        if region.get("boundary_iso")
    }
    named.update({country["iso"]: country["name"] for country in countries})
    return sorted(
        ({"iso": iso, "name": name} for iso, name in named.items()), key=lambda c: c["name"]
    )


async def list_countries() -> list[dict[str, str]]:
    """Every country a place can be picked in."""
    if _COUNTRIES:
        return _COUNTRIES
    listed = await _read_copied_countries() or await fetch_countries()
    # Replaced, not extended: two first requests may both have fetched the listing.
    _COUNTRIES[:] = with_region_countries(listed, get_packaged_regions())
    return _COUNTRIES


async def fetch_countries() -> list[dict[str, str]]:
    """Every country, as geoBoundaries lists them now."""
    async with _session() as session:
        # gbOpen is the release that covers every country.
        listing = await _fetch_json(session, _metadata_url("ALL", "ADM0", "gbOpen"))
    countries = countries_from(listing)
    if not countries:
        raise BoundaryUnavailable("The boundary service listed no countries")
    return countries


async def is_listed_country(iso: str) -> bool:
    """Whether the boundary service lists a country by this code. Asked before fetching
    a country's outlines, so a made-up code costs no request of its own."""
    return any(country["iso"] == iso for country in await list_countries())


async def load_boundary(iso: str, level: str) -> dict[str, Any]:
    """Return ``{"metadata": ..., "geojson": ...}`` for a country and admin level."""
    boundary_type = _boundary_type(level)
    cache_key = (iso, boundary_type)
    cached = _CACHE.get(cache_key)
    if cached:
        return cached
    boundary = await _read_copied_boundary(iso, boundary_type) or await fetch_boundary(
        iso, boundary_type
    )
    _CACHE[cache_key] = boundary
    return boundary


async def load_encoded_boundary(iso: str, level: str) -> bytes:
    """What ``load_boundary`` returns, as gzipped JSON encoded once per country and
    level: a country's smallest units run to tens of megabytes of JSON."""
    cache_key = (iso, _boundary_type(level))
    if (encoded := _ENCODED.get(cache_key)) is not None:
        return encoded
    # One encoding per outline, however many first requests arrive together.
    async with _ENCODING.setdefault(cache_key, asyncio.Lock()):
        if (encoded := _ENCODED.get(cache_key)) is None:
            boundary = await load_boundary(iso, level)
            encoded = await asyncio.to_thread(gzipped_json, boundary)
            _ENCODED[cache_key] = encoded
    return encoded


def _boundary_type(level: str) -> str:
    boundary_type = BOUNDARY_LEVELS.get(level.strip().lower())
    if not boundary_type:
        raise BoundaryUnavailable(f"Unsupported boundary level {level!r}")
    return boundary_type


async def fetch_boundary(iso: str, boundary_type: str) -> dict[str, Any]:
    """A country's outlines at one level (``ADM1``), as geoBoundaries publishes them now."""
    async with _session() as session:
        metadata = await _fetch_metadata(session, iso, boundary_type)
        geojson = await _fetch_json(session, _geojson_url(metadata) or "")
    return {
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


def name_areas(units: dict, parents: dict | None = None) -> list[dict]:
    """Each unit's name, and with ``parents`` the name of the parent unit holding it.

    geoBoundaries features carry no parent name, so a unit's parent is the one
    containing a point inside the unit; a unit no parent contains gets None.
    """
    features = [feature for feature in units.get("features") or [] if unit_name(feature)]
    areas = [{"name": unit_name(feature)} for feature in features]
    if parents is None or not features:
        return areas
    points = np.array([interior_point(feature.get("geometry") or {}) for feature in features])
    parent_of: list[str | None] = [None] * len(features)
    for parent in parents.get("features") or []:
        hits = points_inside(parent.get("geometry") or {}, points[:, 0], points[:, 1])
        for index in np.flatnonzero(hits):
            parent_of[index] = parent_of[index] or unit_name(parent)
    return [{**area, "parent": parent} for area, parent in zip(areas, parent_of, strict=True)]


def interior_point(shape: dict) -> tuple[float, float]:
    """A (lon, lat) inside a geometry's largest polygon, holes excluded.

    The middle of the widest stretch that the polygon's mid-latitude line spends
    inside it, so it lands inside concave shapes where a centroid would not.
    """
    polygons = [[np.asarray(ring, dtype=float) for ring in rings] for rings in polygons_of(shape)]
    if not polygons:
        return math.nan, math.nan
    rings = max(polygons, key=lambda rings: np.ptp(rings[0], axis=0).prod())
    lat = float(rings[0][:, 1].min() + rings[0][:, 1].max()) / 2
    edges = np.concatenate([np.stack([ring[:-1], ring[1:]], axis=1) for ring in rings])
    start, end = edges[:, 0], edges[:, 1]
    crossing = (start[:, 1] > lat) != (end[:, 1] > lat)
    start, end = start[crossing], end[crossing]
    xs = np.sort(
        start[:, 0] + (end[:, 0] - start[:, 0]) * (lat - start[:, 1]) / (end[:, 1] - start[:, 1])
    )
    if xs.size < 2:
        return math.nan, math.nan
    spans = xs[: xs.size // 2 * 2].reshape(-1, 2)
    west, east = spans[np.argmax(spans[:, 1] - spans[:, 0])]
    return float(west + east) / 2, lat


def areas_within(areas: list[dict], parent: str) -> list[dict]:
    """The areas whose parent unit is ``parent``, matched as loosely as select_units."""
    wanted = normalize_area_name(parent)
    inside = [area for area in areas if normalize_area_name(area.get("parent") or "") == wanted]
    if not inside:
        known = sorted({area["parent"] for area in areas if area.get("parent")})
        raise ValueError(f"No area named {parent!r} to look within. Known: {', '.join(known)}")
    return inside


async def attach_unit_outlines(area: FocusArea, region_def: dict | None) -> FocusArea:
    """Freeze the picked units' outlines into the area so the run is reproducible."""
    if not isinstance(area, FocusUnits):
        return area
    iso = area.iso or (region_def or {}).get("boundary_iso")
    if not iso:
        raise ValueError("This region has no administrative boundaries to pick areas from")
    boundary = await load_boundary(iso, area.level)
    # A country is one unit, whatever name it was picked by.
    outlines = (
        boundary["geojson"]
        if area.level == "adm0"
        else select_units(boundary["geojson"], area.units)
    )
    return area.model_copy(update={"geometry": outlines})


async def _read_copied_countries() -> list[dict[str, str]]:
    root = boundary_copies.deployment_root()
    if root is None:
        return []
    return copied_countries(await boundary_copies.read(boundary_copies.countries_path(root)))


async def _read_copied_boundary(iso: str, boundary_type: str) -> dict[str, Any] | None:
    root = boundary_copies.deployment_root()
    if root is None or not _is_country_code(iso):
        return None
    copy = await boundary_copies.read(boundary_copies.boundary_path(root, iso, boundary_type))
    return copied_boundary(copy)


def _is_country_code(iso: str) -> bool:
    """Whether ``iso`` can name a copy's path: callers may pass it unparsed."""
    return len(iso) == 3 and iso.isascii() and iso.isalpha() and iso.isupper()


def _session() -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        connector=aiohttp.TCPConnector(ssl=_SSL_CONTEXT),
    )


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
        except BoundaryUnreachable:
            # A failed connection says nothing about the next release, and trying it would
            # make the caller wait through another round of timeouts first.
            raise
        except BoundaryUnavailable as exc:
            failures.append(str(exc))
            continue
        if _geojson_url(metadata):
            return metadata
        failures.append(f"{release} has no GeoJSON for {iso} {boundary_type}")
    raise BoundaryUnavailable("; ".join(failures))


# What a request can fail with before any response: no connection, a dropped one, or none
# within the session's timeout.
_UNREACHABLE = (aiohttp.ClientError, TimeoutError)


async def _fetch_json(session: aiohttp.ClientSession, url: str) -> Any:
    """The JSON at ``url``. A connection that fails or times out is tried once more, as
    one from a cloud host can, then reported as the boundary service being unreachable,
    so callers answer with their usual 502 rather than an unhandled error."""
    host = _one_line(urlsplit(url).netloc)
    try:
        return await _read_json(session, url)
    except _UNREACHABLE as exc:
        logger.warning("Boundary request to %s failed, trying again: %s", host, _kind(exc))
    try:
        return await _read_json(session, url)
    except _UNREACHABLE as exc:
        logger.warning("Boundary request to %s failed again: %s", host, _kind(exc))
        raise BoundaryUnreachable(
            f"The boundary service at {host} could not be reached ({type(exc).__name__}). "
            "Try again in a moment."
        ) from exc


def _one_line(text: str) -> str:
    """Text from a URL as it may be logged: no line break can start a forged entry."""
    return text.replace("\r", "").replace("\n", "")


def _kind(exc: BaseException) -> str:
    """What went wrong, by its class: the message can repeat the URL."""
    return type(exc).__name__


async def _read_json(session: aiohttp.ClientSession, url: str) -> Any:
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
