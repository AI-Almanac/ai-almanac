"""A blend's day-level settings are parsed once, at submission: what reaches the
run is within bounds, and anything left out keeps the forest's own defaults."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_almanac.server.services.job_submission import BlendParams

_YEARS = {"training_years": "2000:2024", "cv_holdout_years": "2000:2019"}


def test_only_the_settings_given_reach_the_run() -> None:
    """Unset settings are not sent, so the run keeps onset_forest's own defaults."""
    assert BlendParams(**_YEARS, day_level={}).model_dump(exclude_none=True)["day_level"] == {}
    assert BlendParams(**_YEARS, day_level={"years_per_fold": 5}).model_dump(
        exclude_none=True
    )["day_level"] == {"years_per_fold": 5}
    assert "day_level" not in BlendParams(**_YEARS).model_dump(exclude_none=True)


def test_tuned_settings_reach_the_run_as_given() -> None:
    params = BlendParams(
        **_YEARS, day_level={"years_per_fold": 5, "trees": 60, "max_depth": 16, "max_samples": 0.5}
    )

    assert params.model_dump(exclude_none=True)["day_level"] == {
        "years_per_fold": 5,
        "trees": 60,
        "max_depth": 16,
        "max_samples": 0.5,
    }


@pytest.mark.parametrize(
    "day_level",
    [
        {"years_per_fold": 0},
        {"years_per_fold": 11},
        {"trees": 5},
        {"trees": 1000},
        {"max_depth": 0},
        {"min_samples_leaf": 0},
        {"max_samples": 0},
        {"max_samples": 1.5},
        {"n_jobs": 64},
    ],
)
def test_out_of_range_or_unknown_settings_are_refused(day_level: dict) -> None:
    with pytest.raises(ValidationError):
        BlendParams(**_YEARS, day_level=day_level)
