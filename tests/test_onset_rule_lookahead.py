"""An onset definition reads days past each candidate onset day, so a benchmark whose
forecasts are too short for it is refused before it runs rather than failing inside ROMP."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from ai_almanac.server.services.benchmark_domain import validate_benchmark_spec
from ai_almanac.server.services.onset_rules import (
    onset_fit_errors,
    onset_rules,
    rule_lookahead,
)

TESTDATA = Path(__file__).parents[1] / "testdata"
FIXTURE_FORECAST_DAYS = 45  # leads 1-45 in testdata/ethiopia/fuxi; ROMP drops lead 0
SHORT_TWO_STAGE = {"stage1_days": 5, "stage2_days": 10}


def _romp_lookaheads() -> list[tuple[str, dict, int]]:
    recorded = json.loads((TESTDATA / "onset_rule_lookaheads.json").read_text())
    return [
        (name, sample["params"], sample["lookahead"])
        for name, samples in recorded.items()
        for sample in samples
    ]


@pytest.mark.parametrize(("name", "params", "expected"), _romp_lookaheads())
def test_lookahead_matches_what_romp_computes(name: str, params: dict, expected: int) -> None:
    assert rule_lookahead(name, params) == expected


def test_every_catalog_rule_has_romp_lookaheads_to_check_against() -> None:
    assert {name for name, _, _ in _romp_lookaheads()} == set(onset_rules())


def _fit(settings: dict, *, forecast_days: int = 30, forecast_steps: int | None = 45) -> list[str]:
    return onset_fit_errors(
        settings, forecast_days=forecast_days, forecast_steps=forecast_steps, model_name="FuXi"
    )


def test_a_definition_that_needs_more_forecast_days_than_the_model_has_is_refused() -> None:
    (error,) = _fit({"onset_rule": "two_stage"})

    assert "FuXi forecasts run 45 days" in error
    assert "needs 59" in error


def test_shortening_the_definitions_windows_lets_it_fit() -> None:
    assert _fit({"onset_rule": "two_stage", "onset_rule_params": SHORT_TWO_STAGE}) == []


def test_a_definition_must_confirm_onset_within_the_forecast_days_scored() -> None:
    long_two_stage = {"stage1_days": 20, "stage2_days": 20}
    (error,) = _fit(
        {"onset_rule": "two_stage", "onset_rule_params": long_two_stage}, forecast_steps=None
    )

    assert "39 more days" in error
    assert "fewer than the 30 forecast days" in error


def test_the_default_definition_fits_and_reads_the_flat_dry_spell_settings() -> None:
    assert _fit({}) == []
    (error,) = _fit({"wet_spell": 3, "dry_spell": 7, "dry_extent": 20})

    assert "needs 49" in error


def test_a_model_of_unknown_forecast_length_is_only_held_to_the_days_scored() -> None:
    settings = {"onset_rule": "two_stage", "onset_rule_params": SHORT_TWO_STAGE}

    assert _fit(settings, forecast_steps=None) == []


async def _register(client: httpx.AsyncClient, kind: str, metadata: dict) -> dict:
    response = await client.post(
        "/data-sources",
        json={
            "kind": kind,
            "name": f"Ethiopia {kind}",
            "path": str(TESTDATA / "ethiopia" / ("obs" if kind == "obs" else "fuxi")),
            "region": "ethiopia",
            "metadata": metadata,
        },
    )
    assert response.status_code == 201
    return response.json()


async def _register_model(client: httpx.AsyncClient) -> dict:
    return await _register(client, "model", {"file_pattern": "{}.nc", "model_var": "tp"})


async def _sources(client: httpx.AsyncClient) -> tuple[str, str]:
    obs = await _register(client, "obs", {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"})
    return obs["id"], (await _register_model(client))["id"]


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    job_ids: list[str] = []

    async def fake_launch(job_id: str) -> None:
        job_ids.append(job_id)

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", fake_launch)
    return job_ids


@pytest.mark.asyncio
async def test_registering_a_model_records_how_many_days_its_forecasts_run(
    client: httpx.AsyncClient,
) -> None:
    model = await _register_model(client)

    assert model["metadata"]["forecast_steps"] == FIXTURE_FORECAST_DAYS


@pytest.mark.parametrize(("rule_params", "accepted"), [(None, False), (SHORT_TWO_STAGE, True)])
@pytest.mark.asyncio
async def test_submission_refuses_a_definition_the_models_forecasts_cannot_hold(
    client: httpx.AsyncClient,
    as_new_user: dict[str, str],
    launched: list[str],
    rule_params: dict | None,
    accepted: bool,
) -> None:
    obs_id, model_id = await _sources(client)
    params = {"region": "ethiopia", "max_forecast_day": 30, "onset_rule": "two_stage"}
    if rule_params:
        params["onset_rule_params"] = rule_params

    response = await client.post(
        "/jobs",
        headers=as_new_user,
        json={"dataset_id": obs_id, "model_name": model_id, "params": params},
    )

    assert (response.status_code == 201) is accepted, response.text
    assert bool(launched) is accepted
    if not accepted:
        assert f"forecasts run {FIXTURE_FORECAST_DAYS} days" in response.json()["detail"]


@pytest.mark.asyncio
async def test_validating_a_benchmark_reports_the_misfit_before_anything_is_submitted(
    client: httpx.AsyncClient, user_id: str
) -> None:
    obs_id, model_id = await _sources(client)

    result = await validate_benchmark_spec(
        {
            "region_id": "ethiopia",
            "dataset_id": obs_id,
            "model_ids": [model_id],
            "forecast_window_days": 30,
            "advanced_params": {"onset_rule": "two_stage"},
        },
        user_id,
    )

    validation = result["benchmark_validation"]
    assert not validation["can_run"]
    assert any("needs 59" in error for error in validation["errors"]), validation


def test_a_definition_that_does_not_fit_the_forecast_is_named_as_the_user_knows_it() -> None:
    [too_long] = onset_fit_errors(
        {"onset_rule": "two_stage", "onset_rule_params": {"stage1_days": 20, "stage2_days": 20}},
        forecast_days=30,
        forecast_steps=None,
        model_name="AIFS",
    )

    assert too_long.startswith("The Two-stage onset definition needs")
    assert "two_stage" not in too_long
