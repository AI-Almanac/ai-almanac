"""skill_scores service — ROMP skill-score CSV parsing and the job endpoint.

Pure parser tests over literal CSVs (including the blank-cell form pandas
writes for NaN, and model names containing underscores), a service test over a
fake storage backend, and endpoint tests covering the probabilistic,
deterministic-empty, and incomplete-job cases.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from ai_almanac.server.services import skill_scores

_OVERALL_CSV = (
    "Fair_Brier_Score,Fair_Brier_Skill_Score,Fair_RPS,Fair_RPS_Skill_Score,AUC,AUC_ref\n"
    "0.1234,0.2100,0.0890,0.1500,0.8200,0.5100\n"
)

_BINNED_CSV = (
    "Bin,clean_bins,Fair_Brier_Skill_Score,AUC,AUC_ref,"
    "Fair_Brier_Score_Forecast,Fair_Brier_Score_Climatology\n"
    "Days 1-5,1-5,0.31,0.88,0.52,0.09,0.13\n"
    "Days 6-10,6-10,0.18,0.79,0.51,0.11,0.14\n"
    "Days 11-15,11-15,,,,,\n"
)


# ---------------------------------------------------------------------------
# Pure parsers
# ---------------------------------------------------------------------------


def test_parse_overall_maps_to_romp_yaml_ids() -> None:
    scores = skill_scores.parse_overall_csv(_OVERALL_CSV)
    assert scores == {
        "brier_score": 0.1234,
        "brier_skill_score": 0.21,
        "ranked_probability_score": 0.089,
        "ranked_probability_skill_score": 0.15,
        "auc": 0.82,
        "auc_ref": 0.51,
    }


def test_parse_overall_handles_empty() -> None:
    assert skill_scores.parse_overall_csv("") == {}
    assert skill_scores.parse_overall_csv("Fair_Brier_Score,AUC\n") == {}


def test_parse_binned_extracts_lead_days_and_blank_cells() -> None:
    bins = skill_scores.parse_binned_csv(_BINNED_CSV)
    assert [b.label for b in bins] == ["1-5", "6-10", "11-15"]
    assert [b.lead_day_min for b in bins] == [1, 6, 11]
    assert [b.lead_day_max for b in bins] == [5, 10, 15]
    assert bins[0].brier_skill_score == 0.31
    assert bins[0].auc == 0.88
    # pandas writes NaN as an empty cell; it must survive as None, not 0.0.
    assert bins[2].brier_skill_score is None
    assert bins[2].auc is None
    assert bins[2].brier_score_climatology is None


def test_parse_binned_sorts_by_lead_day_not_lexically() -> None:
    scrambled = (
        "Bin,clean_bins,Fair_Brier_Skill_Score,AUC,AUC_ref,"
        "Fair_Brier_Score_Forecast,Fair_Brier_Score_Climatology\n"
        "Days 11-15,11-15,0.1,0.7,0.5,0.1,0.1\n"
        "Days 1-5,1-5,0.3,0.9,0.5,0.1,0.1\n"
    )
    assert [b.lead_day_min for b in skill_scores.parse_binned_csv(scrambled)] == [1, 11]


def test_parse_binned_drops_tail_bins() -> None:
    with_tails = (
        "Bin,clean_bins,Fair_Brier_Skill_Score,AUC,AUC_ref,"
        "Fair_Brier_Score_Forecast,Fair_Brier_Score_Climatology\n"
        "Before day 1,Before day 1,0.1,0.7,0.5,0.1,0.1\n"
        "Days 1-5,1-5,0.3,0.9,0.5,0.1,0.1\n"
        "After day 30,After day 30,0.1,0.7,0.5,0.1,0.1\n"
    )
    assert [b.label for b in skill_scores.parse_binned_csv(with_tails)] == ["1-5"]


def test_parse_float_rejects_non_finite() -> None:
    assert skill_scores._parse_float("nan") is None
    assert skill_scores._parse_float("inf") is None
    assert skill_scores._parse_float("  ") is None
    assert skill_scores._parse_float("0.5") == 0.5


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "kind", "model", "window"),
    [
        ("overall_skill_scores_fuxi_1-15.csv", "overall", "fuxi", "1-15"),
        ("binned_skill_scores_fuxi_16-30.csv", "binned", "fuxi", "16-30"),
        # Model tokens contain underscores — romp_safe_model_name collapses
        # whitespace runs to "_", so a naive split() would mangle these.
        (
            "overall_skill_scores_AIFS_Single_v2_1-15.csv",
            "overall",
            "AIFS_Single_v2",
            "1-15",
        ),
        # Legacy comma-separated window token.
        ("binned_skill_scores_fuxi_1,15.csv", "binned", "fuxi", "1,15"),
    ],
)
def test_skill_file_regex(filename: str, kind: str, model: str, window: str) -> None:
    match = skill_scores._SKILL_FILE_RE.match(filename)
    assert match is not None
    assert match.group("kind") == kind
    assert match.group("model") == model
    assert match.group("window") == window


@pytest.mark.parametrize(
    "filename",
    [
        "spatial_metrics_fuxi_1-15.nc",
        "overall_skill_scores_fuxi.csv",
        "summary_models_pooled.csv",
    ],
)
def test_skill_file_regex_rejects_others(filename: str) -> None:
    assert skill_scores._SKILL_FILE_RE.match(filename) is None


# ---------------------------------------------------------------------------
# Service over a fake backend
# ---------------------------------------------------------------------------


class FakeStorage:
    """Minimal duck-typed storage exposing only what the service touches."""

    def __init__(self, files: dict[str, str]) -> None:
        self._files = files

    def list_result_files(self, job_id: str) -> list[tuple[str, str]]:
        return [("output", name) for name in sorted(self._files)]

    def read_result_text(self, job_id: str, kind: str, filename: str) -> str | None:
        if kind != "output":
            return None
        return self._files.get(filename)


def test_compute_groups_by_model_and_window() -> None:
    storage = FakeStorage(
        {
            "overall_skill_scores_AIFS_Single_1-15.csv": _OVERALL_CSV,
            "binned_skill_scores_AIFS_Single_1-15.csv": _BINNED_CSV,
            "overall_skill_scores_AIFS_Single_16-30.csv": _OVERALL_CSV,
            "spatial_metrics_AIFS_Single_1-15.nc": "not a csv",
        }
    )
    result = skill_scores.compute_job_skill_scores("job-1", storage)  # type: ignore[arg-type]

    assert [(w.model, w.window) for w in result.windows] == [
        ("AIFS_Single", "1-15"),
        ("AIFS_Single", "16-30"),
    ]
    first = result.windows[0]
    assert first.overall["auc"] == 0.82
    assert len(first.bins) == 3
    # Only the overall CSV exists for the second window.
    assert result.windows[1].bins == []


def test_compute_sorts_windows_numerically() -> None:
    storage = FakeStorage(
        {
            "overall_skill_scores_m_16-30.csv": _OVERALL_CSV,
            "overall_skill_scores_m_1-15.csv": _OVERALL_CSV,
        }
    )
    result = skill_scores.compute_job_skill_scores("job-1", storage)  # type: ignore[arg-type]
    assert [w.window for w in result.windows] == ["1-15", "16-30"]


def test_compute_returns_empty_for_deterministic_job() -> None:
    storage = FakeStorage({"spatial_metrics_fuxi_1-15.nc": "binary"})
    result = skill_scores.compute_job_skill_scores("job-1", storage)  # type: ignore[arg-type]
    assert result.windows == []
    assert result.job_id == "job-1"


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


_RUN_ID = "group-1"


async def _insert_job(user_id: str, job_id: str, status: str = "complete") -> None:
    """Insert a job carrying _RUN_ID.

    The run_id matters for the LLM-tool tests: a `benchmark_run_group` scope
    filters on `jobs.run_id == scope.key` (`benchmark_domain._scope_conditions`),
    so a job without one is invisible to the tool.
    """
    from ai_almanac.server.db import get_db

    now = datetime.now(UTC).isoformat()
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO jobs (id, user_id, dataset_id, job_type, status, "
                "config_json, run_id, created_at) VALUES (:id, :uid, 'obs-1', 'benchmark', "
                ":status, '{}', :run_id, :now)"
            ),
            {
                "id": job_id,
                "uid": user_id,
                "status": status,
                "run_id": _RUN_ID,
                "now": now,
            },
        )


def _write_output(job_id: str, filename: str, body: str) -> None:
    from ai_almanac.server.services.storage import get_storage

    path = get_storage().result_file_path(job_id, "output", filename)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


@pytest.mark.asyncio
async def test_endpoint_returns_parsed_scores(client, user_id: str, auth_headers) -> None:
    job_id = str(uuid.uuid4())
    await _insert_job(user_id, job_id)
    _write_output(job_id, "overall_skill_scores_fuxi_1-15.csv", _OVERALL_CSV)
    _write_output(job_id, "binned_skill_scores_fuxi_1-15.csv", _BINNED_CSV)

    resp = await client.get(f"/jobs/{job_id}/skill-scores", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == job_id
    assert len(body["windows"]) == 1
    window = body["windows"][0]
    assert window["model"] == "fuxi"
    assert window["window"] == "1-15"
    assert window["overall"]["brier_skill_score"] == 0.21
    assert [b["label"] for b in window["bins"]] == ["1-5", "6-10", "11-15"]
    assert window["bins"][2]["auc"] is None


@pytest.mark.asyncio
async def test_endpoint_returns_empty_not_404(client, user_id: str, auth_headers) -> None:
    """A deterministic job has no skill CSVs; that is empty, not an error."""
    job_id = str(uuid.uuid4())
    await _insert_job(user_id, job_id)

    resp = await client.get(f"/jobs/{job_id}/skill-scores", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["windows"] == []


@pytest.mark.asyncio
async def test_endpoint_rejects_incomplete(client, user_id: str, auth_headers) -> None:
    job_id = str(uuid.uuid4())
    await _insert_job(user_id, job_id, status="running")

    resp = await client.get(f"/jobs/{job_id}/skill-scores", headers=auth_headers)
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# LLM tool
# ---------------------------------------------------------------------------


def _scope():
    from ai_almanac.server.services.benchmark_state import BenchmarkScope

    return BenchmarkScope(kind="benchmark_run_group", key="group-1")


@pytest.mark.asyncio
async def test_llm_tool_returns_scores_with_interpretation_notes(client, user_id: str) -> None:
    from ai_almanac.server.services import benchmark_domain

    job_id = str(uuid.uuid4())
    await _insert_job(user_id, job_id)
    _write_output(job_id, "overall_skill_scores_fuxi_1-15.csv", _OVERALL_CSV)
    _write_output(job_id, "binned_skill_scores_fuxi_1-15.csv", _BINNED_CSV)

    result = await benchmark_domain.get_skill_scores(job_id, user_id, _scope())

    assert result["windows"][0]["overall"]["auc"] == 0.82
    # The notes exist so the model doesn't have to infer the semantics — and in
    # particular doesn't read an uncomputed metric as a passing one.
    assert "fair" in result["notes"]["scores_are_fair"].lower()
    assert "climatology" in result["notes"]["reference"].lower()
    assert "not" in result["notes"]["not_computed"].lower()


@pytest.mark.asyncio
async def test_llm_tool_points_at_the_other_tool_when_empty(client, user_id: str) -> None:
    from ai_almanac.server.services import benchmark_domain

    job_id = str(uuid.uuid4())
    await _insert_job(user_id, job_id)

    result = await benchmark_domain.get_skill_scores(job_id, user_id, _scope())
    assert "get_job_metrics" in result["error"]


@pytest.mark.asyncio
async def test_spatial_tool_points_at_skill_scores_when_empty(client, user_id: str) -> None:
    """The failure mode this feature exists to fix.

    A probabilistic job has no spatial NetCDF, so get_job_metrics finds nothing.
    Without redirection the assistant reports "no metrics" while the user is
    looking at a populated skill-score table.
    """
    from ai_almanac.server.services import benchmark_domain

    job_id = str(uuid.uuid4())
    await _insert_job(user_id, job_id)

    result = await benchmark_domain.get_job_metrics(job_id, user_id, _scope())
    assert "get_skill_scores" in result["error"]


@pytest.mark.asyncio
async def test_llm_tool_rejects_incomplete(client, user_id: str) -> None:
    from ai_almanac.server.services import benchmark_domain

    job_id = str(uuid.uuid4())
    await _insert_job(user_id, job_id, status="running")

    result = await benchmark_domain.get_skill_scores(job_id, user_id, _scope())
    assert "not complete" in result["error"]
