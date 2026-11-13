"""A turn being streamed keeps its conversation busy, and one whose server died does not."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from ai_almanac.paths import database_path
from ai_almanac.server.services import chat_state, chat_turns


def test_a_streaming_turn_blocks_a_second_writer_until_it_goes_stale() -> None:
    from datetime import UTC, datetime

    touched = datetime(2026, 9, 25, 12, tzinfo=UTC)
    transcript = [{"status": "streaming"}]
    stale = touched + chat_state.STALE_TURN_AFTER
    assert chat_state.turn_in_flight(transcript, touched.isoformat(), now=touched)
    assert not chat_state.turn_in_flight(transcript, touched.isoformat(), now=stale)
    assert not chat_state.turn_in_flight([{"status": "completed"}], touched, now=touched)


def test_a_turn_left_streaming_by_a_dead_server_reads_as_interrupted() -> None:
    from datetime import UTC, datetime

    touched = datetime(2026, 9, 25, 12, tzinfo=UTC)
    transcript = [
        {"role": "user", "status": "completed"},
        {"role": "assistant", "status": "streaming"},
    ]

    live = chat_turns.with_interrupted_turns(transcript, touched, now=touched)
    dead = chat_turns.with_interrupted_turns(
        transcript, touched, now=touched + chat_state.STALE_TURN_AFTER
    )

    assert live == transcript
    assert dead[-1] == {
        "role": "assistant",
        "status": "failed",
        "error": chat_turns.INTERRUPTED,
    }


def test_a_stalled_read_from_the_model_api_is_a_provider_error() -> None:
    import httpx

    from ai_almanac.server.services.chat_state import ChatTurn

    turn = ChatTurn(id="t", role="assistant", created_at="2026-11-06T00:00:00Z")
    assert chat_turns._classify_stream_error(httpx.ReadTimeout(""), turn) == "provider_error"


def _last_touched(session_id: str) -> str:
    with sqlite3.connect(database_path()) as conn:
        (touched,) = conn.execute(
            "SELECT updated_at FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    return touched


@pytest.mark.asyncio
async def test_a_long_turn_keeps_marking_its_conversation_live(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    session = (
        await client.post(
            "/chat/sessions",
            headers=as_new_user,
            json={"title": "slow", "scope": {"kind": "benchmark_setup", "key": "setup-1"}},
        )
    ).json()
    seen: list[str] = []

    async def slow_reply(messages: list, info: AgentInfo):
        seen.append(_last_touched(session["id"]))
        await asyncio.sleep(0.1)
        seen.append(_last_touched(session["id"]))
        yield "Done."

    monkeypatch.setattr(chat_turns, "TURN_HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: FunctionModel(stream_function=slow_reply),
    )

    async for _ in chat_turns.stream_chat_turn(session["id"], user_id, "Take your time.", None):
        pass

    before, during = seen
    assert during > before, "the conversation was touched while the model was still replying"
