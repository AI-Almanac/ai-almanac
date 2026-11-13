"""Administrative outlines served in place of the boundary download."""

from __future__ import annotations

import pytest

from ai_almanac.server.services import boundaries


def square(name: str, lon0: float, lat0: float, lon1: float, lat1: float) -> dict:
    ring = [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]
    return feature(name, [ring])


def feature(name: str, rings: list) -> dict:
    return {
        "type": "Feature",
        "properties": {"shapeName": name},
        "geometry": {"type": "Polygon", "coordinates": rings},
    }


def collection(*features: dict) -> dict:
    return {"type": "FeatureCollection", "features": list(features)}


REGIONS = collection(square("Amhara", 36, 9, 40, 14), square("Oromia", 34, 3, 43, 9))
# U-shaped: its middle, (37, 13), lies in the notch outside the zone.
U_ZONE = feature(
    "North Gondar",
    [
        [[36, 14], [36, 12], [36.5, 12], [36.5, 13.5], [37.5, 13.5], [37.5, 12], [38, 12]]
        + [[38, 14], [36, 14]]
    ],
)
ZONES = collection(
    square("East Gojjam", 37, 10, 38.5, 11),
    U_ZONE,
    square("Borena", 37, 3.5, 39, 5),
)


def serve_boundaries(
    monkeypatch: pytest.MonkeyPatch, by_level: dict[str, dict]
) -> list[tuple[str, str]]:
    """Serve ``by_level`` for every country; returns the (country, level) pairs asked for."""
    loaded: list[tuple[str, str]] = []

    async def fake_load(iso: str, level: str) -> dict:
        loaded.append((iso, level))
        return {
            "metadata": {"boundaryID": f"{iso}-{level.upper()}-test"},
            "geojson": by_level[level],
        }

    monkeypatch.setattr(boundaries, "load_boundary", fake_load)
    return loaded
