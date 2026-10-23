"""Stopping a chat reply part way (POST /chat/sessions/{id}/stop)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from tests.test_chat_api import _create_session, _sse_events


def _tool_call(call_id: str, name: str, status: str) -> str:
    return json.dumps(
        {"type": "tool_call", "tool_call": {"id": call_id, "name": name, "status": status}}
    )


@pytest.fixture
def stalled_reply(monkeypatch: pytest.MonkeyPatch):
    """A reply that says a little, runs one tool, starts another, then waits.

    Returns (reached_wait, closed): set once the stream is waiting, and once the
    stream has been closed.
    """
    reached_wait, closed = asyncio.Event(), asyncio.Event()

    async def fake_stream_response(*_args, **_kwargs) -> AsyncIterator[str]:
        try:
            yield json.dumps({"type": "text_delta", "content": "Rerunning AIFSv2 now"})
            yield _tool_call("call-1", "rerun_job", "running")
            yield json.dumps(
                {"type": "tool_result", "tool_call_id": "call-1", "status": "completed"}
            )
            yield _tool_call("call-2", "rerun_job", "running")
            reached_wait.set()
            await asyncio.sleep(3600)
            yield json.dumps({"type": "done", "turn": {"content": "never"}, "provider_state": []})
        finally:
            closed.set()

    monkeypatch.setattr(
        "ai_almanac.server.services.chat_turns.stream_response", fake_stream_response
    )
    return reached_wait, closed


@pytest.mark.asyncio
async def test_stopping_keeps_what_was_said_and_tells_the_model_what_was_done(
    client: httpx.AsyncClient, as_new_user: dict[str, str], stalled_reply
) -> None:
    reached_wait, closed = stalled_reply
    session_id = (await _create_session(client, as_new_user))["id"]

    sending = asyncio.create_task(
        client.post(
            f"/chat/sessions/{session_id}/message",
            headers=as_new_user,
            json={"content": "Rerun the failed models."},
        )
    )
    await asyncio.wait_for(reached_wait.wait(), timeout=5)
    stop = await client.post(f"/chat/sessions/{session_id}/stop", headers=as_new_user)
    response = await asyncio.wait_for(sending, timeout=5)

    assert stop.json() == {"stopped": True}
    assert closed.is_set(), "the stream to the model was closed, not left running"
    events = _sse_events(response.text)
    assert events[-1]["type"] == "stopped"
    assert events[-1]["turn"]["content"] == "Rerunning AIFSv2 now"

    detail = (await client.get(f"/chat/sessions/{session_id}", headers=as_new_user)).json()
    turn = detail["transcript"][-1]
    assert (turn["status"], turn["stopped"], turn["content"]) == (
        "completed",
        True,
        "Rerunning AIFSv2 now",
    )
    # The call cut off mid-way is not left looking like it is still running.
    assert [(call["id"], call["status"]) for call in turn["tool_calls"]] == [
        ("call-1", "completed"),
        ("call-2", "failed"),
    ]


@pytest.mark.asyncio
async def test_the_model_history_records_the_stopped_question_and_its_actions(
    client: httpx.AsyncClient, as_new_user: dict[str, str], stalled_reply
) -> None:
    from sqlalchemy import text

    from ai_almanac.server.db import get_db

    reached_wait, _ = stalled_reply
    session_id = (await _create_session(client, as_new_user))["id"]
    sending = asyncio.create_task(
        client.post(
            f"/chat/sessions/{session_id}/message",
            headers=as_new_user,
            json={"content": "Rerun the failed models."},
        )
    )
    await asyncio.wait_for(reached_wait.wait(), timeout=5)
    await client.post(f"/chat/sessions/{session_id}/stop", headers=as_new_user)
    await asyncio.wait_for(sending, timeout=5)

    async with get_db() as conn:
        raw = (
            await conn.execute(
                text("SELECT provider_state FROM chat_sessions WHERE id = :id"),
                {"id": session_id},
            )
        ).scalar_one()
    history = json.loads(raw)
    question, reply = history[-2], history[-1]
    assert question["parts"][0]["content"] == "Rerun the failed models."
    said = reply["parts"][0]["content"]
    assert said.startswith("Rerunning AIFSv2 now")
    assert "The user stopped this reply" in said
    assert "rerun_job (completed)" in said
    assert "possibly done or not: rerun_job" in said


@pytest.mark.asyncio
async def test_stopping_with_nothing_running_says_so(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    session_id = (await _create_session(client, as_new_user))["id"]

    response = await client.post(f"/chat/sessions/{session_id}/stop", headers=as_new_user)

    assert response.status_code == 200
    assert response.json() == {"stopped": False}


@pytest.mark.asyncio
async def test_only_the_sessions_owner_can_stop_it(
    client: httpx.AsyncClient, as_new_user: dict[str, str]
) -> None:
    session_id = (await _create_session(client, as_new_user))["id"]

    response = await client.post(
        f"/chat/sessions/{session_id}/stop", headers={"X-Forwarded-User": "someone-else"}
    )

    assert response.status_code == 404
