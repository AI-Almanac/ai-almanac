"""A deployment outlines places from the boundary copies in its data bucket, and asks
geoBoundaries only for what was never copied."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from ai_almanac.server.services import boundaries, boundary_copies
from ai_almanac.settings import settings
from tests.helpers.places import REGIONS

KENYA = {"iso": "KEN", "name": "Kenya"}
KENYA_REGIONS = {"metadata": {"boundaryID": "KEN-ADM1"}, "geojson": REGIONS}


@pytest.fixture
def copies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    root = str(tmp_path / "boundaries")
    monkeypatch.setattr(boundary_copies, "deployment_root", lambda: root)
    return root


@pytest.fixture
def geoboundaries(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What was asked of geoBoundaries, which answers Kenya's regions for any country."""
    asked: list[str] = []

    async def fetch_countries():
        asked.append("countries")
        return [KENYA, {"iso": "UGA", "name": "Uganda"}]

    async def fetch_boundary(iso: str, boundary_type: str):
        asked.append(f"{iso} {boundary_type}")
        return KENYA_REGIONS

    monkeypatch.setattr(boundaries, "fetch_countries", fetch_countries)
    monkeypatch.setattr(boundaries, "fetch_boundary", fetch_boundary)
    return asked


@pytest.mark.asyncio
async def test_countries_come_from_the_copy(
    client: httpx.AsyncClient, copies: str, geoboundaries: list[str]
) -> None:
    boundary_copies.write(boundary_copies.countries_path(copies), [KENYA])

    response = await client.get("/places/countries")

    assert KENYA in response.json()
    assert geoboundaries == []


@pytest.mark.asyncio
async def test_a_region_country_the_listing_leaves_out_can_be_outlined(
    client: httpx.AsyncClient, copies: str, geoboundaries: list[str]
) -> None:
    boundary_copies.write(boundary_copies.countries_path(copies), [KENYA])

    countries = (await client.get("/places/countries")).json()
    outlines = await client.get("/places/IND/boundaries/adm1")

    assert {"iso": "IND", "name": "India"} in countries
    assert outlines.status_code == 200
    assert geoboundaries == ["IND ADM1"]


@pytest.mark.asyncio
async def test_a_copied_country_is_outlined_without_asking_geoboundaries(
    client: httpx.AsyncClient, copies: str, geoboundaries: list[str]
) -> None:
    boundary_copies.write(boundary_copies.countries_path(copies), [KENYA])
    boundary_copies.write(boundary_copies.boundary_path(copies, "KEN", "ADM1"), KENYA_REGIONS)

    response = await client.get("/places/KEN/boundaries/adm1")

    assert response.status_code == 200
    assert response.json() == KENYA_REGIONS
    assert geoboundaries == []


@pytest.mark.asyncio
async def test_what_was_never_copied_is_asked_of_geoboundaries(
    client: httpx.AsyncClient, copies: str, geoboundaries: list[str]
) -> None:
    response = await client.get("/places/UGA/boundaries/adm1")

    assert response.status_code == 200
    assert geoboundaries == ["countries", "UGA ADM1"]


@pytest.mark.asyncio
async def test_a_copy_that_cannot_be_read_is_asked_of_geoboundaries(
    copies: str, geoboundaries: list[str]
) -> None:
    damaged = Path(boundary_copies.boundary_path(copies, "KEN", "ADM1"))
    damaged.parent.mkdir(parents=True)
    damaged.write_text("{not json")

    assert await boundaries.load_boundary("KEN", "adm1") == KENYA_REGIONS
    assert geoboundaries == ["KEN ADM1"]


@pytest.mark.asyncio
async def test_a_copy_that_is_not_an_outline_is_asked_of_geoboundaries(
    copies: str, geoboundaries: list[str]
) -> None:
    boundary_copies.write(boundary_copies.boundary_path(copies, "KEN", "ADM1"), {"geojson": []})

    assert await boundaries.load_boundary("KEN", "adm1") == KENYA_REGIONS
    assert geoboundaries == ["KEN ADM1"]


def test_a_deployment_reads_the_copies_in_its_data_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "storage_backend", "gcs")
    monkeypatch.setattr(settings, "gcs_data_bucket", "shared-data")

    assert boundary_copies.deployment_root() == "gs://shared-data/boundaries"


def test_a_local_install_keeps_no_copies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "storage_backend", "local")
    monkeypatch.setattr(settings, "gcs_data_bucket", "shared-data")

    assert boundary_copies.deployment_root() is None
