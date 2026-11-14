"""A workspace holds another user's run only while that user shares it."""

from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest
import sqlalchemy as sa

from ai_almanac.paths import database_path
from ai_almanac.server import tables
from ai_almanac.server.db import get_db
from ai_almanac.server.services import ledger, workspaces
from tests.helpers.records import BENCHMARK_CONFIG, insert_job
from tests.helpers.workspaces import workspace_with_session, write_job_outputs


async def _signed_in(client) -> tuple[dict[str, str], str]:
    headers = {"X-Forwarded-User": f"pytest-{uuid4()}"}
    await client.get("/chat/sessions", headers=headers)
    async with get_db() as conn:
        user_id = (
            await conn.execute(
                sa.select(tables.users.c.id).where(
                    tables.users.c.external_id == headers["X-Forwarded-User"]
                )
            )
        ).scalar_one()
    return headers, user_id


async def _publish(job_id: str) -> list[dict]:
    with sqlite3.connect(database_path()) as conn:
        conn.execute("UPDATE jobs SET status = 'complete' WHERE id = ?", (job_id,))
        conn.commit()
    write_job_outputs(job_id, 1.0)
    await workspaces.on_job_published(job_id)
    return [artifact for artifact, _ in await ledger.complete_job_step(job_id)]


@pytest.mark.parametrize("published_before_unsharing", [False, True])
@pytest.mark.asyncio
async def test_an_unshared_run_leaves_the_workspaces_it_was_shared_into(
    client, published_before_unsharing: bool
) -> None:
    owner, owner_id = await _signed_in(client)
    reader, _ = await _signed_in(client)
    job_id = insert_job(owner_id, BENCHMARK_CONFIG, status="running", visibility="shared")
    workspace, _ = await workspace_with_session(client, reader)
    wid = workspace["id"]
    linked = await client.post(f"/workspaces/{wid}/jobs", headers=reader, json={"job_id": job_id})
    assert linked.status_code == 200, linked.text

    published = await _publish(job_id) if published_before_unsharing else []
    unshared = await client.post(f"/jobs/{job_id}/unshare", headers=owner)
    assert unshared.status_code == 200, unshared.text
    published = published or await _publish(job_id)
    assert published

    detail = (await client.get(f"/workspaces/{wid}", headers=reader)).json()
    assert detail["jobs"] == [] and detail["artifacts"] == []
    for artifact in published:
        base = f"/workspaces/{wid}/artifacts/{artifact['id']}"
        assert (await client.get(f"{base}/download", headers=reader)).status_code == 404
        assert (await client.get(f"{base}/provenance", headers=reader)).status_code == 404


@pytest.mark.asyncio
async def test_unsharing_keeps_the_run_in_its_owners_workspace(client) -> None:
    owner, owner_id = await _signed_in(client)
    job_id = insert_job(owner_id, BENCHMARK_CONFIG, visibility="shared")
    write_job_outputs(job_id, 1.0)
    workspace, _ = await workspace_with_session(client, owner)
    await workspaces.link_job(workspace["id"], job_id)

    await client.post(f"/jobs/{job_id}/unshare", headers=owner)

    detail = (await client.get(f"/workspaces/{workspace['id']}", headers=owner)).json()
    assert [job["id"] for job in detail["jobs"]] == [job_id]
    assert {artifact["job_id"] for artifact in detail["artifacts"]} == {job_id}


@pytest.mark.asyncio
async def test_a_conversation_promoted_to_a_workspace_brings_only_runs_still_shared(
    client,
) -> None:
    owner, owner_id = await _signed_in(client)
    reader, reader_id = await _signed_in(client)
    theirs = insert_job(owner_id, BENCHMARK_CONFIG, visibility="shared")
    mine = insert_job(reader_id, BENCHMARK_CONFIG)
    scope = {"kind": "benchmark_run_group", "key": "run-1", "job_ids": [theirs, mine]}
    session = await client.post(
        "/chat/sessions", headers=reader, json={"title": "Their run", "scope": scope}
    )
    assert session.status_code == 201, session.text
    await client.post(f"/jobs/{theirs}/unshare", headers=owner)

    promoted = await client.post(
        "/workspaces/promote", headers=reader, json={"session_id": session.json()["id"]}
    )

    assert promoted.status_code == 201, promoted.text
    detail = (await client.get(f"/workspaces/{promoted.json()['id']}", headers=reader)).json()
    assert [job["id"] for job in detail["jobs"]] == [mine]
