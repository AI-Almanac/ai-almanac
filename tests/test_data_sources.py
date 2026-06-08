from __future__ import annotations

from pathlib import Path

import httpx
import pytest


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

    datasets_response = await client.get("/datasets", headers=auth_headers)
    assert datasets_response.status_code == 200
    assert obs["id"] in {dataset["id"] for dataset in datasets_response.json()}

    models_response = await client.get("/jobs/models?region=ethiopia")
    assert models_response.status_code == 200
    assert model["id"] in {item["id"] for item in models_response.json()}

    launched: list[str] = []

    async def fake_launch(job_id: str) -> None:
        launched.append(job_id)

    monkeypatch.setattr("ai_almanac.server.routers.jobs.launch_job", fake_launch)
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
    assert job["model_name"] == model["id"]
    assert job["obs_dir"] == str((root / "obs").resolve())
    assert job["model_dir"] == str((root / "fuxi").resolve())


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
