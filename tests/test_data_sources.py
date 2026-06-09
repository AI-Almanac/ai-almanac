from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_source_validation_does_not_persist_and_returns_inferred_metadata(
    client: httpx.AsyncClient,
) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"
    before = await client.get("/data-sources")

    response = await client.post(
        "/data-sources/validate",
        json={
            "kind": "model",
            "name": "Forecast draft",
            "path": str(root),
            "region": "ethiopia",
            "metadata": {
                "file_pattern": "{}.nc",
                "model_var": "tp",
                "model_type": "AIWP",
            },
        },
    )

    assert response.status_code == 200
    draft = response.json()
    assert draft["status"] == "ready"
    assert draft["metadata"]["init_days"] == "2,5"
    assert draft["metadata"]["init_days_source"] == "inferred"
    after = await client.get("/data-sources")
    assert after.json() == before.json()


@pytest.mark.asyncio
async def test_local_sources_drive_benchmark_selection_and_submission(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia"

    obs_response = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Ethiopia observations",
            "path": str(root / "obs"),
            "region": " Ethiopia ",
            "metadata": {
                "obs_file_pattern": "{}.nc",
                "obs_var": "RAINFALL",
            },
        },
    )
    assert obs_response.status_code == 201
    obs = obs_response.json()
    assert obs["status"] == "ready"
    assert obs["region"] == "ethiopia"
    assert obs["metadata"]["start_year"] == 1998
    assert obs["metadata"]["end_year"] == 2000
    assert obs["metadata"]["spatial_bounds"] == {
        "lat_min": 8.0,
        "lat_max": 9.0,
        "lon_min": 38.0,
        "lon_max": 39.0,
    }

    regions_response = await client.get("/regions")
    assert regions_response.status_code == 200
    regions = {region["id"]: region for region in regions_response.json()}
    assert regions["ethiopia"]["has_data"] is True

    model_response = await client.post(
        "/data-sources",
        json={
            "kind": "model",
            "name": "FuXi test",
            "path": str(root / "fuxi"),
            "region": "ethiopia",
            "metadata": {
                "file_pattern": "{}.nc",
                "model_var": "tp",
                "model_type": "AIWP",
            },
        },
    )
    assert model_response.status_code == 201
    model = model_response.json()
    assert model["status"] == "ready"
    assert model["metadata"]["start_date"] == "1998-01-01"
    assert model["metadata"]["end_date"] == "2000-12-31"
    assert model["metadata"]["init_days"] == "2,5"
    assert model["metadata"]["init_days_source"] == "inferred"
    assert model["metadata"]["init_time_coordinate"] == "time"
    assert model["metadata"]["init_time_sample_count"] == 26

    datasets_response = await client.get("/datasets", headers=auth_headers)
    assert datasets_response.status_code == 200
    assert obs["id"] in {dataset["id"] for dataset in datasets_response.json()}

    models_response = await client.get("/jobs/models?region=ethiopia")
    assert models_response.status_code == 200
    assert model["id"] in {item["id"] for item in models_response.json()}

    launched: list[str] = []

    async def fake_launch(job_id: str) -> None:
        launched.append(job_id)

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", fake_launch)
    job_response = await client.post(
        "/jobs",
        headers=auth_headers,
        json={
            "dataset_id": obs["id"],
            "model_name": model["id"],
            "params": {"region": "ethiopia"},
        },
    )
    assert job_response.status_code == 201
    job = job_response.json()
    assert job["status"] == "queued"
    assert launched == [job["id"]]
    assert job["dataset_id"] == obs["id"]
    assert job["model_name"] == "FuXi test"
    assert job["model_display_name"] == "FuXi test"
    assert job["model_source_id"] == model["id"]
    assert job["obs_dir"] == str((root / "obs").resolve())
    assert job["model_dir"] == str((root / "fuxi").resolve())

    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        row = (
            await conn.execute(
                text("SELECT config_json FROM jobs WHERE id = :id"),
                {"id": job["id"]},
            )
        ).scalar_one()
    config = json.loads(row)
    assert config["model_config"]["model_var"] == "tp"
    assert config["romp_params"]["init_days"] == "2,5"


@pytest.mark.asyncio
async def test_configured_initialization_days_override_inference(
    client: httpx.AsyncClient,
) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"

    response = await client.post(
        "/data-sources",
        json={
            "kind": "model",
            "name": "Configured schedule",
            "path": str(root),
            "region": "ethiopia",
            "metadata": {
                "file_pattern": "{}.nc",
                "model_var": "tp",
                "init_days": "1,4",
            },
        },
    )

    assert response.status_code == 201
    metadata = response.json()["metadata"]
    assert metadata["init_days"] == "1,4"
    assert metadata["init_days_source"] == "configured"
    assert "init_time_coordinate" not in metadata


@pytest.mark.asyncio
async def test_invalid_initialization_days_are_rejected_during_validation(
    client: httpx.AsyncClient,
) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"

    response = await client.post(
        "/data-sources/validate",
        json={
            "kind": "model",
            "name": "Invalid schedule",
            "path": str(root),
            "region": "ethiopia",
            "metadata": {
                "file_pattern": "{}.nc",
                "model_var": "tp",
                "init_days": "Monday,Thursday",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "invalid"
    assert "weekday numbers from 0 to 6" in response.json()["validation_error"]


@pytest.mark.asyncio
async def test_invalid_source_can_be_revalidated(
    client: httpx.AsyncClient,
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "observations"
    response = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Pending observations",
            "path": str(source_dir),
            "region": "ethiopia",
            "metadata": {
                "obs_file_pattern": "{}.nc",
                "obs_var": "RAINFALL",
            },
        },
    )
    assert response.status_code == 201
    source = response.json()
    assert source["status"] == "invalid"
    assert source["validation_error"] == "Directory does not exist."

    fixture = Path(__file__).parents[1] / "testdata" / "ethiopia" / "obs" / "1998.nc"
    source_dir.mkdir()
    (source_dir / "1998.nc").write_bytes(fixture.read_bytes())

    revalidated = await client.post(f"/data-sources/{source['id']}/revalidate")
    assert revalidated.status_code == 200
    assert revalidated.json()["status"] == "ready"
    assert revalidated.json()["validation_error"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("region", "detail"),
    [
        (None, "region is required"),
        ("atlantis", "region 'atlantis' is not configured"),
    ],
)
async def test_source_region_must_be_configured(
    client: httpx.AsyncClient,
    region: str | None,
    detail: str,
) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia" / "obs"

    response = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Unknown coverage",
            "path": str(root),
            "region": region,
            "metadata": {
                "obs_file_pattern": "{}.nc",
                "obs_var": "RAINFALL",
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == detail


@pytest.mark.asyncio
async def test_custom_sources_use_inferred_overlapping_bounds(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia"

    obs_response = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Custom observations",
            "path": str(root / "obs"),
            "region": "custom",
            "metadata": {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"},
        },
    )
    model_response = await client.post(
        "/data-sources",
        json={
            "kind": "model",
            "name": "Custom forecast",
            "path": str(root / "fuxi"),
            "region": "custom",
            "metadata": {
                "file_pattern": "{}.nc",
                "model_var": "tp",
                "model_type": "AIWP",
            },
        },
    )
    assert obs_response.status_code == 201
    assert model_response.status_code == 201

    async def fake_launch(job_id: str) -> None:
        return None

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", fake_launch)
    response = await client.post(
        "/jobs",
        headers=auth_headers,
        json={
            "dataset_id": obs_response.json()["id"],
            "model_name": model_response.json()["id"],
            "params": {"region": "custom"},
        },
    )

    assert response.status_code == 201
    assert response.json()["region_id"] == "custom"
    assert response.json()["region_name"] == "Custom coverage"
    params = response.json()["params"]
    assert {key: params[key] for key in ("region", "lat_min", "lat_max", "lon_min", "lon_max")} == {
        "region": "custom",
        "lat_min": 8.0,
        "lat_max": 9.0,
        "lon_min": 38.0,
        "lon_max": 39.0,
    }


def test_custom_sources_must_overlap() -> None:
    from fastapi import HTTPException

    from ai_almanac.server.routers.jobs import _apply_inferred_custom_bounds

    with pytest.raises(HTTPException, match="do not overlap geographically"):
        _apply_inferred_custom_bounds(
            {"region": "custom"},
            {
                "spatial_bounds": {
                    "lat_min": 0,
                    "lat_max": 5,
                    "lon_min": 0,
                    "lon_max": 5,
                }
            },
            {
                "spatial_bounds": {
                    "lat_min": 10,
                    "lat_max": 15,
                    "lon_min": 10,
                    "lon_max": 15,
                }
            },
        )
