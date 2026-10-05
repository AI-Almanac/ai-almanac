from __future__ import annotations

from ai_almanac.server.services.blend_forecast import parse_blend_forecast

_CSV = """id,time,lat,lon,onset_threshold,cv_week1,cv_week2,cv_week3,cv_week4,cv_later
9.0_39.0,2024-06-01,9.0,39.0,20,0.123456,0.2,0.3,0.1,0.276544
9.0_39.0,2024-06-08,9.0,39.0,20,0.5,0.5,,0,0
8.5_40.0,2024-06-08,,,20,1,0,0,0,0
"""


def test_parse_blend_forecast_aligns_points_on_issue_dates() -> None:
    result = parse_blend_forecast(_CSV)

    assert result["issue_dates"] == ["2024-06-01", "2024-06-08"]
    assert result["onset_threshold"] == 20.0
    first, second = result["points"]
    assert (first["lat"], first["lon"]) == (9.0, 39.0)
    assert first["probs"] == [[0.123, 0.2, 0.3, 0.1, 0.277], [0.5, 0.5, 0.0, 0.0, 0.0]]
    # Coordinates fall back to the point id; a missing issue date reads as no forecast.
    assert (second["lat"], second["lon"]) == (8.5, 40.0)
    assert second["probs"] == [[0.0] * 5, [1.0, 0.0, 0.0, 0.0, 0.0]]
