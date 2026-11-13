"""Which ROMP region a run is scored in, when the run names the place itself.

ROMP ships its own definition of a few countries. A run in one of them keeps
using it, so its results stay comparable with earlier ones. A run anywhere else is
scored on a box around its places, masked to their outlines.
"""

from __future__ import annotations

from collections.abc import Mapping

from .coverage import overlap
from .focus_area import FocusArea, FocusUnits, area_bounds, area_label, country_of

ROMP_REGION_BY_COUNTRY = {"ETH": "Ethiopia", "IND": "India"}


class PlaceNotScorable(ValueError):
    """The place cannot be scored with the selected data; the message says why."""


def place_of(area: FocusArea | None) -> FocusUnits | None:
    """The area when it names its own country, which is what lets it decide the region."""
    return area if isinstance(area, FocusUnits) and country_of(area) else None


def scored_by_place(
    params: Mapping, place: FocusUnits, observation_bounds: object, model_bounds: object
) -> dict:
    """The benchmark settings with the region, box and mask the place calls for."""
    romp_region = ROMP_REGION_BY_COUNTRY.get(place.iso or "")
    if romp_region:
        scored = {**params, "region": romp_region}
        if place.level == "adm0":
            # ROMP's own outline of the country is the mask; ours would move border cells.
            scored.pop("focus_area", None)
        return scored
    if not isinstance(observation_bounds, Mapping) or not isinstance(model_bounds, Mapping):
        raise PlaceNotScorable(
            f"Scoring {area_label(place)} needs the spatial bounds of both data sources, "
            "which are inferred when a source is validated."
        )
    box = overlap(area_bounds(place), observation_bounds, model_bounds)
    if box is None:
        raise PlaceNotScorable(
            f"{area_label(place)} does not overlap the selected observation and forecast data."
        )
    return {**params, "region": "custom", **box, "land_only": False, "shp_only": False}
