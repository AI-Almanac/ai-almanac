"""The assistant can cancel a run, by the same rules as the Cancel control, once the
user has approved a card naming what will stop."""

from __future__ import annotations

import json
import sqlite3
import uuid

import pytest
from pydantic_ai import ApprovalRequired

from ai_almanac.paths import database_path
from ai_almanac.server.services import approval_gate, llm, run_cancel, workspaces
from ai_almanac.server.services.chat_state import ChatScope
from tests.helpers.records import insert_job
from tests.helpers.sse import sse_events
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import workspace_with_session


def _set(job_id: str, **columns: object) -> None:
    assignments = ", ".join(f"{column} = ?" for column in columns)
    with sqlite3.connect(database_path()) as conn:
        conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*columns.values(), job_id))
        conn.commit()


def _status(job_id: str) -> str:
    with sqlite3.connect(database_path()) as conn:
        return conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]


def _someone_else() -> str:
    other = str(uuid.uuid4())
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "INSERT INTO users (id, external_id, created_at) VALUES (?, ?, '2026-01-01T00:00:00')",
            (other, f"pytest-{other}"),
        )
        conn.commit()
    return other


async def _linked_run(
    user_id: str, workspace_id: str, status: str, name: str, visibility: str = "private"
) -> str:
    job_id = insert_job(user_id, {"blend_name": name}, job_type="blend", visibility=visibility)
    _set(job_id, status=status)
    await workspaces.link_job(workspace_id, job_id)
    return job_id


def _scope(workspace_id: str) -> ChatScope:
    return ChatScope(kind="workspace", key=workspace_id, job_ids=[])


@pytest.mark.asyncio
async def test_the_assistant_cancels_the_users_own_running_run(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = await _linked_run(user_id, workspace["id"], "running", "Amhara blend")
    cancel_run = llm._run_control_toolset().tools["cancel_run"].function

    with pytest.raises(ApprovalRequired) as asked:
        await cancel_run(tool_ctx(user_id, session["id"], workspace["id"]), job_id=job_id)
    still_running = _status(job_id)
    approved = tool_ctx(user_id, session["id"], workspace["id"], approved=True)
    approved.tool_call_metadata = {"approved_params": asked.value.metadata["params"]}
    result = await cancel_run(approved, job_id=job_id)

    card = {fact["label"]: fact["value"] for fact in asked.value.metadata["display"]["facts"]}
    assert still_running == "running", "nothing stops before the user approves"
    assert card["Stop"] == "Amhara blend"
    assert card["Covers"] == "This run only; nothing else was submitted with it"
    assert "Results already written are kept" in card["Results"]
    assert job_id not in json.dumps(asked.value.metadata), "the card names runs, never ids"
    assert _status(job_id) == "canceling"
    assert result["canceled"] == [{"name": "Amhara blend", "kind": "blend", "status": "canceling"}]
    assert job_id not in json.dumps(result), "the assistant is told names, never ids"


@pytest.mark.asyncio
async def test_someone_elses_run_is_refused_and_left_running(client, as_new_user, user_id) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    theirs = await _linked_run(_someone_else(), workspace["id"], "running", "Their blend", "shared")

    result = await run_cancel.cancel_run(theirs, user_id, _scope(workspace["id"]))

    assert result == {"error": run_cancel.NOT_FOUND}
    assert _status(theirs) == "running"


@pytest.mark.asyncio
async def test_a_run_outside_the_workspace_is_refused(client, as_new_user, user_id) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    elsewhere = insert_job(user_id, {"blend_name": "Elsewhere"}, job_type="blend")
    _set(elsewhere, status="running")

    result = await run_cancel.cancel_run(elsewhere, user_id, _scope(workspace["id"]))

    assert result == {"error": run_cancel.NOT_FOUND}
    assert _status(elsewhere) == "running"


@pytest.mark.asyncio
async def test_it_says_so_plainly_when_nothing_is_running(client, as_new_user, user_id) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    finished = await _linked_run(user_id, workspace["id"], "complete", "Amhara blend")

    result = await run_cancel.cancel_run(finished, user_id, _scope(workspace["id"]))

    assert result["canceled"] == []
    assert result["message"] == run_cancel.NOTHING_RUNNING
    assert result["not_running"] == [
        {"name": "Amhara blend", "kind": "blend", "status": "complete"}
    ]
    assert _status(finished) == "complete"


@pytest.mark.asyncio
async def test_a_benchmark_is_canceled_whole_unless_one_job_is_asked_for(
    client, as_new_user, user_id
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    scope = _scope(workspace["id"])
    first = await _linked_run(user_id, workspace["id"], "running", "First")
    second = await _linked_run(user_id, workspace["id"], "queued", "Second")
    done = await _linked_run(user_id, workspace["id"], "complete", "Done")
    for job_id in (first, second, done):
        _set(job_id, run_id="run-1")

    only_one = await run_cancel.cancel_run(first, user_id, scope, whole_run=False)
    assert [run["name"] for run in only_one["canceled"]] == ["First"]
    assert _status(second) == "queued"

    whole = await run_cancel.cancel_run(done, user_id, scope)
    assert [run["name"] for run in whole["canceled"]] == ["Second"]
    assert {run["name"] for run in whole["not_running"]} == {"First", "Done"}
    assert _status(second) == "canceling"
    assert _status(done) == "complete"


async def _benchmark_of_three(user_id: str, workspace_id: str) -> tuple[str, str, str]:
    first = await _linked_run(user_id, workspace_id, "running", "First")
    second = await _linked_run(user_id, workspace_id, "queued", "Second")
    done = await _linked_run(user_id, workspace_id, "complete", "Done")
    for job_id in (first, second, done):
        _set(job_id, run_id="run-1")
    return first, second, done


def _card(plan: run_cancel.CancelPlan) -> dict[str, str]:
    return {fact.label: fact.value for fact in run_cancel.approval_facts(plan)}


@pytest.mark.asyncio
async def test_the_card_says_whether_one_models_run_or_all_of_them_will_stop(
    client, as_new_user, user_id
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    scope = _scope(workspace["id"])
    first, second, done = await _benchmark_of_three(user_id, workspace["id"])

    whole = _card(await run_cancel.plan_cancel(first, user_id, scope))
    only_one = _card(await run_cancel.plan_cancel(first, user_id, scope, whole_run=False))

    assert whole["Stop"] == "First; Second" and whole["Already ended"] == "Done"
    assert whole["Covers"] == "The whole run: all 3 of the runs submitted together"
    assert only_one["Stop"] == "First" and "Already ended" not in only_one
    assert only_one["Covers"] == (
        "This one run only; the other 2 runs submitted with it will keep going"
    )
    assert {_status(job_id) for job_id in (first, second)} == {"running", "queued"}


@pytest.mark.asyncio
async def test_nothing_running_needs_no_card_and_a_run_that_is_not_theirs_gets_none(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    finished = await _linked_run(user_id, workspace["id"], "complete", "Amhara blend")
    theirs = await _linked_run(_someone_else(), workspace["id"], "running", "Their blend", "shared")
    cancel_run = llm._run_control_toolset().tools["cancel_run"].function
    ctx = tool_ctx(user_id, session["id"], workspace["id"])

    assert (await cancel_run(ctx, job_id=finished))["message"] == run_cancel.NOTHING_RUNNING
    assert await cancel_run(ctx, job_id=theirs) == {"error": run_cancel.NOT_FOUND}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["benchmark_setup", "blend_setup"])
async def test_a_setup_chat_with_no_runs_of_its_own_cannot_reach_the_users_other_runs(
    client, as_new_user, user_id, kind: str
) -> None:
    elsewhere = insert_job(user_id, {"blend_name": "Elsewhere"}, job_type="blend")
    _set(elsewhere, status="running")
    unscoped = ChatScope(kind=kind, key="setup", job_ids=[])
    its_own = ChatScope(kind=kind, key="setup", job_ids=[elsewhere])

    assert await run_cancel.plan_cancel(elsewhere, user_id, unscoped) is None
    assert await run_cancel.cancel_run(elsewhere, user_id, unscoped) == {
        "error": run_cancel.NOT_FOUND
    }
    assert _status(elsewhere) == "running"
    assert [
        job["id"] for job in (await run_cancel.plan_cancel(elsewhere, user_id, its_own)).jobs
    ] == [elsewhere]


@pytest.mark.asyncio
async def test_a_run_keeps_going_until_the_user_approves_the_cancellation_card(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = await _linked_run(user_id, workspace["id"], "running", "Amhara blend")

    async def stream(messages: list, info: AgentInfo):
        if any(isinstance(part, ToolReturnPart) for part in messages[-1].parts):
            yield "Stopped."
        else:
            yield {
                0: DeltaToolCall(
                    name="cancel_run", json_args=json.dumps({"job_id": job_id}), tool_call_id="c-1"
                )
            }

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model", lambda: FunctionModel(stream_function=stream)
    )
    asked = await client.post(
        f"/chat/sessions/{session['id']}/message",
        headers=as_new_user,
        json={"content": "stop the Amhara blend"},
    )
    [card] = [e for e in sse_events(asked.text) if e["type"] == "tool_approval_request"]
    before = _status(job_id)
    approved = await client.post(
        f"/chat/sessions/{session['id']}/tool-approval",
        headers=as_new_user,
        json={"tool_call_id": "c-1", "approved": True},
    )

    facts = {fact["label"]: fact["value"] for fact in card["metadata"]["display"]["facts"]}
    assert facts["Stop"] == "Amhara blend" and before == "running"
    [result] = [e for e in sse_events(approved.text) if e["type"] == "tool_result"]
    assert [run["name"] for run in result["result"]["canceled"]] == ["Amhara blend"]
    assert _status(job_id) == "canceling"


async def _asked_to_cancel(ctx, job_id: str) -> dict:
    cancel_run = llm._run_control_toolset().tools["cancel_run"].function
    with pytest.raises(ApprovalRequired) as asked:
        await cancel_run(ctx, job_id=job_id)
    return asked.value.metadata["params"]


@pytest.mark.asyncio
async def test_nothing_stops_when_the_runs_changed_after_the_card_was_shown(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    first, second, _ = await _benchmark_of_three(user_id, workspace["id"])
    shown = await _asked_to_cancel(tool_ctx(user_id, session["id"], workspace["id"]), first)

    _set(second, status="complete")
    approved = tool_ctx(user_id, session["id"], workspace["id"], approved=True)
    approved.tool_call_metadata = {"approved_params": shown}
    cancel_run = llm._run_control_toolset().tools["cancel_run"].function
    result = await cancel_run(approved, job_id=first)

    assert result == {"error": run_cancel.RUNS_MOVED}
    assert _status(first) == "running"


@pytest.mark.asyncio
async def test_an_approval_that_does_not_say_what_it_approved_stops_nothing(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = await _linked_run(user_id, workspace["id"], "running", "Amhara blend")
    cancel_run = llm._run_control_toolset().tools["cancel_run"].function

    result = await cancel_run(
        tool_ctx(user_id, session["id"], workspace["id"], approved=True), job_id=job_id
    )

    assert result == {"error": approval_gate.NOT_SHOWN}
    assert _status(job_id) == "running"
