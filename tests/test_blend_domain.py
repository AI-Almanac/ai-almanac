"""blend_domain — chat-driven blend configuration, validation, coverage.

Pure-function tests for coverage/year validation, plus an integration test that
seeds obs/model data sources and a chat session, then patches and validates the
session's blend config end to end.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from ai_almanac.server.services import blend_domain
from ai_almanac.server.services.blend_state import BlendRunSpec

# --- Pure: coverage + year validation -------------------------------------


def _model(start: int, end: int) -> dict:
    return {"id": "m", "name": "m", "region": "india", "start_year": start, "end_year": end}


def test_coverage_intersects_sources_and_reserves_climatology_runway() -> None:
    obs = {"start_year": 1990, "end_year": 2024}
    coverage = blend_domain._coverage(obs, [_model(2000, 2022), _model(2005, 2024)])
    assert coverage == {
        "start": 2005,  # latest start
        "end": 2022,  # earliest end
        # max(obs_start + 10, latest model start) = max(2000, 2005)
        "earliest_forecast": 2005,
    }


def test_coverage_none_without_year_metadata() -> None:
    obs = {"start_year": None, "end_year": None}
    assert blend_domain._coverage(obs, [_model(2000, 2020)]) is None
    assert blend_domain._coverage({"start_year": 2000, "end_year": 2020}, []) is None


def test_year_errors_flags_forecast_before_climatology_runway() -> None:
    coverage = {"start": 2005, "end": 2022, "earliest_forecast": 2015}
    spec = BlendRunSpec(training_years="2010:2012", cv_holdout_years="2013")
    errors = blend_domain._year_errors(spec, coverage)
    assert errors and "Climatology needs" in errors[0]


def test_year_errors_flags_malformed_spec() -> None:
    spec = BlendRunSpec(training_years="not-a-year", cv_holdout_years="2013")
    errors = blend_domain._year_errors(spec, None)
    assert errors and "training_years" in errors[0]


def test_year_errors_clean_when_within_coverage() -> None:
    coverage = {"start": 2005, "end": 2022, "earliest_forecast": 2015}
    spec = BlendRunSpec(training_years="2015:2020", cv_holdout_years="2021,2022")
    assert blend_domain._year_errors(spec, coverage) == []


def test_finalize_reports_missing_fields() -> None:
    spec = blend_domain._finalize_blend_config(BlendRunSpec(name="x"))
    assert spec.status == "collecting"
    assert set(spec.missing_fields) == {
        "observations",
        "models",
        "training_years",
        "cv_holdout_years",
    }


# --- Integration: patch + validate a session's blend config ---------------


async def _seed_source(
    kind: str, name: str, region: str, start_year: int, end_year: int
) -> str:
    from ai_almanac.server.db import get_db

    source_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    metadata = json.dumps({"start_year": start_year, "end_year": end_year})
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO data_sources "
                "(id, kind, name, path, region, metadata, location_type, status, "
                "validation_error, created_at, updated_at) "
                "VALUES (:id, :kind, :name, :path, :region, :metadata, 'gcs', "
                "'ready', NULL, :now, :now)"
            ),
            {
                "id": source_id,
                "kind": kind,
                "name": name,
                "path": f"gs://data/{kind}/{name}",
                "region": region,
                "metadata": metadata,
                "now": now,
            },
        )
    return source_id


async def _seed_session(user_id: str) -> str:
    from ai_almanac.server.db import get_db

    session_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    scope = json.dumps({"kind": "blend_setup", "key": session_id, "job_ids": []})
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO chat_sessions (id, user_id, scope, created_at, updated_at) "
                "VALUES (:id, :uid, :scope, :now, :now)"
            ),
            {"id": session_id, "uid": user_id, "scope": scope, "now": now},
        )
    return session_id


@pytest.mark.asyncio
async def test_update_and_validate_blend_config_runnable(client, user_id: str) -> None:
    obs_id = await _seed_source("obs", "ERA5 India", "india", 1990, 2024)
    gencast_id = await _seed_source("model", "GenCast", "india", 2000, 2024)
    # Different region — must not be selectable once obs is India.
    other_id = await _seed_source("model", "Sahel Model", "sahel", 2000, 2024)
    session_id = await _seed_session(user_id)
    scope = blend_domain.BenchmarkScope(kind="blend_setup", key=session_id)

    patched = await blend_domain.update_blend_config(
        {
            "name": "India blend",
            "obs_dataset_id": obs_id,
            "model_ids": [gencast_id, other_id],
            "training_years": "2015:2020",
            "cv_holdout_years": "2021,2022",
        },
        user_id,
        scope,
        session_id,
    )

    cfg = patched["blend_config"]
    # The off-region model is dropped; only the India model survives.
    assert cfg["model_ids"] == [gencast_id]
    assert cfg["region_id"] == "india"
    assert patched["blend_validation"]["can_run"] is True

    # State persisted: re-reading validates the same runnable config.
    revalidated = await blend_domain.validate_blend_config(user_id, scope, session_id)
    assert revalidated["blend_validation"]["can_run"] is True
    assert revalidated["blend_config"]["name"] == "India blend"


@pytest.mark.asyncio
async def test_validate_blend_config_flags_bad_years(client, user_id: str) -> None:
    obs_id = await _seed_source("obs", "ERA5 India", "india", 1990, 2024)
    gencast_id = await _seed_source("model", "GenCast", "india", 2000, 2024)
    session_id = await _seed_session(user_id)
    scope = blend_domain.BenchmarkScope(kind="blend_setup", key=session_id)

    patched = await blend_domain.update_blend_config(
        {
            "name": "India blend",
            "obs_dataset_id": obs_id,
            "model_ids": [gencast_id],
            # Forecast before the 10-year climatology runway (obs starts 1990,
            # models start 2000 → earliest forecast 2000).
            "training_years": "1995",
            "cv_holdout_years": "1996",
        },
        user_id,
        scope,
        session_id,
    )
    validation = patched["blend_validation"]
    assert validation["can_run"] is False
    assert any("Climatology needs" in e for e in validation["errors"])
