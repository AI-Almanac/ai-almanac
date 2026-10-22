"""A blend whose day-level blend trains in its own Modal call completes with its
week-level blend; the server then follows the day-level call to its end, stops
it with a blend that did not complete, and indexes its outputs when it lands."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

from ai_almanac.server.services import day_level_blends
from ai_almanac.server.services.day_level_blends import (
    CANCELED,
    COMPLETE,
    FAILED,
    NEVER_STARTED,
    RUNNING,
    STOPPED_WITH_JOB,
)

_BLEND_CONFIG = {"job_type": "blend", "train_day_level_blend": True}
_WEEKLY_CALL, _DAY_LEVEL_CALL = "fc-weekly", "fc-day-level"


class _Runner:
    """Modal calls by id, each in its own state; records cancellations."""

    def __init__(self, **statuses: str) -> None:
        self.statuses = statuses
        self.canceled: list[str] = []

    async def inspect(self, handle):
        from ai_almanac.server.services.execution import ExecutionSnapshot

        return ExecutionSnapshot(status=self.statuses[handle.external_id])

    async def cancel(self, handle) -> None:
        self.canceled.append(handle.external_id)


@pytest.fixture
def runner(monkeypatch: pytest.MonkeyPatch):
    from ai_almanac.server.services import job_manager, modal_runner

    async def no_launch(_job_id: str) -> None:
        pass

    monkeypatch.setattr(job_manager, "launch_job", no_launch)
    fake = _Runner()
    monkeypatch.setattr(modal_runner, "get_modal_runner", lambda: fake)
    return fake


async def _blend_job(user_id: str, status: str = "running", config: dict = _BLEND_CONFIG) -> str:
    from ai_almanac.server.db import get_db
    from ai_almanac.server.tables import jobs

    job_id = f"blend-{uuid.uuid4()}"
    async with get_db() as conn:
        await conn.execute(
            sa.insert(jobs).values(
                id=job_id,
                user_id=user_id,
                dataset_id="source",
                job_type="blend",
                status=status,
                config_json=json.dumps(config),
                created_at="2026-10-22T00:00:00+00:00",
                runner="modal",
                runner_handle={"runner": "modal", "external_id": _WEEKLY_CALL, "metadata": {}},
            )
        )
    return job_id


def _job_dir(job_id: str) -> Path:
    from ai_almanac.server.services.storage import get_storage

    return Path(get_storage().job_output_uri(job_id)[0]).parent


def _write_marker(job_id: str, call_id: str = _DAY_LEVEL_CALL) -> None:
    marker = _job_dir(job_id) / day_level_blends.DAY_LEVEL_CALL_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"call_id": call_id}))


async def _row(job_id: str) -> dict:
    from ai_almanac.server.db import get_db
    from ai_almanac.server.tables import jobs

    async with get_db() as conn:
        return dict(
            (await conn.execute(sa.select(jobs).where(jobs.c.id == job_id))).mappings().one()
        )


@pytest.mark.parametrize(
    ("text", "call_id"),
    [
        ('{"call_id": "fc-1"}', "fc-1"),
        ("", None),
        ("not json", None),
        ('{"call_id": ""}', None),
        ('["fc-1"]', None),
    ],
)
def test_only_a_well_formed_marker_names_a_call(text: str, call_id: str | None) -> None:
    assert day_level_blends.parse_call_marker(text) == call_id


@pytest.mark.asyncio
async def test_a_completed_blend_hands_its_running_day_level_blend_on(user_id, runner) -> None:
    from ai_almanac.server.services import job_manager

    job_id = await _blend_job(user_id)
    _write_marker(job_id)
    runner.statuses = {_WEEKLY_CALL: "complete", _DAY_LEVEL_CALL: "running"}

    await job_manager.reconcile_jobs()
    await day_level_blends.reconcile_day_level_blends()

    row = await _row(job_id)
    assert row["status"] == "complete"
    assert (row["day_level_status"], row["day_level_call_id"]) == (RUNNING, _DAY_LEVEL_CALL)
    assert row["day_level_completed_at"] is None


@pytest.mark.parametrize(
    ("outcome", "status", "error_part"),
    [("complete", COMPLETE, None), ("failed", FAILED, "forest exploded")],
)
@pytest.mark.asyncio
async def test_the_day_level_blend_is_followed_to_its_end(
    user_id, runner, outcome: str, status: str, error_part: str | None
) -> None:
    from ai_almanac.server.services import job_manager

    job_id = await _blend_job(user_id)
    _write_marker(job_id)
    (_job_dir(job_id) / day_level_blends.DAY_LEVEL_RUN_LOG).write_text(
        "==> Day-level CV fold 1/3\nValueError: forest exploded\n"
    )
    runner.statuses = {_WEEKLY_CALL: "complete", _DAY_LEVEL_CALL: "running"}
    await job_manager.reconcile_jobs()

    runner.statuses[_DAY_LEVEL_CALL] = outcome
    await day_level_blends.reconcile_day_level_blends()

    row = await _row(job_id)
    assert row["day_level_status"] == status
    assert row["day_level_completed_at"] is not None
    if error_part is None:
        assert row["day_level_error"] is None
    else:
        assert error_part in row["day_level_error"]


@pytest.mark.parametrize(
    ("job_status", "weekly", "final_status"),
    [("running", "failed", "failed"), ("canceling", "running", "canceled")],
)
@pytest.mark.asyncio
async def test_a_blend_that_does_not_complete_stops_its_day_level_blend(
    user_id, runner, job_status: str, weekly: str, final_status: str
) -> None:
    from ai_almanac.server.services import job_manager

    job_id = await _blend_job(user_id, job_status)
    _write_marker(job_id)
    runner.statuses = {_WEEKLY_CALL: weekly, _DAY_LEVEL_CALL: "running"}

    await job_manager.reconcile_jobs()

    row = await _row(job_id)
    assert row["status"] == final_status
    assert _DAY_LEVEL_CALL in runner.canceled
    assert (row["day_level_status"], row["day_level_error"]) == (CANCELED, STOPPED_WITH_JOB)


@pytest.mark.asyncio
async def test_a_completed_blend_whose_day_level_blend_never_started_says_so(
    user_id, runner
) -> None:
    from ai_almanac.server.services import job_manager

    job_id = await _blend_job(user_id)
    runner.statuses = {_WEEKLY_CALL: "complete"}

    await job_manager.reconcile_jobs()

    row = await _row(job_id)
    assert (row["day_level_status"], row["day_level_error"]) == (FAILED, NEVER_STARTED)


@pytest.mark.parametrize(
    "config", [{"job_type": "blend"}, {"job_type": "forecast", "train_day_level_blend": True}]
)
@pytest.mark.asyncio
async def test_jobs_without_a_separate_day_level_blend_are_left_alone(
    user_id, runner, config: dict
) -> None:
    from ai_almanac.server.services import job_manager

    job_id = await _blend_job(user_id, config=config)
    _write_marker(job_id)
    runner.statuses = {_WEEKLY_CALL: "complete"}

    await job_manager.reconcile_jobs()

    row = await _row(job_id)
    assert row["day_level_status"] is None
    assert runner.canceled == []


@pytest.mark.asyncio
async def test_day_level_outputs_are_indexed_once_the_day_level_blend_finishes(
    user_id, runner
) -> None:
    from ai_almanac.server.db import get_db
    from ai_almanac.server.services.artifacts import list_job_artifacts, publish_pending
    from ai_almanac.server.tables import jobs

    job_id = await _blend_job(user_id, "complete")
    output = _job_dir(job_id) / "output"
    (output / "summary_models_pooled_x.csv").write_text("model\nblended_model\n")
    await publish_pending()
    (output / "day_level_summary_models_x.csv").write_text("id,model\nALL,blended_forest\n")

    await publish_pending()  # still training: nothing new is indexed
    names_while_training = {a["filename"] for a in await list_job_artifacts(job_id)}
    async with get_db() as conn:
        await conn.execute(
            sa.update(jobs).where(jobs.c.id == job_id).values(day_level_status=COMPLETE)
        )
    await publish_pending()

    names = [a["filename"] for a in await list_job_artifacts(job_id)]
    assert names_while_training == {"summary_models_pooled_x.csv"}
    assert sorted(names) == ["day_level_summary_models_x.csv", "summary_models_pooled_x.csv"]
    assert (await _row(job_id))["day_level_published_at"] is not None
