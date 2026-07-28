"""blend_cells.build_cell_metrics — per-grid-point blend skill reshaped to grids.

The fixtures use the real per-cell summary header, in which ``lat``/``lon`` are
present but empty for per-point rows and the coordinates live in ``id``.
"""

from __future__ import annotations

import pytest

from ai_almanac.server.services import blend_cells

_HEADER = (
    "id,brier,rps,auc,n,lat,lon,pietra,"
    "brier_week1,brier_week2,brier_week3,brier_week4,brier_later,"
    "auc_week1,auc_week2,auc_week3,auc_week4,auc_later,"
    "model,cv_method"
)


def _row(cell_id: str, brier: str, rps: str, auc: str, n: str, model: str) -> str:
    return (
        f"{cell_id},{brier},{rps},{auc},{n},,,,"
        ",,,,,"  # brier_week1..later are blank for per-point rows
        ",,,,,"  # auc_week1..later likewise
        f"{model},global"
    )


def _csv(*rows: str) -> str:
    return "\n".join([_HEADER, *rows]) + "\n"


# Two points on a 0.25-degree grid: the blend halves the baseline's error at the
# first and doubles it at the second, so skill is +0.5 and -1.0.
_TWO_POINTS = _csv(
    _row("10.00_33.00", "0.50", "0.40", "0.70", "24", "blended_model"),
    _row("10.25_33.00", "0.80", "0.80", "0.60", "24", "blended_model"),
    _row("10.00_33.00", "1.00", "0.80", "0.65", "24", "unc_clim_raw"),
    _row("10.25_33.00", "0.40", "0.40", "0.62", "24", "unc_clim_raw"),
)


def _grid(result, metric: str):
    return next(g for g in result.grids if g.metric == metric)


def test_builds_a_grid_of_skill_against_the_baseline() -> None:
    result = blend_cells.build_cell_metrics("job-1", _TWO_POINTS)
    grid = _grid(result, "ranked_probability_skill_score")

    assert grid.lats == [10.0, 10.25]
    assert grid.lons == [33.0]
    # 1 - 0.40/0.80 = +0.5, then 1 - 0.80/0.40 = -1.0.
    assert grid.values[0][0] == pytest.approx(0.5)
    assert grid.values[1][0] == pytest.approx(-1.0)
    assert result.baseline_model == "unc_clim_raw"


def test_reports_both_skill_metrics() -> None:
    result = blend_cells.build_cell_metrics("job-1", _TWO_POINTS)
    assert [g.metric for g in result.grids] == [
        "ranked_probability_skill_score",
        "brier_skill_score",
    ]
    brier = _grid(result, "brier_skill_score")
    assert brier.values[0][0] == pytest.approx(0.5)


def test_omits_area_under_roc_curve() -> None:
    """Per-point AUC headroom over chance is too small to divide by safely."""
    result = blend_cells.build_cell_metrics("job-1", _TWO_POINTS)
    assert all(g.metric != "auc" for g in result.grids)


def test_reports_the_true_extremes_alongside_the_scale() -> None:
    grid = _grid(
        blend_cells.build_cell_metrics("job-1", _TWO_POINTS),
        "ranked_probability_skill_score",
    )
    assert grid.value_min == pytest.approx(-1.0)
    assert grid.value_max == pytest.approx(0.5)


def test_scale_clips_outliers_so_ordinary_points_stay_visible() -> None:
    """Skill is a ratio, so a near-zero baseline at one point can dwarf the rest.

    Scaling to the true maximum would render every ordinary point neutral, so the
    ramp is clipped to a percentile and the overflow reported.
    """
    rows = []
    # Twenty ordinary points: the blend beats the baseline by a tenth.
    for step in range(20):
        cell = f"{10.0 + step * 0.25:.2f}_33.00"
        rows.append(_row(cell, "0.90", "0.90", "0.70", "24", "blended_model"))
        rows.append(_row(cell, "1.00", "1.00", "0.65", "24", "unc_clim_raw"))
    # One point where the baseline nearly vanished: skill of -99.
    rows.append(_row("20.00_33.00", "1.00", "1.00", "0.70", "24", "blended_model"))
    rows.append(_row("20.00_33.00", "0.01", "0.01", "0.65", "24", "unc_clim_raw"))

    grid = _grid(
        blend_cells.build_cell_metrics("job-1", _csv(*rows)),
        "ranked_probability_skill_score",
    )
    assert grid.value_min == pytest.approx(-99.0)
    # The ramp ignores the outlier, so it stays near the ordinary points' scale.
    assert grid.scale_max_abs is not None
    assert grid.scale_max_abs < 1.0
    # And the outlier is declared rather than silently flattened.
    assert grid.clipped >= 1


def test_carries_the_smaller_observation_count_per_point() -> None:
    csv_text = _csv(
        _row("10.00_33.00", "0.50", "0.40", "0.70", "31", "blended_model"),
        _row("10.00_33.00", "1.00", "0.80", "0.65", "22", "unc_clim_raw"),
    )
    grid = _grid(
        blend_cells.build_cell_metrics("job-1", csv_text),
        "ranked_probability_skill_score",
    )
    # The noisier of the two bounds how much the point can be trusted.
    assert grid.counts[0][0] == 22


def test_infers_cell_size_from_coordinate_spacing() -> None:
    result = blend_cells.build_cell_metrics("job-1", _TWO_POINTS)
    assert result.cell_size_deg == pytest.approx(0.25)


def test_skips_the_pooled_all_row() -> None:
    csv_text = _csv(
        _row("ALL", "0.57", "0.47", "0.83", "26622", "blended_model"),
        _row("ALL", "0.59", "0.55", "0.83", "26622", "unc_clim_raw"),
        _row("10.00_33.00", "0.50", "0.40", "0.70", "24", "blended_model"),
        _row("10.00_33.00", "1.00", "0.80", "0.65", "24", "unc_clim_raw"),
    )
    grid = _grid(
        blend_cells.build_cell_metrics("job-1", csv_text),
        "ranked_probability_skill_score",
    )
    assert grid.lats == [10.0]
    assert grid.counts[0][0] == 24


def test_leaves_points_only_one_model_scored_empty() -> None:
    csv_text = _csv(
        _row("10.00_33.00", "0.50", "0.40", "0.70", "24", "blended_model"),
        _row("10.25_33.00", "0.50", "0.40", "0.70", "24", "blended_model"),
        _row("10.00_33.00", "1.00", "0.80", "0.65", "24", "unc_clim_raw"),
    )
    grid = _grid(
        blend_cells.build_cell_metrics("job-1", csv_text),
        "ranked_probability_skill_score",
    )
    assert grid.lats == [10.0]


def test_zero_baseline_yields_no_value_rather_than_infinity() -> None:
    csv_text = _csv(
        _row("10.00_33.00", "0.50", "0.40", "0.70", "24", "blended_model"),
        _row("10.00_33.00", "0", "0", "0.65", "24", "unc_clim_raw"),
    )
    result = blend_cells.build_cell_metrics("job-1", csv_text)
    # Nothing is mappable, so no grid claims to be.
    assert result.grids == []


def test_blank_cells_are_missing_not_zero() -> None:
    csv_text = _csv(
        _row("10.00_33.00", "", "", "0.70", "", "blended_model"),
        _row("10.00_33.00", "1.00", "0.80", "0.65", "24", "unc_clim_raw"),
    )
    result = blend_cells.build_cell_metrics("job-1", csv_text)
    assert result.grids == []


def test_returns_no_grids_without_a_baseline_model() -> None:
    csv_text = _csv(_row("10.00_33.00", "0.50", "0.40", "0.70", "24", "blended_model"))
    result = blend_cells.build_cell_metrics("job-1", csv_text)
    assert result.grids == []
    assert result.cell_size_deg is None


def test_handles_empty_and_headerless_input() -> None:
    assert blend_cells.build_cell_metrics("job-1", "").grids == []
    assert blend_cells.build_cell_metrics("job-1", "id,model\n").grids == []


def test_negative_coordinates_parse() -> None:
    csv_text = _csv(
        _row("-2.50_-40.00", "0.50", "0.40", "0.70", "24", "blended_model"),
        _row("-2.50_-40.00", "1.00", "0.80", "0.65", "24", "unc_clim_raw"),
    )
    grid = _grid(
        blend_cells.build_cell_metrics("job-1", csv_text),
        "ranked_probability_skill_score",
    )
    assert grid.lats == [-2.5]
    assert grid.lons == [-40.0]


def test_distinguishes_the_per_cell_summary_from_its_pooled_sibling() -> None:
    assert blend_cells.is_per_cell_summary("summary_models_clim_mok_date_2023_2024.csv")
    assert not blend_cells.is_per_cell_summary("summary_models_pooled_clim_mok_date_2023_2024.csv")
