"""Fitting a chat's history into the model's context window (services.llm)."""

from __future__ import annotations

import json

import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)

from ai_almanac.server.services import llm
from ai_almanac.settings import settings


def _outline(points: int) -> dict:
    ring = [[70.0 + i / points, 10.0 + i / points] for i in range(points)]
    return {"type": "MultiPolygon", "coordinates": [[ring]]}


def _rerun_result(points: int = 30_000) -> dict:
    """The shape rerun_job returned when the job had an area of interest."""
    return {
        "source_job_id": "6cada2a6",
        "job": {
            "id": "1ba3ca50",
            "model_name": "AIFSv2",
            "status": "queued",
            "params": {
                "region": "india",
                "focus_area": {"level": "adm1", "units": ["Kerala"], "geometry": _outline(points)},
            },
        },
    }


def _exchange(turn: int, tool_chars: int = 0) -> list:
    """A user prompt, the model calling a tool, its result, and the answer."""
    messages: list = [ModelRequest(parts=[UserPromptPart(content=f"question {turn}")])]
    if tool_chars:
        call_id = f"call-{turn}"
        messages += [
            ModelResponse(
                parts=[ToolCallPart(tool_name="get_job_metrics", args={}, tool_call_id=call_id)]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        tool_name="get_job_metrics", tool_call_id=call_id, content="x" * tool_chars
                    )
                ]
            ),
        ]
    messages.append(ModelResponse(parts=[TextPart(content=f"answer {turn}")]))
    return messages


def _tool_results(messages: list) -> list:
    return [
        part.content
        for message in messages
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]


def test_an_area_outline_never_reaches_the_model() -> None:
    raw = _rerun_result()
    assert len(json.dumps(raw)) > 500_000

    compact = llm._compact_tool_content(raw)

    focus_area = compact["job"]["params"]["focus_area"]
    assert focus_area == {"level": "adm1", "units": ["Kerala"]}
    assert len(json.dumps(compact)) < 1_000


def test_a_tool_result_is_capped_as_a_whole_not_only_its_strings() -> None:
    numbers = {"values": list(range(50_000))}

    compact = llm._compact_tool_content(numbers)

    cap = max(
        settings.llm_tool_result_max_chars * llm._TOOL_RESULT_TOTAL_FACTOR,
        llm._TOOL_RESULT_TOTAL_FLOOR,
    )
    assert isinstance(compact, str)
    assert len(compact) <= cap + len(llm._TRUNCATED_TOOL_RESULT) + 4
    assert compact.endswith(llm._TRUNCATED_TOOL_RESULT)


def test_a_history_within_budget_is_left_alone() -> None:
    messages = _exchange(1, tool_chars=100) + _exchange(2)

    assert llm.fit_history_to_context(messages, budget_chars=10_000) == messages


def test_older_tool_results_are_omitted_first_and_the_current_exchange_kept() -> None:
    messages = _exchange(1, tool_chars=5_000) + _exchange(2, tool_chars=5_000) + _exchange(3, 3_000)

    fitted = llm.fit_history_to_context(messages, budget_chars=9_000)

    results = _tool_results(fitted)
    assert results[0] == llm._OMITTED_TOOL_RESULT, "the oldest result goes first"
    assert results[1] == "x" * 5_000, "only as many as needed are omitted"
    assert results[2] == "x" * 3_000, "the current exchange is never touched"
    assert len(fitted) == len(messages), "no exchange had to be dropped"


def test_whole_old_exchanges_go_when_omitting_results_is_not_enough() -> None:
    long_answer = [ModelResponse(parts=[TextPart(content="y" * 8_000)])]
    messages = _exchange(1) + long_answer + _exchange(2) + _exchange(3, tool_chars=1_000)

    fitted = llm.fit_history_to_context(messages, budget_chars=2_000)

    prompts = [
        part.content
        for message in fitted
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert prompts[-1] == "question 3"
    assert "question 1" not in prompts
    # Every tool result left still follows the call it answers.
    calls = {
        part.tool_call_id
        for message in fitted
        for part in message.parts
        if isinstance(part, ToolCallPart)
    }
    returns = {
        part.tool_call_id
        for message in fitted
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    assert returns <= calls


@pytest.mark.parametrize(
    ("serve_args", "configured", "expected"),
    [
        ("--max-model-len 65536 --enable-auto-tool-choice", 0, 65536),
        ("--max-model-len=32768", 0, 32768),
        ("--enable-auto-tool-choice", 0, None),
        ("--max-model-len 65536", 8192, 8192),
    ],
    ids=["local-model-flag", "equals-form", "no-flag", "setting-wins"],
)
def test_the_context_window_comes_from_the_setting_or_the_local_model(
    monkeypatch, serve_args, configured, expected
) -> None:
    monkeypatch.setattr(settings, "llm_context_tokens", configured)
    monkeypatch.setattr(llm.local_llm, "is_managed", lambda: True)
    monkeypatch.setattr(llm.local_llm, "model_serve_args", lambda: serve_args)

    assert llm._context_window_tokens() == expected


@pytest.mark.asyncio
async def test_the_history_is_budgeted_in_the_local_models_own_tokens(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_context_tokens", 30_000)
    monkeypatch.setattr(llm.local_llm, "is_managed", lambda: True)
    # One token per character: a budget of 10K tokens is 10K characters.
    monkeypatch.setattr(llm.local_llm, "count_tokens", lambda text: len(text))
    history = []
    for turn in range(1, 5):
        history += _exchange(turn, tool_chars=4_000)

    sent = await llm.trim_chat_history(history)

    assert llm._history_chars(sent) <= 30_000 - llm._RESERVED_CONTEXT_TOKENS
    assert _tool_results(sent)[-1] == "x" * 4_000


@pytest.mark.asyncio
async def test_without_a_count_the_budget_assumes_the_worst_case_ratio(monkeypatch) -> None:
    monkeypatch.setattr(llm.local_llm, "is_managed", lambda: True)
    monkeypatch.setattr(llm.local_llm, "count_tokens", lambda text: None)

    assert await llm._chars_per_token(_exchange(1, tool_chars=100)) == llm._CHARS_PER_TOKEN


@pytest.mark.asyncio
async def test_the_failing_rerun_turn_now_fits_the_local_model(monkeypatch) -> None:
    """Replays the turn that overflowed: a rerun result carrying an area outline."""
    monkeypatch.setattr(settings, "llm_context_tokens", 65536)
    history = []
    for turn in range(1, 7):
        history += _exchange(turn, tool_chars=18_000)
    history += [
        ModelRequest(parts=[UserPromptPart(content="Rerun the failed jobs.")]),
        ModelResponse(parts=[ToolCallPart(tool_name="rerun_job", args={}, tool_call_id="rerun")]),
        ModelRequest(
            parts=[
                ToolReturnPart(tool_name="rerun_job", tool_call_id="rerun", content=_rerun_result())
            ]
        ),
    ]

    sent = await llm.trim_chat_history(history)

    budget = (65536 - llm._RESERVED_CONTEXT_TOKENS) * llm._CHARS_PER_TOKEN
    assert llm._history_chars(sent) <= budget
    assert _tool_results(sent)[-1]["job"]["params"]["focus_area"] == {
        "level": "adm1",
        "units": ["Kerala"],
    }


def test_a_context_overflow_is_reported_as_such() -> None:
    from ai_almanac.server.services.chat_state import ChatTurn
    from ai_almanac.server.services.chat_turns import _classify_stream_error

    error = RuntimeError(
        "status_code: 400, body: {'message': \"This model's maximum context length is 65536 "
        'tokens. However, your prompt contains at least 65537 input tokens"}'
    )
    turn = ChatTurn(id="t", role="assistant", created_at="2026-10-23T00:00:00Z")

    assert _classify_stream_error(error, turn) == "context_overflow"
