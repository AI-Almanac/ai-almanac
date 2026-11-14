"""An approval the user has not answered travels with the conversation, so the
card can be shown again after the page is left and reopened."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from ai_almanac.paths import database_path
from ai_almanac.server.services import llm, workspaces
from ai_almanac.server.services.chat_state import ChatTurn, unprocessed_tool_calls
from ai_almanac.server.services.chat_turns import pending_approval, with_step_answered
from ai_almanac.server.services.turn_events import apply_stream_event
from tests.helpers.records import insert_job
from tests.helpers.sse import sse_events
from tests.helpers.workspaces import workspace_with_session


async def _running_run_in(user_id: str, workspace_id: str) -> str:
    job_id = insert_job(user_id, {"blend_name": "Amhara blend"}, job_type="blend", status="running")
    await workspaces.link_job(workspace_id, job_id)
    return job_id


def _asking_to_cancel(job_id: str, then):
    """An assistant that asks to stop ``job_id``; once the decision is back it does ``then``."""

    async def stream(messages: list, info: AgentInfo):
        if any(isinstance(p, ToolReturnPart) for p in messages[-1].parts):
            async for part in then():
                yield part
        else:
            yield {
                0: DeltaToolCall(
                    name="cancel_run",
                    json_args=json.dumps({"job_id": job_id}),
                    tool_call_id="call-1",
                )
            }

    return lambda: FunctionModel(stream_function=stream)


async def _carry_on():
    yield "Carrying on."


async def _fail_to_reply():
    raise TimeoutError("the model's API timed out")
    yield


async def _conversation_waiting_on_an_approval(
    client, as_new_user, user_id, monkeypatch, then=_carry_on
) -> str:
    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = await _running_run_in(user_id, workspace["id"])
    model = _asking_to_cancel(job_id, then)
    monkeypatch.setattr("ai_almanac.server.services.llm._build_model", model)
    await client.post(
        f"/chat/sessions/{session['id']}/message", headers=as_new_user, json={"content": "stop it"}
    )
    return session["id"]


def _runs_canceled() -> int:
    with sqlite3.connect(database_path()) as conn:
        return conn.execute("SELECT COUNT(*) FROM jobs WHERE status = 'canceling'").fetchone()[0]


async def _reloaded(client, as_new_user, session_id: str) -> dict:
    response = await client.get(f"/chat/sessions/{session_id}", headers=as_new_user)
    assert response.status_code == 200, response.text
    return response.json()


async def _decide(client, as_new_user, session_id: str, approved: bool) -> list[dict]:
    response = await client.post(
        f"/chat/sessions/{session_id}/tool-approval",
        headers=as_new_user,
        json={"tool_call_id": "call-1", "approved": approved},
    )
    return sse_events(response.text)


@pytest.mark.asyncio
async def test_an_unanswered_approval_comes_back_with_the_conversation(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _conversation_waiting_on_an_approval(
        client, as_new_user, user_id, monkeypatch
    )

    pending = (await _reloaded(client, as_new_user, session_id))["pending_approval"]

    assert pending["type"] == "tool_approval_request"
    assert pending["tool_call_id"] == "call-1"
    assert pending["tool_call"]["name"] == "cancel_run"
    assert pending["metadata"]["display"]["facts"], "the card's facts come back too"


@pytest.mark.asyncio
async def test_approving_after_a_reload_runs_the_step_and_clears_the_request(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _conversation_waiting_on_an_approval(
        client, as_new_user, user_id, monkeypatch
    )
    await _reloaded(client, as_new_user, session_id)

    events = await _decide(client, as_new_user, session_id, approved=True)

    [result] = [e for e in events if e["type"] == "tool_result"]
    assert [run["name"] for run in result["result"]["canceled"]] == ["Amhara blend"]
    assert (await _reloaded(client, as_new_user, session_id))["pending_approval"] is None


def _stored_steps(conversation: dict) -> dict[str, dict]:
    """Each step as the stored conversation holds it, which is once."""
    calls = [call for turn in conversation["transcript"] for call in turn["tool_calls"]]
    assert len({call["id"] for call in calls}) == len(calls), "a step is stored twice"
    return {call["id"]: call for call in calls}


@pytest.mark.asyncio
async def test_a_declined_step_clears_the_request_and_is_stored_once_as_not_approved(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _conversation_waiting_on_an_approval(
        client, as_new_user, user_id, monkeypatch
    )

    events = await _decide(client, as_new_user, session_id, approved=False)
    conversation = await _reloaded(client, as_new_user, session_id)

    declined = _stored_steps(conversation)["call-1"]
    reply = conversation["transcript"][-1]
    assert events[-1]["type"] == "done" and conversation["pending_approval"] is None
    assert (declined["status"], declined["result"]) == ("failed", {"error": "Not approved"})
    assert reply["content"] == "Carrying on." and reply["tool_calls"] == []
    assert all(block.get("tool_call_id") != "call-1" for block in reply["blocks"])
    assert not [e for e in events if e["type"] in ("tool_call", "tool_result")], (
        "the client already shows the step as not approved; an echo would overwrite that"
    )


@pytest.mark.asyncio
async def test_a_later_message_passes_over_the_request_as_if_it_were_declined(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _conversation_waiting_on_an_approval(
        client, as_new_user, user_id, monkeypatch
    )
    waiting = _stored_steps(await _reloaded(client, as_new_user, session_id))["call-1"]

    await client.post(
        f"/chat/sessions/{session_id}/message",
        headers=as_new_user,
        json={"content": "Actually, hold off."},
    )
    conversation = await _reloaded(client, as_new_user, session_id)
    expired = await _decide(client, as_new_user, session_id, approved=True)

    passed_over = _stored_steps(conversation)["call-1"]
    assert waiting["status"] == "running"
    assert conversation["pending_approval"] is None
    assert (passed_over["status"], passed_over["result"]) == ("failed", {"error": "Not approved"})
    assert expired[-1]["error_type"] == "approval_expired"
    assert _runs_canceled() == 0


# --- a step that ran is never offered again --------------------------------------


@pytest.mark.asyncio
async def test_a_step_that_ran_is_not_offered_again_when_the_reply_about_it_fails(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _conversation_waiting_on_an_approval(
        client, as_new_user, user_id, monkeypatch, then=_fail_to_reply
    )

    first = await _decide(client, as_new_user, session_id, approved=True)
    conversation = await _reloaded(client, as_new_user, session_id)
    again = await _decide(client, as_new_user, session_id, approved=True)

    [result] = [e for e in first if e["type"] == "tool_result"]
    assert result["result"]["canceled"], "the approved step ran"
    assert first[-1]["type"] == "error" and "does not need approving again" in first[-1]["message"]
    assert conversation["pending_approval"] is None
    assert _stored_steps(conversation)["call-1"]["status"] == "completed"
    failed_reply = conversation["transcript"][-1]
    assert failed_reply["status"] == "failed" and "reply about it failed" in failed_reply["error"]
    assert again[-1]["error_type"] == "approval_expired"
    assert _runs_canceled() == 1, "nothing ran twice"


@pytest.mark.asyncio
async def test_a_step_that_never_ran_keeps_its_card_when_the_attempt_fails(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_id = await _conversation_waiting_on_an_approval(
        client, as_new_user, user_id, monkeypatch
    )
    working_model = llm._build_model

    def unreachable():
        raise RuntimeError("the model could not be reached")

    monkeypatch.setattr("ai_almanac.server.services.llm._build_model", unreachable)
    failed = await _decide(client, as_new_user, session_id, approved=True)
    still_waiting = (await _reloaded(client, as_new_user, session_id))["pending_approval"]
    monkeypatch.setattr("ai_almanac.server.services.llm._build_model", working_model)
    retried = await _decide(client, as_new_user, session_id, approved=True)

    assert failed[-1]["type"] == "error"
    assert not [e for e in failed if e["type"] == "tool_result"], "the step did not run"
    assert still_waiting is not None and still_waiting["tool_call_id"] == "call-1"
    [result] = [e for e in retried if e["type"] == "tool_result"]
    assert result["result"]["canceled"]


def test_a_result_recorded_for_a_step_answers_it_and_leaves_others_waiting() -> None:
    asked = [
        ModelResponse(
            parts=[
                ToolCallPart(tool_name="fetch", args={}, tool_call_id="call-1"),
                ToolCallPart(tool_name="fetch", args={}, tool_call_id="call-2"),
            ]
        )
    ]
    transcript = [_turn_asking("call-2")]

    answered = with_step_answered(asked, "call-1", {"ok": True})

    assert [call.tool_call_id for call in unprocessed_tool_calls(answered)] == ["call-2"]
    assert pending_approval(transcript, answered, datetime.now(UTC)).tool_call_id == "call-2"
    assert with_step_answered(answered, "call-1", {"ok": True}) == answered
    both = with_step_answered(answered, "call-2", {"ok": True})
    assert unprocessed_tool_calls(both) == []
    assert pending_approval(transcript, both, datetime.now(UTC)) is None


def _waiting_on(tool_call_id: str) -> list:
    return [
        ModelResponse(parts=[ToolCallPart(tool_name="fetch", args={}, tool_call_id=tool_call_id)])
    ]


def _turn_asking(tool_call_id: str, status: str = "completed") -> dict:
    turn = ChatTurn(id="turn-1", role="assistant", created_at=datetime.now(UTC), status=status)
    apply_stream_event(
        turn,
        {
            "type": "tool_approval_request",
            "turn_id": "turn-1",
            "tool_call_id": tool_call_id,
            "tool_call": {"id": tool_call_id, "name": "fetch", "status": "running"},
            "metadata": {"display": {"facts": [{"label": "Source", "value": "CHIRPS"}]}},
        },
    )
    return turn.model_dump(mode="json")


_STREAMING_REPLY = {"id": "turn-2", "role": "assistant", "status": "streaming"}


@pytest.mark.parametrize(
    ("transcript", "waiting_on", "hours_ago", "pending"),
    [
        ([_turn_asking("call-1"), _STREAMING_REPLY], "call-1", 0, None),
        ([_turn_asking("call-1"), _STREAMING_REPLY], "call-1", 1, "call-1"),
        ([_turn_asking("call-1")], "call-2", 0, None),
        ([_turn_asking("call-1")], None, 0, None),
    ],
    ids=[
        "the decision is being acted on",
        "the reply's server died",
        "the request was for an earlier step",
        "no step is waiting",
    ],
)
def test_the_pending_request_is_the_open_step_nobody_is_acting_on(
    transcript: list, waiting_on: str | None, hours_ago: int, pending: str | None
) -> None:
    asked = _waiting_on(waiting_on) if waiting_on else []
    at = datetime.now(UTC) - timedelta(hours=hours_ago)

    found = pending_approval(transcript, asked, at)

    assert (found.tool_call_id if found else None) == pending


def test_benchmark_and_blend_requests_are_kept_on_the_turn_in_their_streamed_shape() -> None:
    from ai_almanac.server.services.benchmark_state import BenchmarkRunSpec
    from ai_almanac.server.services.blend_state import BlendRunSpec

    turn = ChatTurn(id="turn-1", role="assistant", created_at=datetime.now(UTC))
    for kind, spec in (("benchmark", BenchmarkRunSpec()), ("blend", BlendRunSpec())):
        apply_stream_event(
            turn,
            {
                "type": f"{kind}_approval_request",
                "turn_id": "turn-1",
                "tool_call_id": f"{kind}-call",
                "config": spec.model_dump(mode="json"),
                "validation": None,
            },
        )

    pending = pending_approval(
        [turn.model_dump(mode="json")], _waiting_on("blend-call"), datetime.now(UTC)
    )

    assert pending is not None and pending.type == "blend_approval_request"
    assert pending.config == BlendRunSpec()
