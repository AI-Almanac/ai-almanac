"""A benchmark's gridded skill, averaged over named administrative areas.

A benchmark writes one skill grid per model and lead window, and one per window for the
climatology baseline. An area's value is the mean over the grid cells whose centres fall
inside its outline; the number of those cells is reported with it, so a zone resting on
two cells is not read with the confidence of one resting on forty.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel

from .area_summaries import Area
from .focus_area import cells_inside_shape
from .run_summaries import FINISHED
from .skill_files import SkillFileName, window_start
from .workspace_display import model_display_name

# Table column -> the variable holding it in a skill grid.
METRIC_VARIABLES = {
    "onset_error_days": "mean_mae",
    "false_alarm_rate": "false_alarm_rate",
    "miss_rate": "miss_rate",
}
CLIMATOLOGY = "climatology"
# The most areas one table may hold: every district of a large country, with room to
# spare. Past it a request is asked to narrow down rather than left to run.
MAX_AREAS = 1000
SKILL_MAPS_UNREADABLE = "This run's skill maps could not be read."
_NOT_FINISHED_REASONS = {
    "failed": "its run failed",
    "canceled": "its run was cancelled",
    "canceling": "its run was cancelled",
}
_STILL_RUNNING = "its run has not finished"
_NO_SKILL_MAPS = "its run wrote no skill maps"


class SkillByAreaError(ValueError):
    """Why a run's skill cannot be summarized by area, in words written for the user."""


class SkillMapsUnreadable(Exception):
    """A skill file could not be opened or parsed. Carries no detail: the cause names
    server paths, so it is logged where the file is read and never shown."""

    def __init__(self) -> None:
        super().__init__(SKILL_MAPS_UNREADABLE)


def check_area_count(count: int) -> None:
    if count > MAX_AREAS:
        raise SkillByAreaError(
            f"That is {count} areas, more than the {MAX_AREAS} one table can hold. "
            "Narrow it to the areas inside one larger area, or name the areas wanted."
        )


_DECIMALS = 3


@dataclass(frozen=True)
class SkillGrid:
    """Named metrics on a regular lat/lon grid, each shaped (lat, lon); NaN where a cell
    was not scored."""

    lat: np.ndarray
    lon: np.ndarray
    metrics: Mapping[str, np.ndarray]


@dataclass(frozen=True)
class AreaMeans:
    """One area's mean of each metric, and how many cells those means rest on."""

    area: Area
    cells: int
    means: Mapping[str, float | None]


@dataclass(frozen=True)
class RunGrid:
    """One skill grid of a run: which model it scores and over which lead days."""

    model: str
    window: str
    grid: SkillGrid

    @property
    def is_baseline(self) -> bool:
        return self.model.lower() == CLIMATOLOGY


def parse_skill_grid(dataset: Any, variables: Iterable[str] | None = None) -> SkillGrid:
    """The (lat, lon) variables of an opened skill file, only those named when
    ``variables`` is given. Longitudes are brought to -180..180, the convention area
    outlines use."""
    if "lat" not in dataset.coords or "lon" not in dataset.coords:
        raise ValueError("This file is not a lat/lon grid")
    wanted = None if variables is None else set(variables)
    metrics = {
        str(name): variable.transpose("lat", "lon").values.astype(float)
        for name, variable in dataset.data_vars.items()
        if set(variable.dims) == {"lat", "lon"} and (wanted is None or name in wanted)
    }
    lon = (np.asarray(dataset["lon"].values, dtype=float) + 180.0) % 360.0 - 180.0
    return SkillGrid(np.asarray(dataset["lat"].values, dtype=float), lon, metrics)


def grid_identity(name: SkillFileName | None, attrs: Mapping[str, Any]) -> tuple[str, str] | None:
    """The (model, lead window) a skill file holds, from what the benchmark wrote into
    it or else from its name; None for a file that is not a skill grid."""
    model = str(attrs.get("model") or (name.model if name else ""))
    window = str(attrs.get("verification_window") or (name.window if name else ""))
    return (model, window.replace(",", "-")) if model and window else None


def cell_masks(lat: np.ndarray, lon: np.ndarray, areas: Sequence[Area]) -> np.ndarray:
    """For each area, which (lat, lon) cells have their centre inside it. Even-odd ray
    casting, so holes are cut out and the parts of a multipolygon add up."""
    return np.array([cells_inside_shape(area.geometry, lat, lon) for area in areas]).reshape(
        len(areas), lat.size, lon.size
    )


def area_means(
    grid: SkillGrid, areas: Sequence[Area], masks: np.ndarray | None = None
) -> list[AreaMeans]:
    """Each area's mean of each metric over the scored cells centred inside it. An area
    holding no scored cell has no value for any metric, never zero. ``masks`` are the
    areas' cell_masks on this grid, for callers summarizing many grids that share one."""
    if masks is None:
        masks = cell_masks(grid.lat, grid.lon, areas)
    scored = _scored_cells(grid)
    return [
        AreaMeans(
            area,
            int((inside & scored).sum()),
            {name: _mean(values[inside]) for name, values in grid.metrics.items()},
        )
        for area, inside in zip(areas, masks, strict=True)
    ]


def _scored_cells(grid: SkillGrid) -> np.ndarray:
    scored = np.zeros((grid.lat.size, grid.lon.size), dtype=bool)
    for values in grid.metrics.values():
        scored |= np.isfinite(values)
    return scored


def _mean(values: np.ndarray) -> float | None:
    finite = values[np.isfinite(values)]
    return float(finite.mean()) if finite.size else None


class AreaSkill(BaseModel):
    """Skill averaged over one area. Values are null when no scored cell is centred in it."""

    onset_error_days: float | None
    false_alarm_rate: float | None
    miss_rate: float | None
    cells: int


class AreaSkillRow(AreaSkill):
    area: str
    parent: str | None
    model: str
    window: str
    # The climatology baseline over the same area and lead window, where the run has one.
    climatology: AreaSkill | None


class ModelNotShown(BaseModel):
    """A model of the run that the table has no rows for, and why."""

    model: str
    reason: str


def models_not_shown(
    jobs: Sequence[Mapping[str, Any]], shown: Iterable[str]
) -> list[ModelNotShown]:
    """The run's models missing from its table: one whose job failed, was cancelled or
    is still running, and one that finished without writing skill maps. ``jobs`` carry
    ``status`` and ``config``; ``shown`` are the models the table has rows for."""
    in_table = {name.lower() for name in shown}
    missing: dict[str, ModelNotShown] = {}
    for job in jobs:
        config = job.get("config") or {}
        model = model_display_name(config)
        if not model or str(model).lower() in in_table:
            continue
        reason = (
            _NO_SKILL_MAPS
            if job.get("status") == FINISHED
            else _NOT_FINISHED_REASONS.get(str(job.get("status")), _STILL_RUNNING)
        )
        missing.setdefault(str(model).lower(), ModelNotShown(model=str(model), reason=reason))
    return list(missing.values())


class SkillByArea(BaseModel):
    """A benchmark run's skill per area, model and lead window."""

    # Which areas were summarized: the run's own area of interest, or every area of
    # ``level`` asked for within its region.
    areas_from: Literal["area_of_interest", "boundaries"]
    level: Literal["adm1", "adm2", "adm3"]
    models: list[str]
    windows: list[str]
    rows: list[AreaSkillRow]
    # Areas no scored cell is centred in: outside what the run scored, or smaller than a cell.
    not_covered: list[str]
    # Models the run was asked to score that have no rows here, each with the reason.
    not_shown: list[ModelNotShown] = []


def _area_skill(means: AreaMeans) -> AreaSkill:
    values = {column: means.means.get(variable) for column, variable in METRIC_VARIABLES.items()}
    return AreaSkill(
        **{k: None if v is None else round(v, _DECIMALS) for k, v in values.items()},
        cells=means.cells,
    )


class _MaskCache:
    """Area masks per distinct grid, since a run's grids nearly always share one."""

    def __init__(self, areas: Sequence[Area]) -> None:
        self._areas = areas
        self._masks: dict[tuple[bytes, bytes], np.ndarray] = {}

    def means(self, grid: SkillGrid) -> list[AreaMeans]:
        key = (grid.lat.tobytes(), grid.lon.tobytes())
        if key not in self._masks:
            self._masks[key] = cell_masks(grid.lat, grid.lon, self._areas)
        return area_means(grid, self._areas, self._masks[key])


def skill_by_area(
    areas: Sequence[Area],
    grids: Sequence[RunGrid],
    *,
    areas_from: Literal["area_of_interest", "boundaries"],
    level: Literal["adm1", "adm2", "adm3"],
) -> SkillByArea:
    """The table: one row per area, model and lead window with the baseline alongside,
    ordered by area, then lead window, then model. Areas with no scored cell under any
    model are listed apart instead of filling the table with empty rows."""
    cache = _MaskCache(areas)
    scored = sorted(
        (grid for grid in grids if not grid.is_baseline),
        key=lambda grid: (window_start(grid.window), grid.model),
    )
    baselines: dict[str, list[AreaMeans]] = {}
    for grid in grids:
        if grid.is_baseline:
            baselines.setdefault(grid.window, cache.means(grid.grid))
    per_grid = [(grid, cache.means(grid.grid)) for grid in scored]
    covered = [any(means[i].cells for _, means in per_grid) for i in range(len(areas))]
    rows = [
        AreaSkillRow(
            **_area_skill(means[i]).model_dump(),
            area=area.name,
            parent=area.parent,
            model=grid.model,
            window=grid.window,
            climatology=(
                _area_skill(baselines[grid.window][i]) if grid.window in baselines else None
            ),
        )
        for i, area in enumerate(areas)
        if covered[i]
        for grid, means in per_grid
    ]
    return SkillByArea(
        areas_from=areas_from,
        level=level,
        models=sorted({grid.model for grid in scored}),
        windows=list(dict.fromkeys(grid.window for grid in scored)),
        rows=rows,
        not_covered=[
            area.name for area, has_cells in zip(areas, covered, strict=True) if not has_cells
        ],
    )
