from __future__ import annotations

import pytest

from ai_almanac.server.services.job_submission import skip_missing_years

PARAMS = {"start_date": "2011-05-01", "end_date": "2015-07-31"}


def test_model_gap_is_skipped_from_evaluation_but_not_climatology() -> None:
    params = skip_missing_years(PARAMS, obs_missing=set(), model_missing={2013})

    assert params["years"] == [2011, 2012, 2014, 2015]
    assert params["years_clim"] == [2011, 2012, 2013, 2014, 2015]


def test_observation_gap_is_skipped_from_evaluation_and_climatology() -> None:
    params = skip_missing_years(
        {**PARAMS, "start_year_clim": 2000, "end_year_clim": 2015},
        obs_missing={2005, 2012},
        model_missing=set(),
    )

    assert params["years"] == [2011, 2013, 2014, 2015]
    assert 2005 not in params["years_clim"]
    assert params["years_clim"][0] == 2000


def test_gaps_outside_the_evaluation_window_change_nothing() -> None:
    params = skip_missing_years(PARAMS, obs_missing=set(), model_missing={2009})

    assert params["years"] == [2011, 2012, 2013, 2014, 2015]


def test_a_window_with_no_data_is_rejected() -> None:
    with pytest.raises(ValueError, match="no data"):
        skip_missing_years(
            {"start_date": "2013-05-01", "end_date": "2013-07-31"},
            obs_missing=set(),
            model_missing={2013},
        )
