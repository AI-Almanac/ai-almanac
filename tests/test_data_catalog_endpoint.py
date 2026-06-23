"""GET /data-sources/catalog — serialization and kind filtering."""

from __future__ import annotations

import httpx
import pytest

from ai_almanac.server.routers import data_sources as router
from ai_almanac.server.services.data_catalog import FORECASTS, OBS, Dataset, DatasetRef, Manifest

_FIXTURE = [
    Dataset(
        ref=DatasetRef(FORECASTS, "india", "gencast"),
        years=(2019, 2020),
        manifest=Manifest(
            kind=FORECASTS, region="india", id="gencast", var="tp", ensemble=True
        ),
    ),
    Dataset(ref=DatasetRef(OBS, "india", "imd"), years=(2018,), manifest=None),
]


@pytest.mark.asyncio
async def test_catalog_serializes_discovered_datasets(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(router, "discover_datasets", lambda: _FIXTURE)

    response = await client.get("/data-sources/catalog")

    assert response.status_code == 200
    body = response.json()
    assert [d["id"] for d in body] == ["gencast", "imd"]
    gencast = body[0]
    assert gencast["years"] == [2019, 2020]
    assert gencast["manifest"]["ensemble"] is True
    assert body[1]["manifest"] is None  # no manifest → years-only


@pytest.mark.asyncio
async def test_catalog_filters_by_kind(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(router, "discover_datasets", lambda: _FIXTURE)

    response = await client.get("/data-sources/catalog", params={"kind": "forecasts"})

    assert response.status_code == 200
    body = response.json()
    assert [d["kind"] for d in body] == ["forecasts"]
