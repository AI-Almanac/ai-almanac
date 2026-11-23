"""A finished job's files reach its workspaces: read as a stream when they are kept
remotely, and retried when recording them did not finish."""

from __future__ import annotations

import asyncio
import hashlib
import sqlite3
from pathlib import Path

import pytest

from ai_almanac.paths import database_path
from ai_almanac.server.services import ledger, netcdf, workspaces
from ai_almanac.server.services.artifacts import index_job_artifacts
from ai_almanac.server.services.storage import get_storage
from tests.helpers.records import BENCHMARK_CONFIG, insert_job
from tests.helpers.workspaces import workspace_with_session, write_job_outputs

CLIMATOLOGY = "climatology_onset_doy_1998-2024"


def _finish(job_id: str) -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute("UPDATE jobs SET status = 'complete' WHERE id = ?", (job_id,))
        conn.commit()


async def _running_job_in_workspace(client, as_new_user, user_id) -> tuple[str, str]:
    """A submitted job, linked into a workspace while it runs: (workspace id, job id)."""
    workspace, _ = await workspace_with_session(client, as_new_user)
    job_id = insert_job(user_id, BENCHMARK_CONFIG, status="running")
    await ledger.open_job_step(
        job_id, "benchmark", BENCHMARK_CONFIG, ledger.Attribution.by_user(user_id)
    )
    await workspaces.link_job(workspace["id"], job_id)
    return workspace["id"], job_id


async def _files_shown(client, headers, workspace_id: str) -> dict[str, dict]:
    body = (await client.get(f"/workspaces/{workspace_id}", headers=headers)).json()
    return {artifact["label"]: artifact for artifact in body["artifacts"]}


@pytest.mark.asyncio
async def test_remote_job_files_are_recorded_without_a_local_copy(
    client, as_new_user, user_id, monkeypatch
) -> None:
    def no_local_copies(*_args):
        raise AssertionError("a job's file was copied to local disk")

    monkeypatch.setattr(ledger, "local_path", lambda _artifact: None)
    monkeypatch.setattr(ledger, "stage", no_local_copies)
    workspace_id, job_id = await _running_job_in_workspace(client, as_new_user, user_id)
    write_job_outputs(job_id, 1.0)
    _finish(job_id)
    await index_job_artifacts(job_id)

    await workspaces.record_published_outputs()

    climatology = (await _files_shown(client, as_new_user, workspace_id))[CLIMATOLOGY]
    output, _ = get_storage().job_output_uri(job_id)
    stored = (Path(output) / f"{CLIMATOLOGY}.nc").read_bytes()
    record = (await ledger.get_artifacts(user_id, {climatology["id"]}))[climatology["id"]]
    assert record["content_hash"] == hashlib.sha256(stored).hexdigest()
    assert record["size_bytes"] == len(stored)
    assert record["summary"]["dims"], "the NetCDF is described from its header"


@pytest.mark.asyncio
async def test_files_whose_recording_failed_appear_after_a_wait(
    client, as_new_user, user_id, monkeypatch
) -> None:
    workspace_id, job_id = await _running_job_in_workspace(client, as_new_user, user_id)
    write_job_outputs(job_id, 1.0)
    _finish(job_id)
    await index_job_artifacts(job_id)
    reads = []
    describe = ledger._job_file_facts

    def out_of_memory(*args):
        reads.append(args)
        raise MemoryError("instance memory exceeded")

    monkeypatch.setattr(ledger, "_job_file_facts", out_of_memory)
    monkeypatch.setattr(workspaces, "RECORDING_LEASE_SECONDS", 3600.0)
    await workspaces.record_published_outputs()
    attempts = len(reads)
    await workspaces.record_published_outputs()

    assert await _files_shown(client, as_new_user, workspace_id) == {}
    assert len(reads) == attempts, "a failed recording is not retried before its lease runs out"

    monkeypatch.setattr(ledger, "_job_file_facts", describe)
    monkeypatch.setattr(workspaces, "RECORDING_LEASE_SECONDS", 0.0)
    await workspaces.record_published_outputs()

    assert CLIMATOLOGY in await _files_shown(client, as_new_user, workspace_id)


async def _published_job_in_workspace(client, as_new_user, user_id) -> tuple[str, str]:
    workspace_id, job_id = await _running_job_in_workspace(client, as_new_user, user_id)
    write_job_outputs(job_id, 1.0)
    _finish(job_id)
    await index_job_artifacts(job_id)
    return workspace_id, job_id


@pytest.mark.asyncio
async def test_instances_recording_at_once_read_a_jobs_files_once(
    client, as_new_user, user_id, monkeypatch
) -> None:
    workspace_id, _ = await _published_job_in_workspace(client, as_new_user, user_id)
    reads = []
    describe = ledger._job_file_facts

    def counted(*args):
        reads.append(args[2])
        return describe(*args)

    monkeypatch.setattr(ledger, "_job_file_facts", counted)

    await asyncio.gather(*(workspaces.record_published_outputs() for _ in range(3)))

    assert sorted(reads) == sorted(set(reads)), "each file is read by one instance"
    assert CLIMATOLOGY in await _files_shown(client, as_new_user, workspace_id)


@pytest.mark.asyncio
async def test_files_reach_the_workspace_when_recording_stopped_after_the_ledger(
    client, as_new_user, user_id, monkeypatch
) -> None:
    workspace_id, _ = await _published_job_in_workspace(client, as_new_user, user_id)
    add_to_workspace = workspaces._add_job_outputs

    async def stopped(*_args):
        raise RuntimeError("instance stopped")

    monkeypatch.setattr(workspaces, "_add_job_outputs", stopped)
    await workspaces.record_published_outputs()
    assert await _files_shown(client, as_new_user, workspace_id) == {}

    monkeypatch.setattr(workspaces, "_add_job_outputs", add_to_workspace)
    monkeypatch.setattr(workspaces, "RECORDING_LEASE_SECONDS", 0.0)
    await workspaces.record_published_outputs()

    assert CLIMATOLOGY in await _files_shown(client, as_new_user, workspace_id)


@pytest.mark.asyncio
async def test_a_remote_file_is_not_read_over_the_network_under_the_hdf5_lock(
    client, as_new_user, user_id, monkeypatch
) -> None:
    reads_under_lock = []
    open_bytes = ledger.open_bytes

    class Watched:
        def __init__(self, handle):
            self._handle = handle

        def read(self, *args):
            reads_under_lock.append(netcdf.HDF5_LOCK._is_owned())
            return self._handle.read(*args)

        def readinto(self, buffer):
            reads_under_lock.append(netcdf.HDF5_LOCK._is_owned())
            return self._handle.readinto(buffer)

        def __getattr__(self, name):
            return getattr(self._handle, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._handle.close()

    monkeypatch.setattr(ledger, "local_path", lambda _artifact: None)
    monkeypatch.setattr(ledger, "open_bytes", lambda artifact: Watched(open_bytes(artifact)))
    workspace_id, _ = await _published_job_in_workspace(client, as_new_user, user_id)

    await workspaces.record_published_outputs()

    assert CLIMATOLOGY in await _files_shown(client, as_new_user, workspace_id)
    assert reads_under_lock and not any(reads_under_lock)
