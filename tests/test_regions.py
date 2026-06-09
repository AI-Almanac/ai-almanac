from __future__ import annotations

from pathlib import Path

import httpx
import pytest


def region_body(name: str = "Greater Horn of Africa") -> dict:
    return {
        "display_name": name,
        "description": "Reusable regional coverage.",
        "lat_min": -5,
        "lat_max": 20,
        "lon_min": 25,
        "lon_max": 55,
        "land_only": True,
    }


@pytest.mark.asyncio
async def test_region_lifecycle_and_source_deletion_guard(
    client: httpx.AsyncClient,
) -> None:
    created_response = await client.post("/regions", json=region_body())
    assert created_response.status_code == 201
    created = created_response.json()
    assert created["id"] == "greater-horn-of-africa"
    assert created["romp_region"] == "custom"
    assert created["is_builtin"] is False
    assert created["source_count"] == 0

    updated_response = await client.put(
        f"/regions/{created['id']}",
        json={**region_body("Horn of Africa"), "lat_min": -4},
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["display_name"] == "Horn of Africa"
    assert updated_response.json()["id"] == created["id"]

    root = Path(__file__).parents[1] / "testdata" / "ethiopia" / "obs"
    source_response = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Horn observations",
            "path": str(root),
            "region": created["id"],
            "metadata": {
                "obs_file_pattern": "{}.nc",
                "obs_var": "RAINFALL",
            },
        },
    )
    assert source_response.status_code == 201

    regions_response = await client.get("/regions")
    region = next(
        item for item in regions_response.json() if item["id"] == created["id"]
    )
    assert region["has_data"] is True
    assert region["source_count"] == 1

    delete_response = await client.delete(f"/regions/{created['id']}")
    assert delete_response.status_code == 409
    assert delete_response.json()["detail"] == "region is used by 1 data source(s)"

    await client.delete(f"/data-sources/{source_response.json()['id']}")
    delete_response = await client.delete(f"/regions/{created['id']}")
    assert delete_response.status_code == 204


@pytest.mark.asyncio
async def test_builtin_regions_are_protected(client: httpx.AsyncClient) -> None:
    update_response = await client.put("/regions/ethiopia", json=region_body())
    assert update_response.status_code == 403

    delete_response = await client.delete("/regions/ethiopia")
    assert delete_response.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"display_name": "   "},
        {"lat_min": 20, "lat_max": 10},
        {"lon_min": 50, "lon_max": 40},
        {"lat_min": -91},
        {"lon_max": 181},
    ],
)
async def test_region_bounds_are_parsed_at_api_boundary(
    client: httpx.AsyncClient,
    patch: dict,
) -> None:
    response = await client.post("/regions", json={**region_body(), **patch})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_user_region_bounds_are_applied_to_jobs(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    region = (
        await client.post(
            "/regions",
            json={
                **region_body("Central Highlands"),
                "lat_min": 8,
                "lat_max": 9,
                "lon_min": 38,
                "lon_max": 39,
                "land_only": False,
            },
        )
    ).json()
    root = Path(__file__).parents[1] / "testdata" / "ethiopia"
    obs = (
        await client.post(
            "/data-sources",
            json={
                "kind": "obs",
                "name": "Highland observations",
                "path": str(root / "obs"),
                "region": region["id"],
                "metadata": {
                    "obs_file_pattern": "{}.nc",
                    "obs_var": "RAINFALL",
                },
            },
        )
    ).json()
    model = (
        await client.post(
            "/data-sources",
            json={
                "kind": "model",
                "name": "Highland forecast",
                "path": str(root / "fuxi"),
                "region": region["id"],
                "metadata": {
                    "file_pattern": "{}.nc",
                    "model_var": "tp",
                    "model_type": "AIWP",
                },
            },
        )
    ).json()

    async def fake_launch(job_id: str) -> None:
        return None

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", fake_launch)
    response = await client.post(
        "/jobs",
        headers=auth_headers,
        json={
            "dataset_id": obs["id"],
            "model_name": model["id"],
            "params": {"region": region["id"]},
        },
    )

    assert response.status_code == 201
    job = response.json()
    assert job["region_id"] == region["id"]
    assert job["region_name"] == "Central Highlands"
    assert {
        key: job["params"][key]
        for key in ("region", "lat_min", "lat_max", "lon_min", "lon_max")
    } == {
        "region": "custom",
        "lat_min": 8,
        "lat_max": 9,
        "lon_min": 38,
        "lon_max": 39,
    }


@pytest.mark.asyncio
async def test_broader_observations_can_support_a_custom_benchmark_region(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    region = (
        await client.post(
            "/regions",
            json={
                **region_body("Highland subset"),
                "lat_min": 8,
                "lat_max": 9,
                "lon_min": 38,
                "lon_max": 39,
                "land_only": False,
            },
        )
    ).json()
    root = Path(__file__).parents[1] / "testdata" / "ethiopia"
    obs = (
        await client.post(
            "/data-sources",
            json={
                "kind": "obs",
                "name": "Broader Ethiopia observations",
                "path": str(root / "obs"),
                "region": "ethiopia",
                "metadata": {
                    "obs_file_pattern": "{}.nc",
                    "obs_var": "RAINFALL",
                },
            },
        )
    ).json()
    model = (
        await client.post(
            "/data-sources",
            json={
                "kind": "model",
                "name": "Highland subset forecast",
                "path": str(root / "fuxi"),
                "region": region["id"],
                "metadata": {
                    "file_pattern": "{}.nc",
                    "model_var": "tp",
                    "model_type": "AIWP",
                },
            },
        )
    ).json()

    async def fake_launch(job_id: str) -> None:
        return None

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", fake_launch)
    response = await client.post(
        "/jobs",
        headers=auth_headers,
        json={
            "dataset_id": obs["id"],
            "model_name": model["id"],
            "params": {"region": region["id"]},
        },
    )

    assert response.status_code == 201
    assert response.json()["region_id"] == region["id"]
    assert response.json()["dataset_id"] == obs["id"]
