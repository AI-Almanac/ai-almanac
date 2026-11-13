"""Administrative areas on a map: outlines over a gridded field, or areas shaded by value.

Callers pass a matplotlib Axes; matplotlib is imported here only for its path types.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from .boundaries import interior_point
from .focus_area import polygons_of

# More names than this would cover the map; woreda maps go unlabelled.
MAX_LABELLED_AREAS = 30


def _area_patch(geometry: dict, **style):
    """One patch for a (multi)polygon; its rings form a compound path, so holes stay open."""
    from matplotlib.patches import PathPatch
    from matplotlib.path import Path

    vertices, codes = [], []
    for rings in polygons_of(geometry):
        for ring in rings:
            points = np.asarray(ring, dtype=float)[:, :2]
            vertices.extend(points)
            codes.extend([Path.MOVETO, *[Path.LINETO] * (len(points) - 2), Path.CLOSEPOLY])
    if not vertices:
        return None
    return PathPatch(Path(np.asarray(vertices), codes), **style)


def draw_outlines(ax, collection: dict) -> None:
    for feature in collection.get("features") or []:
        patch = _area_patch(feature["geometry"], fill=False, edgecolor="0.2", linewidth=0.6)
        if patch is not None:
            ax.add_patch(patch)
    label_areas(ax, collection)


def draw_area_values(
    ax, collection: dict, values: Mapping[str, float], *, vmin: float, vmax: float
):
    """Shade each area by its value (areas without one in grey) and return the mappable
    for a colorbar."""
    import matplotlib.pyplot as plt
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import Normalize

    norm = Normalize(vmin=vmin, vmax=vmax)
    cmap = plt.get_cmap("YlGnBu")
    for feature in collection.get("features") or []:
        value = values.get(feature["properties"]["name"])
        face = "0.85" if value is None else cmap(norm(value))
        patch = _area_patch(feature["geometry"], facecolor=face, edgecolor="0.3", linewidth=0.5)
        if patch is not None:
            ax.add_patch(patch)
    ax.autoscale_view()
    ax.set_aspect("equal", adjustable="datalim")
    label_areas(ax, collection)
    return ScalarMappable(norm=norm, cmap=cmap)


def label_areas(ax, collection: dict) -> None:
    features = collection.get("features") or []
    if len(features) > MAX_LABELLED_AREAS:
        return
    for feature in features:
        lon, lat = interior_point(feature["geometry"])
        if not np.isnan(lon):
            ax.annotate(
                feature["properties"]["name"], (lon, lat), fontsize=6, ha="center", va="center"
            )
