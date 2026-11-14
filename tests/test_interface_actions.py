"""Interface actions: the assistant offers something to show, and starts nothing."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_almanac.server.services import interface_actions
from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.server.services.interface_actions import (
    BenchmarkDraft,
    BlendDraft,
    ForecastDraft,
    ShowFile,
    ShowRun,
)
from ai_almanac.server.services.llm import ChatDeps, chat_deps


def _ctx(user_id: str, workspace_id: str):
    scope = ChatScope(kind="workspace", key=workspace_id, job_ids=[])
    return SimpleNamespace(
        deps=ChatDeps(user_id=user_id, session_id="session", scope=scope, workspace_id=workspace_id)
    )


async def _workspace(client, headers) -> dict:
    response = await client.post("/workspaces", headers=headers, json={"name": "Kiremt 2027"})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_a_setup_form_is_offered_with_what_the_assistant_filled_in(
    client, as_new_user, user_id
) -> None:
    workspace = await _workspace(client, as_new_user)
    draft = BlendDraft(name="Kiremt blend", training_years="2008:2020")

    result = await interface_actions.open_panel(_ctx(user_id, workspace["id"]), draft)

    assert result[interface_actions.ACTION_KEY] == {
        "kind": "open_panel",
        "panel": "blend_setup",
        "draft": {
            "name": "Kiremt blend",
            "model_ids": [],
            "training_years": "2008:2020",
            "cv_holdout_years": "",
            "forecast_years": "",
            "true_holdout_years": "",
            "formula_text": "",
            "cutoff_month_day": "",
            "ref_onset_month_day": "",
        },
    }
    assert "Nothing has been started" in result["offered"]


@pytest.mark.asyncio
async def test_a_draft_naming_a_model_the_catalog_does_not_have_is_sent_back_to_be_fixed(
    client, as_new_user, user_id
) -> None:
    workspace = await _workspace(client, as_new_user)
    draft = BlendDraft(model_ids=["no-such-model"])

    result = await interface_actions.open_panel(_ctx(user_id, workspace["id"]), draft)

    assert interface_actions.ACTION_KEY not in result
    assert "no-such-model" in result["error"]
    assert "list_blend_models" in result["error"]


@pytest.mark.asyncio
async def test_a_forecast_form_is_not_offered_for_a_blend_outside_the_workspace(
    client, as_new_user, user_id
) -> None:
    workspace = await _workspace(client, as_new_user)

    result = await interface_actions.open_panel(
        _ctx(user_id, workspace["id"]), ForecastDraft(blend_id="someone-elses-blend")
    )

    assert interface_actions.ACTION_KEY not in result
    assert "not in this workspace" in result["error"]


@pytest.mark.asyncio
async def test_an_empty_draft_opens_a_blank_form(client, as_new_user, user_id) -> None:
    workspace = await _workspace(client, as_new_user)

    result = await interface_actions.open_panel(_ctx(user_id, workspace["id"]), BenchmarkDraft())

    assert result[interface_actions.ACTION_KEY] == {
        "kind": "open_panel",
        "panel": "benchmark_setup",
        "draft": {"model_ids": []},
    }


@pytest.mark.asyncio
async def test_only_what_is_in_the_workspace_can_be_shown(client, as_new_user, user_id) -> None:
    workspace = await _workspace(client, as_new_user)
    ctx = _ctx(user_id, workspace["id"])

    run = await interface_actions.show(ctx, ShowRun(job_ids=["not-here"]))
    file = await interface_actions.show(ctx, ShowFile(artifact="not-here"))

    assert interface_actions.ACTION_KEY not in run
    assert interface_actions.ACTION_KEY not in file


@pytest.mark.asyncio
async def test_nothing_is_offered_in_a_workspace_that_is_not_the_callers(
    client, as_new_user, user_id
) -> None:
    owner = {"X-Forwarded-User": "workspace-owner"}
    theirs = await _workspace(client, owner)
    scope = ChatScope(kind="workspace", key=theirs["id"], job_ids=[])
    ctx = SimpleNamespace(deps=await chat_deps(user_id, "session", scope))

    opened = await interface_actions.open_panel(ctx, BenchmarkDraft())
    shown = await interface_actions.show(ctx, ShowRun(job_ids=["any"]))

    assert opened == shown == {"error": "This workspace is not available in this conversation."}


def test_an_id_is_unknown_only_against_a_catalog_that_could_be_read() -> None:
    assert interface_actions.unknown_ids(["a", "b"], {"a"}) == ["b"]
    assert interface_actions.unknown_ids(["a", "b"], None) == []


@pytest.mark.asyncio
async def test_the_sentence_after_an_action_is_part_of_the_reply_as_it_is_shown(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from pydantic_ai.messages import ModelResponse, ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

    from ai_almanac.server.services import llm

    async def offer_then_say(messages, _info: AgentInfo):
        answered = any(
            isinstance(part, ToolReturnPart)
            for message in messages
            if not isinstance(message, ModelResponse)
            for part in message.parts
        )
        if answered:
            yield "I have set up a forecast form for you."
        else:
            arguments = json.dumps({"draft": {"panel": "forecast_setup"}})
            yield {0: DeltaToolCall(name="open_panel", json_args=arguments, tool_call_id="c1")}

    monkeypatch.setattr(llm, "_build_model", lambda: FunctionModel(stream_function=offer_then_say))
    workspace = await _workspace(client, as_new_user)
    session = (
        await client.post(
            "/chat/sessions",
            headers=as_new_user,
            json={
                "title": "ws",
                "scope": {"kind": "workspace", "key": workspace["id"], "title": "", "job_ids": []},
            },
        )
    ).json()
    sent = await client.post(
        f"/chat/sessions/{session['id']}/message",
        headers=as_new_user,
        json={"content": "Set up a forecast"},
    )
    assert sent.status_code == 200, sent.text

    saved = (await client.get(f"/chat/sessions/{session['id']}", headers=as_new_user)).json()
    turn = saved["transcript"][-1]
    assert turn["content"] == "I have set up a forecast form for you."
    assert [block["kind"] for block in turn["blocks"]] == ["tool", "text"]
    assert (
        turn["tool_calls"][0]["result"][interface_actions.ACTION_KEY]["panel"] == "forecast_setup"
    )
