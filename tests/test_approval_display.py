"""Approval cards read in the user's words (forecaster workflow F4).

An approval carries a ``display`` of facts beside the ``params`` it hashes: names
for ids, year ranges and settings in plain language. The display never changes
what the user approves.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic_ai import ApprovalRequired

from ai_almanac.server.services import (
    approval_display,
    chat_tools,
    proposed_run_facts,
)
from ai_almanac.server.services.primitives import registry, runs
from ai_almanac.server.services.seasons import SeasonWindow
from ai_almanac.settings import settings
from tests.helpers.records import (
    BENCHMARK_CONFIG,
    benchmark_config,
    insert_job,
    ran_for,
    seed_source,
)
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import workspace_with_session


def _facts(raised: pytest.ExceptionInfo[ApprovalRequired]) -> dict[str, str]:
    return {fact["label"]: fact["value"] for fact in raised.value.metadata["display"]["facts"]}


async def _named_source(kind: str, name: str) -> str:
    return await seed_source(
        kind, f"{name}-{uuid.uuid4().hex[:6]}", region="ethiopia", years=(1981, 2024)
    )


@pytest.mark.asyncio
async def test_a_blend_approval_names_its_data_and_shows_exactly_the_params(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    obs = await _named_source("obs", "CHIRPS")
    models = [await _named_source("model", "AIFS"), await _named_source("model", "FuXi")]
    spec = runs.BlendSpec(
        name="Kiremt blend",
        obs_dataset_id=obs,
        model_ids=models,
        training_years="2000:2019",
        cv_holdout_years="2000:2019",
        true_holdout_years="2020,2021",
    )

    with pytest.raises(ApprovalRequired) as raised:
        await runs.submit_blend(
            tool_ctx(user_id, session["id"], workspace["id"], approved=False), spec=spec
        )

    facts = _facts(raised)
    assert facts["Observations"].startswith("CHIRPS-") and "(1981–2024)" in facts["Observations"]
    assert [name.split("-")[0] for name in facts["Models"].split(", ")] == ["AIFS", "FuXi"]
    assert facts["Training years"] == "2000–2019"
    assert not {obs, *models} & set(" ".join(facts.values()).replace(",", " ").split())
    assert raised.value.metadata["params"] == registry.json_params({"spec": spec})


@pytest.mark.asyncio
async def test_a_rerun_approval_names_the_job_and_lists_only_what_changes(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    job_id = insert_job(user_id, BENCHMARK_CONFIG)

    with pytest.raises(ApprovalRequired) as raised:
        await runs.rerun_job(
            tool_ctx(user_id, session["id"], workspace["id"], approved=False),
            job_id=job_id,
            overrides=runs.RerunOverrides(wet_threshold=25.0, onset_rule="two_stage"),
        )

    assert _facts(raised) == {
        "Job": "AIFS · Ethiopia · 1998–2024 · days 1–30",
        "Wet threshold": "25.0",
        "Onset definition": "Two-stage",
        "Runs on": "This computer",
    }


@pytest.mark.asyncio
async def test_a_benchmark_approval_says_its_years_its_onset_definition_and_where_it_runs(
    client, as_new_user, user_id
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    obs = await _named_source("obs", "CHIRPS")
    models = [await _named_source("model", "AIFS"), await _named_source("model", "FuXi")]
    spec = runs.BenchmarkSpec(dataset_id=obs, model_ids=models)

    with pytest.raises(ApprovalRequired) as raised:
        await runs.submit_benchmark(
            tool_ctx(user_id, session["id"], workspace["id"], approved=False), spec=spec
        )

    facts = _facts(raised)
    assert facts["Evaluation years"] == "1981–2024"
    assert facts["Onset definition"] == "Standard wet spell"
    assert facts["Runs on"] == "This computer"
    assert "Expected time" not in facts
    assert not {obs, *models} & set(" ".join(facts.values()).replace(",", " ").split())


@pytest.mark.asyncio
async def test_a_primitive_without_a_describer_still_names_the_ids_it_can_resolve(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry, "REGISTRY", dict(registry.REGISTRY))

    @registry.primitive("test.undescribed", "1.0.0", needs_approval=True)
    async def undescribed(ctx, dataset_id: str, other_id: str, wet_threshold: float) -> dict:
        return {}

    workspace, session = await workspace_with_session(client, as_new_user)
    obs = await _named_source("obs", "CHIRPS")

    with pytest.raises(ApprovalRequired) as raised:
        await undescribed(
            tool_ctx(user_id, session["id"], workspace["id"], approved=False),
            dataset_id=obs,
            other_id="not-a-known-id",
            wet_threshold=20.0,
        )

    facts = _facts(raised)
    assert facts["Observations"].startswith("CHIRPS-")
    assert facts["Other"] == "not-a-known-id"
    assert facts["Wet threshold"] == "20.0"


@pytest.mark.asyncio
async def test_a_describer_that_fails_still_lets_the_user_approve(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(registry, "REGISTRY", dict(registry.REGISTRY))

    async def broken(ctx, **arguments):
        raise RuntimeError("lookup failed")

    @registry.primitive("test.broken", "1.0.0", needs_approval=True, describe=broken)
    async def broken_step(ctx, season_year: int) -> dict:
        return {}

    workspace, session = await workspace_with_session(client, as_new_user)

    with pytest.raises(ApprovalRequired) as raised:
        await broken_step(
            tool_ctx(user_id, session["id"], workspace["id"], approved=False), season_year=2026
        )

    assert _facts(raised) == {"Season year": "2026"}
    assert raised.value.metadata["params"] == {"season_year": 2026}


def test_a_forecast_from_models_reads_their_names_and_the_season() -> None:
    sources = {"m1": {"name": "AIFS"}, "m2": {"name": "FuXi"}}

    facts = approval_display.forecast_facts(
        {"models": ["m1", "m2"], "season": SeasonWindow(name="Kiremt"), "season_year": 2026},
        None,
        sources,
    )

    assert [(fact.label, fact.value) for fact in facts] == [
        ("Models", "AIFS, FuXi"),
        ("Season", "Kiremt 2026"),
    ]


def test_a_forecast_from_a_blend_without_a_season_says_this_season() -> None:
    blend = {"blend_name": "Kiremt blend", "region_id": "ethiopia", "model_names": ["AIFS"]}

    facts = approval_display.forecast_facts({"blend_id": "b1"}, blend, {})

    assert {fact.label: fact.value for fact in facts} == {
        "Blend": "Kiremt blend · Ethiopia",
        "Models": "Every model in the blend (AIFS)",
        "Season": "This season",
    }


# --- what a card says about a run before it is submitted ---------------------------


def test_an_onset_definition_reads_by_its_display_name_and_unset_is_the_default() -> None:
    assert approval_display.onset_definition_label(None) == "Standard wet spell"
    assert approval_display.onset_definition_label("moron_robertson") == "Moron–Robertson"
    assert approval_display.onset_definition_label("some_new_rule") == "Some new rule"


def test_evaluation_years_are_one_span_when_models_agree_and_per_model_when_not() -> None:
    aifs = approval_display.evaluation_span({"start_year": 2000, "end_year": 2023})
    narrowed = approval_display.evaluation_span(
        {"start_year": 2000, "end_year": 2023},
        chat_tools.PerModelRompParams(start_date="2004-05-01"),
    )

    assert approval_display.evaluation_years_label({"AIFS": aifs, "FuXi": aifs}) == "2000–2023"
    assert (
        approval_display.evaluation_years_label({"AIFS": aifs, "FuXi": narrowed})
        == "AIFS 2000–2023; FuXi 2004–2023"
    )
    assert approval_display.evaluation_years_label({"AIFS": None}) is None


@pytest.mark.parametrize(
    ("seconds", "spoken"),
    [
        (20, "Under a minute"),
        (70, "About a minute"),
        (170, "About 3 minutes"),
        (80 * 60, "About 80 minutes"),
        (2 * 3600 + 600, "About 2 hours"),
    ],
)
def test_a_duration_reads_the_way_a_person_would_say_it(seconds: int, spoken: str) -> None:
    assert approval_display.duration_label(seconds) == spoken


def test_several_jobs_read_as_the_longest_per_model_and_a_partial_estimate_says_nothing() -> None:
    assert approval_display.expected_time_label([180]) == "About 3 minutes"
    assert approval_display.expected_time_label([180, 600]) == "About 10 minutes", (
        "a benchmark's jobs run at the same time, so it takes as long as the longest"
    )
    assert approval_display.expected_time_label([None], [480]) == "Up to about 8 minutes"
    assert approval_display.expected_time_label([120, None], [120, 480]) == (
        "Up to about 8 minutes"
    )
    assert approval_display.expected_time_label([None], [20]) == "Under a minute"
    assert approval_display.expected_time_label([None, None], [480, None]) is None
    assert approval_display.expected_time_label([180, None]) is None
    assert approval_display.expected_time_label([]) is None


def _shown(facts: list[approval_display.Fact]) -> dict[str, str]:
    return {fact.label: fact.value for fact in facts}


@pytest.mark.asyncio
async def test_a_proposed_benchmark_says_where_it_runs_and_how_long_runs_like_it_took(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    for minutes in (10, 30):
        insert_job(user_id, benchmark_config(), **ran_for(minutes))
    proposed = proposed_run_facts.benchmark_jobs("ethiopia", [(1998, 2024)])

    assert _shown(await proposed_run_facts.execution_facts(user_id, proposed)) == {
        "Runs on": "This computer",
        "Expected time": "About 20 minutes",
    }
    monkeypatch.setattr(settings, "job_runner", "modal")
    assert _shown(await proposed_run_facts.execution_facts(user_id, proposed))["Runs on"] == (
        "The cloud"
    )


@pytest.mark.asyncio
async def test_a_rerun_is_expected_to_take_as_long_as_the_run_it_repeats(
    client, as_new_user, user_id
) -> None:
    insert_job(user_id, benchmark_config(), **ran_for(45))

    facts = await proposed_run_facts.execution_facts(
        user_id, proposed_run_facts.rerun_jobs(benchmark_config())
    )

    assert _shown(facts)["Expected time"] == "About 45 minutes"
    assert proposed_run_facts.rerun_jobs(None) == []
