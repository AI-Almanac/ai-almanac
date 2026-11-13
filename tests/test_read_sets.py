"""Read sets (D-R8): tools name the artifacts they read, and turns record them."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_almanac.server.services import benchmark_domain, workspaces
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
