"""A local model stuck repeating itself: stopped by vLLM, reported as such.

Twice in an afternoon Qwen3.8 on the Spark looped inside a tool call. Nothing
streams while a tool call is written, so the turn sat silent until the read
timeout failed it as a generic error.
"""

from __future__ import annotations

import pytest
from openai.types.chat import ChatCompletionChunk
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openai import OpenAIChatModel

from ai_almanac.server.services import llm
from ai_almanac.server.services.chat_state import ChatToolCall, ChatTurn
from ai_almanac.server.services.chat_turns import _classify_stream_error
from ai_almanac.settings import settings


def _final_chunk(**choice_extra) -> ChatCompletionChunk:
    # vLLM's last chunk of a reply, as the OpenAI client parses it: its
    # stop_reason is an extra field the client keeps.
    return ChatCompletionChunk.model_validate(
        {
            "id": "c",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls", **choice_extra}],
        }
    )


def test_vllms_stop_reason_survives_when_its_finish_reason_hides_the_loop() -> None:
    # Once a tool call has streamed, vLLM reports finish_reason "tool_calls"
    # whatever stopped the reply; only stop_reason says it was a loop.
    stream = object.__new__(llm._VllmStreamedResponse)  # the hook reads only the chunk

    details = stream._map_provider_details(_final_chunk(stop_reason="repetition_detected"))

    assert details == {"finish_reason": "tool_calls", "stop_reason": "repetition_detected"}
    assert llm._stopped_for_repetition(details)
    assert not llm._stopped_for_repetition(stream._map_provider_details(_final_chunk()))


def _model_saying(response: ModelResponse) -> FunctionModel:
    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return response

    return FunctionModel(respond)


@pytest.mark.parametrize(
    ("parts", "details"),
    [
        pytest.param(
            [TextPart("The monsoon arrived over Kerala. " * 4)],
            {"finish_reason": "repetition"},
            id="in text",
        ),
        pytest.param(
            # The looping argument never closed, so the call arrives empty.
            [ToolCallPart("save_note", {})],
            {"finish_reason": "tool_calls", "stop_reason": "repetition_detected"},
            id="in a tool call",
        ),
    ],
)
async def test_a_reply_stopped_for_repeating_ends_the_turn_at_once(parts, details) -> None:
    calls: list[str] = []
    agent = Agent(
        _model_saying(ModelResponse(parts=parts, provider_details=details)),
        capabilities=[llm.StopOnRepetition()],
    )

    @agent.tool_plain
    def save_note(text: str) -> str:
        calls.append(text)
        return "saved"

    # Not passed on as a finished answer, and not retried: a retry loops again.
    with pytest.raises(llm.ModelRepeated):
        await agent.run("Save a note.")
    assert calls == []


async def test_an_ordinary_reply_is_left_alone() -> None:
    agent = Agent(
        _model_saying(ModelResponse(parts=[TextPart("Onset is June 1.")])),
        capabilities=[llm.StopOnRepetition()],
    )

    assert (await agent.run("When?")).output == "Onset is June 1."


def test_the_turn_says_the_model_repeated_itself() -> None:
    # A loop cut off inside a tool call leaves that call failed, which would
    # otherwise read as a tool error.
    turn = ChatTurn(
        id="t",
        role="assistant",
        created_at="2026-10-24T00:00:00Z",
        tool_calls=[ChatToolCall(id="c", name="save_note", status="failed")],
    )
    error = llm.ModelRepeated("the model repeated itself and its reply was stopped")

    assert _classify_stream_error(error, turn) == "repetition"


def test_only_the_managed_local_model_asks_for_loop_detection(monkeypatch) -> None:
    monkeypatch.setattr(settings, "llm_provider", "openai-compatible")
    monkeypatch.setattr(settings, "llm_base_url", "http://127.0.0.1:8766/v1")
    monkeypatch.setattr(settings, "deployment_mode", "personal")

    monkeypatch.setattr(settings, "llm_engine", "managed")
    local = llm._build_model()
    assert isinstance(local, llm._LocalVllmModel)
    assert local.settings["extra_body"]["repetition_detection"]["min_count"] == 4

    # Another server may not know vLLM's extension, or may reject it.
    monkeypatch.setattr(settings, "llm_engine", "external")
    external = llm._build_model()
    assert type(external) is OpenAIChatModel
    assert not (external.settings or {}).get("extra_body")
