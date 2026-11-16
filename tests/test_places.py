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
    monkeypatch.setattr(boundaries, "_COUNTRIES", [{"iso": "KEN", "name": "Kenya"}])

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


@pytest.mark.asyncio
async def test_a_code_no_country_has_is_refused_without_asking_for_its_outlines(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(iso: str, level: str) -> dict:
        raise AssertionError("the boundary service should not be asked")

    monkeypatch.setattr(boundaries, "_COUNTRIES", [{"iso": "KEN", "name": "Kenya"}])
    monkeypatch.setattr(boundaries, "load_boundary", never)

    response = await client.get("/places/zzz/boundaries/adm1")

    assert response.status_code == 404
    assert "ZZZ" in response.text


@pytest.mark.asyncio
async def test_an_outline_service_that_cannot_be_reached_is_tried_again_then_named(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiohttp

    calls: list[str] = []

    async def unreachable(session, url: str):
        calls.append(url)
        raise aiohttp.ClientConnectionError()

    monkeypatch.setattr(boundaries, "_read_json", unreachable)

    with pytest.raises(boundaries.BoundaryUnavailable, match="www.geoboundaries.org could not"):
        await boundaries._fetch_json(None, "https://www.geoboundaries.org/api/current/x/")
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_a_connection_that_drops_once_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    answers = [TimeoutError(), {"ok": True}]

    async def flaky(session, url: str):
        answer = answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(boundaries, "_read_json", flaky)

    assert await boundaries._fetch_json(None, "https://www.geoboundaries.org/x/") == {"ok": True}


@pytest.mark.asyncio
async def test_a_blend_whose_outlines_cannot_be_fetched_says_so_with_a_502(
    client: httpx.AsyncClient, as_new_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.server.services import job_submission

    async def unreachable(area, region_def):
        raise boundaries.BoundaryUnavailable("The boundary service could not be reached")

    monkeypatch.setattr(boundaries, "attach_unit_outlines", unreachable)

    params = {"focus_area": {"level": "adm1", "units": ["Amhara"], "iso": "ETH"}}
    with pytest.raises(job_submission.HTTPException) as refused:
        await job_submission._with_unit_outlines(params, None)
    assert refused.value.status_code == 502
    assert "could not be reached" in refused.value.detail


@pytest.mark.asyncio
async def test_an_unhandled_error_keeps_its_message_from_the_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.app import app

    async def broken():
        raise RuntimeError("internal detail /srv/secret")

    monkeypatch.setattr(boundaries, "_COUNTRIES", [])
    monkeypatch.setattr(boundaries, "list_countries", broken)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as quiet:
        response = await quiet.get("/places/countries")

    assert response.status_code == 500
    assert "/srv/secret" not in response.text


@pytest.mark.asyncio
async def test_a_service_that_cannot_be_reached_is_not_asked_again_for_another_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiohttp

    calls: list[str] = []

    async def unreachable(session, url: str):
        calls.append(url)
        raise aiohttp.ClientConnectionError()

    monkeypatch.setattr(boundaries, "_read_json", unreachable)

    with pytest.raises(boundaries.BoundaryUnreachable):
        await boundaries._fetch_metadata(None, "ETH", "ADM1")
    assert len({url.split("/api/current/")[1].split("/")[0] for url in calls}) == 1
    assert len(calls) == 2
