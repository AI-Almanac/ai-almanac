"""Benchmark settings reach ROMP only in shapes ROMP's config file can hold safely.

ROMP exec()s its config as Python source, so a setting that smuggles quotes,
parentheses, or newlines into that source would run as code on the runner.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from ai_almanac.server.services.job_submission import RompParams

FIXTURES = Path(__file__).parents[1] / "testdata" / "ethiopia"
CODE = "__import__('os').system('touch /tmp/pwned')"


async def _register(client: httpx.AsyncClient, kind: str, metadata: dict) -> str:
    response = await client.post(
        "/data-sources",
        json={
            "kind": kind,
            "name": f"Ethiopia {kind}",
            "path": str(FIXTURES / ("obs" if kind == "obs" else "fuxi")),
            "region": "ethiopia",
            "metadata": metadata,
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "ready"
    return response.json()["id"]


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    job_ids: list[str] = []

    async def fake_launch(job_id: str) -> None:
        job_ids.append(job_id)

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", fake_launch)
    return job_ids


async def _submit(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    params: dict,
    model_metadata: dict | None = None,
) -> httpx.Response:
    obs_id = await _register(client, "obs", {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"})
    model_id = await _register(
        client, "model", {"file_pattern": "{}.nc", "model_var": "tp", **(model_metadata or {})}
    )
    return await client.post(
        "/jobs",
        headers=auth_headers,
        json={"dataset_id": obs_id, "model_name": model_id, "params": params},
    )


async def _stored_settings(job_id: str) -> dict:
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        row = (
            await conn.execute(text("SELECT config_json FROM jobs WHERE id = :id"), {"id": job_id})
        ).scalar_one()
    return json.loads(row)["romp_params"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("init_days", f"0) or {CODE} or (1"),
        ("members", f"0), {CODE}, (1"),
        ("start_date", f"2019-05-01) or {CODE} or ("),
        ("obs_var", f'RAINFALL"\n{CODE}\nx = "'),
        ("model_var", f'tp"; {CODE}; "'),
        ("obs", f"CHIRPS\n{CODE}"),
        ("ref_model", f'climatology" + str({CODE}) + "'),
        ("event_type", "monsoon onset"),
        ("file_pattern", f'{{}}.nc",)\n{CODE}\n#'),
        ("obs_file_pattern", "../../etc/{}.nc"),
        ("nc_mask", f'gs://bucket/mask.nc"\n{CODE}\n#'),
        ("thresh_file", "/data/thresh.nc'"),
        ("ref_model_dir", "gs://bucket/../other"),
    ],
)
@pytest.mark.asyncio
async def test_settings_that_could_escape_romps_config_are_rejected(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    launched: list[str],
    field: str,
    value: str,
) -> None:
    response = await _submit(client, auth_headers, {"region": "ethiopia", field: value})

    assert response.status_code == 422
    assert field in json.dumps(response.json()["detail"])
    assert launched == []


@pytest.mark.asyncio
async def test_unsafe_model_source_metadata_is_rejected_at_submission(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    launched: list[str],
) -> None:
    response = await _submit(
        client,
        auth_headers,
        {"region": "ethiopia"},
        model_metadata={"members": f"0), {CODE}, (1", "probabilistic": True},
    )

    assert response.status_code == 422
    assert "members" in response.json()["detail"]
    assert launched == []


@pytest.mark.asyncio
async def test_valid_settings_are_stored_in_canonical_form(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    launched: list[str],
) -> None:
    response = await _submit(
        client,
        auth_headers,
        {
            "region": "ethiopia",
            "event_type": "monsoon_onset",
            "init_days": " 0, 3 ",
            "members": "0, 1,2",
            "start_date": "1998-05-02",
            "end_date": "2000-07-29",
            "obs_file_pattern": "{}.nc",
            "nc_mask": "gs://almanac-data-ai-almanac/masks/ethiopia mask.nc",
            "wet_threshold": 25,
        },
    )

    assert response.status_code == 201
    assert launched == [response.json()["id"]]
    settings = await _stored_settings(response.json()["id"])
    assert settings["init_days"] == "0,3"
    assert settings["members"] == "0,1,2"
    assert settings["start_date"] == "1998-05-02"
    assert settings["nc_mask"] == "gs://almanac-data-ai-almanac/masks/ethiopia mask.nc"
    assert settings["region"] == "Ethiopia"


@pytest.mark.parametrize(
    "stored",
    [
        {"init_days": "2,5", "members": "All", "start_date": "1998-05-02"},
        {"init_days": "0", "members": None, "end_date": "2024-07-31"},
        {"members": 51, "date_filter_year": 2020, "file_pattern": "fuxi_{}_*.nc"},
        {"region": "custom", "lat_min": 3.0, "lat_max": 15.0, "lon_min": 33.0, "lon_max": 48.0},
        {"focus_area": {"lat_min": 8.0, "lat_max": 12.0, "lon_min": 38.0, "lon_max": 40.0}},
    ],
)
def test_settings_already_stored_on_jobs_still_parse(stored: dict) -> None:
    """Rerunning a job re-parses its stored settings, so their shapes must round-trip."""
    canonical = RompParams.model_validate(stored).model_dump(mode="json", exclude_none=True)

    assert RompParams.model_validate(canonical).model_dump(mode="json") == (
        RompParams.model_validate(stored).model_dump(mode="json")
    )


@pytest.mark.asyncio
async def test_chat_rerun_reports_invalid_override_without_creating_a_job(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    user_id: str,
    launched: list[str],
) -> None:
    from ai_almanac.server.services import benchmark_domain
    from ai_almanac.server.services.benchmark_state import BenchmarkScope

    job_id = (await _submit(client, auth_headers, {"region": "ethiopia"})).json()["id"]

    result = await benchmark_domain._exec_rerun_job(
        {"job_id": job_id, "params_override": {"obs_var": f'RAINFALL"\n{CODE}'}},
        user_id,
        BenchmarkScope(kind="benchmark_setup", key="setup", title="Setup"),
    )

    assert "obs_var" in result["error"]
    assert launched == [job_id]
