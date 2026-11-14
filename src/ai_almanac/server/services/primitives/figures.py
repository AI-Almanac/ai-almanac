"""Maps of workspace artifacts: gridded fields, side by side, or areas shaded by value.

Deterministic and provenance-stamped; the wording, framing and drawing are in
``map_figures``, so these only find the files and record what was drawn.
"""

from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import logging
from datetime import date
from pathlib import Path

from pydantic_ai import RunContext

from .. import ledger, map_figures, workspaces
from ..focus_area import FocusBox
from ..workspace_display import day_label
from .registry import (
    Refusal,
    artifact_text,
    listed_title,
    output_dir,
    primitive,
    staged_file,
    workspace_artifact,
)

logger = logging.getLogger(__name__)


def _plot_failure(tool: str, exc: Exception) -> Refusal:
    """Why a map could not be drawn, for the assistant. A file error's own text names
    server paths, so it is logged and replaced with a plain sentence."""
    if isinstance(exc, OSError):
        logger.warning("%s could not read its file", tool, exc_info=exc)
        return Refusal(f"{tool} failed: the file could not be read")
    return Refusal(f"{tool} failed: {exc}")


async def _dataset_with(ctx: RunContext, ref: str, variable: str) -> dict:
    """The dataset artifact ``ref`` names, refused unless it holds ``variable``."""
    row = await workspace_artifact(ctx, ref)
    if row["format"] != "netcdf":
        raise Refusal(f"{row['label']} is {row['format']}, not a dataset")
    variables = row["summary"].get("variables", {})
    if variable not in variables:
        raise Refusal(f"{variable!r} not in {row['label']}", variables=sorted(variables))
    return row


async def _outlines(ctx: RunContext, ref: str) -> tuple[dict, dict]:
    """The outlines artifact ``ref`` names, and its GeoJSON."""
    row = await workspace_artifact(ctx, ref)
    if row["format"] != "geojson":
        raise Refusal(f"{row['label']} is {row['format']}, not area outlines")
    try:
        return row, json.loads(await artifact_text(row))
    except ValueError as exc:
        raise Refusal(str(exc)) from exc


async def _panel_titles(rows: list[dict]) -> list[str]:
    """What to call each file a map is drawn from, in the words the workspace lists it by."""
    steps = await ledger.user_steps(rows[0]["user_id"])
    return [map_figures.panel_title(row, steps.get(row["provenance_step_id"])) for row in rows]


def _variable_in_words(row: dict, variable: str) -> tuple[str, str]:
    """A variable as a title names it and as a colour bar labels it, units included."""
    attrs = row["summary"].get("variables", {}).get(variable) or {}
    long_name, units = attrs.get("long_name"), attrs.get("units")
    return (
        map_figures.variable_words(variable, long_name),
        map_figures.colour_bar_label(variable, long_name, units),
    )


def _view(extent: FocusBox | None) -> map_figures.Extent | None:
    return map_figures.Extent(**extent.model_dump()) if extent else None


def _figure(target: Path, title: str) -> dict:
    """A drawn figure, shown in the chat and stored in the workspace under its title."""
    return {
        "artifacts": [
            {
                "kind": "figure",
                "data": target.read_bytes(),
                "filename": target.name,
                "label": target.stem,
            }
        ],
        "_outputs": [
            ledger.OutputFile(
                path=target, label=target.stem, kind="figure", title=listed_title(title)
            )
        ],
    }


async def _draw_dataset_maps(
    tool: str,
    rows: list[dict],
    variable: str,
    *,
    panels: list[str],
    figure_title: str,
    stem: str,
    select: dict[str, str] | None,
    extent: FocusBox | None,
    outlines: dict | None = None,
) -> tuple[Path, dict]:
    """One map per file on a shared view and scale; the PNG and how it was framed."""
    _, bar_label = _variable_in_words(rows[0], variable)
    with output_dir(tool) as out_dir, contextlib.ExitStack() as stack:
        sources = [stack.enter_context(staged_file(row)) for row in rows]
        target = out_dir / f"{workspaces.safe_label(stem)}.png"
        try:
            framing = await asyncio.to_thread(
                map_figures.draw_maps,
                list(zip(panels, sources, strict=True)),
                variable,
                select or {},
                figure_title=figure_title,
                bar_label=bar_label,
                target=target,
                extent=_view(extent),
                outlines=outlines,
            )
        except Exception as exc:  # noqa: BLE001 — the message is the tool result
            raise _plot_failure(tool, exc) from exc
    return target, framing


@primitive("figure.plot_map", "1.2.0", label_args=("artifact", "areas"))
async def plot_map(
    ctx: RunContext,
    artifact: str,
    variable: str,
    title: str | None = None,
    select: dict[str, str] | None = None,
    areas: str | None = None,
    extent: FocusBox | None = None,
) -> dict:
    """Plot one lat/lon variable of a dataset or metrics artifact as a map. Deterministic and
    provenance-stamped; prefer it to writing matplotlib code by hand. `select` picks the slice
    along any other dimension by nearest coordinate value, e.g. {"time": "2026-07-01",
    "lead_time": "10"}; dimensions left out use their first value. `areas` draws the
    outlines artifact `summarize_by_area` wrote over the map, named when there are few.

    `title` is drawn on the map and names the file: say what it shows, where and when.
    Left out, it is the variable in words and the file's own title (its model and lead
    days). The colour bar is labelled in words with units. The map shows only the part
    of the grid that holds data, with a small margin, so a run limited to a few zones
    fills the frame; pass `extent` ({"lat_min", "lat_max", "lon_min", "lon_max"}) to show
    another box. The colour scale ignores outlier cells: they take the end colour and
    `shown.colour_scale.clipped` says so, which is worth a clause when you describe it."""
    row = await _dataset_with(ctx, artifact, variable)
    outlines_row, outlines = await _outlines(ctx, areas) if areas is not None else (None, None)
    words, _ = _variable_in_words(row, variable)
    [panel] = await _panel_titles([row])
    shown_title = title or map_figures.map_title(words, panel)
    target, framing = await _draw_dataset_maps(
        "plot_map",
        [row],
        variable,
        panels=[shown_title],
        figure_title=shown_title,
        stem=f"{row['label']}_{variable}",
        select=select,
        extent=extent,
        outlines=outlines,
    )
    return {
        "shown": {"title": shown_title, **framing},
        **_figure(target, shown_title),
        "_inputs": [("dataset", row), *([("areas", outlines_row)] if outlines_row else [])],
    }


@primitive("figure.plot_maps", "1.1.0", label_args=("artifacts",))
async def plot_maps(
    ctx: RunContext,
    artifacts: list[str],
    variable: str,
    title: str | None = None,
    select: dict[str, str] | None = None,
    extent: FocusBox | None = None,
    panel_titles: list[str] | None = None,
) -> dict:
    """Plot one lat/lon variable from 2-4 artifacts as side-by-side panels on a shared color
    scale, e.g. two models' MAE maps. Deterministic and provenance-stamped; use it instead of
    run_code to compare maps. `select` works as in plot_map and applies to every panel.

    `title` heads the figure and names the file: say what it shows, where and when. Each
    panel is titled with its file's own title (its model and lead days); pass
    `panel_titles`, one per artifact in order, only to say something else. The colour bar
    is labelled in words with units. The panels share one view, cropped to the cells that
    hold data with a small margin; pass `extent` ({"lat_min", "lat_max", "lon_min",
    "lon_max"}) to show another box. The colour scale ignores outlier cells: they take the
    end colour and `shown.colour_scale.clipped` says so."""
    if not 2 <= len(artifacts) <= 4:
        raise Refusal("Pass 2 to 4 artifacts; use plot_map for one")
    if panel_titles is not None and len(panel_titles) != len(artifacts):
        raise Refusal("Pass one panel title per artifact, in the same order")
    rows = [await _dataset_with(ctx, ref, variable) for ref in artifacts]
    words, _ = _variable_in_words(rows[0], variable)
    shown_title = title or words
    shown_panels = panel_titles or await _panel_titles(rows)
    target, framing = await _draw_dataset_maps(
        "plot_maps",
        rows,
        variable,
        panels=shown_panels,
        figure_title=shown_title,
        stem="_vs_".join(row["label"] for row in rows) + f"_{variable}",
        select=select,
        extent=extent,
    )
    return {
        "shown": {"title": shown_title, "panels": shown_panels, **framing},
        **_figure(target, shown_title),
        "_inputs": [(row["label"], row) for row in rows],
    }


@primitive("figure.plot_area_map", "1.1.0", label_args=("table", "areas"))
async def plot_area_map(
    ctx: RunContext,
    table: str,
    areas: str,
    column: str = "p_within_14d",
    issue_date: str | None = None,
    title: str | None = None,
) -> dict:
    """Map one column of a `summarize_by_area` table, shading each area by its value on
    `issue_date` (default: the latest). `areas` is the outlines artifact the same summary
    wrote. Probability columns share a fixed 0-1 scale so maps compare across issue dates;
    areas with no value are grey."""
    table_row = await workspace_artifact(ctx, table)
    outlines_row, outlines = await _outlines(ctx, areas)
    try:
        values, shown_date = _area_values(await artifact_text(table_row), column, issue_date)
    except ValueError as exc:
        raise Refusal(str(exc)) from exc
    shown_title = title or (
        f"{map_figures.column_words(column)}, issued {day_label(date.fromisoformat(shown_date))}"
    )
    stem = workspaces.safe_label(f"{table_row['label']}_{column}_{shown_date}")
    with output_dir("plot_area_map") as out_dir:
        target = out_dir / f"{stem}.png"
        try:
            await asyncio.to_thread(
                map_figures.draw_area_map, outlines, values, shown_title, column, target
            )
        except Exception as exc:  # noqa: BLE001 — the message is the tool result
            raise _plot_failure("plot_area_map", exc) from exc
    return {
        **_figure(target, shown_title),
        "_inputs": [("table", table_row), ("areas", outlines_row)],
        "_params": {"issue_date": shown_date},
    }


def _area_values(text: str, column: str, issue_date: str | None) -> tuple[dict[str, float], str]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "area" not in rows[0]:
        raise ValueError("This is not a summarize_by_area table")
    if column not in rows[0]:
        numeric = [key for key in rows[0] if key.startswith(("p_", "most_likely_share"))]
        raise ValueError(f"No column {column!r}; choose one of {', '.join(numeric)}")
    dates = sorted({row["issue_date"] for row in rows})
    shown = issue_date or dates[-1]
    if shown not in dates:
        raise ValueError(f"No issue date {shown}; the table has {dates[0]} to {dates[-1]}")
    return {row["area"]: float(row[column]) for row in rows if row["issue_date"] == shown}, shown
