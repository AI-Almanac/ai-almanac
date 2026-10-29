"""A chat reply runs apart from the request that asked for it, so reloading the
page mid-reply does not leave the turn stuck as streaming."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from tests.test_chat_api import _create_session


@pytest.fixture
def gated_reply(monkeypatch: pytest.MonkeyPatch) -> tuple[asyncio.Event, asyncio.Event]:
    """A reply that says a little, then waits for `release` before finishing.

    Returns (started, release).
    """
    started, release = asyncio.Event(), asyncio.Event()

    async def fake_stream_response(*_args, **_kwargs) -> AsyncIterator[str]:
        yield json.dumps({"type": "text_delta", "content": "Looking"})
        started.set()
        await release.wait()
        yield json.dumps(
            {"type": "done", "turn": {"content": "Looking done"}, "provider_state": []}
        )

    monkeypatch.setattr(
        "ai_almanac.server.services.chat_turns.stream_response", fake_stream_response
    )
    return started, release


async def _last_turn(client: httpx.AsyncClient, headers: dict[str, str], session_id: str) -> dict:
    detail = (await client.get(f"/chat/sessions/{session_id}", headers=headers)).json()
    return detail["transcript"][-1]


@pytest.mark.asyncio
async def test_reply_finishes_and_saves_after_its_stream_is_dropped(
    client: httpx.AsyncClient, as_new_user: dict[str, str], user_id: str, gated_reply
) -> None:
    from ai_almanac.server.services.chat_turns import reply_running, start_chat_turn

    started, release = gated_reply
    session_id = (await _create_session(client, as_new_user))["id"]

    events = start_chat_turn(session_id, user_id, "Hello", None)
    await anext(events)
    await asyncio.wait_for(started.wait(), timeout=5)
    # The page reloads: its stream is dropped while the reply is still going.
    await events.aclose()
    assert reply_running(session_id)
    assert (await _last_turn(client, as_new_user, session_id))["status"] == "streaming"

    release.set()
    for _ in range(100):
        if not reply_running(session_id):
            break
        await asyncio.sleep(0.05)

    turn = await _last_turn(client, as_new_user, session_id)
    assert (turn["status"], turn["content"]) == ("completed", "Looking done")


@pytest.mark.asyncio
async def test_a_second_send_waits_for_the_reply_in_progress(
    client: httpx.AsyncClient, as_new_user: dict[str, str], gated_reply
) -> None:
    started, release = gated_reply
    session_id = (await _create_session(client, as_new_user))["id"]

    first = asyncio.create_task(
        client.post(
            f"/chat/sessions/{session_id}/message", headers=as_new_user, json={"content": "One"}
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)
    second = await client.post(
        f"/chat/sessions/{session_id}/message", headers=as_new_user, json={"content": "Two"}
    )
    release.set()
    await asyncio.wait_for(first, timeout=5)

    assert second.status_code == 409


@pytest.mark.asyncio
async def test_shutdown_saves_a_running_reply_as_failed(
    client: httpx.AsyncClient, as_new_user: dict[str, str], user_id: str, gated_reply
) -> None:
    from ai_almanac.server.services.chat_turns import cancel_running_replies, start_chat_turn

    started, _release = gated_reply
    session_id = (await _create_session(client, as_new_user))["id"]

    events = start_chat_turn(session_id, user_id, "Hello", None)
    await anext(events)
    await asyncio.wait_for(started.wait(), timeout=5)
    await cancel_running_replies()

    assert (await _last_turn(client, as_new_user, session_id))["status"] == "failed"


async def _orphan_a_reply(session_id: str) -> None:
    """Leave a reply marked streaming with nothing writing it, as a kill would."""
    from sqlalchemy import text

    from ai_almanac.server.db import get_db

    created = "2026-10-29T10:00:00+00:00"
    transcript = [
        {"id": "u1", "role": "user", "content": "Hello", "created_at": created},
        {
            "id": "a1",
            "role": "assistant",
            "content": "",
            "created_at": created,
            "status": "streaming",
        },
    ]
    async with get_db() as conn:
        await conn.execute(
            text("UPDATE chat_sessions SET transcript = :transcript WHERE id = :id"),
            {"transcript": json.dumps(transcript), "id": session_id},
        )


@pytest.mark.asyncio
async def test_startup_saves_replies_a_kill_left_streaming_and_leaves_running_ones(
    client: httpx.AsyncClient, as_new_user: dict[str, str], user_id: str, gated_reply
) -> None:
    from ai_almanac.server.services.chat_turns import (
        INTERRUPTED_REPLY,
        fail_orphaned_replies,
        reply_running,
        start_chat_turn,
    )

    started, release = gated_reply
    orphaned = (await _create_session(client, as_new_user))["id"]
    running = (await _create_session(client, as_new_user))["id"]
    await _orphan_a_reply(orphaned)
    events = start_chat_turn(running, user_id, "Hello", None)
    await anext(events)
    await asyncio.wait_for(started.wait(), timeout=5)

    assert await fail_orphaned_replies() == 1

    turn = await _last_turn(client, as_new_user, orphaned)
    assert (turn["status"], turn["error"]) == ("failed", INTERRUPTED_REPLY)
    assert (await _last_turn(client, as_new_user, running))["status"] == "streaming"
    release.set()
    await events.aclose()
    for _ in range(100):
        if not reply_running(running):
            break
        await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "status"), [("personal", "failed"), ("shared", "streaming")])
async def test_a_stop_that_finds_nothing_writing_the_reply_saves_it_cut_off(
    client: httpx.AsyncClient,
    as_new_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    status: str,
) -> None:
    from ai_almanac.settings import settings

    session_id = (await _create_session(client, as_new_user))["id"]
    await _orphan_a_reply(session_id)
    monkeypatch.setattr(settings, "deployment_mode", mode)

    response = await client.post(f"/chat/sessions/{session_id}/stop", headers=as_new_user)

    assert response.json() == {"stopped": False}
    # Shared: another instance may be writing it, so it is left to that one.
    assert (await _last_turn(client, as_new_user, session_id))["status"] == status
