"""A failed reply is reported as what actually failed: a step the assistant ran, the
model's API, or the platform itself."""

from __future__ import annotations

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UserError
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from ai_almanac.server.services import chat_tools, chat_turns
from tests.helpers.sse import sse_events


def _error_type(exc: Exception) -> str:
    return chat_turns._classify_stream_error(exc)


async def _look_up_regions(messages: list, info: AgentInfo):
    yield {0: DeltaToolCall(name="list_regions", json_args="{}", tool_call_id="call-1")}


async def _crash(*_args, **_kwargs):
    raise KeyError("region")


async def _reply_error(client, as_new_user) -> dict:
    created = await client.post(
        "/chat/sessions",
        headers=as_new_user,
        json={
            "title": "Session",
            "scope": {"kind": "benchmark_run_group", "key": "g", "title": "G", "job_ids": []},
        },
    )
    response = await client.post(
        f"/chat/sessions/{created.json()['id']}/message",
        headers=as_new_user,
        json={"content": "which regions?"},
    )
    [error] = [e for e in sse_events(response.text) if e["type"] == "error"]
    return error


@pytest.mark.asyncio
async def test_a_step_that_crashes_is_reported_as_a_failed_step(
    client, as_new_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: FunctionModel(stream_function=_look_up_regions),
    )
    monkeypatch.setattr(chat_tools, "list_regions", _crash)

    error = await _reply_error(client, as_new_user)

    assert error["error_type"] == "tool_error"


def test_a_platform_fault_that_mentions_tools_is_not_blamed_on_a_step() -> None:
    fault = UserError("Tool call results need to be provided for all deferred tool calls.")

    assert _error_type(fault) == "internal_error"


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ReadTimeout(""),
        httpx.ConnectError("refused"),
        ModelHTTPError(status_code=529, model_name="m", body="overloaded"),
        UnexpectedModelBehavior("Exceeded maximum retries"),
    ],
)
def test_a_failure_of_the_models_api_is_a_provider_error(failure: Exception) -> None:
    assert _error_type(failure) == "provider_error"


def test_an_unrelated_bug_is_an_internal_error() -> None:
    assert _error_type(ValueError("bad tool config")) == "internal_error"


def test_a_reply_that_fails_after_its_approved_step_ran_says_the_step_ran() -> None:
    event = chat_turns._failure_event(httpx.ReadTimeout(""), step_ran=True)

    assert '"error_type": "step_ran_reply_failed"' in event
    assert "does not need approving again" in event
