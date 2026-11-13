"""Follow-ups: assistant work queued in a workspace chat until its jobs finish."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime

import pytest

from ai_almanac.paths import database_path
from ai_almanac.server.services import chat_turns, follow_ups, workspaces
from ai_almanac.server.services.primitives import workspace as workspace_tools
from tests.helpers.records import insert_job
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import workspace_with_session


def _set_job(job_id: str, status: str, published: bool = False) -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "UPDATE jobs SET status = ?, artifacts_published_at = ? WHERE id = ?",
            (status, "2026-09-24T00:00:00" if published else None, job_id),
        )
        conn.commit()


class _Turns(list):
    """The content of each turn started, with what started it in ``triggers``."""

    triggers: list[str]


@pytest.fixture
def turns(monkeypatch: pytest.MonkeyPatch) -> _Turns:
    sent = _Turns()
    sent.triggers = []

    async def fake_turn(session_id, user_id, content, scope, **kwargs):
        sent.append(content)
        sent.triggers.append(kwargs.get("trigger", "user"))
        yield f"data: {json.dumps({'type': 'done', 'turn': {}})}\n\n"

    monkeypatch.setattr(chat_turns, "stream_chat_turn", fake_turn)
    return sent


async def _tick() -> None:
    await follow_ups.start_ready()
    await asyncio.gather(*follow_ups._running)


@pytest.mark.asyncio
async def test_follow_up_continues_the_chat_once_its_job_outputs_are_in(
    client, as_new_user, user_id, turns
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    job_id = insert_job(user_id, {}, job_type="blend")
    _set_job(job_id, "running")
    await workspaces.link_job(wid, job_id)
    ctx = tool_ctx(user_id, session["id"], wid)

    queued = await workspace_tools.continue_after_jobs(
        ctx, job_ids=[job_id], instruction="compare with blend b-old"
    )
    assert queued["waiting_on"] == [job_id]

    await _tick()
    _set_job(job_id, "complete")
    await _tick()
    assert turns == [], "starts only after the job's outputs are registered"

    _set_job(job_id, "complete", published=True)
    await _tick()
    await _tick()
    assert len(turns) == 1
    assert "compare with blend b-old" in turns[0]
    assert job_id not in turns[0], "the turn is told runs' names, not their ids"
    [row] = await follow_ups.list_for_workspace(wid)
    assert row["status"] == "done"
    assert turns.triggers == [f"follow_up:{row['id']}"], "the platform starts it, not the user"


@pytest.mark.asyncio
async def test_failed_job_still_continues_with_its_error(
    client, as_new_user, user_id, turns
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = insert_job(user_id, {}, job_type="blend")
    await workspaces.link_job(workspace["id"], job_id)
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "UPDATE jobs SET status = 'failed', error = 'Out of memory' WHERE id = ?", (job_id,)
        )
        conn.commit()
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    await workspace_tools.continue_after_jobs(ctx, job_ids=[job_id], instruction="plot it")
    await _tick()
    assert "failed (Out of memory)" in turns[0]


@pytest.mark.asyncio
async def test_only_linked_jobs_can_be_waited_on_and_any_wait_can_be_canceled(
    client, as_new_user, user_id, turns
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    ctx = tool_ctx(user_id, session["id"], wid)
    job_id = insert_job(user_id, {})

    refused = await workspace_tools.continue_after_jobs(ctx, job_ids=[job_id], instruction="x")
    assert refused["not_linked"] == [job_id]

    await workspaces.link_job(wid, job_id)
    queued = await workspace_tools.continue_after_jobs(ctx, job_ids=[job_id], instruction="x")
    running = await _running_blend(user_id, wid, "Amhara blend")
    report = await follow_ups.report_when_finished(wid, session["id"], user_id, [running])
    for follow_up in (queued, report):
        response = await client.delete(
            f"/workspaces/{wid}/follow-ups/{follow_up['queued']}", headers=as_new_user
        )
        assert response.status_code == 204
    await _tick()
    assert turns == []


def _mark_running_since(follow_up_id: str, started_at: str) -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "UPDATE follow_ups SET status = 'running', started_at = ? WHERE id = ?",
            (started_at, follow_up_id),
        )
        conn.commit()


@pytest.mark.asyncio
async def test_a_follow_up_whose_turn_died_is_failed_but_a_live_one_is_left_alone(
    client, as_new_user, user_id, turns
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    job_id = insert_job(user_id, {}, job_type="blend")
    _set_job(job_id, "running")
    await workspaces.link_job(wid, job_id)
    ctx = tool_ctx(user_id, session["id"], wid)
    queued = await workspace_tools.continue_after_jobs(ctx, job_ids=[job_id], instruction="plot it")
    _mark_running_since(queued["queued"], "2026-09-24T00:00:00+00:00")

    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "UPDATE chat_sessions SET transcript = ? WHERE id = ?",
            (json.dumps([{"status": "streaming"}]), session["id"]),
        )
        conn.commit()
    await _tick()
    [row] = await follow_ups.list_for_workspace(wid)
    assert row["status"] == "running", "its chat is still streaming the turn"

    with sqlite3.connect(database_path()) as conn:
        conn.execute("UPDATE chat_sessions SET transcript = '[]' WHERE id = ?", (session["id"],))
        conn.commit()
    await _tick()
    [row] = await follow_ups.list_for_workspace(wid)
    assert row["status"] == "failed"
    assert turns == []


def _asked(*ids: str):
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    return ModelResponse(
        parts=[ToolCallPart(tool_name="fetch", args={}, tool_call_id=i) for i in ids]
    )


def _answered(*ids: str):
    from pydantic_ai.messages import ModelRequest, ToolReturnPart

    return ModelRequest(
        parts=[ToolReturnPart(tool_name="fetch", content="ok", tool_call_id=i) for i in ids]
    )


_JUST_NOW = datetime.now(UTC)
_LONG_AGO = datetime(2026, 9, 25, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("last_turn", "history", "touched", "awaiting"),
    [
        ("streaming", [], _JUST_NOW, True),
        ("completed", [_asked("call-1", "call-2")], _JUST_NOW, True),
        ("completed", [_asked("call-1", "call-2"), _answered("call-1")], _JUST_NOW, True),
        (
            "completed",
            [_asked("call-1", "call-2"), _answered("call-1", "call-2")],
            _JUST_NOW,
            False,
        ),
        ("completed", [], _JUST_NOW, False),
        ("streaming", [], _LONG_AGO, False),
    ],
    ids=[
        "a turn is streaming",
        "steps wait on approval",
        "the second step still waits once the first is answered",
        "every step is answered",
        "the last turn finished",
        "the streaming turn's server died",
    ],
)
def test_a_follow_up_waits_while_the_chat_is_busy_or_waiting_on_the_user(
    last_turn: str, history: list, touched: datetime, awaiting: bool
) -> None:
    assert follow_ups.awaiting_input([{"status": last_turn}], history, touched) is awaiting


@pytest.mark.asyncio
async def test_follow_ups_ready_together_share_one_turn(
    client, as_new_user, user_id, turns
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    job_id = insert_job(user_id, {}, job_type="blend")
    await workspaces.link_job(wid, job_id)
    _set_job(job_id, "complete", published=True)
    ctx = tool_ctx(user_id, session["id"], wid)
    await workspace_tools.continue_after_jobs(ctx, job_ids=[job_id], instruction="report skill")
    await workspace_tools.continue_after_jobs(
        ctx, job_ids=[job_id], instruction="then forecast 2026"
    )

    await _tick()
    await _tick()

    [turn] = turns
    assert turn.count("blend ") == 1, "each run is reported once"
    assert turn.index("1. report skill") < turn.index("2. then forecast 2026")
    rows = await follow_ups.list_for_workspace(wid)
    assert [row["status"] for row in rows] == ["done", "done"]


def _write_transcript(session_id: str, transcript: list[dict]) -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "UPDATE chat_sessions SET transcript = ? WHERE id = ?",
            (json.dumps(transcript), session_id),
        )
        conn.commit()


def _transcript(session_id: str) -> list[dict]:
    with sqlite3.connect(database_path()) as conn:
        (value,) = conn.execute(
            "SELECT transcript FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return json.loads(value)


def _failing_once(monkeypatch: pytest.MonkeyPatch, after_failure: list[dict]) -> list[str]:
    """A turn whose first attempt the model's API fails, leaving its failed exchange
    (then ``after_failure``) on the transcript; later attempts finish."""
    attempts: list[str] = []

    async def fake_turn(session_id, user_id, content, scope, **kwargs):
        attempts.append(content)
        if len(attempts) == 1:
            failed = [
                {"role": "user", "trigger": kwargs["trigger"], "content": content},
                {"role": "assistant", "status": "failed", "error": "ReadTimeout"},
            ]
            _write_transcript(session_id, _transcript(session_id) + failed + after_failure)
            yield f"data: {json.dumps({'type': 'error', 'error_type': 'provider_error'})}\n\n"
            return
        yield f"data: {json.dumps({'type': 'done', 'turn': {}})}\n\n"

    monkeypatch.setattr(chat_turns, "stream_chat_turn", fake_turn)
    monkeypatch.setattr(follow_ups, "RETRY_AFTER_SECONDS", 0)
    return attempts


async def _ready_follow_up(client, as_new_user, user_id) -> tuple[str, str]:
    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = insert_job(user_id, {}, job_type="blend")
    _set_job(job_id, "complete", published=True)
    await workspaces.link_job(workspace["id"], job_id)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    await workspace_tools.continue_after_jobs(ctx, job_ids=[job_id], instruction="summarize")
    return workspace["id"], session["id"]


@pytest.mark.asyncio
async def test_a_follow_up_the_model_api_failed_is_tried_again_in_place(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = _failing_once(monkeypatch, after_failure=[])
    wid, session_id = await _ready_follow_up(client, as_new_user, user_id)

    await _tick()

    assert len(attempts) == 2
    assert _transcript(session_id) == [], "the failed attempt is replaced, not left behind"
    [row] = await follow_ups.list_for_workspace(wid)
    assert row["status"] == "done"


@pytest.mark.asyncio
async def test_a_failed_follow_up_is_not_retried_over_what_the_user_wrote_since(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_wrote = [{"role": "user", "content": "what happened?"}]
    attempts = _failing_once(monkeypatch, after_failure=user_wrote)
    wid, session_id = await _ready_follow_up(client, as_new_user, user_id)

    await _tick()

    assert len(attempts) == 1
    assert _transcript(session_id)[-1] == user_wrote[0]
    [row] = await follow_ups.list_for_workspace(wid)
    assert row["status"] == "failed"


@pytest.mark.asyncio
async def test_a_platform_started_turn_is_marked_on_the_transcript_and_the_turn_log(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.models.test import TestModel

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: TestModel(call_tools=[], custom_output_text="Done."),
    )
    _, session = await workspace_with_session(client, as_new_user)

    async for _ in chat_turns.stream_chat_turn(
        session["id"], user_id, "Jobs finished; continue.", None, trigger="follow_up:f-1"
    ):
        pass

    shown = await client.get(f"/chat/sessions/{session['id']}", headers=as_new_user)
    with sqlite3.connect(database_path()) as conn:
        logged = conn.execute(
            "SELECT trigger FROM assistant_turn_logs WHERE session_id = ?", (session["id"],)
        ).fetchall()
    prompt, answer = shown.json()["transcript"]
    assert (prompt["role"], prompt["trigger"]) == ("user", "follow_up:f-1")
    assert answer["role"] == "assistant"
    assert logged == [("follow_up:f-1",)]


async def _running_blend(user_id: str, workspace_id: str, name: str) -> str:
    job_id = insert_job(user_id, {"blend_name": name}, job_type="blend")
    _set_job(job_id, "running")
    await workspaces.link_job(workspace_id, job_id)
    return job_id


@pytest.mark.asyncio
async def test_a_run_submitted_from_a_chat_is_reported_once_when_it_finishes(
    client, as_new_user, user_id, turns
) -> None:
    """Unasked: the assistant did not queue anything. Two jobs of one submission
    get one report."""
    from ai_almanac.server.services import llm

    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    first = await _running_blend(user_id, wid, "First")
    second = await _running_blend(user_id, wid, "Second")
    deps = tool_ctx(user_id, session["id"], wid).deps

    await llm._report_when_finished(
        deps, "submit_benchmark", {"run_id": "run-1", "jobs": [{"id": first}, {"id": second}]}
    )
    await llm._report_when_finished(deps, "list_jobs", {"jobs": [{"id": first}]})

    [queued] = await follow_ups.list_for_workspace(wid)
    assert queued["instruction"] == follow_ups.REPORT_INSTRUCTION
    _set_job(first, "complete", published=True)
    await _tick()
    assert turns == [], "waits for the whole submission"
    _set_job(second, "failed")
    await _tick()
    await _tick()
    [turn] = turns
    assert follow_ups.REPORT_INSTRUCTION in turn
    assert '"First": complete' in turn and '"Second": failed' in turn


@pytest.mark.asyncio
async def test_a_refused_submission_or_a_run_that_already_ended_queues_no_report(
    client, as_new_user, user_id
) -> None:
    from ai_almanac.server.services import llm

    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    ended = insert_job(user_id, {}, job_type="forecast")
    await workspaces.link_job(wid, ended)
    deps = tool_ctx(user_id, session["id"], wid).deps

    await llm._report_when_finished(deps, "run_forecast", {"job": {"id": ended}})
    await llm._report_when_finished(deps, "submit_blend", {"error": "Invalid spec"})

    assert await follow_ups.list_for_workspace(wid) == []


@pytest.mark.asyncio
async def test_the_default_report_gives_way_to_what_the_assistant_queued_on_the_same_run(
    client, as_new_user, user_id, turns
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    job_id = await _running_blend(user_id, wid, "Amhara blend")
    ctx = tool_ctx(user_id, session["id"], wid)
    await follow_ups.report_when_finished(wid, session["id"], user_id, [job_id])
    await workspace_tools.continue_after_jobs(
        ctx, job_ids=[job_id], instruction="compare with last year"
    )

    _set_job(job_id, "complete", published=True)
    await _tick()
    await _tick()

    [turn] = turns
    assert "compare with last year" in turn
    assert follow_ups.REPORT_INSTRUCTION not in turn
    rows = await follow_ups.list_for_workspace(wid)
    assert [row["status"] for row in rows] == ["done", "done"]


@pytest.mark.asyncio
async def test_a_follow_up_whose_chat_was_deleted_stops_waiting(
    client, as_new_user, user_id, turns
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = insert_job(user_id, {}, job_type="blend")
    _set_job(job_id, "running")
    await workspaces.link_job(workspace["id"], job_id)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    await workspace_tools.continue_after_jobs(ctx, job_ids=[job_id], instruction="summarize")

    deleted = await client.delete(f"/chat/sessions/{session['id']}", headers=as_new_user)
    assert deleted.status_code == 204
    _set_job(job_id, "complete", published=True)
    await _tick()

    [row] = await follow_ups.list_for_workspace(workspace["id"])
    assert row["status"] == "canceled"
    assert turns == []


@pytest.mark.asyncio
async def test_a_follow_up_runs_under_the_ruleset_its_admin_pinned(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.server.services import rulesets

    await rulesets.seed_packaged_rulesets()
    await rulesets.save_ruleset(rulesets.Ruleset(id="draft-preview", name="Draft"))
    await rulesets.set_admin_enabled("draft-preview", True)
    used: list[str | None] = []

    async def fake_stream_response(*args, active_ruleset=None, **kwargs):
        used.append(active_ruleset.id if active_ruleset else None)
        done = {"type": "done", "turn": {"content": "Done."}, "provider_state": []}
        yield json.dumps(done)

    monkeypatch.setattr(chat_turns, "stream_response", fake_stream_response)
    workspace, session = await workspace_with_session(client, as_new_user)
    pinned = await client.patch(
        f"/chat/sessions/{session['id']}",
        headers=as_new_user,
        json={"ruleset_id": "draft-preview"},
    )
    assert pinned.status_code == 200, pinned.text
    job_id = insert_job(user_id, {}, job_type="blend")
    _set_job(job_id, "complete", published=True)
    await workspaces.link_job(workspace["id"], job_id)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    await workspace_tools.continue_after_jobs(ctx, job_ids=[job_id], instruction="summarize")

    await _tick()

    assert used == ["draft-preview"]
