"""Read sets (D-R8): tools name the artifacts they read, and turns record them."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from ai_almanac.paths import database_path
from ai_almanac.server.services import benchmark_domain, chat_turns, workspaces
from ai_almanac.server.services.chat_state import ChatScope
from ai_almanac.server.services.primitives import inspection
from tests.helpers.files import write_nc
from tests.helpers.records import BENCHMARK_CONFIG, insert_job
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import register_file, workspace_with_session, write_job_outputs


@pytest.mark.asyncio
async def test_inspecting_an_artifact_names_it_as_the_source(
    client, as_new_user, user_id, tmp_path: Path
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    await register_file(user_id, workspace["id"], write_nc(tmp_path / "obs.nc"), "obs")

    result = await inspection.inspect_artifact(
        tool_ctx(user_id, session["id"], workspace["id"]), artifact="obs"
    )

    assert result["source_artifacts"] == ["obs"]


@pytest.mark.asyncio
async def test_a_job_result_names_only_the_job_files_it_read(client, as_new_user, user_id) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    job_id = insert_job(user_id, BENCHMARK_CONFIG)
    write_job_outputs(job_id, 1.0)
    await workspaces.link_job(workspace["id"], job_id)
    scope = ChatScope(kind="workspace", key=workspace["id"], job_ids=[])

    result = await benchmark_domain.get_spatial_summary(
        benchmark_domain.SpatialMetricRequest(
            job_id=job_id, model="AIFS", window="1-15", metric="far"
        ),
        user_id,
        scope,
    )

    assert result.get("error") is None, result
    assert result["source_artifacts"] == ["spatial_metrics_aifs_1-15"]


@pytest.mark.asyncio
async def test_a_turn_records_the_artifacts_its_tools_read(
    client, as_new_user, user_id, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

    workspace, session = await workspace_with_session(client, as_new_user)
    obs = await register_file(user_id, workspace["id"], write_nc(tmp_path / "obs.nc"), "obs")

    async def stream(messages: list, info: AgentInfo) -> AsyncIterator[str | dict]:
        if len(messages) == 1:
            yield {
                0: DeltaToolCall(
                    name="inspect_artifact", json_args='{"artifact": "obs"}', tool_call_id="c1"
                )
            }
        else:
            yield "It is a 3 by 4 grid."

    monkeypatch.setattr(
        "ai_almanac.server.services.llm._build_model", lambda: FunctionModel(stream_function=stream)
    )

    async for _ in chat_turns.stream_chat_turn(session["id"], user_id, "what is obs?", None):
        pass

    with sqlite3.connect(database_path()) as conn:
        (raw,) = conn.execute(
            "SELECT transcript FROM chat_sessions WHERE id = ?", (session["id"],)
        ).fetchone()
        (read_ids,) = conn.execute(
            "SELECT read_artifact_ids FROM assistant_turn_logs WHERE session_id = ?",
            (session["id"],),
        ).fetchone()
    prompt, answer = json.loads(raw)
    assert prompt["trigger"] == "user"
    assert answer["source_artifacts"] == [{"id": obs["id"], "label": "obs"}]
    assert json.loads(read_ids) == [obs["id"]]
