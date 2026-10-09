"""A chat turn records what happened in the order it happened."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.function import (
    AgentInfo,
    DeltaThinkingCalls,
    DeltaThinkingPart,
    DeltaToolCall,
    DeltaToolCalls,
    FunctionModel,
)

from ai_almanac.server.services.chat_state import ChatScope, ChatTurn, utc_now
from ai_almanac.server.services.turn_events import apply_stream_event


def _turn() -> ChatTurn:
    return ChatTurn(id="turn-1", role="assistant", created_at=utc_now())


def _block_kinds(turn: ChatTurn) -> list[str]:
    return [block.kind for block in turn.blocks]


def test_text_resuming_after_a_tool_starts_a_new_block_and_paragraph() -> None:
    turn = _turn()
    for event in [
        {"type": "text_delta", "content": "Let me check"},
        {"type": "text_delta", "content": " the metrics."},
        {"type": "tool_call", "tool_call": {"id": "c1", "name": "get_job_metrics"}},
        {"type": "text_delta", "content": "FuXi leads."},
    ]:
        apply_stream_event(turn, event)

    assert _block_kinds(turn) == ["text", "tool", "text"]
    assert turn.blocks[0].text == "Let me check the metrics."
    assert turn.content == "Let me check the metrics.\n\nFuXi leads."


def test_a_tool_announced_while_its_arguments_stream_is_advanced_not_duplicated() -> None:
    turn = _turn()
    apply_stream_event(
        turn,
        {"type": "tool_call", "tool_call": {"id": "c1", "name": "run_code", "status": "pending"}},
    )
    apply_stream_event(
        turn,
        {
            "type": "tool_call",
            "tool_call": {
                "id": "c1",
                "name": "run_code",
                "status": "running",
                "input": {"code": "print(1)"},
            },
        },
    )
    apply_stream_event(
        turn, {"type": "tool_result", "tool_call_id": "c1", "status": "completed", "result": {}}
    )

    assert _block_kinds(turn) == ["tool"]
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].status == "completed"
    assert turn.tool_calls[0].input == {"code": "print(1)"}


def test_thinking_is_kept_out_of_the_turn_text() -> None:
    turn = _turn()
    apply_stream_event(turn, {"type": "thinking_delta", "content": "Comparing both "})
    apply_stream_event(turn, {"type": "thinking_delta", "content": "windows."})
    apply_stream_event(turn, {"type": "text_delta", "content": "Answer."})

    assert _block_kinds(turn) == ["thinking", "text"]
    assert turn.blocks[0].text == "Comparing both windows."
    assert turn.content == "Answer."


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://openrouter.ai/api/v1", True),
        ("https://eu.openrouter.ai/api/v1", True),
        ("https://api.openai.com/v1", False),
        ("https://openrouter.ai.example.com/v1", False),
    ],
)
def test_openrouter_is_recognised_by_host(base_url: str, expected: bool) -> None:
    from ai_almanac.server.services import llm

    assert llm._is_openrouter(base_url) is expected


async def _progress_then_tool_then_answer(
    messages: list[ModelMessage], _info: AgentInfo
) -> AsyncIterator[str | DeltaToolCalls | DeltaThinkingCalls]:
    tool_ran = any(
        isinstance(part, ToolReturnPart)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )
    if tool_ran:
        yield "Three regions are set up."
        return
    yield {0: DeltaThinkingPart(content="Checking which regions exist first.")}
    yield {1: DeltaToolCall(name="list_regions", json_args="{}", tool_call_id="call-1")}


@pytest.mark.asyncio
async def test_streamed_turn_keeps_progress_tool_and_answer_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services import llm

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: FunctionModel(stream_function=_progress_then_tool_then_answer),
    )
    scope = ChatScope(kind="benchmark_run_group", key="group-1", title="Group 1", job_ids=[])

    events = [
        json.loads(event)
        async for event in llm.stream_response(
            [], "user-1", "session-1", scope, latest_user_message="Which regions?"
        )
    ]

    tool_statuses = [e["tool_call"]["status"] for e in events if e["type"] == "tool_call"]
    assert tool_statuses == ["pending", "running"]
    assert any(e["type"] == "thinking_delta" for e in events)
    turn = events[-1]["turn"]
    assert [block["kind"] for block in turn["blocks"]] == ["thinking", "tool", "text"]
    assert turn["content"] == "Three regions are set up."
