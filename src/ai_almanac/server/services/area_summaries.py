"""Onset probabilities summarized over named administrative areas.

A blend forecast holds probabilities per grid cell (ids ``"lat_lon"``) or, where the
blend runs on administrative units, per named unit point. An area's value is the mean
over the cells or points that stand for it. A grid cell is weighted by how much of the
area it covers, measured on a lattice finer than the grid, so a small zone is not decided
by whichever cell centre happens to fall inside it.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from .blend_forecast import DAILY_COLUMNS, WEEKLY_COLUMNS
from .boundaries import interior_point, normalize_area_name, select_units

if TYPE_CHECKING:
    from .chat_tools import RegionAreas
from .focus_area import points_inside, polygons_of

# Each grid cell is sampled on an n x n lattice to measure how much of an area it covers.
SAMPLES_PER_CELL = 8
DEFAULT_WITHIN_DAYS = (7, 14, 28)


@dataclass(frozen=True)
class Window:
    """One onset window: its name and the last lead day it covers (None for "later")."""

    name: str
    last_day: int | None


@dataclass(frozen=True)
class ForecastTable:
    """Onset probabilities per point, issue date and window. NaN where a point has no
    row for an issue date, so a missing forecast is not read as "no chance"."""

    issue_dates: tuple[str, ...]
    windows: tuple[Window, ...]
    ids: tuple[str, ...]
    lat: np.ndarray
    lon: np.ndarray
    probs: np.ndarray
    cell_size: tuple[float, float] | None
    # Model runs behind each row: 1 for one deterministic run, whose "probabilities" are
    # 0 or 1; None when the table does not say (a blend's calibrated probabilities).
    members: int | None = None


@dataclass(frozen=True)
class Area:
    name: str
    parent: str | None
    geometry: dict


def picked_areas(found: RegionAreas, names: list[str] | None) -> list[Area]:
    """The areas named, or every area found, with their outlines and parent areas."""
    parent_of = {area["name"]: area.get("parent") for area in found.areas}
    chosen = select_units(found.units, names or list(parent_of))["features"]
    if outside := [
        f["properties"]["name"] for f in chosen if f["properties"]["name"] not in parent_of
    ]:
        raise ValueError(f"Not among the areas asked for: {', '.join(outside)}")
    return [
        Area(f["properties"]["name"], parent_of[f["properties"]["name"]], f["geometry"])
        for f in chosen
    ]


@dataclass(frozen=True)
class AreaWeights:
    """Which points stand for an area, and the share of the area each one does."""

    area: Area
    points: np.ndarray
    weights: np.ndarray
    coverage: float | None


def parse_forecast_table(text: str) -> ForecastTable:
    reader = csv.DictReader(io.StringIO(text))
    columns, windows = _probability_columns(reader.fieldnames or [])
    by_point: dict[str, dict[str, list[float]]] = {}
    coords: dict[str, tuple[float, float]] = {}
    issue_dates: dict[str, None] = {}
    members: set[int] = set()
    for row in reader:
        point, issue_date = row["id"], row["time"]
        issue_dates[issue_date] = None
        by_point.setdefault(point, {})[issue_date] = [float(row[c] or "nan") for c in columns]
        coords.setdefault(point, _point_coords(point, row))
        if row.get("members"):
            members.add(int(row["members"]))
    if not by_point:
        raise ValueError("The forecast table has no rows")
    dates = tuple(sorted(issue_dates))
    missing = [math.nan] * len(columns)
    probs = np.array([[series.get(d, missing) for d in dates] for series in by_point.values()])
    ids = tuple(by_point)
    lat = np.array([coords[point][0] for point in ids])
    lon = np.array([coords[point][1] for point in ids])
    cell_size = _grid_step(lat, lon) if all(map(_is_cell_id, ids)) else None
    runs = min(members) if members else None
    return ForecastTable(dates, windows, ids, lat, lon, probs, cell_size, runs)


def _probability_columns(header: Sequence[str]) -> tuple[tuple[str, ...], tuple[Window, ...]]:
    if set(WEEKLY_COLUMNS) <= set(header):
        weeks = [Window(f"week{n}", 7 * n) for n in range(1, 5)]
        return WEEKLY_COLUMNS, (*weeks, Window("later", None))
    if set(DAILY_COLUMNS) <= set(header):
        days = [Window(f"day_{n}", n) for n in range(1, 29)]
        return DAILY_COLUMNS, (*days, Window("later", None))
    raise ValueError(
        "This table holds no onset probabilities: expected weekly columns "
        "(cv_week1 to cv_week4, cv_later) or daily ones (p_day_1 to p_day_28, p_later)"
    )


def _point_coords(point: str, row: dict[str, str]) -> tuple[float, float]:
    if row.get("lat") and row.get("lon"):
        return float(row["lat"]), float(row["lon"])
    lat, _, lon = point.partition("_")
    return float(lat), float(lon)


def _is_cell_id(point: str) -> bool:
    lat, _, lon = point.partition("_")
    try:
        float(lat), float(lon)
    except ValueError:
        return False
    return True


def _grid_step(lat: np.ndarray, lon: np.ndarray) -> tuple[float, float] | None:
    """The grid's (lat, lon) spacing. A grid one cell across along an axis takes the other
    axis's spacing, as the forecast grids are square; a single cell has none."""
    dlat, dlon = (_spacing(axis) for axis in (lat, lon))
    if dlat is None and dlon is None:
        return None
    return dlat or dlon, dlon or dlat


def _spacing(axis: np.ndarray) -> float | None:
    steps = np.diff(np.unique(axis))
    return float(steps.min()) if steps.size else None


def within_days_errors(table: ForecastTable, within_days: Sequence[int]) -> list[str]:
    edges = [w.last_day for w in table.windows if w.last_day is not None]
    off = [n for n in within_days if n not in edges]
    if not off:
        return []
    return [
        f"These probabilities come in windows ending on lead days {_list(edges)}, so they "
        f"cannot say the chance of onset within {_list(off)} days"
    ]


def _list(values: Sequence[int]) -> str:
    return ", ".join(map(str, values)) if len(values) < 8 else f"{values[0]} to {values[-1]}"


def area_weights(table: ForecastTable, areas: Sequence[Area]) -> list[AreaWeights]:
    weigh = _cell_weights if table.cell_size else _point_weights
    return [weigh(table, area) for area in areas]


@dataclass(frozen=True)
class _Grid:
    """A regular lat/lon grid: the centre of one cell and the spacing between them."""

    lat0: float
    lon0: float
    dlat: float
    dlon: float

    def cell(self, lat: float, lon: float) -> tuple[int, int]:
        return round((lat - self.lat0) / self.dlat), round((lon - self.lon0) / self.dlon)

    def samples(self, geometry: dict) -> tuple[np.ndarray, np.ndarray]:
        """Points inside the area on a lattice SAMPLES_PER_CELL times finer than the grid,
        on sub-cell centres, so every cell holds the same number of them."""
        rings = [np.asarray(r, dtype=float) for rings in polygons_of(geometry) for r in rings]
        if not rings:
            return np.empty(0), np.empty(0)
        stacked = np.concatenate(rings)
        (west, south), (east, north) = stacked.min(axis=0), stacked.max(axis=0)
        lats = _lattice(south, north, self.lat0, self.dlat)
        lons = _lattice(west, east, self.lon0, self.dlon)
        grid_lat, grid_lon = np.meshgrid(lats, lons, indexing="ij")
        inside = points_inside(geometry, grid_lon, grid_lat)
        return grid_lat[inside], grid_lon[inside]


def _lattice(low: float, high: float, centre: float, step: float) -> np.ndarray:
    sub = step / SAMPLES_PER_CELL
    edge = centre - step / 2
    first = math.floor((low - edge) / sub)
    last = math.ceil((high - edge) / sub)
    return edge + (np.arange(first, last) + 0.5) * sub


def _cell_weights(table: ForecastTable, area: Area) -> AreaWeights:
    """Overlap weights: the share of the area's sample points that fall in each cell,
    each sample weighted by cos(lat) so cells keep their true relative area."""
    dlat, dlon = table.cell_size or (0.0, 0.0)
    grid = _Grid(float(table.lat.min()), float(table.lon.min()), dlat, dlon)
    cell_of = {
        grid.cell(lat, lon): index
        for index, (lat, lon) in enumerate(zip(table.lat, table.lon, strict=True))
    }
    sample_lat, sample_lon = grid.samples(area.geometry)
    if sample_lat.size == 0:
        return _nearest_cell(area, grid, cell_of)
    cells = np.array(
        [
            cell_of.get(grid.cell(lat, lon), -1)
            for lat, lon in zip(sample_lat, sample_lon, strict=True)
        ]
    )
    area_weight = np.cos(np.radians(sample_lat))
    covered = cells >= 0
    per_cell = np.bincount(cells[covered], weights=area_weight[covered], minlength=len(table.ids))
    points = np.flatnonzero(per_cell)
    total = per_cell.sum()
    coverage = float(total / area_weight.sum())
    weights = per_cell[points] / total if total else per_cell[points]
    return AreaWeights(area, points, weights, coverage)


def _nearest_cell(area: Area, grid: _Grid, cell_of: dict[tuple[int, int], int]) -> AreaWeights:
    """An area smaller than the sampling lattice takes the cell its interior point is in."""
    lon, lat = interior_point(area.geometry)
    index = None if math.isnan(lat) else cell_of.get(grid.cell(lat, lon))
    if index is None:
        return AreaWeights(area, np.empty(0, dtype=int), np.empty(0), 0.0)
    return AreaWeights(area, np.array([index]), np.array([1.0]), 1.0)


def _point_weights(table: ForecastTable, area: Area) -> AreaWeights:
    """A named point stands for the area of the same name; otherwise every point inside
    the area counts equally."""
    named = [i for i, point in enumerate(table.ids) if _same_name(point, area.name)]
    if named:
        points = np.array(named[:1])
    else:
        points = np.flatnonzero(points_inside(area.geometry, table.lon, table.lat))
    weights = np.full(points.size, 1.0 / points.size) if points.size else np.empty(0)
    return AreaWeights(area, points, weights, None)


def _same_name(point: str, name: str) -> bool:
    return normalize_area_name(point) == normalize_area_name(name)


def summarize_areas(
    table: ForecastTable, weighted: Sequence[AreaWeights], within_days: Sequence[int]
) -> list[dict]:
    """One row per area and issue date with a forecast there. Points without a forecast
    on a date drop out of that date's mean rather than counting as no chance."""
    rows = []
    for area in weighted:
        if area.points.size == 0:
            continue
        probs = table.probs[area.points]
        for d, issue_date in enumerate(table.issue_dates):
            row = _area_row(table, area, probs[:, d, :], within_days)
            if row is not None:
                rows.append({"issue_date": issue_date, **row})
    return rows


def _area_row(
    table: ForecastTable, area: AreaWeights, probs: np.ndarray, within_days: Sequence[int]
) -> dict | None:
    present = ~np.isnan(probs).any(axis=1)
    if not present.any():
        return None
    weights = area.weights[present] / area.weights[present].sum()
    probs = probs[present]
    mean = weights @ probs
    share = np.bincount(probs.argmax(axis=1), weights=weights, minlength=len(table.windows))
    likeliest = int(share.argmax())
    return {
        "area": area.area.name,
        "parent": area.area.parent,
        "points": int(present.sum()),
        "coverage": None if area.coverage is None else round(area.coverage, 3),
        **{f"p_{w.name}": _rounded(p) for w, p in zip(table.windows, mean, strict=True)},
        **{f"p_within_{n}d": _rounded(_chance_within(table, mean, n)) for n in within_days},
        "most_likely": table.windows[likeliest].name,
        "most_likely_share": _rounded(share[likeliest]),
    }


def _chance_within(table: ForecastTable, mean: np.ndarray, days: int) -> float:
    return float(
        sum(
            p
            for w, p in zip(table.windows, mean, strict=True)
            if w.last_day is not None and w.last_day <= days
        )
    )


def _rounded(value: float) -> float:
    return round(float(value), 3)


def unsummarized(weighted: Sequence[AreaWeights]) -> list[str]:
    """Areas no forecast point stands for, e.g. outside the forecast's domain."""
    return [area.area.name for area in weighted if area.points.size == 0]


def table_csv(rows: Sequence[dict]) -> str:
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def boundary_document(areas: Sequence[Area], source: dict) -> str:
    """The outlines a summary used, as GeoJSON naming the geoBoundaries release. Written
    byte-for-byte the same each time, so the ledger keeps one copy per region and level."""
    collection = {
        "type": "FeatureCollection",
        "source": source,
        "features": [
            {
                "type": "Feature",
                "properties": {"name": area.name, "parent": area.parent},
                "geometry": area.geometry,
            }
            for area in areas
        ],
    }
    return json.dumps(collection, sort_keys=True, separators=(",", ":"))
