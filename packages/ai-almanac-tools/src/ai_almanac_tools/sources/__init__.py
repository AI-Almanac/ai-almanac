"""Public data sources, each opened lazily and subset before any bytes move."""

from __future__ import annotations

from . import era5_arco
from .catalog import FetchPlan, FetchRequest, Source, plan

SOURCES: dict[str, Source] = {source.name: source for source in (era5_arco.SOURCE,)}


def get_source(name: str) -> Source:
    try:
        return SOURCES[name]
    except KeyError:
        raise KeyError(f"No source {name!r}; available: {', '.join(SOURCES)}") from None


def plan_fetch(request: FetchRequest) -> FetchPlan:
    return plan(get_source(request.source), request)
