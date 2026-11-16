"""Fetching data into a workspace: it waits for approval, refuses what it cannot do
before asking, and seals the source record it read."""

from __future__ import annotations

import json

import pytest
from pydantic_ai import ApprovalRequired

from ai_almanac.server.services import (
    tools_sandbox,
    workspaces,
)
from ai_almanac.server.services.primitives import acquire
from ai_almanac.settings import settings
from tests.helpers.fetch import local_fetch_sandbox, senegal_fetch
from tests.helpers.sse import sse_events
from tests.helpers.tool_calls import approve, tool_ctx
from tests.helpers.workspaces import (
    workspace_with_session,
)


@pytest.mark.asyncio
async def test_fetch_waits_for_approval_then_seals_the_source_record_it_read(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = local_fetch_sandbox(monkeypatch)
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]

    with pytest.raises(ApprovalRequired):
        await acquire.fetch(tool_ctx(user_id, session["id"], wid), request=senegal_fetch())
    assert allowed == []

    result = await approve(
        acquire.fetch, tool_ctx(user_id, session["id"], wid), request=senegal_fetch()
    )
    assert result.get("ok"), result
    assert allowed == [("storage.googleapis.com",)]

    artifact = await workspaces.resolve(wid, "era5_arco_precip_2026-06-01_2026-06-10")
    described = await workspaces.describe(wid, artifact, verify=True)
    [step] = [s for s in described["steps"] if s["primitive"] == "acquire.fetch"]
    assert step["runtime"]["source_version"] == "record through 2026-06-30"
    assert step["runtime"]["ai_almanac_tools"] == tools_sandbox.tools_digest()
    assert step["params"]["request"]["lon_bounds"] == [-17.0, -14.0]
    assert step["approval_id"] is not None
    assert step["verification"] == "verified"


@pytest.mark.asyncio
async def test_days_past_the_record_are_refused_before_approval(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = local_fetch_sandbox(monkeypatch)
    workspace, session = await workspace_with_session(client, as_new_user)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    late = senegal_fetch(start="2026-09-29", end="2026-09-30")

    planned = await acquire.plan_fetch(ctx, request=late)
    fetched = await acquire.fetch(ctx, request=late)

    assert planned == {
        "ok": False,
        "error": "era5_arco has data only up to 2026-07-31; ask for days up to then.",
        "record_end": "2026-07-31",
    }
    assert fetched == planned
    assert allowed == []


@pytest.mark.asyncio
async def test_a_fetch_over_the_size_limit_is_refused_before_approval(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = local_fetch_sandbox(monkeypatch)
    monkeypatch.setattr(settings, "max_fetch_bytes", 1_000_000)
    workspace, session = await workspace_with_session(client, as_new_user)
    ctx = tool_ctx(user_id, session["id"], workspace["id"])
    globe = senegal_fetch(lat_bounds=(-90, 90), lon_bounds=(0, 360), end="2026-06-01")

    planned = await acquire.plan_fetch(ctx, request=globe)
    fetched = await acquire.fetch(ctx, request=globe)

    assert planned["ok"] is False and "about 4 MB, over the 1 MB limit" in planned["error"]
    assert fetched == planned
    assert allowed == []


@pytest.mark.asyncio
async def test_what_the_assistant_does_after_an_approval_reaches_the_user(
    client, as_new_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the continuation after an approval was run but only the approved
    call's result was kept, so its reply and a second approval request never showed."""
    from pydantic_ai.messages import ToolReturnPart
    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

    local_fetch_sandbox(monkeypatch)

    def fetch_call(call_id: str, start: str, end: str) -> dict:
        args = senegal_fetch(start=start, end=end).model_dump(mode="json")
        return {
            0: DeltaToolCall(
                name="fetch", json_args=json.dumps({"request": args}), tool_call_id=call_id
            )
        }

    async def stream(messages: list, info: AgentInfo):
        returned = {p.tool_call_id for p in messages[-1].parts if isinstance(p, ToolReturnPart)}
        if "call-2" in returned:
            yield "Both months are in."
        elif "call-1" in returned:
            yield "June is in. Fetching July next."
            yield fetch_call("call-2", "2026-07-01", "2026-07-10")
        else:
            yield fetch_call("call-1", "2026-06-01", "2026-06-10")

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model", lambda: FunctionModel(stream_function=stream)
    )
    workspace, session = await workspace_with_session(client, as_new_user)
    sid = session["id"]

    asked = sse_events(
        (
            await client.post(
                f"/chat/sessions/{sid}/message", headers=as_new_user, json={"content": "fetch"}
            )
        ).text
    )
    assert [e["tool_call"]["id"] for e in asked if e["type"] == "tool_approval_request"] == [
        "call-1"
    ]

    approve = {"tool_call_id": "call-1", "approved": True}
    resumed = sse_events(
        (
            await client.post(
                f"/chat/sessions/{sid}/tool-approval", headers=as_new_user, json=approve
            )
        ).text
    )
    types = [e["type"] for e in resumed]
    assert types[-1] == "done", resumed
    [result] = [e for e in resumed if e["type"] == "tool_result"]
    assert result["tool_call_id"] == "call-1" and result["result"]["ok"] is True
    assert "June is in" in "".join(e["content"] for e in resumed if e["type"] == "text_delta")
    assert [e["tool_call"]["id"] for e in resumed if e["type"] == "tool_approval_request"] == [
        "call-2"
    ]

    transcript = (await client.get(f"/chat/sessions/{sid}", headers=as_new_user)).json()[
        "transcript"
    ]
    proposing, continuation = transcript[-2], transcript[-1]
    [call_1] = proposing["tool_calls"]
    assert call_1["id"] == "call-1" and call_1["status"] == "completed"
    assert continuation["trigger"] == "approval:call-1"
    assert continuation["content"].startswith("June is in")
    assert [c["id"] for c in continuation["tool_calls"]] == ["call-2"]

    again = await client.post(
        f"/chat/sessions/{sid}/tool-approval", headers=as_new_user, json=approve
    )
    assert sse_events(again.text)[-1]["error_type"] == "approval_expired"
