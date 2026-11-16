"""A workspace benchmark run's skill by administrative area: the shell around
``skill_area_summaries`` that finds the run's jobs, names its areas and reads its
skill grids. Shared by the workspace page and the assistant's tool.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

import sqlalchemy as sa

from ai_almanac.server import tables

from . import area_summaries, boundaries, run_summaries, skill_area_summaries, workspaces
from .chat_tools import load_region_areas
from .focus_area import FocusUnits, country_of, outline_features
from .skill_files import SkillFileName
from .workspace_display import display_model

logger = logging.getLogger(__name__)


class RunNotFound(skill_area_summaries.SkillByAreaError):
    """The workspace holds no benchmark run by this key."""


# The level a run that covered its whole region is summarized at when none is asked for.
_WHOLE_REGION_LEVEL: boundaries.AreaLevel = "adm1"
AreasFrom = Literal["area_of_interest", "boundaries"]


async def summarize_skill_by_area(
    workspace_id: str,
    run: str,
    *,
    level: boundaries.AreaLevel | None = None,
    within: str | None = None,
    areas: list[str] | None = None,
) -> skill_area_summaries.SkillByArea:
    """A benchmark run's skill by administrative area: for each area, model and lead
    window, the mean onset error in days, false alarm rate and miss rate over the grid
    cells centred in the area, the number of those cells, and the climatology baseline
    over the same cells.

    ``run`` is the run's key in the workspace: the ``run_id`` its jobs share, or the id
    of a job submitted on its own. With no ``level``, ``within`` or ``areas`` the areas
    are the run's own area of interest, exactly as it was scored; a run that covered
    its whole region is summarized by state or region (adm1). Otherwise the areas are
    those of ``level`` in the run's region, named as ``list_areas`` names them, narrowed
    to ``areas`` or to everything inside ``within``.

    Raises RunNotFound when the workspace has no such benchmark run; SkillByAreaError,
    worded for the user, when the run has not finished, its areas cannot be named or
    there are too many of them; and SkillMapsUnreadable, which says no more than that,
    when a skill file cannot be read.
    """
    unavailable = skill_area_summaries.SkillByAreaError
    jobs = await _benchmark_run_jobs(workspace_id, run)
    if not jobs:
        raise RunNotFound("This workspace has no such benchmark run")
    finished = [job for job in jobs if job["status"] == run_summaries.FINISHED]
    if not finished:
        raise unavailable("This benchmark has not finished, so it has no skill by area yet")
    try:
        picked, areas_from, used_level = await _skill_areas(
            finished[0]["config"], level, within, areas
        )
    except ValueError as exc:  # our own wording: an unknown region, level or area name
        raise unavailable(str(exc)) from exc
    skill_area_summaries.check_area_count(len(picked))
    grids = await asyncio.to_thread(_run_skill_grids, finished)
    if not grids:
        raise unavailable("This benchmark wrote no skill maps to summarize by area")
    table = await asyncio.to_thread(
        skill_area_summaries.skill_by_area, picked, grids, areas_from=areas_from, level=used_level
    )
    not_shown = skill_area_summaries.models_not_shown(jobs, table.models)
    return table.model_copy(update={"not_shown": not_shown})


async def _benchmark_run_jobs(workspace_id: str, run: str) -> list[dict]:
    """The workspace's benchmark jobs submitted together as ``run``, one per model,
    oldest first."""
    jobs = tables.jobs
    return await workspaces.job_rows(
        sa.and_(
            workspaces.in_workspace(workspace_id),
            jobs.c.job_type == "benchmark",
            sa.or_(jobs.c.run_id == run, sa.and_(jobs.c.run_id.is_(None), jobs.c.id == run)),
        ),
        oldest_first=True,
    )


def _run_focus_units(config: dict) -> FocusUnits | None:
    """The named areas a run was limited to; None for a box or the whole region."""
    area = run_summaries.focus_area_of(config)
    return area if isinstance(area, FocusUnits) else None


def _scored_areas(focus: FocusUnits) -> list[area_summaries.Area]:
    """The outlines frozen into the run at submission: what it was actually scored on.
    Empty when any outline has no name or no shape to go by, so the areas are looked
    up by the run's area names instead of some being silently dropped."""
    features = outline_features(focus)
    named = [
        area_summaries.Area(str(name), None, feature["geometry"])
        for feature in features
        if isinstance(feature, Mapping)
        and (name := (feature.get("properties") or {}).get("name"))
        and feature.get("geometry")
    ]
    return named if len(named) == len(features) else []


async def _skill_areas(
    config: dict, level: boundaries.AreaLevel | None, within: str | None, names: list[str] | None
) -> tuple[list[area_summaries.Area], AreasFrom, boundaries.AreaLevel]:
    focus = _run_focus_units(config)
    areas_from: AreasFrom = "boundaries"
    if focus and level is None and within is None and names is None:
        areas_from = "area_of_interest"
        if scored := _scored_areas(focus):
            return scored, areas_from, focus.level
        names = focus.units
    used_level = level or (focus.level if focus else _WHOLE_REGION_LEVEL)
    country = country_of(focus)
    if not config.get("region_id") and not country:
        raise ValueError("This run has no region or country whose areas could be named")
    found = await load_region_areas(config.get("region_id"), used_level, within, country)
    return area_summaries.picked_areas(found, names), areas_from, used_level


def _run_skill_grids(jobs: list[dict]) -> list[skill_area_summaries.RunGrid]:
    """Every skill grid the run's jobs wrote, the baseline once per lead window however
    many of the jobs wrote it. Files are read whole through the storage's NetCDF reader,
    which holds the server's one HDF5 lock."""
    from .storage import get_storage

    storage = get_storage()
    grids: dict[tuple[str, str], skill_area_summaries.RunGrid] = {}
    for job in jobs:
        for path in storage.list_nc_output_files(job["id"]):
            grid = _skill_grid(storage, path, job["config"])
            if grid is not None:
                grids.setdefault((grid.model.lower(), grid.window), grid)
    return list(grids.values())


def _skill_grid(storage, path: object, config: dict) -> skill_area_summaries.RunGrid | None:
    name = SkillFileName.parse(Path(str(path)).name)
    if name is None or name.e2s:
        return None
    try:
        dataset = storage.open_nc_dataset(path)
        identity = skill_area_summaries.grid_identity(name, dataset.attrs)
        grid = skill_area_summaries.parse_skill_grid(
            dataset, skill_area_summaries.METRIC_VARIABLES.values()
        )
    except (OSError, ValueError, KeyError) as exc:
        # The cause names the file's path, which holds the job id: log it, show nothing.
        logger.warning("Could not read skill file %s", path, exc_info=True)
        raise skill_area_summaries.SkillMapsUnreadable from exc
    if identity is None or not grid.metrics:
        return None
    model, window = identity
    return skill_area_summaries.RunGrid(_scored_model(model, config), window, grid)


def _scored_model(model: str, config: dict) -> str:
    """The name the model goes by in the product. Climatology keeps the name the skill
    files give it, which is how the baseline is told apart."""
    if model.lower() == skill_area_summaries.CLIMATOLOGY:
        return model
    return display_model(model, config)
