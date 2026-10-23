"""The day-level blend trains on the same folds, rows, and outcome as the
weekly blend, and its live scores keep every live-season row."""

from __future__ import annotations

import datetime as dt
import io
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
                (2002,): (2000, 2001, 2003, 2004),
                (2010,): (2000, 2001, 2003, 2004),
                (2003,): (2000, 2001, 2004),
                (2004,): (2000, 2001, 2003),
            },
        ),
        (None, {(2003,): (2000, 2001, 2002, 2004), (2004,): (2000, 2001, 2002, 2003)}),
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


def test_cv_years_held_out_together_train_one_forest_per_block(workflow) -> None:
    folds = workflow._cv_folds(
        training_years=list(range(2000, 2012)),
        cv_holdout_years=[2009, 2000, 2001, 2002, 2003, 2004, 2005, 2006, 2007, 2008],
        true_holdout_years=[2011],
        years_per_fold=4,
    )

    assert [test_years for test_years, _ in folds] == [
        (2011,),
        (2000, 2001, 2002, 2003),
        (2004, 2005, 2006, 2007),
        (2008, 2009),
    ]
    test_years, train_years = folds[1]
    assert train_years == (2004, 2005, 2006, 2007, 2008, 2009, 2010)
    assert all(2011 not in train for _, train in folds)


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


def _fold_counting_onset_forest(calls: list, fail_on_year: int | None = None) -> ModuleType:
    """onset_forest's fit/predict, recording each fit's years and seed, plus a
    cross_validate with its seeding (random_state + i + 1) and its skipping of
    folds with no test rows."""
    from dataclasses import dataclass

    module = ModuleType("onset_forest")

    @dataclass(frozen=True)
    class ForestParams:
        n_estimators: int = 100
        min_samples_leaf: int = 20
        max_samples: float = 1.0
        max_depth: int | None = None
        random_state: int = 123
        n_jobs: int = -1

    def fit(train, model_names, params):
        years = sorted(train["year"].unique().tolist())
        calls.append(years)
        return {"seed": params.random_state, "trained_on": len(years)}

    def predict(fitted, rows):
        if fail_on_year is not None and (rows["year"] == fail_on_year).any():
            raise RuntimeError("preempted")
        return pd.DataFrame(
            {"seed": fitted["seed"], "trained_on": fitted["trained_on"]}, index=rows.index
        )

    def cross_validate(train, predict_on, folds, model_names, params):
        from dataclasses import replace

        parts = []
        for i, (test_year, train_years) in enumerate(folds):
            rows = predict_on.loc[predict_on["year"] == test_year]
            if rows.empty:
                continue
            fitted = fit(
                train.loc[train["year"].isin(train_years)],
                model_names,
                replace(params, random_state=params.random_state + i + 1),
            )
            parts.append(pd.concat([rows, predict(fitted, rows)], axis=1))
        if not parts:
            raise ValueError("No predict_on rows fall in any fold's test year.")
        return pd.concat(parts, ignore_index=True)

    module.ForestParams = ForestParams
    module.fit = fit
    module.predict = predict
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


_FOLDS = [((2001,), (2000, 2002, 2003)), ((2009,), (2000,)), ((2003,), (2000, 2001, 2002))]


def test_forest_cv_by_fold_equals_one_run_over_every_fold(
    workflow, monkeypatch, tmp_path: Path
) -> None:
    calls: list = []
    fake = _fold_counting_onset_forest(calls)
    monkeypatch.setitem(sys.modules, "onset_forest", fake)
    monkeypatch.setattr(workflow, "_dissemination_rows", lambda frame, _path: frame)
    frame = _forest_frame_for_folds()

    params = fake.ForestParams()
    by_fold = workflow._cross_validate_forest(
        frame, tmp_path / "cells.csv", _FOLDS, ["aifs"], params
    )
    one_year_folds = [(test_years[0], train_years) for test_years, train_years in _FOLDS]
    at_once = fake.cross_validate(
        frame, workflow._multinomial_scoreable(frame), one_year_folds, ["aifs"], params
    )

    pd.testing.assert_frame_equal(by_fold, at_once)
    assert by_fold["seed"].unique().tolist() == [124, 126]  # fold 2 (2009) has no rows


def test_a_block_of_cv_years_is_predicted_by_one_forest(workflow, monkeypatch, tmp_path) -> None:
    calls: list = []
    fake = _fold_counting_onset_forest(calls)
    monkeypatch.setitem(sys.modules, "onset_forest", fake)
    monkeypatch.setattr(workflow, "_dissemination_rows", lambda frame, _path: frame)
    folds = [((2000, 2001), (2002, 2003)), ((2002, 2003), (2000, 2001))]

    result = workflow._cross_validate_forest(
        _forest_frame_for_folds(), tmp_path / "cells.csv", folds, ["aifs"], fake.ForestParams()
    )

    assert calls == [[2002, 2003], [2000, 2001]]  # one fit per block, on its training years
    assert sorted(result["year"].unique().tolist()) == [2000, 2001, 2002, 2003]
    assert result.groupby("year")["seed"].first().to_dict() == {
        2000: 124,
        2001: 124,
        2002: 125,
        2003: 125,
    }


def test_the_thread_count_never_changes_a_folds_cache_entry(
    workflow, monkeypatch, tmp_path: Path
) -> None:
    from dataclasses import replace

    calls: list = []
    fake = _fold_counting_onset_forest(calls)
    monkeypatch.setitem(sys.modules, "onset_forest", fake)
    monkeypatch.setattr(workflow, "_dissemination_rows", lambda frame, _path: frame)
    frame, cache_dir = _forest_frame_for_folds(), str(tmp_path / "cache")

    for n_jobs in (4, 16):
        workflow._cross_validate_forest(
            frame,
            tmp_path / "c.csv",
            _FOLDS,
            ["aifs"],
            replace(fake.ForestParams(), n_jobs=n_jobs),
            cache_dir,
        )

    assert len(calls) == 2  # the second run read both folds from the cache


def test_a_restarted_forest_cv_refits_only_the_folds_it_had_not_finished(
    workflow, monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(workflow, "_dissemination_rows", lambda frame, _path: frame)
    frame = _forest_frame_for_folds()
    cache_dir = str(tmp_path / "cache")
    first: list = []
    monkeypatch.setitem(sys.modules, "onset_forest", _fold_counting_onset_forest(first, 2003))

    params = _fold_counting_onset_forest([]).ForestParams()

    with pytest.raises(RuntimeError, match="preempted"):
        workflow._cross_validate_forest(
            frame, tmp_path / "cells.csv", _FOLDS, ["aifs"], params, cache_dir
        )
    restarted: list = []
    monkeypatch.setitem(sys.modules, "onset_forest", _fold_counting_onset_forest(restarted))
    result = workflow._cross_validate_forest(
        frame, tmp_path / "cells.csv", _FOLDS, ["aifs"], params, cache_dir
    )

    assert first == [[2000, 2002, 2003], [2000, 2001, 2002]]  # fit 2 failed before caching
    assert restarted == [[2000, 2001, 2002]]  # only the unfinished fold is refit
    assert sorted(result["year"].unique().tolist()) == [2001, 2003]


def test_day_level_scoring_fits_nothing_and_scores_only_the_forest(workflow, tmp_path) -> None:
    spec = _spec(workflow, tmp_path)

    scoring = workflow._day_level_scoring_spec(spec, "cv_preds_blended_forest_global.pkl")

    assert all(not formula["enabled"] for formula in scoring["models"]["formulas"].values())
    assert scoring["extras"]["external_predictions"] == [
        {"name": "blended_forest", "file": "cv_preds_blended_forest_global.pkl", "method": "global"}
    ]
    # clim_raw stays only so the script can read its week bins; no forecasts are scored.
    assert [task["name"] for task in scoring["extras"]["clim_logits"]] == ["clim_raw"]
    assert set(scoring["extras"]) == {"clim_logits", "external_predictions"}
    assert scoring["run"] == spec["run"]
    assert scoring["cell"] == spec["cell"]


@pytest.mark.parametrize(
    ("results_name", "output_name"),
    [
        ("forest_blended_forest_global_final.pkl", "forest_blended_forest_global_final.pkl"),
        ("cv_preds_blended_forest_global_2020.pkl", "cv_preds_blended_forest_global_2020.pkl"),
        ("summary_models_fixed_cutoff_2020.csv", "day_level_summary_models_fixed_cutoff_2020.csv"),
        ("yearly_metrics_global_2020.csv", "day_level_yearly_metrics_global_2020.csv"),
        ("almanac_training_spec.yml", None),
    ],
)
def test_day_level_outputs_never_share_a_name_with_weekly_scores(
    workflow, results_name: str, output_name: str | None
) -> None:
    assert workflow._day_level_output_name(results_name) == output_name


class _MarkerBucket:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    def blob(self, name: str):
        bucket = self

        class _Blob:
            def exists(self) -> bool:
                return name in bucket.objects

            def download_as_text(self) -> str:
                return bucket.objects[name]

            def upload_from_string(self, text: str) -> None:
                bucket.objects[name] = text

        return _Blob()


def test_a_restarted_blend_reuses_the_day_level_call_it_started(
    workflow, monkeypatch, tmp_path: Path
) -> None:
    bucket = _MarkerBucket()
    client = SimpleNamespace(bucket=lambda _name: bucket)
    spawned: list = []
    uploads: list = []
    monkeypatch.setattr(
        workflow,
        "run_day_level_blend",
        SimpleNamespace(
            spawn=lambda *args: (
                spawned.append(args) or SimpleNamespace(object_id=f"fc-{len(spawned)}")
            )
        ),
    )
    monkeypatch.setattr(workflow, "_upload_combined_table", lambda *args: uploads.append(args))
    combined = tmp_path / "combined_wide.pkl"

    first = workflow._start_day_level_blend(client, "job-1", {"k": 1}, "outputs", combined)
    restarted = workflow._start_day_level_blend(client, "job-1", {"k": 1}, "outputs", combined)

    assert first == restarted == "fc-1"
    assert spawned == [("job-1", {"k": 1}, "outputs")]
    assert len(uploads) == 1  # the running call's input is not rewritten


def test_day_level_scores_ship_only_the_forest_rows(workflow, tmp_path: Path) -> None:
    table = pd.DataFrame(
        {
            "id": ["ALL", "ALL", "c1"],
            "model": ["clim_raw", "blended_forest", "blended_forest"],
            # Values pandas' default CSV float parser does not read back exactly.
            "rps": [0.5, 0.9008045652179817, float("nan")],
        }
    )
    csv_path, pkl_path = tmp_path / "summary_models_x.csv", tmp_path / "summary_models_x.pkl"
    table.to_csv(csv_path, index=False)
    table.to_pickle(pkl_path)

    from_csv = pd.read_csv(io.BytesIO(workflow._forest_score_rows(csv_path)))
    from_pkl = pd.read_pickle(io.BytesIO(workflow._forest_score_rows(pkl_path)))

    for shipped in (from_csv, from_pkl):
        assert shipped["model"].tolist() == ["blended_forest", "blended_forest"]
        assert shipped["id"].tolist() == ["ALL", "c1"]
    # The CSV keeps each cell exactly as the evaluation wrote it.
    written = csv_path.read_text().splitlines()
    assert workflow._forest_score_rows(csv_path).decode().splitlines() == [
        written[0],
        *[line for line in written[1:] if ",blended_forest," in line],
    ]


def test_an_unwritten_marker_cancels_the_day_level_call_it_was_for(
    workflow, monkeypatch, tmp_path: Path
) -> None:
    canceled: list = []

    class _FailingMarkerBucket(_MarkerBucket):
        def blob(self, name: str):
            marker = super().blob(name)
            marker.upload_from_string = lambda text: (_ for _ in ()).throw(OSError("503"))
            return marker

    bucket = _FailingMarkerBucket()
    call = SimpleNamespace(object_id="fc-1", cancel=lambda: canceled.append("fc-1"))
    monkeypatch.setattr(workflow, "run_day_level_blend", SimpleNamespace(spawn=lambda *a: call))
    monkeypatch.setattr(workflow, "_upload_combined_table", lambda *args: None)

    with pytest.raises(OSError, match="503"):
        workflow._start_day_level_blend(
            SimpleNamespace(bucket=lambda _name: bucket), "job-1", {}, "outputs", tmp_path / "c.pkl"
        )

    assert canceled == ["fc-1"]


@pytest.mark.parametrize("fails", [False, True])
def test_a_blend_run_log_is_published_whether_or_not_the_run_fails(
    workflow, monkeypatch, fails: bool
) -> None:
    pytest.importorskip("google.cloud.storage", reason="Modal-only GCS path; not in the local env")
    from google.cloud import storage as gcs

    uploaded: dict = {}
    monkeypatch.setattr(workflow, "_write_gcp_credentials_from_secret", lambda: None)
    monkeypatch.setattr(gcs, "Client", lambda: "client")
    monkeypatch.setattr(
        workflow,
        "_upload_run_log_to_gcs",
        lambda client, bucket, job_id, text, log_name: uploaded.update(
            {"where": (client, bucket, job_id, log_name), "text": text}
        ),
    )

    def body(client):
        print(f"working with {client}")
        if fails:
            raise ValueError("forest exploded")
        return {"day_level_call_id": "fc-1"}

    if fails:
        with pytest.raises(RuntimeError, match="day_level_run.log.*forest exploded"):
            workflow._with_published_run_log("job-1", "outputs", "day_level_run.log", body)
    else:
        result = workflow._with_published_run_log("job-1", "outputs", "day_level_run.log", body)
        assert result == {"day_level_call_id": "fc-1"}

    assert uploaded["where"] == ("client", "outputs", "job-1", "day_level_run.log")
    assert "working with client" in uploaded["text"]
    assert ("forest exploded" in uploaded["text"]) is fails


def test_day_level_settings_become_forest_params(workflow, monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "onset_forest", _fold_counting_onset_forest([]))

    tuned = workflow._forest_params(
        {"years_per_fold": 5, "trees": 50, "max_depth": 12, "min_samples_leaf": 40}, n_jobs=8
    )
    untouched = workflow._forest_params(None)

    assert (tuned.n_estimators, tuned.max_depth, tuned.n_jobs) == (50, 12, 8)
    assert untouched == workflow._forest_params({})  # the forest's own defaults


@pytest.mark.parametrize(
    ("train_rows", "cpus", "jobs"),
    [(100_000, 16, 16), (2_854_955, 16, 16), (40_000_000, 16, 3), (10**12, 16, 1)],
)
def test_trees_grow_as_many_at_once_as_the_memory_budget_holds(
    workflow, train_rows: int, cpus: int, jobs: int
) -> None:
    assert workflow._forest_jobs(train_rows, cpus, 48 * 2**30) == jobs
