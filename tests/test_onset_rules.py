from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_almanac.server.services.benchmark_domain import _onset_rule_errors
from ai_almanac.server.services.job_submission import RompParams
from ai_almanac.server.services.onset_rules import onset_rules


def test_catalog_carries_romps_built_in_rules() -> None:
    assert {"legacy", "two_stage", "moron_robertson"} <= set(onset_rules())


def test_job_params_accept_a_valid_onset_definition() -> None:
    params = RompParams(
        onset_rule="moron_robertson", onset_rule_params={"threshold": None, "wet_days": 7}
    )
    assert params.onset_rule_params == {"threshold": None, "wet_days": 7}


@pytest.mark.parametrize(
    ("rule", "params", "message"),
    [
        ("pentad", {}, "Unknown onset rule"),
        ("two_stage", {"days": 3}, "unknown parameter"),
        ("two_stage", {"stage1_days": 0}, "at least 1"),
        ("two_stage", {"stage1_days": 2.5}, "must be a int"),
        ("two_stage", {"inclusive": 1}, "must be a bool"),
        ("legacy", {"wet_spell": None}, "is required"),
    ],
)
def test_job_params_reject_bad_onset_definitions(rule, params, message) -> None:
    with pytest.raises(ValidationError, match=message):
        RompParams(onset_rule=rule, onset_rule_params=params)


def test_params_without_a_rule_name_are_checked_against_legacy() -> None:
    with pytest.raises(ValidationError, match="legacy: unknown parameter"):
        RompParams(onset_rule_params={"stage1_mm": 10})


def test_benchmark_validation_reports_onset_errors_before_submission() -> None:
    assert _onset_rule_errors({}) == []
    assert _onset_rule_errors({"onset_rule_params": [1]}) == ["onset_rule_params must be an object"]
    assert _onset_rule_errors({"onset_rule": "two_stage", "onset_rule_params": {"x": 1}})
