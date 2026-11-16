"""What a workspace says about each run: its area, its onset definition, and how long a
run like it is expected to take."""

from __future__ import annotations

import uuid

import pytest

from ai_almanac.server.services import chat_tools, onset_rules, run_summaries, workspaces
from tests.helpers.records import benchmark_config, insert_job, ran_for
from tests.helpers.workspaces import workspace_with_session

AMHARA_ZONES = ["East Gojjam", "North Gondar"]


# --- area of interest ------------------------------------------------------------


def test_a_run_limited_to_named_areas_lists_them_and_a_whole_region_run_lists_none() -> None:
    zones = benchmark_config(focus_area={"level": "adm2", "units": AMHARA_ZONES})
    box = benchmark_config(focus_area={"lat_min": 9, "lat_max": 14, "lon_min": 36, "lon_max": 40})

    assert run_summaries.area_of_interest(zones) == AMHARA_ZONES
    assert run_summaries.area_of_interest(box) == ["9°N–14°N, 36°E–40°E"]
    assert run_summaries.area_of_interest(benchmark_config()) is None


# --- onset definition ------------------------------------------------------------


def test_an_unset_onset_definition_is_the_default_by_its_display_name() -> None:
    definition = run_summaries.onset_definition(benchmark_config())

    assert definition is not None
    assert (definition.name, definition.is_default, definition.changes) == (
        "Standard wet spell",
        True,
        [],
    )


def test_only_the_settings_that_differ_from_the_definitions_defaults_are_listed() -> None:
    two_stage = run_summaries.onset_definition(
        benchmark_config(
            onset_rule="two_stage", onset_rule_params={"stage1_mm": 15.0, "stage2_days": 20}
        )
    )
    longer_spell = run_summaries.onset_definition(benchmark_config(wet_spell=5))

    assert two_stage is not None and longer_spell is not None
    assert (two_stage.name, two_stage.is_default) == ("Two-stage", False)
    assert [change.model_dump() for change in two_stage.changes] == [
        {
            "label": "First stage rain",
            "short_label": "first stage rain",
            "value": 15.0,
            "unit": "mm",
        }
    ]
    assert (longer_spell.name, longer_spell.is_default) == ("Standard wet spell", False)
    assert [change.model_dump() for change in longer_spell.changes] == [
        {"label": "Wet spell length", "short_label": "wet spell", "value": 5, "unit": "days"}
    ]


def test_a_changed_setting_has_a_short_label_for_a_runs_name() -> None:
    changed = run_summaries.onset_definition(
        benchmark_config(onset_rule="moron_robertson", onset_rule_params={"threshold": 25.0})
    )

    assert changed is not None
    [threshold] = changed.changes
    assert (threshold.label, threshold.short_label) == ("Wet spell rain total", "onset threshold")
    for rule in onset_rules.onset_rules().values():
        for spec in rule["params"]:
            assert 0 < len(spec["short_label"]) <= len(spec["label"]), spec["name"]
            assert "_" not in spec["short_label"]


def test_a_job_that_scores_against_no_onset_definition_has_none() -> None:
    assert run_summaries.onset_definition({"forecast_models": ["fuxi"]}) is None


def test_every_onset_definition_has_a_name_a_description_and_labelled_settings() -> None:
    names = onset_rules._packaged_json("onset_rule_names.json")

    for key, rule in onset_rules.onset_rules().items():
        assert names[key]["display_name"] and names[key]["description"]
        assert set(names[key]["params"]) == {spec["name"] for spec in rule["params"]}
        assert rule["display_name"] != key and "_" not in rule["display_name"]


def test_the_assistant_is_given_display_names_for_onset_definitions_and_plans() -> None:
    listed = chat_tools.list_onset_rules()
    plan = chat_tools.with_onset_definition_names(
        {"spec": {"advanced_params": {"onset_rule": "moron_robertson"}}, "models": [{"id": "a"}]}
    )
    default_plan = chat_tools.with_onset_definition_names({"advanced_params": {"onset_rule": None}})

    assert {
        rule["internal_value"]: rule["display_name"] for rule in listed["onset_definitions"]
    } == {
        "legacy": "Standard wet spell",
        "moron_robertson": "Moron–Robertson",
        "two_stage": "Two-stage",
    }
    assert listed["default_display_name"] == "Standard wet spell"
    assert plan["spec"]["advanced_params"]["onset_definition"] == "Moron–Robertson"
    assert plan["models"] == [{"id": "a"}]
    assert default_plan["advanced_params"]["onset_definition"] == "Standard wet spell"


# --- expected duration -----------------------------------------------------------


def _job(config: dict, status: str = "running", minutes: int | None = None, **fields) -> dict:
    return {
        "job_type": "benchmark",
        "status": status,
        "config": config,
        "created_at": "2026-06-01T00:00:00",
        "started_at": "2026-06-01T01:00:00",
        "completed_at": None if minutes is None else f"2026-06-01T01:{minutes:02d}:00",
        **fields,
    }


def _finished(config: dict, minutes: int, **fields) -> dict:
    return _job(config, "complete", minutes, **fields)


@pytest.mark.parametrize(
    ("history", "expected_minutes"),
    [
        ([_finished(benchmark_config(), minutes) for minutes in (10, 40, 20)], 20),
        ([_finished(benchmark_config(years=(2004, 2024)), 12)], 12),
    ],
    ids=["the median of runs its size", "a similar year span"],
)
def test_a_waiting_run_expects_the_time_past_runs_like_it_took(
    history: list[dict], expected_minutes: int
) -> None:
    for status in ("running", "queued"):
        expected = run_summaries.expected_seconds(_job(benchmark_config(), status), history)
        assert expected == expected_minutes * 60
    assert run_summaries.expected_seconds(_finished(benchmark_config(), 5), history) is None


@pytest.mark.parametrize(
    "past",
    [
        _finished(benchmark_config(years=(2020, 2024)), 10),
        _finished({**benchmark_config(), "region_id": "india"}, 10),
        _finished(benchmark_config(), 10, job_type="blend"),
        _finished({**benchmark_config(), "model_names": ["a", "b"]}, 10),
        _job(benchmark_config(), "failed", 10),
    ],
    ids=["far fewer years", "another region", "another kind", "more models", "did not finish"],
)
def test_a_run_of_another_shape_is_no_guide(past: dict) -> None:
    assert run_summaries.expected_seconds(_job(benchmark_config()), [past]) is None


_AMHARA = {"level": "adm2", "units": AMHARA_ZONES}


def test_a_whole_region_run_is_no_guide_to_one_limited_to_a_few_zones() -> None:
    whole_region = [_finished(benchmark_config(), minutes) for minutes in (7, 8, 8)]
    zones = benchmark_config(focus_area=_AMHARA)

    assert run_summaries.expected_seconds(_job(zones), whole_region) is None
    assert run_summaries.expected_seconds(_job(benchmark_config()), [_finished(zones, 2)]) is None


def test_a_run_over_the_same_zones_is_a_guide_however_they_were_listed() -> None:
    history = [
        _finished(benchmark_config(), 8),
        _finished(
            benchmark_config(
                focus_area={
                    "level": "adm2",
                    "units": list(reversed(AMHARA_ZONES)),
                    "geometry": {"type": "FeatureCollection", "features": []},
                }
            ),
            2,
        ),
    ]

    assert (
        run_summaries.expected_seconds(_job(benchmark_config(focus_area=_AMHARA)), history)
        == 2 * 60
    )


@pytest.mark.parametrize(
    "other_area",
    [
        {"level": "adm2", "units": ["East Gojjam"]},
        {"level": "adm1", "units": AMHARA_ZONES},
        {"lat_min": 9, "lat_max": 14, "lon_min": 36, "lon_max": 40},
    ],
    ids=["fewer zones", "another level", "a box"],
)
def test_a_run_over_a_different_area_is_no_guide(other_area: dict) -> None:
    past = _finished(benchmark_config(focus_area=other_area), 2)

    assert (
        run_summaries.expected_seconds(_job(benchmark_config(focus_area=_AMHARA)), [past]) is None
    )
    assert (
        run_summaries.expected_seconds(_job(benchmark_config(focus_area=other_area)), [past]) == 120
    )


def test_a_run_over_a_new_area_should_take_no_longer_than_its_whole_region_did() -> None:
    whole_region = [_finished(benchmark_config(), minutes) for minutes in (7, 8, 9)]
    smaller_area = _finished(benchmark_config(focus_area={"level": "adm2", "units": ["Awi"]}), 1)
    zones = _job(benchmark_config(focus_area=_AMHARA))

    assert run_summaries.longest_likely_seconds(zones, [*whole_region, smaller_area]) == 8 * 60
    assert run_summaries.longest_likely_seconds(zones, [smaller_area]) is None
    assert run_summaries.longest_likely_seconds(_job(benchmark_config()), whole_region) is None
    assert zones["config"]["romp_params"]["focus_area"] == _AMHARA, "the job is left as it was"


@pytest.mark.asyncio
async def test_history_is_read_per_owner_and_per_kind_of_job(client, as_new_user, user_id) -> None:
    from ai_almanac.server.services import job_history

    other = str(uuid.uuid4())
    for _ in range(job_history.HISTORY_JOBS + 5):
        insert_job(other, benchmark_config(), **ran_for(30))
    insert_job(user_id, benchmark_config(), **ran_for(10))
    insert_job(user_id, benchmark_config(), status="failed", **ran_for(None))

    history = await job_history.finished_jobs_of(
        [(user_id, "benchmark"), (other, "benchmark"), (user_id, "blend")]
    )

    assert len(history[(other, "benchmark")]) == job_history.HISTORY_JOBS
    assert [run_summaries.run_seconds(job) for job in history[(user_id, "benchmark")]] == [600]
    assert history[(user_id, "blend")] == []
    assert await job_history.finished_jobs_of([]) == {}


def test_the_tool_listing_of_onset_definitions_never_names_a_rule_by_its_key() -> None:
    listed = chat_tools.list_onset_rules()

    for definition in listed["onset_definitions"]:
        assert "name" not in definition
        assert definition["internal_value"] not in (
            definition["display_name"],
            definition["description"],
        )
    assert "default" not in listed
    assert [d["display_name"] for d in listed["onset_definitions"] if d["is_default"]] == [
        listed["default_display_name"]
    ]


# --- the workspace's run list ----------------------------------------------------


@pytest.mark.asyncio
async def test_a_workspaces_runs_say_their_area_definition_and_expected_time(
    client, as_new_user, user_id
) -> None:
    workspace, _ = await workspace_with_session(client, as_new_user)
    zones = benchmark_config(
        focus_area={"level": "adm2", "units": AMHARA_ZONES},
        onset_rule="two_stage",
        onset_rule_params={"stage1_mm": 15.0},
    )
    for minutes in (10, 30):
        insert_job(user_id, zones, **ran_for(minutes))
    running = insert_job(user_id, zones, status="running", **ran_for(None))
    whole_region = insert_job(
        user_id, benchmark_config(years=(2020, 2024)), status="queued", **ran_for(None)
    )
    for job_id in (running, whole_region):
        await workspaces.link_job(workspace["id"], job_id)

    body = (await client.get(f"/workspaces/{workspace['id']}", headers=as_new_user)).json()
    jobs = {job["id"]: job for job in body["jobs"]}

    assert jobs[running]["area_of_interest"] == AMHARA_ZONES
    assert jobs[running]["onset_definition"] == {
        "name": "Two-stage",
        "description": onset_rules.onset_rules()["two_stage"]["description"],
        "is_default": False,
        "changes": [
            {
                "label": "First stage rain",
                "short_label": "first stage rain",
                "value": 15.0,
                "unit": "mm",
            }
        ],
    }
    assert jobs[running]["expected_seconds"] == 20 * 60
    assert jobs[running]["started_at"] == "2026-01-01T01:00:00"
    assert jobs[whole_region]["area_of_interest"] is None
    assert jobs[whole_region]["onset_definition"]["is_default"] is True
    assert jobs[whole_region]["expected_seconds"] is None
