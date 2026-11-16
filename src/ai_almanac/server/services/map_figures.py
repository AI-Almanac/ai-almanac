"""How a map the assistant draws is worded and framed, so it reads well without the
model's help: titles and colour-bar labels in words, the view cropped to the cells that
hold data, and a colour scale one outlier cell cannot flatten.

Pure functions over plain values, numpy arrays and xarray fields, with the drawing that
uses them at the bottom.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import numpy as np

from . import workspace_display

# The variables a benchmark's skill files hold, as a forecaster names them. The table
# of skill by area uses the same words for its columns.
_METRIC_WORDS = {
    "mean_mae": ("Onset error", "days"),
    "false_alarm_rate": ("False alarm rate", "share of forecast onsets"),
    "miss_rate": ("Miss rate", "share of observed onsets"),
}
# Units that say nothing a reader needs on a colour bar.
_UNITLESS = {"", "1", "-", "none", "dimensionless"}
# One grid cell of margin around the data, plus the half cell from its centre to its edge.
_MARGIN_CELLS = 1.5
# Tukey's outer fences: a value this far outside the middle half is an outlier.
_FENCE_IQRS = 3.0
_FALLBACK_SPACING = 1.0

Extend = Literal["neither", "min", "max", "both"]


# --- words --------------------------------------------------------------------


def _identifier_words(identifier: str) -> str:
    words = identifier.replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def variable_words(variable: str, long_name: str | None = None) -> str:
    """What a variable measures, in words: a skill metric by its name, anything else by
    the long name its file gives it, else its identifier spelled out."""
    if variable in _METRIC_WORDS:
        return _METRIC_WORDS[variable][0]
    return (long_name or "").strip() or _identifier_words(variable)


def colour_bar_label(variable: str, long_name: str | None = None, units: str | None = None) -> str:
    """The variable in words with its units, e.g. "Onset error (days)"."""
    words = variable_words(variable, long_name)
    shown_units = _METRIC_WORDS[variable][1] if variable in _METRIC_WORDS else (units or "").strip()
    return words if shown_units.lower() in _UNITLESS else f"{words} ({shown_units})"


def panel_title(artifact: Mapping, step: Mapping | None) -> str:
    """What to call the file a map was drawn from: the title its owner gave it, else
    its model and lead days ("AIFS · days 1–15"), never its label or file name."""
    if artifact.get("item_title"):
        return str(artifact["item_title"])
    title = workspace_display.artifact_title(dict(artifact), dict(step) if step else None)
    parts = [part for part in title.split(" · ") if part != workspace_display.GROUP_SPATIAL]
    return " · ".join(parts)


def map_title(variable_in_words: str, panel: str) -> str:
    """A single map's title: what it shows, then what it was drawn from."""
    return f"{variable_in_words}, {panel}"


# --- extent -------------------------------------------------------------------


@dataclass(frozen=True)
class Extent:
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float

    def as_dict(self) -> dict[str, float]:
        return {
            "lat_min": self.lat_min,
            "lat_max": self.lat_max,
            "lon_min": self.lon_min,
            "lon_max": self.lon_max,
        }


def _spacing(coords: np.ndarray) -> float:
    steps = np.abs(np.diff(coords))
    return float(np.median(steps)) if steps.size else _FALLBACK_SPACING


def _span_with_data(coords: np.ndarray, has_data: np.ndarray) -> tuple[float, float]:
    """The stretch of one axis whose cells hold data, with a margin, kept within the
    grid's own edges."""
    spacing = _spacing(coords)
    held = coords[has_data]
    low = max(float(held.min()) - _MARGIN_CELLS * spacing, float(coords.min()) - spacing / 2)
    high = min(float(held.max()) + _MARGIN_CELLS * spacing, float(coords.max()) + spacing / 2)
    return low, high


def data_extent(lat: Sequence[float], lon: Sequence[float], values: np.ndarray) -> Extent | None:
    """The box around the cells of a (lat, lon) field that hold a value, with a margin
    of one cell; None when no cell does. A run limited to a few zones is NaN over the
    rest of its region, and a map of the whole region would shrink them to a corner."""
    lats, lons = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    held = np.isfinite(np.asarray(values, dtype=float))
    if not held.any():
        return None
    lat_min, lat_max = _span_with_data(lats, held.any(axis=1))
    lon_min, lon_max = _span_with_data(lons, held.any(axis=0))
    return Extent(lat_min, lat_max, lon_min, lon_max)


def covering(extents: Iterable[Extent | None]) -> Extent | None:
    """The smallest box holding every extent, so panels share one view; None when no
    panel holds data."""
    found = [extent for extent in extents if extent is not None]
    if not found:
        return None
    return Extent(
        min(e.lat_min for e in found),
        max(e.lat_max for e in found),
        min(e.lon_min for e in found),
        max(e.lon_max for e in found),
    )


# --- colour scale -------------------------------------------------------------


@dataclass(frozen=True)
class ColourRange:
    """The values a colour scale spans, and which ends stand for values beyond it."""

    vmin: float
    vmax: float
    extend: Extend = "neither"

    @property
    def clipped(self) -> bool:
        return self.extend != "neither"


def robust_range(values: Iterable[np.ndarray]) -> ColourRange | None:
    """A colour scale for the fields in ``values`` that an outlier cell cannot stretch.

    The scale runs between the most extreme values inside Tukey's outer fences (three
    interquartile ranges beyond the middle half). Values outside them are drawn in the
    end colour and ``extend`` names that end, so the bar is marked as extended. Fields
    with no such outlier keep their full range. None when no cell holds a value.
    """
    finite = np.concatenate([np.asarray(field, dtype=float).ravel() for field in values] or [[]])
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return None
    first, third = np.percentile(finite, [25, 75])
    reach = _FENCE_IQRS * (third - first)
    if reach == 0:
        return ColourRange(float(finite.min()), float(finite.max()))
    inside = finite[(finite >= first - reach) & (finite <= third + reach)]
    low, high = float(inside.min()), float(inside.max())
    return ColourRange(low, high, _extend(low > finite.min(), high < finite.max()))


def _extend(below: bool, above: bool) -> Extend:
    if below and above:
        return "both"
    if below:
        return "min"
    return "max" if above else "neither"


def framing(view: Extent | None, scale: ColourRange | None) -> dict:
    """What a drawn map covers and how it is coloured, for the assistant to describe."""
    return {
        "extent": view.as_dict() if view else None,
        "colour_scale": (
            {"from": scale.vmin, "to": scale.vmax, "clipped": scale.clipped} if scale else None
        ),
    }


# --- planes -------------------------------------------------------------------


def plane(data, keep: tuple[str, str], select: Mapping[str, str]):
    """Reduce ``data`` to the ``keep`` dims: nearest match for each selected dim, else index 0."""
    unknown = sorted(set(select) - set(data.dims))
    if unknown:
        raise ValueError(f"{unknown} not among dims {list(data.dims)}")
    for dim in (d for d in data.dims if d not in keep):
        if dim not in select:
            data = data.isel({dim: 0})
            continue
        coord = data[dim]
        if np.issubdtype(coord.dtype, np.datetime64):
            value = np.datetime64(select[dim])
        elif np.issubdtype(coord.dtype, np.timedelta64):
            value = np.timedelta64(int(float(select[dim])), "D")
        else:
            value = float(select[dim])
        data = data.sel({dim: value}, method="nearest")
    return data


def map_plane(data, select: Mapping[str, str]):
    """The lat/lon slice of ``data``, with the names of its lat and lon dims."""
    lat = next((d for d in data.dims if d.lower().startswith("lat")), None)
    lon = next((d for d in data.dims if d.lower().startswith("lon")), None)
    if lat is None or lon is None:
        raise ValueError(f"{data.name} has dims {list(data.dims)}, need lat and lon")
    return plane(data, (lat, lon), select), lat, lon


def in_view(field, lat: str, lon: str, view: Extent | None):
    """The cells of a (lat, lon) field inside the view; all of them when there is none."""
    if view is None:
        return field
    shown = (
        (field[lat] >= view.lat_min)
        & (field[lat] <= view.lat_max)
        & (field[lon] >= view.lon_min)
        & (field[lon] <= view.lon_max)
    )
    return field.where(shown)


# --- area summary columns -----------------------------------------------------

_WINDOW_COLUMN = re.compile(
    r"^p_(?:within_(?P<within>\d+)d|week(?P<week>\d)|day_(?P<day>\d+)|(?P<later>later))$"
)


def column_words(column: str) -> str:
    """An area summary column in words, e.g. p_within_14d -> "Onset within 14 days"."""
    match = _WINDOW_COLUMN.match(column)
    if match is None:
        return (
            "Share of the area expecting its likeliest window"
            if column == "most_likely_share"
            else column
        )
    if match["within"]:
        return f"Onset within {match['within']} days"
    if match["week"]:
        return f"Onset in week {match['week']}"
    if match["day"]:
        return f"Onset on day {match['day']}"
    return "Onset after 4 weeks"


# --- drawing ------------------------------------------------------------------
# The imperative edge: these read files and write PNGs, so callers run them in a thread.


def _pyplot():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def save_png(fig, target: Path) -> None:
    fig.savefig(target, format="png", dpi=110, metadata={"Software": None})
    _pyplot().close(fig)


def _label_axes(ax, title: str) -> None:
    ax.set_title(title)
    ax.set_xlabel("Longitude (°E)")
    ax.set_ylabel("Latitude (°N)")


def draw_maps(
    panels: Sequence[tuple[str, Path]],
    variable: str,
    select: Mapping[str, str],
    *,
    figure_title: str,
    bar_label: str,
    target: Path,
    extent: Extent | None = None,
    outlines: dict | None = None,
) -> dict:
    """Draw one map per panel on a shared view and colour scale; returns the framing."""
    from . import area_maps, netcdf

    planes = []
    for _, source in panels:
        with netcdf.open_dataset(source, engine="h5netcdf") as ds:
            field, lat, lon = map_plane(ds[variable], select)
            planes.append((field.transpose(lat, lon).load(), lat, lon))
    view = extent or covering(
        data_extent(field[lat].values, field[lon].values, field.values)
        for field, lat, lon in planes
    )
    scale = robust_range(in_view(field, lat, lon, view).values for field, lat, lon in planes)
    limits = {"vmin": scale.vmin, "vmax": scale.vmax} if scale else {}
    fig, axes = _pyplot().subplots(
        1, len(planes), figsize=(5.5 * len(planes) + 1.5, 5), squeeze=False, layout="constrained"
    )
    for ax, (panel_title, _), (field, lat, lon) in zip(axes[0], panels, planes, strict=True):
        mesh = field.plot.pcolormesh(x=lon, y=lat, ax=ax, add_colorbar=False, **limits)
        if outlines:
            area_maps.draw_outlines(ax, outlines)
        if view:
            ax.set_xlim(view.lon_min, view.lon_max)
            ax.set_ylim(view.lat_min, view.lat_max)
        _label_axes(ax, panel_title)
    fig.colorbar(
        mesh,
        ax=axes[0].tolist(),
        label=bar_label,
        extend=scale.extend if scale else "neither",
        shrink=0.85,
    )
    if len(planes) > 1:
        fig.suptitle(figure_title)
    save_png(fig, target)
    return framing(view, scale)


def draw_area_map(
    outlines: dict, values: Mapping[str, float], title: str, column: str, target: Path
) -> None:
    """Shade each area by its value; probability columns share a fixed 0-1 scale so maps
    compare across issue dates."""
    from . import area_maps

    probability = column.startswith("p_") or column == "most_likely_share"
    low, high = (0.0, 1.0) if probability else (min(values.values()), max(values.values()))
    fig, ax = _pyplot().subplots(figsize=(7, 6))
    mappable = area_maps.draw_area_values(ax, outlines, values, vmin=low, vmax=high)
    fig.colorbar(mappable, ax=ax, label=column_words(column), shrink=0.8)
    _label_axes(ax, title)
    save_png(fig, target)
