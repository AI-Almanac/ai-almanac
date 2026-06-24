"""blend_domain.get_blend_results — pooled summary parsing and artifact read.

A pure parser test plus an integration test that writes a summary CSV to the
local artifact store, indexes it, and confirms the tool returns parsed skill
rows ordered blend-first along with the artifact listing.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from ai_almanac.server.services import blend_domain
from ai_almanac.server.services.benchmark_state import BenchmarkScope

_SUMMARY_CSV = (
    "model,auc,brier_skill,auc_week1,auc_week2,auc_week3,auc_week4,auc_later\n"
    "aifs_raw,0.78,0.10,0.88,0.82,0.76,0.72,0.65\n"
    "blended_model,0.82,0.15,0.90,0.85,0.80,0.78,0.70\n"
)


def test_parse_pooled_summary_orders_blend_first() -> None:
    rows = blend_domain._parse_pooled_summary(_SUMMARY_CSV)
    assert [r["model"] for r in rows] == ["blended_model", "aifs_raw"]
    blend = rows[0]
    assert blend["is_blend"] is True
    assert blend["auc"] == 0.82
    assert blend["brier_skill"] == 0.15
    assert blend["auc_by_lead"] == [0.90, 0.85, 0.80, 0.78, 0.70]


def test_parse_pooled_summary_handles_empty() -> None:
    assert blend_domain._parse_pooled_summary("") == []
    assert blend_domain._parse_pooled_summary("model,auc\n") == []


async def _insert_blend_job(user_id: str, job_id: str, status: str = "complete") -> None:
    from ai_almanac.server.db import get_db

    now = datetime.now(UTC).isoformat()
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO jobs (id, user_id, dataset_id, job_type, status, "
                "config_json, created_at) VALUES (:id, :uid, 'obs-1', 'blend', "
                ":status, '{}', :now)"
            ),
            {"id": job_id, "uid": user_id, "status": status, "now": now},
        )


async def _index_artifact(job_id: str, kind: str, filename: str, size: int) -> None:
    from ai_almanac.server.db import get_db

    now = datetime.now(UTC).isoformat()
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO job_artifacts (id, job_id, kind, filename, media_type, "
                "size_bytes, checksum, storage_key, created_at) VALUES "
                "(:id, :job_id, :kind, :filename, 'text/csv', :size, 'x', :key, :now)"
            ),
            {
                "id": str(uuid.uuid4()),
                "job_id": job_id,
                "kind": kind,
                "filename": filename,
                "size": size,
                "key": f"{job_id}/{kind}/{filename}",
                "now": now,
            },
        )


@pytest.mark.asyncio
async def test_get_blend_results_reads_summary(client, user_id: str) -> None:
    from ai_almanac.server.services.storage import get_storage

    job_id = str(uuid.uuid4())
    await _insert_blend_job(user_id, job_id)

    storage = get_storage()
    path = storage.result_file_path(job_id, "output", "summary_models_pooled.csv")
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SUMMARY_CSV)
    await _index_artifact(job_id, "output", "summary_models_pooled.csv", len(_SUMMARY_CSV))

    scope = BenchmarkScope(kind="blend_setup", key="setup")
    result = await blend_domain.get_blend_results(job_id, user_id, scope)

    assert result["job_id"] == job_id
    assert result["skill"][0]["is_blend"] is True
    assert any(
        a["filename"] == "summary_models_pooled.csv" for a in result["artifacts"]
    )


@pytest.mark.asyncio
async def test_get_blend_results_rejects_incomplete(client, user_id: str) -> None:
    job_id = str(uuid.uuid4())
    await _insert_blend_job(user_id, job_id, status="running")
    scope = BenchmarkScope(kind="blend_setup", key="setup")
    result = await blend_domain.get_blend_results(job_id, user_id, scope)
    assert "not complete" in result["error"]
