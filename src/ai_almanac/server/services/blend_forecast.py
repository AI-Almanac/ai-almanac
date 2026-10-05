"""Blended onset probabilities per issue date and grid point, for the forecast map.

The source CSV is ~12 MB per forecast, so it is parsed once into
`BlendForecastGrid` and stored with the job's outputs.

All functions here are synchronous and intended to be called via
asyncio.to_thread() from the async route handlers in routers/jobs.py.
"""

from __future__ import annotations

import csv
import io
from functools import lru_cache

from pydantic import BaseModel

from .job_cache import read_or_build
from .storage import StorageBackend

SOURCE_FILENAME = "blended_forecast_probabilities.csv"
CACHE_NAME = "blend_forecast.v1.json"
# ponytail: 0.1 % resolution is finer than the map's colour bins; cuts the
# payload ~3x. Raise it if probabilities are ever shown with decimals.
_DECIMALS = 3
_PROB_COLUMNS = ("cv_week1", "cv_week2", "cv_week3", "cv_week4", "cv_later")
_EMPTY_PROBS = [0.0] * len(_PROB_COLUMNS)


class BlendForecastPoint(BaseModel):
    id: str
    lat: float
    lon: float
    probs: list[list[float]]  # [issue date][week1..week4, later]


class BlendForecastGrid(BaseModel):
    issue_dates: list[str]
    points: list[BlendForecastPoint]
    onset_threshold: float | None


def _probs(row: dict[str, str]) -> list[float]:
    return [round(float(row.get(col) or 0), _DECIMALS) for col in _PROB_COLUMNS]


def _threshold(row: dict[str, str]) -> float | None:
    try:
        return float(row.get("onset_threshold") or "")
    except ValueError:
        return None


def _coords_from_id(point_id: str) -> tuple[float, float]:
    """Point ids are `{lat}_{lon}`; used when the CSV has no lat/lon columns."""
    lat, lon = point_id.split("_", 1)
    return float(lat), float(lon)


def parse_blend_forecast(text: str) -> BlendForecastGrid:
    by_point: dict[str, dict[str, list[float]]] = {}
    coords: dict[str, tuple[float, float]] = {}
    issue_dates: dict[str, None] = {}  # insertion-ordered set
    onset_threshold: float | None = None
    for row in csv.DictReader(io.StringIO(text)):
        point_id, date = row["id"], row["time"]
        issue_dates[date] = None
        if onset_threshold is None:
            onset_threshold = _threshold(row)
        by_point.setdefault(point_id, {})[date] = _probs(row)
        if point_id not in coords and row.get("lat") and row.get("lon"):
            coords[point_id] = (float(row["lat"]), float(row["lon"]))
    dates = list(issue_dates)

    def point(point_id: str, probs_by_date: dict[str, list[float]]) -> BlendForecastPoint:
        lat, lon = coords.get(point_id) or _coords_from_id(point_id)
        probs = [probs_by_date.get(d, _EMPTY_PROBS) for d in dates]
        return BlendForecastPoint(id=point_id, lat=lat, lon=lon, probs=probs)

    return BlendForecastGrid(
        issue_dates=dates,
        points=[point(pid, probs) for pid, probs in by_point.items()],
        onset_threshold=onset_threshold,
    )


def _build(job_id: str, storage: StorageBackend, kind: str) -> BlendForecastGrid:
    text = storage.read_result_text(job_id, kind, SOURCE_FILENAME)
    if not text:
        raise FileNotFoundError(f"{SOURCE_FILENAME} missing for job {job_id}")
    return parse_blend_forecast(text)


@lru_cache(maxsize=16)
def load_blend_forecast(job_id: str, storage: StorageBackend, kind: str) -> BlendForecastGrid:
    return read_or_build(
        storage, job_id, CACHE_NAME, BlendForecastGrid, lambda: _build(job_id, storage, kind)
    )
