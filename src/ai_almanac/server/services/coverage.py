"""Where a data source has data, which is what decides the sources it can be scored with.

A source's extent is inferred when it is validated. Sources from before that are
matched by the region they were filed under, as every source once was.
"""

from __future__ import annotations

from collections.abc import Mapping

Bounds = Mapping[str, float]

_EDGES = ("lat_min", "lat_max", "lon_min", "lon_max")


def box_of(value: object) -> Bounds | None:
    """The value as a lat/lon box, when it has all four edges as numbers."""
    if not isinstance(value, Mapping):
        return None
    edges = {edge: value.get(edge) for edge in _EDGES}
    if not all(isinstance(edge, int | float) for edge in edges.values()):
        return None
    return edges


def bounds_of(source: Mapping) -> Bounds | None:
    """A source's inferred extent, from a data source row or a model registry entry
    (which carries the row's metadata at its top level)."""
    metadata = source.get("metadata")
    holder = metadata if isinstance(metadata, Mapping) else source
    return box_of(holder.get("spatial_bounds"))


def overlap(*boxes: Bounds) -> dict[str, float] | None:
    """The box common to all of them; None when they share none."""
    shared = {
        "lat_min": max(box["lat_min"] for box in boxes),
        "lat_max": min(box["lat_max"] for box in boxes),
        "lon_min": max(box["lon_min"] for box in boxes),
        "lon_max": min(box["lon_max"] for box in boxes),
    }
    if shared["lat_min"] > shared["lat_max"] or shared["lon_min"] > shared["lon_max"]:
        return None
    return shared


def _filed_under(source: Mapping) -> str:
    return str(source.get("region") or "").strip().lower()


def _name(source: Mapping) -> str:
    return str(source.get("name") or source.get("display_name") or source.get("id"))


def mismatch(observation: Mapping, model: Mapping) -> str | None:
    """Why a forecast source cannot be scored against an observation source; None when
    it can."""
    observed, forecast = bounds_of(observation), bounds_of(model)
    if observed and forecast:
        if overlap(observed, forecast) is None:
            return f"{_name(model)} has no data where {_name(observation)} does."
        return None
    if _filed_under(observation) != _filed_under(model):
        return (
            f"{_name(model)} and {_name(observation)} are filed under different regions, "
            "and the extent of one of them is not known."
        )
    return None


def scored_together(observation: Mapping, model: Mapping) -> bool:
    return mismatch(observation, model) is None


def reaches(area: object, *sources: Mapping) -> bool:
    """Whether every source of known extent has data inside the area. An area with no
    box of its own cannot be checked and is taken to be reached."""
    box = box_of(area)
    if box is None:
        return True
    extents = [bounds for source in sources if (bounds := bounds_of(source))]
    return all(overlap(box, extent) is not None for extent in extents)
