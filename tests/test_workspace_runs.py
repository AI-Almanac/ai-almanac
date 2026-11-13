"""Runs in a workspace: linking one shows its files once however many runs made them,
the event stream reports what changed, and runs can be added by hand."""

from __future__ import annotations

import pytest

from ai_almanac.server.services import (
    ledger,
    workspaces,
)
from tests.helpers.records import BENCHMARK_CONFIG, insert_job
from tests.helpers.workspaces import (
    job_step_in_record,
    workspace_with_session,
    write_job_outputs,
)


@pytest.mark.asyncio
async def test_linked_jobs_get_semantic_labels_and_share_identical_outputs(
    client, as_new_user, user_id
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    job_ids = []
    for seed in (1.0, 2.0):
        job_id = insert_job(user_id, BENCHMARK_CONFIG)
        write_job_outputs(job_id, seed)
        await workspaces.link_job(wid, job_id)
        job_ids.append(job_id)

    body = (await client.get(f"/workspaces/{wid}", headers=as_new_user)).json()
    by_label = {a["label"]: a for a in body["artifacts"]}
    assert sorted(by_label) == [
        "climatology_onset_doy_1998-2024",
        "spatial_metrics_aifs_1-15",
        "spatial_metrics_aifs_1-15_2",
    ]
    climatology = by_label["climatology_onset_doy_1998-2024"]
    assert climatology["title"] == "Climatology · onset day of year · 1998–2024"
    assert climatology["job_id"] == job_ids[0]
    assert by_label["spatial_metrics_aifs_1-15_2"]["job_id"] == job_ids[1]
    assert [job["scope"] for job in body["jobs"]] == 2 * [
        {
            "place": "Ethiopia",
            "years": {"start": 1998, "end": 2024},
            "lead_days": {"start": 1, "end": 30},
        }
    ]

    steps = await ledger.user_steps(user_id)
    assert all(climatology["id"] in step["outputs"] for step in steps.values())
    assert len(steps) == 2

    other = (await workspaces.create_workspace(user_id, "Second look"))["id"]
    await workspaces.link_job(other, job_ids[0])
    assert len(await ledger.user_steps(user_id)) == 2, "a job has one step in every workspace"
    assert "climatology_onset_doy_1998-2024" in (await workspaces.labels(other)).values()

    response = await client.get(
        f"/workspaces/{wid}/artifacts/{climatology['id']}/provenance", headers=as_new_user
    )
    body = response.json()
    assert len(body["producers"]) == 2, "both runs produced these bytes"
    step = body["steps"][0]
    assert step["description"].startswith("Benchmark run of AIFS against CHIRPS Ethiopia")
    assert "obs_dir" in step["params"] and "obs_dir" not in step["display_params"]


@pytest.mark.asyncio
async def test_workspace_events_send_jobs_then_only_unseen_artifacts(
    client, as_new_user, user_id, monkeypatch
) -> None:
    import json

    from ai_almanac.server.routers import workspaces as workspaces_router

    monkeypatch.setattr(workspaces_router, "EVENTS_POLL_SECONDS", 0.01)
    monkeypatch.setattr(workspaces_router, "EVENTS_LIFETIME_SECONDS", 0.05)
    workspace, _ = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    job_id = insert_job(user_id, BENCHMARK_CONFIG)
    write_job_outputs(job_id, 1.0)
    await workspaces.link_job(wid, job_id)

    async def events(since: str | None = None) -> list[dict]:
        params = {"since": since} if since else {}
        async with client.stream(
            "GET", f"/workspaces/{wid}/events", headers=as_new_user, params=params
        ) as response:
            assert response.status_code == 200
            body = "".join([chunk async for chunk in response.aiter_text()])
        return [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]

    first = await events()
    types = [event["type"] for event in first]
    assert types.index("jobs") < types.index("artifacts")
    jobs, artifacts = (
        next(e for e in first if e["type"] == kind) for kind in ("jobs", "artifacts")
    )
    assert [job["id"] for job in jobs["jobs"]] == [job_id]
    newest = max(artifact["created_at"] for artifact in artifacts["artifacts"])
    assert len(artifacts["artifacts"]) == 2

    resumed = await events(since=newest)
    resent = {
        a["id"] for event in resumed if event["type"] == "artifacts" for a in event["artifacts"]
    }
    assert resent == {a["id"] for a in artifacts["artifacts"] if a["created_at"] == newest}


@pytest.mark.asyncio
async def test_the_record_says_when_an_existing_run_was_added_by_hand(
    client, as_new_user, user_id
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    picked = insert_job(user_id, BENCHMARK_CONFIG)
    write_job_outputs(picked, 1.0)
    started = insert_job(user_id, BENCHMARK_CONFIG)
    write_job_outputs(started, 2.0)

    added = await client.post(
        f"/workspaces/{wid}/jobs", headers=as_new_user, json={"job_id": picked}
    )
    await client.post(
        f"/workspaces/{wid}/jobs",
        headers=as_new_user,
        json={"job_id": started, "origin": "started"},
    )

    assert added.status_code == 200, added.text
    by_hand = await job_step_in_record(client, as_new_user, wid, picked)
    made_here = await job_step_in_record(client, as_new_user, wid, started)
    assert by_hand["added_at"] is not None and by_hand["added_by"] == "user"
    assert (made_here["added_at"], made_here["added_by"]) == (None, None)


@pytest.mark.asyncio
async def test_the_runs_offered_for_adding_stop_at_the_most_recent_few(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    monkeypatch.setattr(workspaces, "ADDABLE_JOBS_LIMIT", 2)
    for _ in range(3):
        insert_job(user_id, BENCHMARK_CONFIG)

    offered = await client.get(f"/workspaces/{workspace['id']}/addable-jobs", headers=as_new_user)

    assert len(offered.json()) == 2
    assert all("config" not in job and "config_json" not in job for job in offered.json())


@pytest.mark.asyncio
async def test_the_runs_offered_for_adding_are_the_users_own_that_are_not_here_yet(
    client, as_new_user, user_id
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    already_here = insert_job(user_id, BENCHMARK_CONFIG)
    await workspaces.link_job(wid, already_here)
    not_here = insert_job(user_id, BENCHMARK_CONFIG, job_type="blend")
    insert_job("someone-else", BENCHMARK_CONFIG)

    offered = await client.get(f"/workspaces/{wid}/addable-jobs", headers=as_new_user)

    assert offered.status_code == 200, offered.text
    assert [(job["id"], job["job_type"]) for job in offered.json()] == [(not_here, "blend")]

    await client.post(f"/workspaces/{wid}/jobs", headers=as_new_user, json={"job_id": not_here})
    after = await client.get(f"/workspaces/{wid}/addable-jobs", headers=as_new_user)
    assert after.json() == []
