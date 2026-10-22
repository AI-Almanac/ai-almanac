"""A blend's CV summaries, one table each, wherever its day-level blend trained.

Blends that train the day-level blend in their own run write its rows into the
weekly summary files. Blends whose day-level blend trains in its own call
(day_level_blends) publish them separately under DAY_LEVEL_PREFIX: per-point and
yearly rows only, scored exactly as a combined run scores them. Their pooled row
is relative to the weekly run's unc_clim_raw baseline, so it is derived here with
onset_blending's summarize_models_pooled:

    brier_skill = 1 - brier / baseline brier
    rps_skill   = 1 - rps / baseline rps
    AUC diff    = (auc - baseline auc) * 100

Every table read here comes out in the single-file shape, so readers stay unaware
of where the day-level blend trained.
"""

from __future__ import annotations

import asyncio
import csv
import io
from collections.abc import Callable
from typing import Literal

from ai_almanac.server.services import blend_cells

DAY_LEVEL_PREFIX = "day_level_"
_POOLED_ROW_ID = "ALL"
_BASELINE_MODEL = "unc_clim_raw"

SummaryTable = Literal["pooled", "yearly", "cells"]


def _is_pooled(filename: str) -> bool:
    return filename.startswith("summary_models_pooled") and filename.endswith(".csv")


def _is_yearly(filename: str) -> bool:
    return filename.startswith("yearly_metrics_global") and filename.endswith(".csv")


_MATCHERS: dict[SummaryTable, Callable[[str], bool]] = {
    "pooled": _is_pooled,
    "yearly": _is_yearly,
    "cells": blend_cells.is_per_cell_summary,
}


def _rows(text: str) -> tuple[list[str], list[dict]]:
    reader = csv.DictReader(io.StringIO(text))
    return list(reader.fieldnames or []), list(reader)


def _write(fieldnames: list[str], rows: list[dict]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames, lineterminator="\n", restval="")
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def concat_tables(first: str, second: str) -> str:
    """first's rows then second's, under first's columns plus any second adds."""
    first_fields, first_rows = _rows(first)
    second_fields, second_rows = _rows(second)
    fields = first_fields + [name for name in second_fields if name not in first_fields]
    return _write(fields, first_rows + second_rows)


def _ratio_skill(value: str | None, reference: str | None) -> str:
    try:
        return repr(1.0 - float(value) / float(reference))
    except (TypeError, ValueError, ZeroDivisionError):
        return ""


def _auc_diff(value: str | None, reference: str | None) -> str:
    try:
        return repr((float(value) - float(reference)) * 100.0)
    except (TypeError, ValueError):
        return ""


def pooled_rows(cell_text: str, pooled_text: str) -> str:
    """Pooled rows for the models in a per-point table, scored against the
    unc_clim_raw row of another run's pooled table; "" without that baseline."""
    _, pooled = _rows(pooled_text)
    baseline = next((row for row in pooled if row.get("model") == _BASELINE_MODEL), None)
    fields, cells = _rows(cell_text)
    if baseline is None or not cells:
        return ""
    rows = [
        {
            **row,
            "brier_skill": _ratio_skill(row.get("brier"), baseline.get("brier")),
            "rps_skill": _ratio_skill(row.get("rps"), baseline.get("rps")),
            "AUC diff": _auc_diff(row.get("auc"), baseline.get("auc")),
        }
        for row in cells
        if row.get("id") == _POOLED_ROW_ID
    ]
    return _write(fields + ["brier_skill", "rps_skill", "AUC diff"], rows)


def merge_summary(table: SummaryTable, weekly_text: str, day_level: dict[str, str]) -> str:
    """One summary table from the weekly run's and the day-level run's files.

    day_level maps "cells" and "yearly" to the day-level blend's tables, when it
    published them."""
    if table == "pooled":
        extra = pooled_rows(day_level.get("cells", ""), weekly_text) if weekly_text else ""
    else:
        extra = day_level.get(table, "")
    if not extra:
        return weekly_text
    if not weekly_text:
        return extra
    return concat_tables(weekly_text, extra)


def find(artifacts: list[dict], table: SummaryTable, *, day_level: bool = False) -> dict | None:
    """The artifact holding one table, from the weekly run or the day-level one."""
    matches = _MATCHERS[table]
    for artifact in artifacts:
        name = artifact["filename"]
        if day_level != name.startswith(DAY_LEVEL_PREFIX):
            continue
        if matches(name.removeprefix(DAY_LEVEL_PREFIX)):
            return artifact
    return None


async def read_summary(job_id: str, artifacts: list[dict], table: SummaryTable) -> str:
    """A blend's `table` as CSV text, day-level rows included; "" when absent."""
    from ai_almanac.server.services.storage import get_storage

    storage = get_storage()

    async def read(artifact: dict | None) -> str:
        if artifact is None:
            return ""
        text = await asyncio.to_thread(
            storage.read_result_text, job_id, artifact["kind"], artifact["filename"]
        )
        return text or ""

    weekly = await read(find(artifacts, table))
    source = "cells" if table == "pooled" else table
    day_level = await read(find(artifacts, source, day_level=True))
    return merge_summary(table, weekly, {source: day_level} if day_level else {})
