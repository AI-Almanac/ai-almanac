"""Places are looked up from boundary reference data: countries, and the units inside one."""

from __future__ import annotations

import httpx
import pytest

from ai_almanac.server.services import boundaries

LISTING = [
    {"boundaryISO": "KEN", "boundaryName": "Kenya", "boundaryType": "ADM0"},
    {"boundaryISO": "eth", "boundaryName": "Ethiopia", "boundaryType": "ADM0"},
    {"boundaryISO": "KEN", "boundaryName": "Kenya", "boundaryType": "ADM0"},
    {"boundaryName": "Nameless code"},
    "not an entry",
]


def test_each_country_is_listed_once_by_name_with_its_code() -> None:
    assert boundaries.countries_from(LISTING) == [
        {"iso": "ETH", "name": "Ethiopia"},
        {"iso": "KEN", "name": "Kenya"},
    ]


def test_a_listing_that_is_not_a_list_names_no_countries() -> None:
    assert boundaries.countries_from({"error": "upstream"}) == []


@pytest.mark.asyncio
async def test_countries_are_asked_of_the_boundary_service_once(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    async def listing(session, url: str):
        asked.append(url)
        return LISTING

    monkeypatch.setattr(boundaries, "_COUNTRIES", [])
    monkeypatch.setattr(boundaries, "_fetch_json", listing)

    first = await client.get("/places/countries")
    second = await client.get("/places/countries")

    assert first.status_code == 200
    assert (
        first.json()
        == second.json()
        == [
            {"iso": "ETH", "name": "Ethiopia"},
            {"iso": "KEN", "name": "Kenya"},
        ]
    )
    assert len(asked) == 1 and asked[0].endswith("/gbOpen/ALL/ADM0/")


@pytest.mark.asyncio
async def test_no_countries_is_reported_as_the_boundary_service_failing(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def nothing(session, url: str):
        return []

    monkeypatch.setattr(boundaries, "_COUNTRIES", [])
    monkeypatch.setattr(boundaries, "_fetch_json", nothing)

    assert (await client.get("/places/countries")).status_code == 502


@pytest.mark.asyncio
async def test_outlines_are_read_by_country_code_with_no_region_involved(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, str]] = []

    async def kenya(iso: str, level: str) -> dict:
        asked.append((iso, level))
        return {"metadata": {}, "geojson": {"type": "FeatureCollection", "features": []}}

    monkeypatch.setattr(boundaries, "load_boundary", kenya)

    response = await client.get("/places/ken/boundaries/adm1")

    assert response.status_code == 200
    assert response.json()["geojson"]["type"] == "FeatureCollection"
    assert asked == [("KEN", "adm1")]


@pytest.mark.asyncio
async def test_something_that_is_not_a_country_code_has_no_outlines(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(iso: str, level: str) -> dict:
        raise AssertionError("the boundary service should not be asked")

    monkeypatch.setattr(boundaries, "load_boundary", never)

    assert (await client.get("/places/kenya/boundaries/adm1")).status_code == 404
