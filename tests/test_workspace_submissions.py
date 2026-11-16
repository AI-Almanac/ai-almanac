"""Typed, stateless benchmark and blend submissions in a workspace (D-R7).

A workspace session submits a whole spec per call instead of editing a session
draft: the user approves exactly that spec, an invalid one submits nothing, and
every job it creates is linked into the workspace with the assistant's
attribution and the approval on its provenance step.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from pydantic_ai import ApprovalRequired

from ai_almanac.server import tables
from ai_almanac.server.db import get_db
from ai_almanac.server.services import (
    boundaries,
    job_submission,
    ledger,
    provenance,
    workspaces,
)
from ai_almanac.server.services.focus_area import FocusUnits
from ai_almanac.server.services.primitives import registry, runs
from ai_almanac.settings import settings
from tests.helpers import ledger_reads
from tests.helpers.records import insert_job
from tests.helpers.specs import india_blend_spec
from tests.helpers.tool_calls import approve, tool_ctx
from tests.helpers.workspaces import workspace_with_session


async def _job_count(user_id: str) -> int:
    async with get_db() as conn:
        return (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(tables.jobs)
                .where(tables.jobs.c.user_id == user_id)
            )
        ).scalar_one()


async def _linked(workspace_id: str) -> list[str]:
    async with get_db() as conn:
        result = await conn.execute(workspaces.linked_job_ids_query(sa.literal(workspace_id)))
        return [row[0] for row in result]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (
            runs.submit_benchmark,
            {"spec": runs.BenchmarkSpec(dataset_id="d", model_ids=["m"])},
        ),
        (runs.submit_blend, None),
        (runs.rerun_job, {"job_id": "j", "overrides": None}),
    ],
)
async def test_a_submission_waits_for_approval_and_submits_nothing(
    client, as_new_user, user_id, tool, arguments
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    arguments = arguments or {"spec": await india_blend_spec("2020,2021")}
    before = await _job_count(user_id)

    with pytest.raises(ApprovalRequired) as raised:
        await tool(tool_ctx(user_id, session["id"], workspace["id"], approved=False), **arguments)

    shown = raised.value.metadata["params"]
    assert shown == registry.json_params(arguments)
    assert "spec" not in arguments or shown["spec"]["model_ids"] == arguments["spec"].model_ids
    assert await _job_count(user_id) == before


@pytest.mark.asyncio
async def test_an_approved_blend_is_linked_and_its_step_carries_who_proposed_and_approved_it(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    spec = await india_blend_spec("2020,2021")
    with pytest.raises(ApprovalRequired) as raised:
        await runs.submit_blend(
            tool_ctx(user_id, session["id"], workspace["id"], approved=False), spec=spec
        )
    shown = raised.value.metadata["params"]

    result = await approve(
        runs.submit_blend,
        tool_ctx(user_id, session["id"], workspace["id"], approved=False),
        spec=spec,
    )

    assert "error" not in result, result
    [job] = result["jobs"]
    assert job["id"] in await _linked(workspace["id"])
    step = await ledger.job_step(job["id"])
    assert (step["actor_kind"], step["session_id"], step["turn_id"], step["model_name"]) == (
        "assistant",
        session["id"],
        "t1",
        "test-model",
    )
    approval = await ledger_reads.get_approval(step["approval_id"])
    assert approval["payload_hash"] == provenance.params_hash(shown)


@pytest.mark.asyncio
async def test_an_invalid_spec_returns_its_errors_and_creates_nothing(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    ctx = tool_ctx(user_id, session["id"], workspace["id"], approved=False)
    before = await _job_count(user_id)

    leaky = await approve(runs.submit_blend, ctx, spec=await india_blend_spec("2018,2019"))
    unknown = await approve(
        runs.submit_benchmark,
        ctx,
        spec=runs.BenchmarkSpec(
            dataset_id="no-such-dataset", model_ids=["no-such-model"], region_id="atlantis"
        ),
    )

    assert leaky["error"] and not leaky["blend_validation"]["can_run"]
    assert any("holdout" in error.lower() for error in leaky["blend_validation"]["errors"])
    errors = " ".join(unknown["benchmark_validation"]["errors"])
    assert "atlantis" in errors and "no-such-dataset" in errors
    assert "jobs" not in leaky and "jobs" not in unknown
    assert await _job_count(user_id) == before
    assert await _linked(workspace["id"]) == []


@pytest.mark.asyncio
async def test_rerun_refuses_a_job_this_workspace_does_not_hold(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = insert_job(user_id, {"romp_params": {}})
    before = await _job_count(user_id)

    result = await approve(
        runs.rerun_job,
        tool_ctx(user_id, session["id"], workspace["id"], approved=False),
        job_id=job_id,
        overrides=None,
    )

    assert "not found" in result["error"]
    assert await _job_count(user_id) == before


async def _job_config(job_id: str) -> dict:
    async with get_db() as conn:
        raw = (
            await conn.execute(
                sa.select(tables.jobs.c.config_json).where(tables.jobs.c.id == job_id)
            )
        ).scalar_one()
    return json.loads(raw)


def _square_unit(name: str) -> dict:
    ring = [[76.0, 8.0], [77.0, 8.0], [77.0, 9.0], [76.0, 9.0], [76.0, 8.0]]
    return {
        "type": "Feature",
        "properties": {"shapeName": name},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


@pytest.mark.asyncio
async def test_a_blend_spec_carries_its_area_and_onset_definition_into_the_job(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def kerala_states(iso: str, level: str) -> dict:
        return {"geojson": {"type": "FeatureCollection", "features": [_square_unit("Kerala")]}}

    monkeypatch.setattr(boundaries, "load_boundary", kerala_states)
    workspace, session = await workspace_with_session(client, as_new_user)
    spec = (await india_blend_spec("2020,2021")).model_copy(
        update={
            "focus_area": FocusUnits(level="adm1", units=["Kerala"]),
            "threshold_mm": 25.0,
            "cutoff_month_day": "05-15",
        }
    )

    result = await approve(
        runs.submit_blend,
        tool_ctx(user_id, session["id"], workspace["id"], approved=False),
        spec=spec,
    )

    [job] = result["jobs"]
    params = (await _job_config(job["id"]))["blend_params"]
    assert params["focus_area"]["units"] == ["Kerala"]
    assert [f["properties"]["name"] for f in params["focus_area"]["geometry"]["features"]] == [
        "Kerala"
    ]
    assert (params["threshold_mm"], params["cutoff_month_day"]) == (25.0, "05-15")


@pytest.mark.asyncio
async def test_validating_a_spec_names_its_data_and_submits_nothing(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    before = await _job_count(user_id)

    result = await runs.validate_blend(
        tool_ctx(user_id, session["id"], workspace["id"]), spec=await india_blend_spec("2020,2021")
    )

    assert result["blend_validation"]["can_run"], result
    shown = result["blend_spec"]
    assert shown["obs_dataset_name"].startswith("obs-")
    assert [name[:2] for name in shown["model_names"]] == ["m0", "m1"]
    assert await _job_count(user_id) == before


@pytest.mark.asyncio
async def test_asking_for_day_level_probabilities_where_they_are_off_is_refused(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "enable_day_level_blend", False)
    workspace, session = await workspace_with_session(client, as_new_user)
    spec = (await india_blend_spec("2020,2021")).model_copy(update={"train_day_level_blend": True})

    result = await runs.validate_blend(
        tool_ctx(user_id, session["id"], workspace["id"], approved=False), spec=spec
    )

    assert not result["blend_validation"]["can_run"]
    assert any("Day-level" in error for error in result["blend_validation"]["errors"])


@pytest.mark.parametrize(
    ("enabled", "requested", "trains"),
    [(True, None, True), (True, False, False), (False, None, False)],
)
def test_a_blend_trains_day_level_probabilities_only_where_the_deployment_allows(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, requested: bool | None, trains: bool
) -> None:
    monkeypatch.setattr(settings, "enable_day_level_blend", enabled)

    assert job_submission.trains_day_level_blend(requested) is trains
