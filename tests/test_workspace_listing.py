"""How the workspace listing places a file under the runs it came from (D-R18)."""

from __future__ import annotations

import pytest

from ai_almanac.server.services import ledger, workspaces
from tests.helpers import ledger_reads


def _step(step_id: str, created_at: str, outputs: list[str], inputs: list[str], job_id=None):
    return {
        "id": step_id,
        "created_at": created_at,
        "primitive": "job" if job_id else "romp.compare",
        "job_id": job_id,
        "turn_id": None if job_id else "turn-analysis",
        "outputs": outputs,
        "inputs": [{"role": f"in{i}", "artifact_id": aid} for i, aid in enumerate(inputs)],
        "parent_step_ids": [],
    }


def _artifact(artifact_id: str, step_id: str) -> dict:
    return {
        "id": artifact_id,
        "label": artifact_id,
        "provenance_step_id": step_id,
        "storage_key": f"{artifact_id}.nc",
        "filename": f"{artifact_id}.nc",
        "kind": "metrics",
        "format": "netcdf",
        "added_at": "2026-09-25T00:00:00",
        "summary": {},
    }


STEPS = {
    "s-bench-a": _step("s-bench-a", "2026-09-01", ["a"], [], job_id="job-a"),
    "s-bench-b": _step("s-bench-b", "2026-09-02", ["b"], [], job_id="job-b"),
    "s-blend": _step("s-blend", "2026-09-03", ["blended"], ["a", "b"], job_id="job-blend"),
    "s-diff": _step("s-diff", "2026-09-04", ["diff"], ["a", "b"]),
    "s-fig": _step("s-fig", "2026-09-05", ["fig"], ["diff", "blended"]),
    "s-upload": _step("s-upload", "2026-09-06", ["upload"], []),
}


def _item(artifact_id: str, step_id: str) -> dict:
    return workspaces.listing_item(
        _artifact(artifact_id, step_id), STEPS, workspaces.producer_index(STEPS)
    )


def test_a_derived_file_is_placed_under_every_run_its_inputs_came_from() -> None:
    assert _item("diff", "s-diff")["run_job_ids"] == ["job-a", "job-b"]


def test_ancestry_stops_at_the_nearest_run() -> None:
    assert _item("blended", "s-blend")["run_job_ids"] == ["job-blend"]
    assert _item("fig", "s-fig")["run_job_ids"] == ["job-a", "job-b", "job-blend"]


def test_a_file_with_no_run_upstream_has_no_runs_and_keeps_its_turn() -> None:
    item = _item("upload", "s-upload")
    assert item["run_job_ids"] == []
    assert item["turn_id"] == "turn-analysis"


def test_identical_bytes_from_two_runs_belong_to_both() -> None:
    steps = {
        **STEPS,
        "s-bench-a2": _step("s-bench-a2", "2026-09-07", ["a"], [], job_id="job-a2"),
    }
    item = workspaces.listing_item(
        _artifact("a", "s-bench-a"), steps, workspaces.producer_index(steps)
    )
    assert item["run_job_ids"] == ["job-a", "job-a2"]


@pytest.mark.asyncio
async def test_approval_times_says_when_each_approval_was_given(user_id) -> None:
    approval_id = await ledger.record_approval(
        ledger.Attribution.by_user(user_id), "submit_benchmark", {"a": 1}
    )
    times = await workspaces.approval_times({approval_id, "unknown"})
    assert times == {approval_id: (await ledger_reads.get_approval(approval_id))["created_at"]}
    assert await workspaces.approval_times(set()) == {}
