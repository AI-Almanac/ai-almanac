"""An approval covers the workspace settings a step runs with, and a step whose settings
moved after its card was shown is not run."""

from __future__ import annotations

import pytest

from ai_almanac.server.services import chat_turns
from ai_almanac.server.services.focus_area import FocusUnits
from ai_almanac.server.services.primitives import registry, runs
from ai_almanac.server.services.primitives import workspace as workspace_tools
from tests.helpers.sse import sse_events
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import workspace_with_session

KENYA = FocusUnits(level="adm0", units=["Kenya"], iso="KEN")
UGANDA = FocusUnits(level="adm0", units=["Uganda"], iso="UGA")


def _benchmark() -> runs.BenchmarkSpec:
    return runs.BenchmarkSpec(dataset_id="observations", model_ids=["model"])


def test_the_step_being_decided_is_checked_against_what_its_card_showed() -> None:
    transcript = [
        {"approval_requests": [{"tool_call_id": "earlier", "metadata": {"params": {"a": 1}}}]},
        {
            "approval_requests": [
                {"tool_call_id": "this-one", "metadata": {"params": {"spec": {"area": "Kenya"}}}}
            ]
        },
        {"approval_requests": [{"tool_call_id": "old-card", "metadata": {}}]},
    ]

    assert chat_turns.approved_step(transcript, "this-one") == {
        "approved_params": {"spec": {"area": "Kenya"}}
    }
    assert chat_turns.approved_step(transcript, "old-card") == {}, "a card that kept no params"
    assert chat_turns.approved_step(transcript, "unknown") == {}


# --- through a real conversation -------------------------------------------------


def _asks_to_benchmark_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """An assistant that asks to submit a benchmark naming no area, then carries on."""
    import json

    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

    spec = _benchmark().model_dump(mode="json")

    async def stream(messages: list, info: AgentInfo):
        if [part for part in messages[-1].parts if isinstance(part, ToolReturnPart)]:
            yield "Carrying on."
        else:
            yield {
                0: DeltaToolCall(
                    name="submit_benchmark",
                    json_args=json.dumps({"spec": spec}),
                    tool_call_id="call-1",
                )
            }

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model", lambda: FunctionModel(stream_function=stream)
    )


async def _approve(client, headers, session_id: str) -> dict:

    response = await client.post(
        f"/chat/sessions/{session_id}/tool-approval",
        headers=headers,
        json={"tool_call_id": "call-1", "approved": True},
    )
    [result] = [event for event in sse_events(response.text) if event["type"] == "tool_result"]
    return result["result"]


async def _waiting_on_a_benchmark_in(client, headers, user_id, monkeypatch, area) -> tuple:
    _asks_to_benchmark_once(monkeypatch)
    workspace, session = await workspace_with_session(client, headers)
    await workspace_tools.set_workspace_area(
        tool_ctx(user_id, session["id"], workspace["id"]), area=area
    )
    await client.post(
        f"/chat/sessions/{session['id']}/message", headers=headers, json={"content": "benchmark"}
    )
    return workspace, session


async def test_approving_a_card_after_the_workspaces_area_changed_runs_nothing(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, session = await _waiting_on_a_benchmark_in(
        client, as_new_user, user_id, monkeypatch, KENYA
    )
    card = (await client.get(f"/chat/sessions/{session['id']}", headers=as_new_user)).json()
    shown = card["pending_approval"]["metadata"]["params"]["spec"]["advanced_params"]
    assert shown["focus_area"]["units"] == ["Kenya"]

    moved = await client.patch(
        f"/workspaces/{workspace['id']}/settings",
        headers=as_new_user,
        json={"study_area": UGANDA.model_dump(mode="json")},
    )
    assert moved.status_code == 200, moved.text
    result = await _approve(client, as_new_user, session["id"])

    assert result["error"] == registry.SETTINGS_MOVED


async def test_approving_a_card_whose_settings_stayed_put_runs_the_step_shown(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, session = await _waiting_on_a_benchmark_in(client, as_new_user, user_id, monkeypatch, KENYA)

    result = await _approve(client, as_new_user, session["id"])

    assert result.get("error") != registry.SETTINGS_MOVED
