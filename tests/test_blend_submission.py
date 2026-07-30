"""create_blend_for_user — blend job assembly and dispatch.

Seeds gs:// obs/model data sources directly (bypassing path validation), stubs
the job runner so no Modal call happens, and asserts the persisted job carries
the blend discriminator and routing config the ModalRunner relies on.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text

from ai_almanac.server.services import job_submission
from ai_almanac.server.services.execution import RunnerHandle
from ai_almanac.server.services.job_submission import (
    BlendCreate,
    BlendParams,
    _blend_model_key,
    create_blend_for_user,
)
from ai_almanac.server.tables import jobs


class _FakeRunner:
    name = "modal"

    async def submit(self, request):  # noqa: ANN001 - test double
        return RunnerHandle(runner="modal", external_id="fc-test")


async def _seed_source(
    kind: str,
    name: str,
    path: str,
    years: tuple[int, int] | None = None,
) -> str:
    from ai_almanac.server.db import get_db

    source_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    metadata = json.dumps({"start_year": years[0], "end_year": years[1]}) if years else "{}"
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, kind, name, path, region, metadata, location_type, status, "
                "validation_error, created_at, updated_at) "
                "VALUES (:id, :kind, :name, :path, 'india', :metadata, 'gcs', 'ready', "
                "NULL, :now, :now)"
            ),
            {
                "id": source_id,
                "kind": kind,
                "name": name,
                "path": path,
                "metadata": metadata,
                "now": now,
            },
        )
    return source_id


@pytest.mark.parametrize(
    ("name", "expected"),
    [("GenCast", "gencast"), ("AIFS v2", "aifs_v2"), ("a/b c", "a_b_c")],
)
def test_blend_model_key_slugifies(name: str, expected: str) -> None:
    assert _blend_model_key(name) == expected


@pytest_asyncio.fixture
async def _stub_runner(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(job_submission, "get_job_runner", lambda: _FakeRunner())


@pytest.mark.asyncio
async def test_create_blend_persists_blend_routing_config(
    client, user_id: str, _stub_runner
) -> None:
    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "ERA5 India", "gs://data/obs/india")
    gencast_id = await _seed_source("model", "GenCast", "gs://data/models/gencast")
    aifs_id = await _seed_source("model", "AIFS", "gs://data/models/aifs")

    out = await create_blend_for_user(
        BlendCreate(
            name="my blend",
            obs_dataset_id=obs_id,
            model_ids=[gencast_id, aifs_id],
            params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
        ),
        user_id,
    )

    assert out.name == "my blend"
    assert out.model_names == ["gencast", "aifs"]

    async with get_db() as conn:
        row = (await conn.execute(sa.select(jobs).where(jobs.c.id == out.id))).mappings().fetchone()
    assert row["job_type"] == "blend"
    assert row["status"] == "running"  # remote runner is live once spawned
    assert row["runner"] == "modal"
    config = json.loads(row["config_json"])
    assert config["modal_function"] == "run_blend"
    assert config["modal_app"]  # blend app name travels on the job
    assert config["model_names"] == ["gencast", "aifs"]
    # training 2019:2024 ∪ cv 2024 → the staging years, resolved server-side.
    assert config["forecast_years"] == [2019, 2020, 2021, 2022, 2023, 2024]
    assert config["model_files"]["gencast"] == [
        f"gs://data/models/gencast/{year}.nc" for year in range(2019, 2025)
    ]
    assert config["model_files"]["aifs"] == [
        f"gs://data/models/aifs/{year}.nc" for year in range(2019, 2025)
    ]
    assert config["blend_params"]["training_years"] == "2019:2024"


@pytest.mark.asyncio
async def test_post_blends_rejects_thin_climatology_coverage(
    client, user_id: str, auth_headers: dict[str, str], _stub_runner
) -> None:
    """The API must apply the same coverage rule as the UI and the chat path:
    the onset climatology needs MIN_ONSET_YEARS of observations before the
    first forecast year."""
    obs_id = await _seed_source("obs", "ERA5 thin", "gs://data/obs/thin", years=(2010, 2024))
    model_id = await _seed_source("model", "AIFS", "gs://data/models/aifs", years=(2010, 2024))

    response = await client.post(
        "/blends",
        headers=auth_headers,
        json={
            "name": "too early",
            "obs_dataset_id": obs_id,
            "model_ids": [model_id],
            # Obs start 2010 → the first estimable forecast year is 2020.
            "params": {"training_years": "2015:2024", "cv_holdout_years": "2024"},
        },
    )

    assert response.status_code == 400
    assert "Climatology needs 10 years of observations" in response.json()["detail"]


@pytest.mark.asyncio
async def test_post_blends_accepts_sufficient_coverage(
    client, user_id: str, auth_headers: dict[str, str], _stub_runner
) -> None:
    obs_id = await _seed_source("obs", "ERA5 deep", "gs://data/obs/deep", years=(2010, 2024))
    model_id = await _seed_source("model", "AIFS", "gs://data/models/aifs", years=(2010, 2024))

    response = await client.post(
        "/blends",
        headers=auth_headers,
        json={
            "name": "late enough",
            "obs_dataset_id": obs_id,
            "model_ids": [model_id],
            "params": {"training_years": "2020:2024", "cv_holdout_years": "2024"},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["warnings"] == []


@pytest.mark.asyncio
async def test_create_blend_warns_when_a_member_cannot_forecast_live(
    client, user_id: str, _stub_runner
) -> None:
    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "ERA5 India", "gs://data/obs/india")
    ngcm_id = await _seed_source("model", "NeuralGCM", "gs://data/models/ngcm")
    aifs_id = await _seed_source("model", "AIFS", "gs://data/models/aifs")

    out = await create_blend_for_user(
        BlendCreate(
            name="history only",
            obs_dataset_id=obs_id,
            model_ids=[ngcm_id, aifs_id],
            params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
        ),
        user_id,
    )

    assert len(out.warnings) == 1
    assert "NeuralGCM" in out.warnings[0]
    assert "cannot be run as a live forecast" in out.warnings[0]

    async with get_db() as conn:
        row = (await conn.execute(sa.select(jobs).where(jobs.c.id == out.id))).mappings().fetchone()
    assert json.loads(row["config_json"])["warnings"] == out.warnings


@pytest.mark.asyncio
async def test_create_forecast_rejects_blend_with_an_unforecastable_member(
    client, user_id: str, _stub_runner
) -> None:
    """Live scoring needs every member's season, so requesting only the runnable
    subset of a history-only blend must fail at submission, not after rollout."""
    from fastapi import HTTPException

    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "ERA5 India", "gs://data/obs/india")
    ngcm_id = await _seed_source("model", "NeuralGCM", "gs://data/models/ngcm")
    aifs_id = await _seed_source("model", "AIFS", "gs://data/models/aifs")

    blend = await create_blend_for_user(
        BlendCreate(
            name="ngcm + aifs",
            obs_dataset_id=obs_id,
            model_ids=[ngcm_id, aifs_id],
            params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
        ),
        user_id,
    )
    async with get_db() as conn:
        await conn.execute(sa.update(jobs).where(jobs.c.id == blend.id).values(status="complete"))

    with pytest.raises(HTTPException) as exc:
        await job_submission.create_forecast_for_user(
            job_submission.ForecastCreate(blend_id=blend.id, forecast_model_ids=["aifs"]),
            user_id,
        )
    assert exc.value.status_code == 400
    assert "no live forecast model" in exc.value.detail
    assert "neuralgcm" in exc.value.detail


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (["aifs", "neuralgcm"], ["neuralgcm"]),
        (["ngcm", "ifs", "fuxi_s2s", "aifs_daily"], ["aifs_daily", "fuxi_s2s", "ifs", "ngcm"]),
        (["aifs", "aifs_single_v2", "graphcast", "fuxi"], []),
    ],
)
def test_models_without_live_forecast(names: list[str], expected: list[str]) -> None:
    """Guards the alias normalization: no blendable-only model may accidentally
    resolve to a live forecast registry entry (and vice versa)."""
    assert job_submission.models_without_live_forecast(names) == expected


@pytest.mark.asyncio
async def test_create_blend_rejects_non_model_source(client, user_id: str, _stub_runner) -> None:
    from fastapi import HTTPException

    obs_id = await _seed_source("obs", "ERA5 India", "gs://data/obs/india")

    with pytest.raises(HTTPException) as exc:
        await create_blend_for_user(
            BlendCreate(
                name="bad",
                obs_dataset_id=obs_id,
                model_ids=[obs_id],  # an obs source is not a valid model
                params=BlendParams(training_years="2024", cv_holdout_years="2024"),
            ),
            user_id,
        )
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    ("metadata", "expected"),
    [
        ({"start_year": 1990, "end_year": 2020}, (1990, 2020)),
        ({"start_year": "1990", "end_year": "2020"}, (1990, 2020)),
        ({"start_year": "unknown", "end_year": 2020}, (None, 2020)),
        ({"start_year": {"a": 1}, "end_year": 2020}, (None, 2020)),
        ({"start_year": True, "end_year": 2020}, (None, 2020)),
        ({}, (None, None)),
    ],
)
def test_source_year_range_parses_registered_years(
    metadata: dict, expected: tuple[int | None, int | None]
) -> None:
    """Source metadata is a free-form caller-supplied dict, so a year that isn't
    one must read as unregistered rather than reaching the coverage arithmetic
    and 500ing."""
    assert job_submission.source_year_range({"metadata": metadata}) == expected


@pytest.mark.asyncio
async def test_create_blend_hides_another_users_private_obs_source(
    client, user_id: str, _stub_runner
) -> None:
    """An obs source id is not a capability: the coverage error would otherwise
    disclose a private source's registered years to anyone holding its id."""
    from fastapi import HTTPException

    from ai_almanac.server.db import get_db

    obs_id = await _seed_source("obs", "Someone else's ERA5", "gs://data/obs/india", (1990, 2024))
    gencast_id = await _seed_source("model", "GenCast", "gs://data/models/gencast", (2000, 2024))
    async with get_db() as conn:
        await conn.execute(
            text(
                "UPDATE data_sources SET owner_id = 'another-user', visibility = 'private' "
                "WHERE id = :id"
            ),
            {"id": obs_id},
        )

    with pytest.raises(HTTPException) as exc:
        await create_blend_for_user(
            BlendCreate(
                name="peek",
                obs_dataset_id=obs_id,
                model_ids=[gencast_id],
                params=BlendParams(training_years="2019:2024", cv_holdout_years="2024"),
            ),
            user_id,
        )
    assert exc.value.status_code == 404
    assert "1990" not in str(exc.value.detail)
