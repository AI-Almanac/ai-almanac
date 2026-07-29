"""Per-grid-point blend skill, reshaped into a lat/lon grid for the map.

The blend writes two summary CSVs with the same stem. ``summary_models_pooled_*``
holds one row per model pooled over the region and drives the skill table;
``summary_models_*`` holds one row per model *per grid point* plus a pooled
``ALL`` row, and nothing read it until this module. It is the only per-point
skill the blend produces, so it is the only way to answer where the blend beats
climatology rather than whether it does on average.

Per-point rows carry ``id``, ``brier``, ``rps``, ``auc`` and ``n``. The ``lat``
and ``lon`` columns are written but left empty for them, so coordinates come from
the ``id``, which the blend builds as ``"{lat}_{lon}"`` on a regular grid.
"""

from __future__ import annotations

import re
from itertools import pairwise

from pydantic import BaseModel

# Cell ids look like "10.00_33.25". Latitudes are signed; longitudes may be too.
_CELL_ID = re.compile(r"^(-?\d+(?:\.\d+)?)_(-?\d+(?:\.\d+)?)$")

_POOLED_ROW_ID = "ALL"
_BLEND_MODEL = "blended_model"
_BASELINE_MODEL = "unc_clim_raw"

# Metric id -> the CSV column it comes from. Both are lower-is-better scores, so
# both become skill in the standard ``1 - value / reference`` form.
#
# The Area Under ROC Curve is deliberately absent. Pooled, it is measured against
# climatology's headroom over chance (~0.33 for this region), which is stable.
# Per point, that headroom is computed from ~25 observations and can approach
# zero, so dividing by it turns map noise into enormous skill values.
_SKILL_METRICS: dict[str, tuple[str, str]] = {
    "ranked_probability_skill_score": ("rps", "Ranked Probability Skill Score"),
    "brier_skill_score": ("brier", "Brier Skill Score"),
}

# A single grid point is scored on a few dozen point-years, so its skill is noisy.
# Points below this are returned with their value but flagged by ``counts`` so the
# map can mute them rather than dropping data silently.
DEFAULT_MIN_OBSERVATIONS = 10

# Skill is a ratio, so a point where climatology happened to score near zero
# produces an enormous value: on the Ethiopia run the 99th percentile of |skill|
# is 0.71 while the maximum is 10.8. Scaling a diverging ramp to that maximum
# renders every ordinary point neutral, so the ramp is clipped at this percentile
# instead and the overflow is reported rather than hidden. The blend package's own
# plot_metric_map clips at the 5th/95th quantiles for the same reason.
SCALE_PERCENTILE = 0.95


class BlendCellGrid(BaseModel):
    """One metric's per-point skill, indexed ``values[lat_index][lon_index]``."""

    metric: str
    label: str
    lats: list[float]
    lons: list[float]
    values: list[list[float | None]]
    counts: list[list[int | None]]
    # Symmetric extent of the diverging scale, so zero stays at its midpoint.
    # Clipped to SCALE_PERCENTILE rather than the true extreme.
    scale_max_abs: float | None
    # True extremes, so the legend can name what the ramp's ends leave out.
    value_min: float | None
    value_max: float | None
    # How many points fall outside the ramp and render at its ends.
    clipped: int


class BlendCellMetrics(BaseModel):
    job_id: str
    baseline_model: str
    cell_size_deg: float | None
    min_observations: int
    grids: list[BlendCellGrid]


class _Cell(BaseModel):
    lat: float
    lon: float
    values: dict[str, float]
    n: int | None


def _parse_float(raw: str | None) -> float | None:
    """Parse a CSV cell, treating blanks and non-numerics as missing.

    pandas writes NaN as an empty string, and ``float('')`` raises rather than
    returning a sentinel, so every read goes through here.
    """
    if raw is None or raw.strip() == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    # NaN and infinities survive float() but are not values a map can place.
    return value if value == value and value not in (float("inf"), float("-inf")) else None


def _skill(value: float | None, reference: float | None) -> float | None:
    """Skill of a lower-is-better score against a reference. None if undefined."""
    if value is None or reference is None or reference == 0:
        return None
    return 1.0 - value / reference


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    """Linearly interpolated percentile of an already-sorted list."""
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight


def _spacing(values: list[float]) -> float | None:
    """Smallest gap between adjacent coordinates — the grid's cell size."""
    if len(values) < 2:
        return None
    gaps = [b - a for a, b in pairwise(values) if b > a]
    return min(gaps) if gaps else None


def _cells_by_model(csv_text: str) -> dict[str, dict[tuple[float, float], _Cell]]:
    """Index the per-point rows of a per-cell summary CSV by model, then position."""
    lines = [line for line in csv_text.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return {}
    header = lines[0].split(",")
    index = {name: position for position, name in enumerate(header)}
    if "id" not in index or "model" not in index:
        return {}

    def field(cells: list[str], name: str) -> str | None:
        position = index.get(name)
        if position is None or position >= len(cells):
            return None
        return cells[position]

    by_model: dict[str, dict[tuple[float, float], _Cell]] = {}
    for line in lines[1:]:
        cells = line.split(",")
        cell_id = (field(cells, "id") or "").strip()
        model = (field(cells, "model") or "").strip()
        if not model or cell_id == _POOLED_ROW_ID:
            continue
        match = _CELL_ID.match(cell_id)
        if match is None:
            continue
        lat, lon = float(match.group(1)), float(match.group(2))
        observations = _parse_float(field(cells, "n"))
        values = {}
        for column, _ in _SKILL_METRICS.values():
            parsed = _parse_float(field(cells, column))
            if parsed is not None:
                values[column] = parsed
        by_model.setdefault(model, {})[(lat, lon)] = _Cell(
            lat=lat,
            lon=lon,
            values=values,
            n=None if observations is None else int(observations),
        )
    return by_model


def build_cell_metrics(
    job_id: str,
    csv_text: str,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
) -> BlendCellMetrics:
    """Reshape a per-cell summary CSV into per-metric grids of blend skill.

    Skill is the blend against ``unc_clim_raw``, matching the pooled table, so a
    point reads on the same scale in both places: zero is climatology, positive
    beats it.
    """
    by_model = _cells_by_model(csv_text)
    blend = by_model.get(_BLEND_MODEL, {})
    baseline = by_model.get(_BASELINE_MODEL, {})

    empty = BlendCellMetrics(
        job_id=job_id,
        baseline_model=_BASELINE_MODEL,
        cell_size_deg=None,
        min_observations=min_observations,
        grids=[],
    )
    if not blend or not baseline:
        return empty

    # Only points both models scored can express skill.
    shared = sorted(set(blend) & set(baseline))
    if not shared:
        return empty

    lats = sorted({lat for lat, _ in shared})
    lons = sorted({lon for _, lon in shared})
    lat_index = {lat: i for i, lat in enumerate(lats)}
    lon_index = {lon: j for j, lon in enumerate(lons)}

    grids: list[BlendCellGrid] = []
    for metric, (column, label) in _SKILL_METRICS.items():
        values: list[list[float | None]] = [[None] * len(lons) for _ in lats]
        counts: list[list[int | None]] = [[None] * len(lons) for _ in lats]
        scored: list[float] = []
        for position in shared:
            lat, lon = position
            skill = _skill(
                blend[position].values.get(column),
                baseline[position].values.get(column),
            )
            i, j = lat_index[lat], lon_index[lon]
            blend_n, baseline_n = blend[position].n, baseline[position].n
            if blend_n is not None and baseline_n is not None:
                counts[i][j] = min(blend_n, baseline_n)
            else:
                counts[i][j] = blend_n if blend_n is not None else baseline_n
            if skill is None:
                continue
            values[i][j] = skill
            scored.append(skill)
        if not scored:
            continue
        extent = _percentile(sorted(abs(v) for v in scored), SCALE_PERCENTILE)
        grids.append(
            BlendCellGrid(
                metric=metric,
                label=label,
                lats=lats,
                lons=lons,
                values=values,
                counts=counts,
                scale_max_abs=extent or None,
                value_min=min(scored),
                value_max=max(scored),
                clipped=(0 if not extent else sum(1 for v in scored if abs(v) > extent)),
            )
        )

    return BlendCellMetrics(
        job_id=job_id,
        baseline_model=_BASELINE_MODEL,
        # Both axes share a spacing on this grid; latitude is the safer read
        # because a region can span a single column of longitudes.
        cell_size_deg=_spacing(lats) or _spacing(lons),
        min_observations=min_observations,
        grids=grids,
    )


class BlendCellCoverage(BaseModel):
    """One metric's grid reduced to the numbers that fit in a sentence."""

    metric: str
    label: str
    points: int
    points_better: int
    share_better: float
    # Median rather than mean: skill is a ratio, and the same near-zero baselines
    # that force SCALE_PERCENTILE would drag a mean around by a handful of points.
    median_skill: float | None
    value_min: float | None
    value_max: float | None


def coverage_summary(metrics: BlendCellMetrics) -> list[BlendCellCoverage]:
    """Reduce each grid to a spatial claim that can be stated in one sentence.

    A grid runs to a thousand points per metric, which is worth drawing and not
    worth reading. The map's own caption makes exactly this claim — "the blend
    beats climatology at 593 of 1032 points" — so summarising here is what lets a
    reader be told the same thing the map shows, rather than a pooled average that
    hides whether the gain is everywhere or in one corner.
    """
    summaries: list[BlendCellCoverage] = []
    for grid in metrics.grids:
        scored = [value for row in grid.values for value in row if value is not None]
        if not scored:
            continue
        better = sum(1 for value in scored if value > 0)
        summaries.append(
            BlendCellCoverage(
                metric=grid.metric,
                label=grid.label,
                points=len(scored),
                points_better=better,
                share_better=better / len(scored),
                median_skill=_percentile(sorted(scored), 0.5),
                value_min=grid.value_min,
                value_max=grid.value_max,
            )
        )
    return summaries


def is_per_cell_summary(filename: str) -> bool:
    """True for the per-grid-point summary, not its pooled sibling.

    ``summary_models_`` prefixes both files, so the pooled one must be excluded
    explicitly or it matches first and yields a grid with no points.
    """
    return filename.startswith("summary_models_") and not filename.startswith(
        "summary_models_pooled"
    )
