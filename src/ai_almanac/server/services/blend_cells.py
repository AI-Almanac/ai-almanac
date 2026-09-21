"""Per-point blend skill, reshaped for the map.

The blend writes two summary CSVs with the same stem. ``summary_models_pooled_*``
holds one row per model pooled over the region and drives the skill table;
``summary_models_*`` holds one row per model *per point* plus a pooled ``ALL``
row, and nothing read it until this module. It is the only per-point skill the
blend produces, so it is the only way to answer where the blend beats
climatology rather than whether it does on average.

Per-point rows carry ``id``, ``brier``, ``rps``, ``auc`` and ``n``. On a lat/lon
domain the ``id`` is ``"{lat}_{lon}"`` and the ``lat``/``lon`` columns are empty,
so coordinates come from the id and the points form a regular grid. On an
administrative domain the ``id`` is the unit's name and the blend fills
``lat``/``lon`` with the unit's centroid; those points are irregular, so they are
returned as named areas for the map to join onto boundary polygons.
"""

from __future__ import annotations

import math
import re
from itertools import pairwise

from pydantic import BaseModel

# Grid ids look like "10.00_33.25". Latitudes are signed; longitudes may be too.
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

# A single point is scored on a few dozen point-years, so its skill is noisy.
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


class SkillScale(BaseModel):
    """The diverging ramp shared by every representation of one metric."""

    metric: str
    label: str
    # Symmetric extent of the diverging scale, so zero stays at its midpoint.
    # Clipped to SCALE_PERCENTILE rather than the true extreme.
    scale_max_abs: float | None
    # True extremes, so the legend can name what the ramp's ends leave out.
    value_min: float | None
    value_max: float | None
    # How many points fall outside the ramp and render at its ends.
    clipped: int


class BlendCellGrid(SkillScale):
    """One metric's per-point skill, indexed ``values[lat_index][lon_index]``."""

    lats: list[float]
    lons: list[float]
    values: list[list[float | None]]
    counts: list[list[int | None]]


class BlendAreaSkill(BaseModel):
    """Skill at one named administrative unit, located by its centroid."""

    id: str
    lat: float
    lon: float
    skill: float | None
    count: int | None


class BlendAreaMetric(SkillScale):
    """One metric's skill per named area, for domains that are not a grid."""

    areas: list[BlendAreaSkill]


class BlendCellMetrics(BaseModel):
    job_id: str
    baseline_model: str
    cell_size_deg: float | None
    min_observations: int
    # Exactly one of these is populated: grids for a lat/lon domain, areas for an
    # administrative one. The region lets the map fetch that domain's boundaries.
    grids: list[BlendCellGrid]
    areas: list[BlendAreaMetric] = []
    region_id: str | None = None


class _Cell(BaseModel):
    id: str
    lat: float
    lon: float
    named: bool
    values: dict[str, float]
    n: int | None


def _parse_float(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def _skill(value: float | None, reference: float | None) -> float | None:
    if value is None or reference is None or reference == 0:
        return None
    return 1 - value / reference


def _percentile(sorted_values: list[float], fraction: float) -> float | None:
    if not sorted_values:
        return None
    position = fraction * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _spacing(values: list[float]) -> float | None:
    """Smallest gap between adjacent coordinates — the grid's cell size."""
    if len(values) < 2:
        return None
    gaps = [b - a for a, b in pairwise(values) if b > a]
    return min(gaps) if gaps else None


def _locate(
    cell_id: str, lat_field: str | None, lon_field: str | None
) -> tuple[float, float, bool] | None:
    """Coordinates for a row: from a grid id, else from its centroid columns."""
    match = _CELL_ID.match(cell_id)
    if match is not None:
        return float(match.group(1)), float(match.group(2)), False
    lat, lon = _parse_float(lat_field), _parse_float(lon_field)
    if lat is None or lon is None:
        return None
    return lat, lon, True


def _cells_by_model(csv_text: str) -> dict[str, dict[str, _Cell]]:
    """Index the per-point rows of a per-cell summary CSV by model, then id."""
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

    by_model: dict[str, dict[str, _Cell]] = {}
    for line in lines[1:]:
        cells = line.split(",")
        cell_id = (field(cells, "id") or "").strip()
        model = (field(cells, "model") or "").strip()
        if not model or cell_id == _POOLED_ROW_ID:
            continue
        located = _locate(cell_id, field(cells, "lat"), field(cells, "lon"))
        if located is None:
            continue
        lat, lon, named = located
        observations = _parse_float(field(cells, "n"))
        values = {}
        for column, _ in _SKILL_METRICS.values():
            parsed = _parse_float(field(cells, column))
            if parsed is not None:
                values[column] = parsed
        by_model.setdefault(model, {})[cell_id] = _Cell(
            id=cell_id,
            lat=lat,
            lon=lon,
            named=named,
            values=values,
            n=None if observations is None else int(observations),
        )
    return by_model


class _Scored(BaseModel):
    cell: _Cell
    skill: float | None
    count: int | None


def _score(blend: dict[str, _Cell], baseline: dict[str, _Cell], column: str) -> list[_Scored]:
    """Skill and observation count at every point both models scored."""
    scored: list[_Scored] = []
    for cell_id in sorted(set(blend) & set(baseline)):
        cell, reference = blend[cell_id], baseline[cell_id]
        if cell.n is not None and reference.n is not None:
            count: int | None = min(cell.n, reference.n)
        else:
            count = cell.n if cell.n is not None else reference.n
        skill = _skill(cell.values.get(column), reference.values.get(column))
        scored.append(_Scored(cell=cell, skill=skill, count=count))
    return scored


def _scale(metric: str, label: str, skills: list[float]) -> SkillScale:
    extent = _percentile(sorted(abs(v) for v in skills), SCALE_PERCENTILE)
    return SkillScale(
        metric=metric,
        label=label,
        scale_max_abs=extent or None,
        value_min=min(skills),
        value_max=max(skills),
        clipped=(0 if not extent else sum(1 for v in skills if abs(v) > extent)),
    )


def _grid(scale: SkillScale, scored: list[_Scored]) -> BlendCellGrid:
    lats = sorted({s.cell.lat for s in scored})
    lons = sorted({s.cell.lon for s in scored})
    lat_index = {lat: i for i, lat in enumerate(lats)}
    lon_index = {lon: j for j, lon in enumerate(lons)}
    values: list[list[float | None]] = [[None] * len(lons) for _ in lats]
    counts: list[list[int | None]] = [[None] * len(lons) for _ in lats]
    for s in scored:
        i, j = lat_index[s.cell.lat], lon_index[s.cell.lon]
        values[i][j] = s.skill
        counts[i][j] = s.count
    return BlendCellGrid(**scale.model_dump(), lats=lats, lons=lons, values=values, counts=counts)


def _areas(scale: SkillScale, scored: list[_Scored]) -> BlendAreaMetric:
    return BlendAreaMetric(
        **scale.model_dump(),
        areas=[
            BlendAreaSkill(
                id=s.cell.id, lat=s.cell.lat, lon=s.cell.lon, skill=s.skill, count=s.count
            )
            for s in scored
        ],
    )


def build_cell_metrics(
    job_id: str,
    csv_text: str,
    min_observations: int = DEFAULT_MIN_OBSERVATIONS,
    region_id: str | None = None,
) -> BlendCellMetrics:
    """Reshape a per-cell summary CSV into per-metric skill for the map.

    Skill is the blend against ``unc_clim_raw``, matching the pooled table, so a
    point reads on the same scale in both places: zero is climatology, positive
    beats it.
    """
    by_model = _cells_by_model(csv_text)
    blend = by_model.get(_BLEND_MODEL, {})
    baseline = by_model.get(_BASELINE_MODEL, {})
    result = BlendCellMetrics(
        job_id=job_id,
        baseline_model=_BASELINE_MODEL,
        cell_size_deg=None,
        min_observations=min_observations,
        grids=[],
        region_id=region_id,
    )
    if not blend or not baseline:
        return result

    named = any(cell.named for cell in blend.values())
    for metric, (column, label) in _SKILL_METRICS.items():
        scored = _score(blend, baseline, column)
        skills = [s.skill for s in scored if s.skill is not None]
        if not skills:
            continue
        scale = _scale(metric, label, skills)
        if named:
            result.areas.append(_areas(scale, scored))
        else:
            result.grids.append(_grid(scale, scored))

    if result.grids:
        # Both axes share a spacing on this grid; latitude is the safer read
        # because a region can span a single column of longitudes.
        first = result.grids[0]
        result.cell_size_deg = _spacing(first.lats) or _spacing(first.lons)
    return result


def scored_values(layer: BlendCellGrid | BlendAreaMetric) -> list[float]:
    """Every non-missing skill value, whichever shape the metric came in."""
    if isinstance(layer, BlendCellGrid):
        return [value for row in layer.values for value in row if value is not None]
    return [area.skill for area in layer.areas if area.skill is not None]


class BlendCellCoverage(BaseModel):
    """One metric's map reduced to the numbers that fit in a sentence."""

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
    """Reduce each metric to a spatial claim that can be stated in one sentence.

    A grid runs to a thousand points per metric, which is worth drawing and not
    worth reading. The map's own caption makes exactly this claim — "the blend
    beats climatology at 593 of 1032 points" — so summarising here is what lets a
    reader be told the same thing the map shows, rather than a pooled average that
    hides whether the gain is everywhere or in one corner.
    """
    summaries: list[BlendCellCoverage] = []
    for layer in [*metrics.grids, *metrics.areas]:
        scored = scored_values(layer)
        if not scored:
            continue
        better = sum(1 for value in scored if value > 0)
        summaries.append(
            BlendCellCoverage(
                metric=layer.metric,
                label=layer.label,
                points=len(scored),
                points_better=better,
                share_better=better / len(scored),
                median_skill=_percentile(sorted(scored), 0.5),
                value_min=layer.value_min,
                value_max=layer.value_max,
            )
        )
    return summaries


def is_per_cell_summary(filename: str) -> bool:
    """True for the per-point summary, not its pooled sibling.

    ``summary_models_`` prefixes both files, so the pooled one must be excluded
    explicitly or it matches first and yields a grid with no points.
    """
    return filename.startswith("summary_models_") and not filename.startswith(
        "summary_models_pooled"
    )
