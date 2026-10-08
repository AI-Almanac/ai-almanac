"""Reshape a blend forecast's probabilities CSV into per-point series for the forecast map."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

WEEKLY_COLUMNS = ("cv_week1", "cv_week2", "cv_week3", "cv_week4", "cv_later")
DAILY_COLUMNS = (*(f"p_day_{day}" for day in range(1, 29)), "p_later")
# Probabilities are drawn as colour bins and shown as whole percentages.
_PROB_DECIMALS = 3

ForecastModel = Literal["weekly_model", "daily_model"]
ForecastResolution = Literal["weekly", "daily"]

_SOURCE_FILES: dict[ForecastModel, str] = {
    "weekly_model": "blended_forecast_probabilities.csv",
    "daily_model": "daily_onset_probabilities.csv",
}
_COLUMNS: dict[ForecastResolution, tuple[str, ...]] = {
    "weekly": WEEKLY_COLUMNS,
    "daily": DAILY_COLUMNS,
}


class UnsupportedForecastView(ValueError):
    """A model/resolution pair the forecast outputs cannot provide."""


@dataclass(frozen=True)
class ForecastView:
    """One way to read a forecast: which blend's probabilities, binned by week or day."""

    model: ForecastModel
    resolution: ForecastResolution

    @property
    def source_filename(self) -> str:
        return _SOURCE_FILES[self.model]

    @property
    def columns(self) -> tuple[str, ...]:
        return _COLUMNS[self.resolution]

    @property
    def payload_name(self) -> str:
        # The original weekly payload keeps its name so existing jobs are not rebuilt.
        if self == DEFAULT_VIEW:
            return "blend_forecast.v1.json"
        return f"blend_forecast.{self.model}.{self.resolution}.v1.json"

    def as_dict(self) -> dict[str, str]:
        return {"model": self.model, "resolution": self.resolution}


DEFAULT_VIEW = ForecastView("weekly_model", "weekly")
SUPPORTED_VIEWS = (
    DEFAULT_VIEW,
    ForecastView("daily_model", "weekly"),
    ForecastView("daily_model", "daily"),
)


def parse_forecast_view(model: ForecastModel, resolution: ForecastResolution) -> ForecastView:
    """Return the view, or raise ``UnsupportedForecastView`` (the weekly blend has no days)."""
    view = ForecastView(model, resolution)
    if view not in SUPPORTED_VIEWS:
        raise UnsupportedForecastView(f"The {model} forecast has no {resolution} probabilities")
    return view


def available_views(filenames: Iterable[str]) -> list[dict[str, str]]:
    """Views whose source CSV is among the job's output files, in display order."""
    present = set(filenames)
    return [view.as_dict() for view in SUPPORTED_VIEWS if view.source_filename in present]


def _probs(row: dict[str, str], columns: Sequence[str]) -> list[float]:
    return [round(float(row.get(col) or 0), _PROB_DECIMALS) for col in columns]


def _onset_threshold(row: dict[str, str]) -> float | None:
    try:
        return float(row.get("onset_threshold") or "")
    except ValueError:
        return None


def _point_coords(point_id: str, row: dict[str, str]) -> tuple[float, float]:
    if row.get("lat") and row.get("lon"):
        return float(row["lat"]), float(row["lon"])
    lat, lon = point_id.split("_", 1)
    return float(lat), float(lon)


def parse_blend_forecast(text: str, columns: Sequence[str]) -> dict:
    """Return ``issue_dates``, ``points`` (``columns`` probs per issue date) and ``onset_threshold``."""
    no_forecast = [0.0] * len(columns)
    probs_by_point: dict[str, dict[str, list[float]]] = {}
    coords_by_point: dict[str, tuple[float, float]] = {}
    issue_dates: dict[str, None] = {}  # insertion-ordered set
    onset_threshold: float | None = None
    for row in csv.DictReader(io.StringIO(text)):
        point_id, date = row["id"], row["time"]
        issue_dates[date] = None
        if onset_threshold is None:
            onset_threshold = _onset_threshold(row)
        probs_by_point.setdefault(point_id, {})[date] = _probs(row, columns)
        if point_id not in coords_by_point:
            coords_by_point[point_id] = _point_coords(point_id, row)

    return {
        "issue_dates": list(issue_dates),
        "points": [
            {
                "id": point_id,
                "lat": coords_by_point[point_id][0],
                "lon": coords_by_point[point_id][1],
                "probs": [by_date.get(d, no_forecast) for d in issue_dates],
            }
            for point_id, by_date in probs_by_point.items()
        ],
        "onset_threshold": onset_threshold,
    }
