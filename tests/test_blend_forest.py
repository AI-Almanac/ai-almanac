"""The day-level blend trains on the same folds, rows, and outcome as the
weekly blend, and its live scores keep every live-season row."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

from ai_almanac.envs.blend_entrypoint import _load_workflow


@pytest.fixture(scope="module")
def workflow():
    return _load_workflow()


@pytest.mark.parametrize(
    ("true_holdout_years", "expected"),
    [
        (
            [2002, 2010],
            {
                2002: (2000, 2001, 2003, 2004),
                2010: (2000, 2001, 2003, 2004),
                2003: (2000, 2001, 2004),
                2004: (2000, 2001, 2003),
            },
        ),
        (None, {2003: (2000, 2001, 2002, 2004), 2004: (2000, 2001, 2002, 2003)}),
    ],
)
def test_cv_folds_test_every_holdout_and_never_train_on_true_holdouts(
    workflow, true_holdout_years: list[int] | None, expected: dict
) -> None:
    folds = workflow._cv_folds(
        training_years=[2000, 2001, 2002, 2003, 2004],
        cv_holdout_years=[2003, 2004],
        true_holdout_years=true_holdout_years,
    )

    assert dict(folds) == expected


def _weekly() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": ["10.0_20.0", "10.0_20.0", "11.0_21.0"],
            "time": [dt.date(2020, 6, 1), dt.date(2020, 6, 8), dt.date(2020, 6, 1)],
            "year": [2020, 2020, 2020],
            "lat": [10.0, 10.0, 11.0],
            "lon": [20.0, 20.0, 21.0],
            "outcome": ["week1", "later", "week3"],
            "prob_clim_mr_week1": [0.1, 0.2, np.nan],
        }
    )


def _combined() -> pd.DataFrame:
    # Different row order and timestamp-typed issue times, as the builder writes them.
    return pd.DataFrame(
        {
            "id": ["11.0_21.0", "10.0_20.0", "10.0_20.0"],
            "time": pd.to_datetime(["2020-06-01", "2020-06-08", "2020-06-01"]),
            "year": [2020, 2020, 2020],
            "true_onset_date": pd.to_datetime(["2020-06-20", None, "2020-06-05"]),
            "aifs_rain_mean_day_1": [3.0, 2.0, 1.0],
            "aifs_rain_mean_day_2": [30.0, 20.0, 10.0],
            "clim_p_onset_day_1": [0.3, 0.2, 0.1],
            "clim_unc_p_onset_day_1": [0.9, 0.9, 0.9],
            "aifs_p_onset_fixed_cutoff_day_1": [0.5, 0.5, 0.5],
            "aifs_onset_thresh": [25.0, 25.0, 25.0],
        }
    )


def test_forest_frame_keeps_weekly_rows_and_attaches_their_daily_inputs(workflow) -> None:
    frame = workflow._forest_frame(_weekly(), _combined())

    assert len(frame) == 3
    assert frame[["id", "lat", "lon", "outcome"]].values.tolist() == (
        _weekly()[["id", "lat", "lon", "outcome"]].values.tolist()
    )
    assert frame["aifs_rain_mean_day_1"].tolist() == [1.0, 2.0, 3.0]
    assert frame["clim_p_onset_day_1"].tolist() == [0.1, 0.2, 0.3]
    assert frame["true_onset_date"].iloc[0] == pd.Timestamp("2020-06-05")
    assert pd.isna(frame["true_onset_date"].iloc[1])
    assert not {
        "clim_unc_p_onset_day_1",
        "aifs_p_onset_fixed_cutoff_day_1",
        "aifs_onset_thresh",
    } & set(frame.columns)


def test_forest_frame_refuses_weekly_rows_without_daily_inputs(workflow) -> None:
    combined = _combined().iloc[:2]

    with pytest.raises(ValueError, match="1 weekly rows have no daily inputs"):
        workflow._forest_frame(_weekly(), combined)


def _spec(workflow, tmp_path: Path, **kwargs) -> dict:
    return workflow._build_blend_spec(
        model_names=["aifs"],
        training_years=[2000, 2001],
        cv_holdout_years=[2001],
        true_holdout_years=None,
        cutoff_mode="fixed_cutoff",
        formula_text="outcome ~ prob_clim_mr_qx",
        include_raw_forecasts=True,
        include_calibrated_forecasts=True,
        work_dir=tmp_path,
        results_dir=tmp_path,
        dissemination_path=tmp_path / "dissemination_cells.csv",
        **kwargs,
    )


@pytest.mark.parametrize("with_forest", [True, False])
def test_blend_spec_asks_the_evaluation_to_score_the_forest_cv_predictions(
    workflow, tmp_path: Path, with_forest: bool
) -> None:
    tag = workflow._blend_output_tag("_fixed_cutoff", [2022, 2020, 2021])
    filename = workflow._forest_cv_preds_filename(tag)
    extra = {"external_predictions": workflow._forest_external_predictions(filename)}

    spec = _spec(workflow, tmp_path, **(extra if with_forest else {}))

    if with_forest:
        assert spec["extras"]["external_predictions"] == [
            {"name": "blended_forest", "file": filename, "method": "global"}
        ]
    else:
        assert "external_predictions" not in spec["extras"]


def _fake_onset_forest() -> ModuleType:
    module = ModuleType("onset_forest")
    module.DAILY_COLUMNS = ("p_day_1", "p_later")
    module.WEEKLY_COLUMNS = ("cv_week1", "cv_later")

    def predict(fitted, frame):
        return pd.DataFrame(
            {"p_day_1": 0.25, "p_later": 0.75, "cv_week1": 0.25, "cv_later": 0.75},
            index=frame.index,
        )

    module.predict = predict
    return module


def test_daily_scores_cover_live_rows_the_weekly_model_cannot_score(workflow, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "onset_forest", _fake_onset_forest())
    frame = pd.concat(
        [_weekly(), _weekly().assign(year=2019, time=dt.date(2019, 6, 1))], ignore_index=True
    )

    rows = workflow._daily_onset_rows(object(), frame, 2020)

    assert list(rows.columns) == [
        "id",
        "time",
        "lat",
        "lon",
        "p_day_1",
        "p_later",
        "cv_week1",
        "cv_later",
    ]
    assert rows["id"].tolist() == ["10.0_20.0", "10.0_20.0", "11.0_21.0"]
    assert rows["p_later"].tolist() == [0.75, 0.75, 0.75]


def test_a_failing_day_level_step_reports_why_instead_of_raising(workflow) -> None:
    def broken_step():
        raise KeyError("aifs_rain_mean_day_40")

    result, error = workflow._try_day_level("scoring", broken_step)

    assert result is None
    assert "aifs_rain_mean_day_40" in error


@pytest.mark.parametrize(
    ("forest_pkl", "weekly_finished", "fit_error", "shipped", "error"),
    [
        (b"forest", True, None, b"forest", None),
        (b"forest", False, None, None, "week-level blend did not finish"),
        (None, True, "final fit failed: KeyError", None, "final fit failed"),
        (None, False, "final fit failed: KeyError", None, "final fit failed"),
    ],
)
def test_the_day_level_blend_ships_only_with_a_finished_week_level_blend(
    workflow, forest_pkl, weekly_finished, fit_error, shipped, error
) -> None:
    result, reported = workflow._shipped_forest(forest_pkl, weekly_finished, fit_error)

    assert result == shipped
    assert (reported is None) if error is None else (error in reported)


def _fold_counting_onset_forest(calls: list, fail_on_year: int | None = None) -> ModuleType:
    """cross_validate with onset_forest's seeding (random_state + i + 1) and
    skipping of folds with no test rows; records each fit's test year."""
    from dataclasses import dataclass

    module = ModuleType("onset_forest")

    @dataclass(frozen=True)
    class ForestParams:
        n_estimators: int = 100
        random_state: int = 123

    def cross_validate(train, predict_on, folds, model_names, params):
        parts = []
        for i, (test_year, train_years) in enumerate(folds):
            rows = predict_on.loc[predict_on["year"] == test_year]
            if rows.empty:
                continue
            if test_year == fail_on_year:
                raise RuntimeError("preempted")
            calls.append(test_year)
            parts.append(rows.assign(seed=params.random_state + i + 1, trained_on=len(train_years)))
        if not parts:
            raise ValueError("No predict_on rows fall in any fold's test year.")
        return pd.concat(parts, ignore_index=True)

    module.ForestParams = ForestParams
    module.cross_validate = cross_validate
    return module


def _forest_frame_for_folds() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [f"c{i % 3}" for i in range(12)],
            "year": [2000 + i % 4 for i in range(12)],
            "outcome": ["week1"] * 12,
            "prob_clim_mr_week1": [0.1] * 12,
        }
    )


_FOLDS = [(2001, (2000, 2002, 2003)), (2009, (2000,)), (2003, (2000, 2001, 2002))]


def test_forest_cv_by_fold_equals_one_run_over_every_fold(
    workflow, monkeypatch, tmp_path: Path
) -> None:
    calls: list = []
    fake = _fold_counting_onset_forest(calls)
    monkeypatch.setitem(sys.modules, "onset_forest", fake)
    monkeypatch.setattr(workflow, "_dissemination_rows", lambda frame, _path: frame)
    frame = _forest_frame_for_folds()

    by_fold = workflow._cross_validate_forest(frame, tmp_path / "cells.csv", _FOLDS, ["aifs"])
    at_once = fake.cross_validate(
        frame, workflow._multinomial_scoreable(frame), _FOLDS, ["aifs"], fake.ForestParams()
    )

    pd.testing.assert_frame_equal(by_fold, at_once)
    assert by_fold["seed"].unique().tolist() == [124, 126]  # fold 2 (2009) has no rows


def test_a_restarted_forest_cv_refits_only_the_folds_it_had_not_finished(
    workflow, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(workflow, "_dissemination_rows", lambda frame, _path: frame)
    frame = _forest_frame_for_folds()
    cache_dir = str(tmp_path / "cache")
    first: list = []
    monkeypatch.setitem(sys.modules, "onset_forest", _fold_counting_onset_forest(first, 2003))

    with pytest.raises(RuntimeError, match="preempted"):
        workflow._cross_validate_forest(frame, tmp_path / "cells.csv", _FOLDS, ["aifs"], cache_dir)
    restarted: list = []
    monkeypatch.setitem(sys.modules, "onset_forest", _fold_counting_onset_forest(restarted))
    result = workflow._cross_validate_forest(
        frame, tmp_path / "cells.csv", _FOLDS, ["aifs"], cache_dir
    )

    assert first == [2001]
    assert restarted == [2003]
    assert sorted(result["year"].unique().tolist()) == [2001, 2003]
