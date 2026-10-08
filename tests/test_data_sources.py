from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from ai_almanac.server.services.data_sources import _season_window


def test_season_window_uses_schedule_bounds_else_full_year() -> None:
    assert _season_window(["05-02", "06-01", "07-29"]) == ("05-02", "07-29")
    assert _season_window(None) == ("01-01", "12-31")
    assert _season_window([]) == ("01-01", "12-31")


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
async def test_source_validation_reports_years_missing_inside_the_range(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    fixtures = Path(__file__).parents[1] / "testdata" / "ethiopia" / "obs"
    for year in (1998, 2000):
        (tmp_path / f"{year}.nc").write_bytes((fixtures / f"{year}.nc").read_bytes())

    response = await client.post(
        "/data-sources/validate",
        json={
            "kind": "obs",
            "name": "Gappy observations",
            "path": str(tmp_path),
            "region": "ethiopia",
            "metadata": {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"},
        },
    )

    assert response.status_code == 200
    draft = response.json()
    assert draft["status"] == "ready"
    assert draft["metadata"]["start_year"] == 1998
    assert draft["metadata"]["end_year"] == 2000
    assert draft["metadata"]["years"] == [1998, 2000]
    assert draft["metadata"]["missing_years"] == [1999]


def test_year_detection_keeps_the_first_year_in_each_filename() -> None:
    from ai_almanac.server.services.data_sources import _coverage_years

    files = [Path("aifs_2p0_2001_v2025.nc"), Path("aifs_2p0_2002_v2025.nc")]
    assert _coverage_years(files) == [2001, 2002]


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
    assert obs["metadata"]["grid_step_deg"] == 0.25

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
    # start/end dates span the coverage years but use the init-time season
    # month-days (fuxi is initialized 05-02..07-29), not the full calendar year.
    assert model["metadata"]["start_date"] == "1998-05-02"
    assert model["metadata"]["end_date"] == "2000-07-29"
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


def _write_fuxi_variant(directory: Path, transform) -> Path:
    """Copy the first fuxi fixture into `directory` after applying `transform`."""
    import xarray as xr

    source = sorted((Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi").glob("*.nc"))[0]
    with xr.open_dataset(source) as ds:
        directory.mkdir(parents=True, exist_ok=True)
        transform(ds.load()).to_netcdf(directory / "2001.nc")
    return directory


async def _validate_model(client: httpx.AsyncClient, root: Path) -> dict:
    response = await client.post(
        "/data-sources/validate",
        json={
            "kind": "model",
            "name": "Model draft",
            "path": str(root),
            "region": "ethiopia",
            "metadata": {"file_pattern": "{}.nc", "model_var": "tp"},
        },
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_forecast_dims_are_recorded_under_romps_names(client: httpx.AsyncClient) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"

    draft = await _validate_model(client, root)

    assert draft["metadata"]["forecast_dims"] == {
        "lat": "lat",
        "lon": "lon",
        "init_time": "time",
        "step": "day",
    }


@pytest.mark.asyncio
async def test_timedelta_lead_time_is_found_whatever_it_is_called(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    # The 0.25° archives name their lead time "prediction_timedelta_daily",
    # which also contains "time"; the dtype, not the name, identifies it.
    import pandas as pd

    def archive_layout(ds):
        lead = pd.to_timedelta(ds["day"].values + 1, unit="D")
        ds = ds.assign_coords(day=lead)
        return ds.rename({"day": "prediction_timedelta_daily", "time": "issued"})

    draft = await _validate_model(client, _write_fuxi_variant(tmp_path / "e2s", archive_layout))

    assert draft["status"] == "ready"
    assert draft["metadata"]["forecast_dims"]["init_time"] == "issued"
    assert draft["metadata"]["forecast_dims"]["step"] == "prediction_timedelta_daily"


@pytest.mark.asyncio
async def test_start_date_under_any_name_sets_the_initialization_schedule(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    renamed = _write_fuxi_variant(tmp_path / "issued", lambda ds: ds.rename({"time": "issued"}))
    original = _write_fuxi_variant(tmp_path / "time", lambda ds: ds)

    renamed_draft = await _validate_model(client, renamed)
    original_draft = await _validate_model(client, original)

    schedule = ("init_days", "init_days_source", "init_month_days", "start_date", "end_date")
    assert {key: renamed_draft["metadata"][key] for key in schedule} == {
        key: original_draft["metadata"][key] for key in schedule
    }
    assert renamed_draft["metadata"]["init_days_source"] == "inferred"


@pytest.mark.asyncio
async def test_dim_names_romp_cannot_carry_are_rejected_at_registration(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    root = _write_fuxi_variant(tmp_path / "spaced", lambda ds: ds.rename({"day": "lead day"}))

    draft = await _validate_model(client, root)

    assert draft["status"] == "invalid"
    assert "lead day" in draft["validation_error"]


@pytest.mark.asyncio
async def test_leftover_dim_is_recorded_as_the_ensemble_member(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    root = tmp_path / "ens"
    _write_ensemble_model_source(root)

    draft = await _validate_model(client, root)

    assert draft["metadata"]["forecast_dims"]["member"] == "number"


@pytest.mark.asyncio
async def test_unidentifiable_lead_time_is_rejected(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    root = _write_fuxi_variant(tmp_path / "no-lead", lambda ds: ds.rename({"day": "horizon"}))

    draft = await _validate_model(client, root)

    assert draft["status"] == "invalid"
    assert "lead time" in draft["validation_error"]


@pytest.mark.asyncio
async def test_more_than_one_leftover_dim_is_rejected(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    root = _write_fuxi_variant(tmp_path / "extra", lambda ds: ds.expand_dims(number=2, height=2))

    draft = await _validate_model(client, root)

    assert draft["status"] == "invalid"
    assert "ensemble member" in draft["validation_error"]


@pytest.mark.asyncio
async def test_single_valued_leftover_dim_is_not_taken_for_an_ensemble(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    root = _write_fuxi_variant(tmp_path / "height", lambda ds: ds.expand_dims(height=1))

    draft = await _validate_model(client, root)

    assert draft["status"] == "invalid"
    assert "'height'" in draft["validation_error"]


@pytest.mark.asyncio
async def test_precipitation_in_metres_converts_to_millimetres(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    def in_metres(ds):
        ds["tp"].attrs["units"] = "m"
        return ds

    draft = await _validate_model(client, _write_fuxi_variant(tmp_path / "metres", in_metres))

    assert draft["status"] == "ready"
    assert draft["metadata"]["unit_cvt"] == 1000.0


@pytest.mark.asyncio
async def test_unrecognized_precipitation_units_are_rejected(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    def per_second(ds):
        ds["tp"].attrs["units"] = "kg m-2 s-1"
        return ds

    draft = await _validate_model(client, _write_fuxi_variant(tmp_path / "rate", per_second))

    assert draft["status"] == "invalid"
    assert "'kg m-2 s-1'" in draft["validation_error"]


@pytest.mark.asyncio
async def test_precipitation_without_units_keeps_the_default_conversion(
    client: httpx.AsyncClient,
) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"

    draft = await _validate_model(client, root)

    assert draft["metadata"]["unit_cvt"] == 1.0


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
    from tests.range_read_fs import gcs_storage_over

    gs_path = "gs://bucket/ethiopia/obs"
    storage, _ = gcs_storage_over(_OBS_ROOT, gs_path)
    monkeypatch.setattr(storage_mod, "get_storage", lambda: storage)

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


def _fuxi_draft(forecast_model_id: str | None) -> dict:
    metadata = {"file_pattern": "{}.nc", "model_var": "tp", "model_type": "AIWP"}
    if forecast_model_id is not None:
        metadata["forecast_model_id"] = forecast_model_id
    return {
        "kind": "model",
        "name": "Ethiopia hindcasts",
        "path": str(Path(__file__).parents[1] / "testdata" / "ethiopia" / "fuxi"),
        "region": "ethiopia",
        "metadata": metadata,
    }


@pytest.mark.asyncio
async def test_model_source_links_to_the_live_forecast_model_chosen_at_registration(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/data-sources", json=_fuxi_draft("fuxi"))

    assert response.status_code == 201
    source = response.json()
    assert source["status"] == "ready"
    assert source["metadata"]["forecast_model_id"] == "fuxi"
    assert source["live_forecast"] == {"status": "ready", "detail": None, "model_id": "fuxi"}


@pytest.mark.asyncio
@pytest.mark.parametrize("forecast_model_id", [None, "", "  "])
async def test_unlinked_model_source_is_valid_for_past_seasons_only(
    client: httpx.AsyncClient, forecast_model_id: str | None
) -> None:
    response = await client.post("/data-sources", json=_fuxi_draft(forecast_model_id))

    source = response.json()
    assert source["status"] == "ready"
    assert "forecast_model_id" not in source["metadata"]
    assert source["live_forecast"]["status"] == "unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("forecast_model_id", "reason"),
    [("graphcast", "1° grid"), ("no_such_model", "not an available live forecast model")],
)
async def test_model_source_rejects_a_live_forecast_model_that_cannot_extend_it(
    client: httpx.AsyncClient, forecast_model_id: str, reason: str
) -> None:
    response = await client.post("/data-sources/validate", json=_fuxi_draft(forecast_model_id))

    draft = response.json()
    assert draft["status"] == "invalid"
    assert reason in draft["validation_error"]
