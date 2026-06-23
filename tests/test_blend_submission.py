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


async def _seed_source(kind: str, name: str, path: str) -> str:
    from ai_almanac.server.db import get_db

    source_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, kind, name, path, region, metadata, location_type, status, "
                "validation_error, created_at, updated_at) "
                "VALUES (:id, :kind, :name, :path, 'india', '{}', 'gcs', 'ready', "
                "NULL, :now, :now)"
            ),
            {"id": source_id, "kind": kind, "name": name, "path": path, "now": now},
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
        row = (
            (
                await conn.execute(sa.select(jobs).where(jobs.c.id == out.id))
            )
            .mappings()
            .fetchone()
        )
    assert row["job_type"] == "blend"
    assert row["status"] == "running"  # remote runner is live once spawned
    assert row["runner"] == "modal"
    config = json.loads(row["config_json"])
    assert config["modal_function"] == "run_blend"
    assert config["modal_app"]  # blend app name travels on the job
    assert config["model_dirs"] == [
        "gs://data/models/gencast",
        "gs://data/models/aifs",
    ]
    assert config["model_names"] == ["gencast", "aifs"]
    assert config["blend_params"]["training_years"] == "2019:2024"


@pytest.mark.asyncio
async def test_create_blend_rejects_non_model_source(
    client, user_id: str, _stub_runner
) -> None:
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
