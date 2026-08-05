from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.test import TestModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


def _scope(*, job_ids: list[str] | None = None) -> dict:
    return {
        "kind": "benchmark_run_group",
        "key": "group-1",
        "title": "Group 1",
        "job_ids": job_ids or [],
    }


_STATIC_CUSTOM_REGION = {
    "id": "central-highlands",
    "display_name": "Central Highlands",
    "description": "",
    "romp_name": None,
    "lat_min": 20.0,
    "lat_max": 27.0,
    "lon_min": 88.0,
    "lon_max": 93.0,
    "land_only": False,
    "shp_only": False,
}


def _packaged_catalog(models: tuple = (), extra_regions: tuple = ()):
    from ai_almanac.server.services.registry import CatalogSnapshot
    from ai_almanac.settings import get_packaged_regions

    return CatalogSnapshot(regions=(*get_packaged_regions(), *extra_regions), models=models)


def _sse_events(body: str) -> list[dict]:
    events: list[dict] = []
    for chunk in body.strip().split("\n\n"):
        if not chunk.startswith("data: "):
            continue
        events.append(json.loads(chunk.removeprefix("data: ")))
    return events


def test_chat_json_helpers_treat_empty_strings_as_defaults() -> None:
    from ai_almanac.server.services.chat_turns import json_dict, json_list

    assert json_list("") == []
    assert json_list("  \n") == []
    assert json_dict("") == {}
    assert json_dict("  \n") == {}


def test_parse_llm_event_skips_blank_events() -> None:
    from ai_almanac.server.services.chat_turns import parse_llm_event

    assert parse_llm_event("") is None
    assert parse_llm_event("  \n") is None
    assert parse_llm_event('{"type":"done"}') == {"type": "done"}


def test_romp_params_merge_shared_and_per_model_advanced_params() -> None:
    from ai_almanac.server.services.benchmark_domain import _clamp_model_params
    from ai_almanac.server.services.benchmark_state import BenchmarkRunSpec

    model = {
        "id": "fuxi",
        "start_date": "1964-05-01",
        "end_date": "2024-07-31",
        "start_year_clim": 1964,
        "end_year_clim": 2024,
        "init_days": "0,3",
        "probabilistic": False,
        "model_var": "tp",
        "file_pattern": "{}.nc",
    }
    spec = BenchmarkRunSpec(
        region_id="india",
        model_ids=["fuxi"],
        advanced_params={
            "wet_threshold": 10,
            "per_model_params": {
                "fuxi": {
                    "start_date": "2019-05-01",
                    "end_date": "2021-07-31",
                    "start_year_clim": 1991,
                    "end_year_clim": 2021,
                },
                "aifs": {"start_date": "2018-05-01"},
            },
        },
    )

    params = _clamp_model_params(model, spec)

    assert params["wet_threshold"] == 10
    assert params["start_date"] == "2019-05-01"
    assert params["end_date"] == "2021-07-31"
    assert params["start_year_clim"] == 1991
    assert params["end_year_clim"] == 2021
    assert "per_model_params" not in params


def test_apply_region_params_adds_custom_region_bounds() -> None:
    from ai_almanac.server.services.job_submission import apply_region_params

    params = apply_region_params(
        {"region": "central-highlands", "start_date": "2020-05-01"},
        _packaged_catalog(extra_regions=(_STATIC_CUSTOM_REGION,)),
    )

    assert params == {
        "region": "custom",
        "start_date": "2020-05-01",
        "lat_min": 20.0,
        "lat_max": 27.0,
        "lon_min": 88.0,
        "lon_max": 93.0,
        "land_only": False,
        "shp_only": False,
    }


def test_apply_region_params_maps_builtin_region_name() -> None:
    from ai_almanac.server.services.job_submission import apply_region_params

    params = apply_region_params({"region": "india"}, _packaged_catalog())

    assert params == {"region": "India"}


def test_job_region_metadata_prefers_dataset_region_for_custom_romp_region() -> None:
    from ai_almanac.server.services.job_submission import job_region_metadata

    metadata = job_region_metadata(
        {
            "dataset_config": {"region": "central-highlands"},
            "romp_params": {"region": "custom"},
        },
        _packaged_catalog(extra_regions=(_STATIC_CUSTOM_REGION,)),
    )

    assert metadata == {
        "region_id": "central-highlands",
        "region_name": "Central Highlands",
        "romp_region": "custom",
    }


def test_job_region_metadata_maps_builtin_romp_region() -> None:
    from ai_almanac.server.services.job_submission import job_region_metadata

    metadata = job_region_metadata({"romp_params": {"region": "India"}}, _packaged_catalog())

    assert metadata == {
        "region_id": "india",
        "region_name": "India",
        "romp_region": "India",
    }


def test_job_region_metadata_preserves_persisted_region_snapshot() -> None:
    from ai_almanac.server.services.job_submission import job_region_metadata

    metadata = job_region_metadata(
        {
            "region_id": "central-highlands",
            "region_name": "Central Highlands",
            "romp_region": "custom",
            "romp_params": {"region": "custom"},
        },
        _packaged_catalog(),
    )

    assert metadata == {
        "region_id": "central-highlands",
        "region_name": "Central Highlands",
        "romp_region": "custom",
    }


def test_job_output_uses_display_name_for_legacy_uuid_model() -> None:
    from ai_almanac.server.services.job_submission import row_to_job_out

    job = row_to_job_out(
        {
            "id": "job-1",
            "user_id": "local",
            "dataset_id": "obs-1",
            "status": "complete",
            "config_json": json.dumps(
                {
                    "model_name": "db956a33-e511-4ac7-8484-ac6b7fc3e877",
                    "model_config": {
                        "id": "db956a33-e511-4ac7-8484-ac6b7fc3e877",
                        "display_name": "FuXi Ethiopia",
                    },
                }
            ),
            "created_at": "2026-06-08T20:00:00+00:00",
        },
        "local",
        _packaged_catalog(),
    )

    assert job.model_name == "db956a33-e511-4ac7-8484-ac6b7fc3e877"
    assert job.model_display_name == "FuXi Ethiopia"
    assert job.model_source_id == "db956a33-e511-4ac7-8484-ac6b7fc3e877"


@pytest.mark.asyncio
async def test_submit_benchmark_passes_region_id_to_job_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services import benchmark_domain
    from ai_almanac.server.services.benchmark_state import (
        BenchmarkRunSpec,
        BenchmarkScope,
        BenchmarkValidation,
    )

    captured_params = []

    class CreatedJob:
        def model_dump(self, mode: str) -> dict:
            return {"id": "job-1"}

    class DbConnection:
        async def execute(self, *args, **kwargs) -> None:
            return None

    class DbContext:
        async def __aenter__(self) -> DbConnection:
            return DbConnection()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    async def load_config(session_id: str, user_id: str) -> BenchmarkRunSpec:
        return BenchmarkRunSpec(
            region_id="bangladesh",
            region_name="Bangladesh",
            romp_region="custom",
            dataset_id="demo:bangladesh",
            dataset_name="Bangladesh",
            model_ids=["e2s-test"],
            model_names=["E2S (Test)"],
            forecast_window_days=30,
        )

    async def save_state(*args) -> None:
        return None

    async def create_job_for_user(body, user_id):
        assert user_id == "user-1"
        captured_params.append(body.params.model_dump(exclude_none=True))
        return CreatedJob()

    monkeypatch.setattr(benchmark_domain, "_load_benchmark_config", load_config)
    monkeypatch.setattr(benchmark_domain, "_save_benchmark_state", save_state)
    monkeypatch.setattr("ai_almanac.server.db.get_db", lambda: DbContext())
    monkeypatch.setattr(
        benchmark_domain,
        "_validation_for_config",
        lambda spec, catalog: BenchmarkValidation(can_run=True, status="runnable"),
    )

    catalog = _packaged_catalog(
        models=(
            {
                "id": "e2s-test",
                "region": "bangladesh",
                "start_date": "2020-05-01",
                "end_date": "2022-09-30",
                "start_year_clim": 2020,
                "end_year_clim": 2022,
                "init_days": "0,3",
                "probabilistic": False,
            },
        )
    )

    async def load_catalog():
        return catalog

    monkeypatch.setattr(benchmark_domain, "load_catalog", load_catalog)
    monkeypatch.setattr(
        "ai_almanac.server.services.job_submission.create_job_for_user",
        create_job_for_user,
    )

    await benchmark_domain._exec_submit_benchmark(
        {},
        "user-1",
        BenchmarkScope(kind="benchmark_setup", key="setup-1", title="Setup"),
        "session-1",
    )

    assert captured_params[0]["region"] == "bangladesh"


@pytest.mark.asyncio
async def test_trim_chat_history_limits_messages_and_tool_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services.llm import trim_chat_history

    monkeypatch.setattr("ai_almanac.settings.settings.llm_history_max_messages", 2)
    monkeypatch.setattr("ai_almanac.settings.settings.llm_tool_result_max_chars", 8)

    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="get_job_logs",
                    tool_call_id="call-1",
                    content={
                        "logs": "0123456789abcdef",
                        "artifacts": [{"id": "artifact-1"}],
                    },
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="get_job_metrics",
                    tool_call_id="call-2",
                    content={"metric": "short"},
                )
            ]
        ),
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="get_job_logs",
                    tool_call_id="call-3",
                    content="abcdefghijk",
                )
            ]
        ),
    ]

    trimmed = await trim_chat_history(messages)

    assert len(trimmed) == 2
    assert isinstance(trimmed[0], ModelRequest)
    assert isinstance(trimmed[0].parts[0], ToolReturnPart)
    assert trimmed[0].parts[0].content == {"metric": "short"}
    assert isinstance(trimmed[1], ModelRequest)
    assert isinstance(trimmed[1].parts[0], ToolReturnPart)
    assert trimmed[1].parts[0].content == (
        "abcdefgh\n\n[tool result trimmed from conversation history]"
    )


@pytest.mark.asyncio
async def test_chat_agent_registers_expected_toolsets_and_uses_test_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services import rulesets
    from ai_almanac.server.services.chat_state import ChatScope
    from ai_almanac.server.services.llm import ChatDeps, _build_agent

    monkeypatch.setattr("ai_almanac.settings.settings.llm_base_url", "http://test.local")
    model = TestModel(call_tools=[], custom_output_text="ready")
    scope = ChatScope(
        kind="benchmark_run_group",
        key="group-1",
        title="Group 1",
        job_ids=[],
    )
    agent = _build_agent(scope, rulesets.packaged_ruleset("builtin"))

    with agent.override(model=model):
        result = await agent.run(
            "hello",
            deps=ChatDeps(user_id="user-1", session_id="session-1", scope=scope),
        )

    assert result.output == "ready"
    assert model.last_model_request_parameters is not None
    tool_names = {tool.name for tool in model.last_model_request_parameters.function_tools}
    assert {
        "list_regions",
        "update_benchmark_config",
        "submit_benchmark",
        "list_failed_jobs",
        "get_job_logs",
        "get_job_metrics",
    }.issubset(tool_names)


@pytest.mark.asyncio
async def test_stream_response_runs_with_pydantic_ai_test_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services import llm
    from ai_almanac.server.services.chat_state import ChatScope

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: TestModel(call_tools=[], custom_output_text="Synthetic answer."),
    )
    scope = ChatScope(
        kind="benchmark_run_group",
        key="group-1",
        title="Group 1",
        job_ids=[],
    )

    events = [
        json.loads(event)
        async for event in llm.stream_response(
            [],
            "user-1",
            "session-1",
            scope,
            latest_user_message="Summarize this run",
        )
    ]

    assert (
        "".join(event["content"] for event in events if event["type"] == "text_delta")
        == "Synthetic answer."
    )
    assert events[-1]["type"] == "done"
    assert events[-1]["turn"]["content"] == "Synthetic answer."
    assert events[-1]["provider_state"]


@pytest.mark.asyncio
async def test_stream_response_emits_tool_call_and_result_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TestModel(call_tools='all') exercises the FunctionToolResultEvent branch,
    which a tool-free run skips — guarding the pydantic-ai event-field renames."""
    from ai_almanac.server.services import llm
    from ai_almanac.server.services.chat_state import ChatScope

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: TestModel(call_tools="all"),
    )
    scope = ChatScope(kind="benchmark_run_group", key="group-1", title="Group 1", job_ids=[])

    events = [
        json.loads(event)
        async for event in llm.stream_response(
            [], "user-1", "session-1", scope, latest_user_message="run a tool"
        )
    ]

    types = [event["type"] for event in events]
    assert "tool_call" in types
    assert "tool_result" in types
    assert types[-1] == "done"


def test_guardrail_event_is_built_from_the_tool_result_not_the_model() -> None:
    """The banner the user sees comes off the validation payload, so no prose and
    no "ignore your rules" instruction can suppress it."""
    from ai_almanac.server.services import llm

    event = llm._guardrail_event(
        "turn-1",
        "call-1",
        {
            "blend_config": {},
            "blend_validation": {
                "errors": ["True holdout years were also used for training: 2018."],
                "warnings": ["Blending 3 models risks overfitting."],
            },
        },
    )

    assert event is not None
    payload = json.loads(event)
    assert payload["type"] == "guardrail"
    assert payload["turn_id"] == "turn-1"
    assert payload["tool_call_id"] == "call-1"
    assert "2018" in payload["errors"][0]
    assert "overfitting" in payload["warnings"][0]


def test_guardrail_event_is_omitted_when_a_config_is_clean() -> None:
    from ai_almanac.server.services import llm

    assert (
        llm._guardrail_event("t", "c", {"blend_validation": {"errors": [], "warnings": []}}) is None
    )
    assert llm._guardrail_event("t", "c", {"regions": []}) is None
    assert llm._guardrail_event("t", "c", "not a dict") is None


def test_guardrail_events_are_recorded_on_the_turn() -> None:
    """Persisted rather than stream-only, so the caution survives a reload."""
    from ai_almanac.server.services.chat_state import ChatTurn, utc_now
    from ai_almanac.server.services.chat_turns import _apply_stream_event

    turn = ChatTurn(id="turn-1", role="assistant", created_at=utc_now())
    _apply_stream_event(
        turn,
        {
            "type": "guardrail",
            "tool_call_id": "call-1",
            "errors": ["holdout leak"],
            "warnings": ["small sample"],
        },
    )

    assert turn.guardrails[0].errors == ["holdout leak"]
    assert turn.guardrails[0].warnings == ["small sample"]
    assert turn.guardrails[0].tool_call_id == "call-1"


async def _create_session(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    *,
    title: str = "Session",
    ruleset_id: str | None = None,
) -> dict:
    response = await client.post(
        "/chat/sessions",
        headers=auth_headers,
        json={"title": title, "scope": _scope(), "ruleset_id": ruleset_id},
    )
    assert response.status_code == 201
    return response.json()


async def _insert_job(engine: AsyncEngine, user_id: str, job_id: str) -> None:
    now = datetime.now(UTC).isoformat()
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO jobs (id, user_id, dataset_id, status, config_json, created_at)
                VALUES (:id, :user_id, :dataset_id, 'complete', '{}'::text, :created_at)
                """
            ),
            {
                "id": job_id,
                "user_id": user_id,
                "dataset_id": f"dataset-{job_id}",
                "created_at": now,
            },
        )


@pytest.mark.asyncio
async def test_chat_session_lifecycle(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    created = await _create_session(client, auth_headers, title="Original title")
    session_id = created["id"]

    list_response = await client.get(
        "/chat/sessions",
        headers=auth_headers,
        params={"scope_kind": "benchmark_run_group", "scope_key": "group-1"},
    )
    assert list_response.status_code == 200
    assert [session["id"] for session in list_response.json()] == [session_id]

    detail_response = await client.get(f"/chat/sessions/{session_id}", headers=auth_headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["transcript"] == []

    rename_response = await client.patch(
        f"/chat/sessions/{session_id}",
        headers=auth_headers,
        json={"title": "Renamed"},
    )
    assert rename_response.status_code == 200
    assert rename_response.json()["title"] == "Renamed"

    delete_response = await client.delete(f"/chat/sessions/{session_id}", headers=auth_headers)
    assert delete_response.status_code == 204

    missing_response = await client.get(f"/chat/sessions/{session_id}", headers=auth_headers)
    assert missing_response.status_code == 404


@pytest.mark.asyncio
async def test_send_message_persists_user_and_assistant_turns(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await _create_session(client, auth_headers)
    session_id = created["id"]

    async def fake_stream_response(
        provider_state: list,
        user_id: str,
        session_id_arg: str,
        scope: dict,
        *,
        latest_user_message: str | None = None,
        deferred_tool_results=None,
        **_ruleset_and_comparison: object,
    ) -> AsyncIterator[str]:
        assert latest_user_message == "How did this run do?"
        assert session_id_arg == session_id
        yield json.dumps({"type": "text_delta", "content": "It"})
        yield json.dumps(
            {
                "type": "done",
                "turn": {
                    "content": "It finished successfully.",
                    "tool_calls": [],
                    "artifacts": [],
                },
                "provider_state": [],
            }
        )

    monkeypatch.setattr(
        "ai_almanac.server.services.chat_turns.stream_response", fake_stream_response
    )

    response = await client.post(
        f"/chat/sessions/{session_id}/message",
        headers=auth_headers,
        json={"content": "How did this run do?"},
    )
    assert response.status_code == 200

    events = _sse_events(response.text)
    assert events[0] == {"type": "text_delta", "content": "It"}
    assert events[-1]["type"] == "done"
    assert events[-1]["turn"]["content"] == "It finished successfully."

    detail_response = await client.get(f"/chat/sessions/{session_id}", headers=auth_headers)
    transcript = detail_response.json()["transcript"]
    assert [turn["role"] for turn in transcript] == ["user", "assistant"]
    assert transcript[0]["content"] == "How did this run do?"
    assert transcript[1]["content"] == "It finished successfully."
    assert transcript[1]["status"] == "completed"


@pytest.mark.asyncio
async def test_send_message_persists_failed_assistant_turn_on_stream_error(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await _create_session(client, auth_headers)
    session_id = created["id"]

    async def failing_stream_response(
        provider_state: list,
        user_id: str,
        session_id_arg: str,
        scope: dict,
        *,
        latest_user_message: str | None = None,
        deferred_tool_results=None,
        **_ruleset_and_comparison: object,
    ) -> AsyncIterator[str]:
        assert session_id_arg == session_id
        yield json.dumps({"type": "text_delta", "content": "Partial"})
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(
        "ai_almanac.server.services.chat_turns.stream_response", failing_stream_response
    )

    response = await client.post(
        f"/chat/sessions/{session_id}/message",
        headers=auth_headers,
        json={"content": "Summarize this failure"},
    )
    assert response.status_code == 200

    events = _sse_events(response.text)
    assert events[0] == {"type": "text_delta", "content": "Partial"}
    assert events[-1]["type"] == "error"
    assert events[-1]["message"] == "Chat response failed"

    detail_response = await client.get(f"/chat/sessions/{session_id}", headers=auth_headers)
    transcript = detail_response.json()["transcript"]
    assert [turn["role"] for turn in transcript] == ["user", "assistant"]
    assert transcript[0]["content"] == "Summarize this failure"
    assert transcript[1]["status"] == "failed"
    assert transcript[1]["content"] == "Partial"
    assert transcript[1]["error"] == "provider exploded"


@pytest.mark.asyncio
async def test_send_message_denies_pending_tool_calls_before_new_prompt(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    _test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services.llm import serialize_model_messages

    created = await _create_session(client, auth_headers)
    session_id = created["id"]
    provider_state = serialize_model_messages(
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="submit_benchmark",
                        args={},
                        tool_call_id="approval-1",
                    )
                ]
            )
        ]
    )
    async with _test_engine.begin() as conn:
        await conn.execute(
            text("UPDATE chat_sessions SET provider_state = :state WHERE id = :id"),
            {"state": json.dumps(provider_state), "id": session_id},
        )

    async def fake_stream_response(
        provider_state: list,
        user_id: str,
        session_id_arg: str,
        scope: dict,
        *,
        latest_user_message: str | None = None,
        deferred_tool_results=None,
        **_ruleset_and_comparison: object,
    ) -> AsyncIterator[str]:
        assert latest_user_message == "Let's revise this first."
        assert isinstance(provider_state[-1], ModelRequest)
        denied_return = provider_state[-1].parts[0]
        assert isinstance(denied_return, ToolReturnPart)
        assert denied_return.tool_call_id == "approval-1"
        assert denied_return.outcome == "denied"
        yield json.dumps(
            {
                "type": "done",
                "turn": {
                    "content": "We can revise it.",
                    "tool_calls": [],
                    "artifacts": [],
                },
                "provider_state": serialize_model_messages(provider_state),
            }
        )

    monkeypatch.setattr(
        "ai_almanac.server.services.chat_turns.stream_response", fake_stream_response
    )

    response = await client.post(
        f"/chat/sessions/{session_id}/message",
        headers=auth_headers,
        json={"content": "Let's revise this first."},
    )
    assert response.status_code == 200
    assert _sse_events(response.text)[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_send_message_refreshes_scope_job_ids(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    user_id: str,
    _test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await _create_session(client, auth_headers)
    session_id = created["id"]
    job_id = f"job-{uuid4()}"
    await _insert_job(_test_engine, user_id, job_id)

    async def fake_stream_response(
        provider_state: list,
        user_id_arg: str,
        session_id_arg: str,
        scope: dict,
        *,
        latest_user_message: str | None = None,
        deferred_tool_results=None,
        **_ruleset_and_comparison: object,
    ) -> AsyncIterator[str]:
        assert session_id_arg == session_id
        assert scope.job_ids == [job_id]
        yield json.dumps(
            {
                "type": "done",
                "turn": {
                    "content": "Scoped response",
                    "tool_calls": [],
                    "artifacts": [],
                },
                "provider_state": [],
            }
        )

    monkeypatch.setattr(
        "ai_almanac.server.services.chat_turns.stream_response", fake_stream_response
    )

    response = await client.post(
        f"/chat/sessions/{session_id}/message",
        headers=auth_headers,
        json={
            "content": "Use the latest jobs",
            "scope": _scope(job_ids=[job_id, job_id]),
        },
    )
    assert response.status_code == 200
    assert _sse_events(response.text)[-1]["type"] == "done"

    detail_response = await client.get(f"/chat/sessions/{session_id}", headers=auth_headers)
    assert detail_response.status_code == 200
    assert detail_response.json()["scope"]["job_ids"] == [job_id]


@pytest.mark.asyncio
async def test_get_job_metrics_returns_tool_error_for_unreadable_nc(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    user_id: str,
    _test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services.benchmark_domain import get_job_metrics
    from ai_almanac.server.services.chat_state import ChatScope

    response = await client.get("/chat/sessions", headers=auth_headers)
    assert response.status_code == 200

    job_id = f"job-{uuid4()}"
    await _insert_job(_test_engine, user_id, job_id)

    class UnreadableStorage:
        def list_nc_output_files(self, job_id_arg: str) -> list[str]:
            assert job_id_arg == job_id
            return ["/outputs/spatial_metrics_model_1-15.nc"]

        def open_nc_dataset(self, path: str):
            assert path == "/outputs/spatial_metrics_model_1-15.nc"
            raise RuntimeError("NetCDF: HDF error")

    monkeypatch.setattr(
        "ai_almanac.server.services.storage.get_storage", lambda: UnreadableStorage()
    )

    payload = await get_job_metrics(
        job_id,
        user_id,
        ChatScope(
            kind="benchmark_run_group",
            key="group-1",
            title="Group 1",
            job_ids=[job_id],
        ),
    )

    assert payload["job_id"] == job_id
    assert "Could not read metric output" in payload["error"]
    assert "NetCDF: HDF error" in payload["error"]


@pytest.mark.asyncio
async def test_run_code_sandbox_preserves_figure_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services.chat_state import ChatArtifact, ChatScope
    from ai_almanac.server.services.chat_tools import (
        CodeSandboxRequest,
        run_code_sandbox,
    )

    artifact_bytes = b"fake-webp-bytes"

    class FakeModalFunction:
        def remote(self, code: str) -> dict:
            assert "compute" in code
            return {
                "ok": True,
                "result": {"summary": "created plot"},
                "artifacts": [
                    {
                        "kind": "figure",
                        "filename": "plot.webp",
                        "label": "Plot",
                        "media_type": "image/webp",
                        "data": artifact_bytes,
                    }
                ],
            }

    monkeypatch.setattr("ai_almanac.settings.settings.enable_run_code_sandbox", True)
    monkeypatch.setattr("ai_almanac.settings.settings.modal_token_id", "token-id")
    monkeypatch.setattr("ai_almanac.settings.settings.modal_token_secret", "token-secret")
    monkeypatch.setattr("modal.Function.from_name", lambda *args: FakeModalFunction())

    async def fake_create_chat_figure_artifact(
        session_id: str,
        user_id: str,
        data: bytes,
        *,
        label: str | None = None,
        filename: str | None = None,
        media_type: str | None = None,
    ) -> ChatArtifact:
        assert session_id == "session-1"
        assert user_id == "user-1"
        assert data == artifact_bytes
        return ChatArtifact(
            id="artifact-1",
            kind="figure",
            url="/chat/figures/artifact-1/public?exp=1&sig=test",
            label=label,
            filename=filename,
            media_type=media_type,
            created_at=datetime.now(UTC),
        )

    monkeypatch.setattr(
        "ai_almanac.server.services.chat_tools.create_chat_figure_artifact",
        fake_create_chat_figure_artifact,
    )

    result = await run_code_sandbox(
        CodeSandboxRequest(code="def compute() -> dict:\n    return {}"),
        "user-1",
        ChatScope(key="group-1"),
        "session-1",
    )

    assert result["ok"] is True
    assert result["result"] == {"summary": "created plot"}
    assert len(result["artifacts"]) == 1
    assert result["artifacts"][0]["id"] == "artifact-1"
    assert result["artifacts"][0]["label"] == "Plot"
    assert result["artifacts"][0]["filename"] == "plot.webp"


@pytest.mark.asyncio
async def test_a_ruleset_model_pin_does_not_override_a_users_own_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ruleset decides wording, never whose API key pays for the turn.

    Pinning a model is how a comparison runs two models under one policy. If the
    pin bypassed profile resolution, a shared-deployment user who chose "use my
    own key" would have their prompts silently sent through the host's provider.
    """
    from ai_almanac.server.services import llm, llm_profiles, rulesets
    from ai_almanac.server.services.chat_state import ChatScope

    resolved: list[str] = []

    async def fake_resolve(user_id: str):
        resolved.append(user_id)
        return llm_profiles.ResolvedLLMProfile(
            provider_type="openai-compatible",
            base_url="http://user-choice.local",
            model_name="user-picked-model",
            api_key="user-key",
        )

    monkeypatch.setattr("ai_almanac.settings.settings.deployment_mode", "shared")
    monkeypatch.setattr(llm_profiles, "resolve_llm_for_user", fake_resolve)

    built: dict = {}
    real_build_agent = llm._build_agent

    def capture(scope, ruleset, model=None):
        built["model"] = model
        return real_build_agent(scope, ruleset, TestModel(call_tools=[], custom_output_text="ok"))

    monkeypatch.setattr(llm, "_build_agent", capture)

    pinned = rulesets.packaged_ruleset("builtin").model_copy(update={"model": "pinned-model"})
    scope = ChatScope(kind="benchmark_run_group", key="group-1", title="G", job_ids=[])

    events = [
        json.loads(event)
        async for event in llm.stream_response(
            [], "user-1", "session-1", scope, latest_user_message="hi", active_ruleset=pinned
        )
    ]

    assert events[-1]["type"] == "done"
    # The user's profile was consulted despite the pin...
    assert resolved == ["user-1"]
    # ...and the pin only swapped the model name onto that resolved provider,
    # so the credentials in play are still the user's.
    model = built["model"]
    assert model.model_name == "pinned-model"
    assert "user-choice.local" in str(model.client.base_url)


# ---------------------------------------------------------------------------
# Per-session ruleset selection
# ---------------------------------------------------------------------------


async def _archive_ruleset(ruleset_id: str) -> None:
    from ai_almanac.server.db import get_db

    async with get_db() as conn:
        await conn.execute(
            text("UPDATE assistant_rulesets SET archived = TRUE WHERE id = :id"),
            {"id": ruleset_id},
        )


async def _seed_exposed_rulesets() -> None:
    """Seed the packaged rulesets and expose them to users, as an admin would."""
    from ai_almanac.server.services import rulesets

    await rulesets.seed_packaged_rulesets()
    await rulesets.set_comparison_enabled("builtin", True)
    await rulesets.set_comparison_enabled("unconstrained", True)


@pytest.mark.asyncio
async def test_a_streamed_turn_can_be_rated_by_its_transcript_id(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    _test_engine: AsyncEngine,
) -> None:
    """The id the UI rates is the transcript's, so the turn log must use it too.

    Regression test: the log once recorded the LLM layer's internal turn id,
    which made every rating a 404 against a row that was really there.
    """
    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model",
        lambda: TestModel(call_tools=[], custom_output_text="Rated answer."),
    )
    created = await _create_session(client, auth_headers)
    session_id = created["id"]

    response = await client.post(
        f"/chat/sessions/{session_id}/message", headers=auth_headers, json={"content": "hi"}
    )
    assert response.status_code == 200
    done = _sse_events(response.text)[-1]
    assert done["type"] == "done"
    turn_id = done["turn"]["id"]

    rating = await client.post(
        f"/chat/sessions/{session_id}/turns/{turn_id}/rating",
        headers=auth_headers,
        json={"value": 1},
    )
    assert rating.status_code == 204, rating.text

    async with _test_engine.begin() as conn:
        row = (
            (
                await conn.execute(
                    text(
                        "SELECT rating FROM assistant_turn_logs "
                        "WHERE session_id = :sid AND turn_id = :tid"
                    ),
                    {"sid": session_id, "tid": turn_id},
                )
            )
            .mappings()
            .fetchone()
        )
    assert row is not None and row["rating"] == 1


@pytest.mark.asyncio
async def test_a_session_carries_its_chosen_ruleset(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await _seed_exposed_rulesets()

    created = await _create_session(client, auth_headers, ruleset_id="unconstrained")
    session_id = created["id"]
    assert created["ruleset_id"] == "unconstrained"

    detail = await client.get(f"/chat/sessions/{session_id}", headers=auth_headers)
    assert detail.json()["ruleset_id"] == "unconstrained"

    # A rename does not disturb the ruleset choice.
    renamed = await client.patch(
        f"/chat/sessions/{session_id}", headers=auth_headers, json={"title": "Renamed"}
    )
    assert renamed.json()["title"] == "Renamed"
    assert renamed.json()["ruleset_id"] == "unconstrained"

    # Switching and clearing are both PATCHes on the same field.
    switched = await client.patch(
        f"/chat/sessions/{session_id}", headers=auth_headers, json={"ruleset_id": "builtin"}
    )
    assert switched.json()["ruleset_id"] == "builtin"
    cleared = await client.patch(
        f"/chat/sessions/{session_id}", headers=auth_headers, json={"ruleset_id": None}
    )
    assert cleared.json()["ruleset_id"] is None
    assert cleared.json()["title"] == "Renamed"


@pytest.mark.asyncio
async def test_an_unavailable_ruleset_cannot_be_selected(
    client: httpx.AsyncClient, auth_headers: dict[str, str]
) -> None:
    await _seed_exposed_rulesets()

    unknown = await client.post(
        "/chat/sessions",
        headers=auth_headers,
        json={"title": "S", "scope": _scope(), "ruleset_id": "no-such-ruleset"},
    )
    assert unknown.status_code == 400

    created = await _create_session(client, auth_headers)
    patched = await client.patch(
        f"/chat/sessions/{created['id']}",
        headers=auth_headers,
        json={"ruleset_id": "no-such-ruleset"},
    )
    assert patched.status_code == 400

    await _archive_ruleset("unconstrained")
    try:
        archived = await client.patch(
            f"/chat/sessions/{created['id']}",
            headers=auth_headers,
            json={"ruleset_id": "unconstrained"},
        )
        assert archived.status_code == 400
    finally:
        await _seed_exposed_rulesets()


@pytest.mark.asyncio
async def test_a_sessions_ruleset_governs_its_turns_until_it_disappears(
    client: httpx.AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_exposed_rulesets()

    created = await _create_session(client, auth_headers, ruleset_id="unconstrained")
    session_id = created["id"]

    seen_rulesets: list[object] = []

    async def fake_stream_response(
        provider_state: list,
        user_id: str,
        session_id_arg: str,
        scope: dict,
        *,
        latest_user_message: str | None = None,
        deferred_tool_results=None,
        active_ruleset=None,
        **_comparison: object,
    ) -> AsyncIterator[str]:
        seen_rulesets.append(active_ruleset)
        yield json.dumps(
            {
                "type": "done",
                "turn": {"content": "ok", "tool_calls": [], "artifacts": []},
                "provider_state": [],
            }
        )

    monkeypatch.setattr(
        "ai_almanac.server.services.chat_turns.stream_response", fake_stream_response
    )

    response = await client.post(
        f"/chat/sessions/{session_id}/message", headers=auth_headers, json={"content": "hi"}
    )
    assert response.status_code == 200
    assert response.text  # drain the stream

    # The pinned ruleset went missing: the turn degrades to the active one
    # (None lets the LLM layer resolve it) rather than failing the chat.
    await _archive_ruleset("unconstrained")
    try:
        response = await client.post(
            f"/chat/sessions/{session_id}/message", headers=auth_headers, json={"content": "hi"}
        )
        assert response.status_code == 200
        assert response.text
    finally:
        await _seed_exposed_rulesets()

    assert [getattr(r, "id", None) for r in seen_rulesets] == ["unconstrained", None]
