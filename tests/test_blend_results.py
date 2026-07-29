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

# The real column set the blend writes, so the parser is exercised against the
# shape it actually meets. Values are the Ethiopia aifs+fuxi run, rounded.
_FULL_HEADER = (
    "id,brier,rps,auc,n,lat,lon,pietra,"
    "brier_week1,brier_week2,brier_week3,brier_week4,brier_later,"
    "auc_week1,auc_week2,auc_week3,auc_week4,auc_later,"
    "model,cv_method,brier_skill,rps_skill,AUC diff"
)
_FULL_BLEND = (
    "ALL,0.576,0.479,0.836,26622,,,0.487,"
    "0.088,0.113,0.118,0.119,0.137,"
    "0.890,0.787,0.737,0.716,0.881,"
    "blended_model,global,0.037,0.129,0.62"
)
_FULL_BASELINE = (
    "ALL,0.598,0.551,0.830,26622,,,0.485,"
    "0.091,0.114,0.117,0.120,0.156,"
    "0.886,0.783,0.735,0.714,0.878,"
    "unc_clim_raw,global,0.0,0.0,0.0"
)


def _full_csv(*rows: str) -> str:
    return "\n".join([_FULL_HEADER, *rows]) + "\n"


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


def test_parse_pooled_summary_keeps_pooled_scores() -> None:
    """The Ranked Probability Skill Score is the metric that shows the blend's edge."""
    rows = blend_domain._parse_pooled_summary(_full_csv(_FULL_BLEND, _FULL_BASELINE))
    blend = rows[0]
    assert blend["rps_skill"] == 0.129
    assert blend["rps"] == 0.479
    assert blend["brier"] == 0.576
    assert blend["pietra"] == 0.487
    assert blend["observations"] == 26622
    assert blend["brier_by_lead"] == [0.088, 0.113, 0.118, 0.119, 0.137]


def test_parse_pooled_summary_flags_the_baseline() -> None:
    rows = blend_domain._parse_pooled_summary(_full_csv(_FULL_BLEND, _FULL_BASELINE))
    assert [r["model"] for r in rows if r["is_baseline"]] == ["unc_clim_raw"]


def test_parse_pooled_summary_derives_per_lead_brier_skill() -> None:
    rows = blend_domain._parse_pooled_summary(_full_csv(_FULL_BLEND, _FULL_BASELINE))
    blend = rows[0]
    assert blend["brier_skill_by_lead"][0] == pytest.approx(1 - 0.088 / 0.091)
    # Week 3 is genuinely negative — the blend is slightly worse than climatology
    # there, and the chart has to be able to show that.
    assert blend["brier_skill_by_lead"][2] == pytest.approx(1 - 0.118 / 0.117)
    assert blend["brier_skill_by_lead"][2] < 0


def test_parse_pooled_summary_scores_baseline_at_zero() -> None:
    rows = blend_domain._parse_pooled_summary(_full_csv(_FULL_BLEND, _FULL_BASELINE))
    baseline = next(r for r in rows if r["is_baseline"])
    assert baseline["brier_skill_by_lead"] == [0.0, 0.0, 0.0, 0.0, 0.0]


def test_parse_pooled_summary_without_baseline_yields_no_lead_skill() -> None:
    """Inventing a reference would silently change what the numbers claim."""
    rows = blend_domain._parse_pooled_summary(_full_csv(_FULL_BLEND))
    assert rows[0]["brier_skill_by_lead"] == [None] * 5
    assert rows[0]["brier_by_lead"][0] == 0.088


def test_parse_pooled_summary_survives_zero_baseline_brier() -> None:
    zeroed = _FULL_BASELINE.replace("0.091,0.114,0.117,0.120,0.156", "0,0,0,0,0")
    rows = blend_domain._parse_pooled_summary(_full_csv(_FULL_BLEND, zeroed))
    assert rows[0]["brier_skill_by_lead"] == [None] * 5


def test_parse_pooled_summary_treats_blanks_as_missing() -> None:
    """pandas writes NaN as an empty string; float('') must not become 0.0."""
    blanks = "ALL,,,0.7,,,,,0.1,0.1,0.1,0.1,0.1,0.7,0.6,0.55,0.54,0.6,blended_model,global,,,"
    rows = blend_domain._parse_pooled_summary(_full_csv(blanks))
    assert rows[0]["rps"] is None
    assert rows[0]["rps_skill"] is None
    assert rows[0]["pietra"] is None
    assert rows[0]["observations"] is None


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
    assert any(a["filename"] == "summary_models_pooled.csv" for a in result["artifacts"])


@pytest.mark.asyncio
async def test_get_blend_results_rejects_incomplete(client, user_id: str) -> None:
    job_id = str(uuid.uuid4())
    await _insert_blend_job(user_id, job_id, status="running")
    scope = BenchmarkScope(kind="blend_setup", key="setup")
    result = await blend_domain.get_blend_results(job_id, user_id, scope)
    assert "not complete" in result["error"]
