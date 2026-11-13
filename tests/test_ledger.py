"""The ledger: files are stored exactly as produced and the chain is stamped only into
downloads, drift is detected on verify, seals are role-keyed, and approvals are
recorded only for what a person approved."""

from __future__ import annotations

import io
import uuid
from pathlib import Path

import pytest

from ai_almanac.server.services import ledger, provenance, workspaces
from tests.helpers import ledger_reads
from tests.helpers.files import write_nc, write_png
from tests.helpers.records import seed_source
from tests.helpers.workspaces import (
    register_file,
    workspace_with_session,
)


@pytest.mark.parametrize("writer", [write_nc, write_png])
def test_a_stamped_copy_carries_the_chain(tmp_path: Path, writer) -> None:
    path = writer(tmp_path / ("a.nc" if writer is write_nc else "a.png"))
    assert provenance.stamp_history(path, [{"primitive": "x", "step_hash": "1"}])
    assert ledger_reads.read_history(path) == [{"primitive": "x", "step_hash": "1"}]


def test_tampering_with_data_changes_hash_and_strip_removes_foreign_history(tmp_path: Path) -> None:
    path = write_nc(tmp_path / "a.nc")
    before = provenance.content_hash(path)
    write_nc(path, seed=2.0)
    assert provenance.content_hash(path) != before

    import h5netcdf

    with h5netcdf.File(path, "a") as handle:
        handle.attrs["weather_skills_history"] = "[]"
        handle.attrs[provenance.HISTORY_ATTR] = "[]"
    assert provenance.strip_history(path)
    with h5netcdf.File(path, "r") as handle:
        assert provenance.HISTORY_ATTR not in handle.attrs
        assert "weather_skills_history" not in handle.attrs


def test_step_hash_is_keyed_by_role_and_covers_parents() -> None:
    inputs = [("forecast", "a"), ("observations", "b")]
    base = provenance.step_hash("p", "1.0.0", "ph", inputs, None, ["s1"])
    assert base == provenance.step_hash("p", "1.0.0", "ph", list(reversed(inputs)), None, ["s1"])
    swapped = [("forecast", "b"), ("observations", "a")]
    assert base != provenance.step_hash("p", "1.0.0", "ph", swapped, None, ["s1"])
    assert base != provenance.step_hash("p", "1.0.0", "ph", inputs, None, ["s2"])
    assert base != provenance.step_hash("p", "1.0.1", "ph", inputs, None, ["s1"])
    assert base != provenance.step_hash("p", "1.0.0", "ph", inputs, "sha256:env", ["s1"])


async def _derive(user_id: str, workspace_id: str, source: dict, path: Path) -> dict:
    """Record ``path`` as made from ``source`` and show it in the workspace."""
    _, [derived] = await ledger.write_step(
        ledger.StepDraft(
            primitive="derive.test", version="1.0.0", params={}, inputs=[("dataset", source)]
        ),
        ledger.Attribution.by_user(user_id),
        [ledger.OutputFile(path=path, label="derived", kind="dataset")],
    )
    await workspaces.add_artifacts(workspace_id, [(derived, "derived")])
    return derived


@pytest.mark.asyncio
async def test_workspace_chain_registers_verifies_and_detects_tampering(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]

    obs = await register_file(user_id, wid, write_nc(tmp_path / "obs.nc"), "obs_2026")
    stored = ledger.local_path(obs)
    assert provenance.content_hash(stored) == obs["content_hash"]
    derived = await _derive(user_id, wid, obs, write_nc(tmp_path / "derived.nc", seed=2.0))

    response = await client.get(f"/workspaces/{wid}", headers=as_new_user)
    listing = {a["label"]: a for a in response.json()["artifacts"]}
    assert listing["derived"]["breadcrumb"] == ["fetch.test", "derive.test"]

    response = await client.get(
        f"/workspaces/{wid}/artifacts/{derived['id']}/provenance",
        headers=as_new_user,
        params={"verify": "true"},
    )
    body = response.json()
    assert {s["verification"] for s in body["steps"]} == {"verified"}
    assert "obs_2026" in body["lineage"] and "derive.test v1.0.0" in body["lineage"]
    [derive_step] = [s for s in body["steps"] if s["primitive"] == "derive.test"]
    assert derive_step["inputs"][0]["role"] == "dataset"

    download = await client.get(
        f"/workspaces/{wid}/artifacts/{obs['id']}/download", headers=as_new_user
    )
    copy = tmp_path / "download.nc"
    copy.write_bytes(download.content)
    assert [e["primitive"] for e in ledger_reads.read_history(copy)] == ["fetch.test"]
    assert ledger_reads.read_history(stored) is None, "the stored file is left as produced"
    assert provenance.content_hash(stored) == obs["content_hash"]

    write_nc(stored, seed=3.0)
    response = await client.get(
        f"/workspaces/{wid}/artifacts/{derived['id']}/provenance",
        headers=as_new_user,
        params={"verify": "true"},
    )
    by_primitive = {s["primitive"]: s["verification"] for s in response.json()["steps"]}
    assert by_primitive["fetch.test"] == "hash_mismatch"
    assert by_primitive["derive.test"] == "hash_mismatch"

    other = {"X-Forwarded-User": "someone-else"}
    assert (await client.get(f"/workspaces/{wid}", headers=other)).status_code == 404


@pytest.mark.asyncio
async def test_approvals_are_recorded_only_for_what_a_human_approved(user_id) -> None:
    by_rest = await ledger.record_approval(
        ledger.Attribution.by_user(user_id), "submit_x", {"a": 1}
    )
    approval = await ledger_reads.get_approval(by_rest)
    assert approval["payload_hash"] == provenance.params_hash({"a": 1})
    unapproved = ledger.Attribution.by_assistant(user_id, "s1", approved=False, turn=None)
    assert await ledger.record_approval(unapproved, "rerun_job", {"a": 1}) is None


def test_under_gcs_an_artifact_is_downloaded_to_be_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BucketOnly:
        def artifact_path(self, key):
            return None

        def open_artifact(self, key):
            return io.BytesIO(b"bytes in the bucket") if key == "u/abc.csv" else None

    monkeypatch.setattr(ledger, "get_storage", BucketOnly)
    artifact = {"location_kind": ledger.OWNED, "path": "u/abc.csv", "filename": "table.csv"}
    staged = ledger.stage(artifact, tmp_path)
    assert staged == tmp_path / "table.csv"
    assert staged.read_bytes() == b"bytes in the bucket"
    assert ledger.stage({**artifact, "path": "u/gone.csv"}, tmp_path) is None


@pytest.mark.asyncio
async def test_a_run_submitted_by_hand_gets_its_step_at_submission(client, as_new_user) -> None:
    obs = await seed_source("obs", f"obs-{uuid.uuid4().hex[:8]}", years=(1980, 2024))
    models = [
        await seed_source("model", f"m{n}-{uuid.uuid4().hex[:8]}", years=(1980, 2024))
        for n in range(2)
    ]
    body = {
        "name": "India blend",
        "obs_dataset_id": obs,
        "model_ids": models,
        "params": {
            "training_years": "2000:2019",
            "cv_holdout_years": "2000:2019",
            "true_holdout_years": "2020,2021",
        },
    }

    response = await client.post("/blends", json=body, headers=as_new_user)

    assert response.status_code < 400, response.text
    step = await ledger.job_step(response.json()["id"])
    assert step["primitive"] == "blend.job" and step["actor_kind"] == "user"
    assert step["completed_at"] is None, "outputs are attached when the job publishes"
    assert (await ledger_reads.get_approval(step["approval_id"]))["kind"] == "submit_blend"
