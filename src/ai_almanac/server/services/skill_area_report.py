"""A benchmark's skill by area as the assistant quotes it and as the file a user opens.

Both are the same flat table: one row per area, model and lead window, with the
number of grid cells behind each value and the climatology baseline alongside.
"""

from __future__ import annotations

from . import area_summaries
from .skill_area_summaries import AreaSkill, AreaSkillRow, SkillByArea

_METRICS = ("onset_error_days", "false_alarm_rate", "miss_rate")
COLUMNS = (
    "area",
    "parent",
    "model",
    "window",
    "cells",
    *_METRICS,
    *(f"climatology_{metric}" for metric in _METRICS),
)

# Rows returned inline; the table file holds all of them.
INLINE_ROWS = 120

READING = (
    "Each value is the mean over `cells` grid cells centred in the area, with the "
    "climatology baseline over the same cells. An area with one or two cells is close "
    "to a point value: say how many cells it rests on. Areas in `not_covered` hold no "
    "scored cell, so they have no value at all. Models in `not_shown` belong to this "
    "run but have no rows: say which are missing and why rather than passing over them."
)


def _baseline(climatology: AreaSkill | None, metric: str) -> float | None:
    return None if climatology is None else getattr(climatology, metric)


def flat_row(row: AreaSkillRow) -> dict:
    return {
        "area": row.area,
        "parent": row.parent,
        "model": row.model,
        "window": row.window,
        "cells": row.cells,
        **{metric: getattr(row, metric) for metric in _METRICS},
        **{f"climatology_{metric}": _baseline(row.climatology, metric) for metric in _METRICS},
    }


def table_csv(table: SkillByArea) -> str:
    return area_summaries.table_csv([flat_row(row) for row in table.rows])


def quotable(table: SkillByArea) -> dict:
    """The table as a tool result: column names once, then one short list per row."""
    rows = [list(flat_row(row).values()) for row in table.rows]
    return {
        "areas_from": table.areas_from,
        "level": table.level,
        "models": table.models,
        "windows": table.windows,
        "columns": list(COLUMNS),
        "rows": rows[:INLINE_ROWS],
        **({"truncated": True} if len(rows) > INLINE_ROWS else {}),
        "not_covered": table.not_covered,
        "not_shown": [missing.model_dump() for missing in table.not_shown],
        "reading": READING,
    }
