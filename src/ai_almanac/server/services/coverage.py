"""Where a data source has data, which is what decides the sources it can be scored with.

A source's extent is inferred when it is validated. Sources from before that are
matched by the region they were filed under, as every source once was.

Longitudes are compared on the circle: a source stored 0..360 and one stored -180..180
overlap where their data does. ROMP conforms a box to each dataset's own convention,
so a shared box can be handed to it in either.
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


# A grid this wide goes all the way round; the gap at its seam is one grid step.
_WHOLE_GLOBE_DEG = 350.0


def overlap(*boxes: Bounds) -> dict[str, float] | None:
    """The box common to all of them; None when they share none. Its longitudes are
    in the first box's convention, unless that box goes all the way round."""
    lat_min = max(box["lat_min"] for box in boxes)
    lat_max = min(box["lat_max"] for box in boxes)
    if lat_min > lat_max:
        return None
    lon: tuple[float, float] | None = (boxes[0]["lon_min"], boxes[0]["lon_max"])
    for box in boxes[1:]:
        lon = lon and _lon_overlap(lon, (box["lon_min"], box["lon_max"]))
    if lon is None:
        return None
    return {"lat_min": lat_min, "lat_max": lat_max, "lon_min": lon[0], "lon_max": lon[1]}


def _lon_overlap(
    first: tuple[float, float], second: tuple[float, float]
) -> tuple[float, float] | None:
    """The longitudes two spans share, in the first's convention; the widest part when
    they share two."""
    if _whole_globe(second):
        return first
    if _whole_globe(first):
        return second
    shared = [
        (max(first[0], second[0] + turn), min(first[1], second[1] + turn))
        for turn in (-360.0, 0.0, 360.0)
    ]
    parts = [part for part in shared if part[0] <= part[1]]
    return max(parts, key=lambda part: part[1] - part[0], default=None)


def _whole_globe(span: tuple[float, float]) -> bool:
    return span[1] - span[0] >= _WHOLE_GLOBE_DEG


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
