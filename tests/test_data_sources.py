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
    # ROMP rejects whitespace in model names, so the internal name is sanitized
    # while the human-facing display name is preserved.
    assert job["model_name"] == "FuXi_test"
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


def _write_ensemble_model_source(directory: Path) -> None:
    """Copy the fuxi fixture into `directory` with an added ensemble member dim."""
    import xarray as xr

    source = sorted((Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi").glob("*.nc"))[0]
    with xr.open_dataset(source) as ds:
        ensemble = ds.expand_dims(number=3)
        directory.mkdir(parents=True, exist_ok=True)
        ensemble.to_netcdf(directory / "2001.nc")


@pytest.mark.asyncio
async def test_ensemble_member_dim_defaults_probabilistic(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    root = tmp_path / "aifs-ens"
    _write_ensemble_model_source(root)

    response = await client.post(
        "/data-sources/validate",
        json={
            "kind": "model",
            "name": "Ensemble model",
            "path": str(root),
            "region": "ethiopia",
            "metadata": {"file_pattern": "{}.nc", "model_var": "tp"},
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["metadata"]["probabilistic"] is True


@pytest.mark.asyncio
async def test_ensemble_dim_forces_probabilistic_over_stored_flag(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    # A stale/incorrect probabilistic=False (e.g. from a pre-detection
    # registration) must not survive: the deterministic path crashes on the
    # ensemble dim, so the file shape wins.
    root = tmp_path / "aifs-ens-forced-det"
    _write_ensemble_model_source(root)

    response = await client.post(
        "/data-sources/validate",
        json={
            "kind": "model",
            "name": "Ensemble model, stale deterministic flag",
            "path": str(root),
            "region": "ethiopia",
            "metadata": {"file_pattern": "{}.nc", "model_var": "tp", "probabilistic": False},
        },
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["probabilistic"] is True


@pytest.mark.asyncio
async def test_deterministic_source_stays_non_probabilistic(
    client: httpx.AsyncClient,
) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"

    response = await client.post(
        "/data-sources/validate",
        json={
            "kind": "model",
            "name": "Deterministic model",
            "path": str(root),
            "region": "ethiopia",
            "metadata": {"file_pattern": "{}.nc", "model_var": "tp"},
        },
    )

    assert response.status_code == 200
    assert response.json()["metadata"]["probabilistic"] is False


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


def test_custom_sources_must_overlap() -> None:
    from fastapi import HTTPException

    from ai_almanac.server.services.job_submission import apply_inferred_custom_bounds

    with pytest.raises(HTTPException, match="do not overlap geographically"):
        apply_inferred_custom_bounds(
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


# ---------------------------------------------------------------------------
# Ownership and pointer (gs://) registration
# ---------------------------------------------------------------------------

_OBS_ROOT = Path(__file__).parents[1] / "testdata" / "ethiopia" / "obs"


def _proxy_users(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proxy auth where only 'root' is admin; others are plain users."""
    from ai_almanac.settings import settings

    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "root")


def _obs_body(name: str, path: str | None = None) -> dict:
    return {
        "kind": "obs",
        "name": name,
        "path": path or str(_OBS_ROOT),
        "region": "ethiopia",
        "metadata": {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"},
    }


@pytest.mark.asyncio
async def test_non_admin_source_is_private_and_invisible_to_others(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _proxy_users(monkeypatch)
    alice = {"X-Forwarded-User": "alice"}
    bob = {"X-Forwarded-User": "bob"}

    created = await client.post("/data-sources", json=_obs_body("Alice obs"), headers=alice)
    assert created.status_code == 201
    row = created.json()
    assert row["visibility"] == "private"
    assert row["is_owner"] is True

    bob_list = await client.get("/data-sources", headers=bob)
    assert row["id"] not in [s["id"] for s in bob_list.json()]

    for attempt in (
        client.put(f"/data-sources/{row['id']}", json=_obs_body("Steal"), headers=bob),
        client.post(f"/data-sources/{row['id']}/revalidate", headers=bob),
        client.delete(f"/data-sources/{row['id']}", headers=bob),
    ):
        assert (await attempt).status_code == 404

    deleted = await client.delete(f"/data-sources/{row['id']}", headers=alice)
    assert deleted.status_code == 204


@pytest.mark.asyncio
async def test_admin_source_is_shared(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _proxy_users(monkeypatch)
    created = await client.post(
        "/data-sources",
        json=_obs_body("Built-in obs"),
        headers={"X-Forwarded-User": "root"},
    )
    assert created.status_code == 201
    row = created.json()
    assert row["visibility"] == "shared"

    other = await client.get("/data-sources", headers={"X-Forwarded-User": "bob"})
    assert row["id"] in [s["id"] for s in other.json()]

    await client.delete(f"/data-sources/{row['id']}", headers={"X-Forwarded-User": "root"})


@pytest.mark.asyncio
async def test_shared_deployment_rejects_non_admin_local_paths(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.settings import settings

    _proxy_users(monkeypatch)
    monkeypatch.setattr(settings, "deployment_mode", "shared")

    response = await client.post(
        "/data-sources",
        json=_obs_body("Local sneak"),
        headers={"X-Forwarded-User": "alice", "X-Forwarded-Issuer": "test-idp"},
    )
    assert response.status_code == 400
    assert "gs://" in response.json()["detail"]


@pytest.mark.asyncio
async def test_gs_path_survives_registration_unmangled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.server.services import storage as storage_mod
    from tests.test_gcs_source_validation import _FakeGcsStorage

    gs_path = "gs://bucket/ethiopia/obs"
    fake = _FakeGcsStorage(_OBS_ROOT, gs_path)
    monkeypatch.setattr(storage_mod, "get_storage", lambda: fake)

    created = await client.post("/data-sources", json=_obs_body("GCS obs", path=gs_path))
    assert created.status_code == 201
    row = created.json()
    assert row["path"] == gs_path
    assert row["location_type"] == "gcs"
    assert row["status"] == "ready"

    await client.delete(f"/data-sources/{row['id']}")


@pytest.mark.asyncio
async def test_unreadable_gcs_path_reports_clear_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services import data_sources as svc
    from ai_almanac.server.services import storage as storage_mod

    class _DeniedStorage:
        def list_dataset_files(self, path: str, glob: str) -> list[str]:
            raise PermissionError("403 forbidden")

    monkeypatch.setattr(storage_mod, "get_storage", lambda: _DeniedStorage())

    status, error, _ = await svc.validate_source(
        "obs", "gs://locked-bucket/obs", {"obs_file_pattern": "{}.nc"}
    )
    assert status == "invalid"
    assert "readable by the service account" in error


@pytest.mark.asyncio
async def test_remote_provider_source_registers_ready_without_inspection(
    client: httpx.AsyncClient,
) -> None:
    created = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "ERA5 Ethiopia",
            "path": "gs://gcp-public-data-arco-era5/ar/full.zarr-v3",
            "region": "ethiopia",
            "metadata": {
                "provider": "era5_arco",
                "arco_url": "gs://gcp-public-data-arco-era5/ar/full.zarr-v3",
                "precip_var": "total_precipitation",
                "unit_cvt": 1000.0,
            },
        },
    )
    assert created.status_code == 201
    row = created.json()
    assert row["status"] == "ready"
    assert row["metadata"]["provider"] == "era5_arco"

    await client.delete(f"/data-sources/{row['id']}")
