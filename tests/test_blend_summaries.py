"""A blend whose day-level blend trained apart from it reads as one set of
summary tables, the same shape a combined run writes."""

from __future__ import annotations

import csv
import io

import pytest

from ai_almanac.server.services import blend_cells, blend_summaries

_WEEKLY_POOLED = (
    "id,model,cv_method,brier,rps,auc,n,brier_skill,rps_skill,AUC diff\n"
    "ALL,blended_model,global,0.15,0.20,0.70,90,0.25,0.2,10.0\n"
    "ALL,unc_clim_raw,global,0.2,0.25,0.6,90,0.0,0.0,0.0\n"
)
_DAY_LEVEL_CELLS = (
    "id,model,cv_method,brier,rps,auc,n,lat,lon\n"
    "ALL,blended_forest,global,0.1,0.2,0.75,90,,\n"
    "10.00_70.00,blended_forest,global,0.12,0.22,0.7,30,,\n"
)
_WEEKLY_CELLS = (
    "id,model,cv_method,brier,rps,auc,n\n"
    "ALL,blended_model,global,0.15,0.20,0.70,90\n"
    "10.00_70.00,blended_model,global,0.16,0.21,0.69,30\n"
    "10.00_70.00,unc_clim_raw,global,0.2,0.25,0.6,30\n"
)


def _table(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_the_forest_pooled_row_is_scored_against_the_weekly_baseline() -> None:
    merged = _table(
        blend_summaries.merge_summary("pooled", _WEEKLY_POOLED, {"cells": _DAY_LEVEL_CELLS})
    )

    forest = [row for row in merged if row["model"] == "blended_forest"]
    assert [row["model"] for row in merged[:2]] == ["blended_model", "unc_clim_raw"]
    assert len(forest) == 1  # the pooled ALL row only, not its per-point rows
    assert float(forest[0]["brier_skill"]) == pytest.approx(1 - 0.1 / 0.2)
    assert float(forest[0]["rps_skill"]) == pytest.approx(1 - 0.2 / 0.25)
    assert float(forest[0]["AUC diff"]) == pytest.approx((0.75 - 0.6) * 100)


def test_per_point_and_yearly_rows_of_both_runs_read_as_one_table() -> None:
    merged = blend_summaries.merge_summary("cells", _WEEKLY_CELLS, {"cells": _DAY_LEVEL_CELLS})

    models = [row["model"] for row in _table(merged)]
    assert models.count("blended_forest") == 2
    assert models.count("unc_clim_raw") == 1
    metrics = blend_cells.build_cell_metrics("job", merged, model="blended_forest")
    assert metrics.available_models == ["blended_model", "blended_forest"]


@pytest.mark.parametrize("table", ["pooled", "cells", "yearly"])
def test_a_blend_without_day_level_files_reads_unchanged(table) -> None:
    weekly = _WEEKLY_POOLED if table == "pooled" else _WEEKLY_CELLS

    assert blend_summaries.merge_summary(table, weekly, {}) == weekly


def test_without_a_weekly_baseline_the_forest_gets_no_pooled_row() -> None:
    no_baseline = "id,model,brier,rps,auc\nALL,blended_model,0.15,0.2,0.7\n"

    merged = blend_summaries.merge_summary("pooled", no_baseline, {"cells": _DAY_LEVEL_CELLS})

    assert merged == no_baseline


@pytest.mark.parametrize(
    ("table", "day_level", "expected"),
    [
        ("pooled", False, "summary_models_pooled_x.csv"),
        ("cells", False, "summary_models_x.csv"),
        ("cells", True, "day_level_summary_models_x.csv"),
        ("yearly", True, "day_level_yearly_metrics_global_x.csv"),
        ("pooled", True, None),
    ],
)
def test_each_table_is_found_in_its_own_run(table, day_level: bool, expected) -> None:
    artifacts = [
        {"filename": name}
        for name in (
            "day_level_summary_models_x.csv",
            "day_level_summary_models_x.pkl",
            "day_level_yearly_metrics_global_x.csv",
            "summary_models_pooled_x.csv",
            "summary_models_x.csv",
            "yearly_metrics_global_x.csv",
        )
    ]

    found = blend_summaries.find(artifacts, table, day_level=day_level)

    assert (found or {}).get("filename") == expected
