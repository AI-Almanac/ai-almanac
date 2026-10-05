"""Reshape a blend forecast's probabilities CSV into per-point series for the forecast map."""

from __future__ import annotations

import csv
import io

_WEEK_COLUMNS = ("cv_week1", "cv_week2", "cv_week3", "cv_week4", "cv_later")
_NO_FORECAST = [0.0] * len(_WEEK_COLUMNS)
# Probabilities are drawn as colour bins and shown as whole percentages.
_PROB_DECIMALS = 3


def _probs(row: dict[str, str]) -> list[float]:
    return [round(float(row.get(col) or 0), _PROB_DECIMALS) for col in _WEEK_COLUMNS]


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


def parse_blend_forecast(text: str) -> dict:
    """Return ``issue_dates``, ``points`` (probs indexed by issue date) and ``onset_threshold``."""
    probs_by_point: dict[str, dict[str, list[float]]] = {}
    coords_by_point: dict[str, tuple[float, float]] = {}
    issue_dates: dict[str, None] = {}  # insertion-ordered set
    onset_threshold: float | None = None
    for row in csv.DictReader(io.StringIO(text)):
        point_id, date = row["id"], row["time"]
        issue_dates[date] = None
        if onset_threshold is None:
            onset_threshold = _onset_threshold(row)
        probs_by_point.setdefault(point_id, {})[date] = _probs(row)
        if point_id not in coords_by_point:
            coords_by_point[point_id] = _point_coords(point_id, row)

    return {
        "issue_dates": list(issue_dates),
        "points": [
            {
                "id": point_id,
                "lat": coords_by_point[point_id][0],
                "lon": coords_by_point[point_id][1],
                "probs": [by_date.get(d, _NO_FORECAST) for d in issue_dates],
            }
            for point_id, by_date in probs_by_point.items()
        ],
        "onset_threshold": onset_threshold,
    }
