"""Chat blend HTTP endpoints — PATCH /blend/config and session surfacing.

Exercises the new session-attached blend wiring end to end over HTTP: seed obs
and model data sources, open a blend_setup chat session, patch its blend config,
and confirm the config validates and is surfaced on a subsequent session read.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import text


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


def _blend_scope() -> dict:
    return {"kind": "blend_setup", "key": str(uuid.uuid4()), "job_ids": []}


@pytest.mark.asyncio
async def test_patch_blend_config_validates_and_surfaces_on_session(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    obs_id = await _seed_source("obs", "ERA5 India", "india", 1990, 2024)
    gencast_id = await _seed_source("model", "GenCast", "india", 2000, 2024)

    created = await client.post(
        "/chat/sessions",
        headers=auth_headers,
        json={"title": "Blend", "scope": _blend_scope()},
    )
    assert created.status_code == 201
    session_id = created.json()["id"]

    patched = await client.patch(
        f"/chat/sessions/{session_id}/blend/config",
        headers=auth_headers,
        json={
            "name": "India blend",
            "obs_dataset_id": obs_id,
            "model_ids": [gencast_id],
            "training_years": "2015:2020",
            "cv_holdout_years": "2021,2022",
        },
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["blend_config"]["model_ids"] == [gencast_id]
    assert body["blend_config"]["region_id"] == "india"
    assert body["blend_validation"]["can_run"] is True

    # The persisted blend config is surfaced on a fresh session read.
    fetched = await client.get(
        f"/chat/sessions/{session_id}", headers=auth_headers
    )
    assert fetched.status_code == 200
    assert fetched.json()["blend_config"]["name"] == "India blend"
