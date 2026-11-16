"""Regions are read from the catalogue; nobody creates, edits or removes one."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest


def region_body() -> dict:
    return {
        "display_name": "Greater Horn of Africa",
        "description": "Reusable regional coverage.",
        "lat_min": -5,
        "lat_max": 20,
        "lon_min": 25,
        "lon_max": 55,
        "land_only": True,
    }


@pytest.mark.asyncio
async def test_a_region_lists_the_data_filed_under_it(client: httpx.AsyncClient) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia" / "obs"
    before = {region["id"]: region for region in (await client.get("/regions")).json()}

    registered = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Highland observations",
            "path": str(root),
            "region": "ethiopia",
            "metadata": {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"},
        },
    )

    assert registered.status_code == 201, registered.text
    after = {region["id"]: region for region in (await client.get("/regions")).json()}
    assert after["ethiopia"]["has_data"] is True
    assert after["ethiopia"]["source_count"] == before["ethiopia"]["source_count"] + 1


@pytest.mark.asyncio
async def test_a_region_cannot_be_created_edited_or_removed(client: httpx.AsyncClient) -> None:
    before = (await client.get("/regions")).json()

    created = await client.post("/regions", json=region_body())
    edited = await client.put("/regions/ethiopia", json=region_body())
    removed = await client.delete("/regions/ethiopia")

    assert {created.status_code, edited.status_code, removed.status_code} <= {404, 405}
    assert (await client.get("/regions")).json() == before
