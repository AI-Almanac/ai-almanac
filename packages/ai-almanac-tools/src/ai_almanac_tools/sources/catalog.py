"""What a public source offers, the requests it accepts, and what a request would cost."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..bbox import BBox

FLOAT32_BYTES = 4


class FetchRequest(BaseModel):
    """Variables from one source over a box and an inclusive range of days."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(description="Source name from list_sources, e.g. 'era5_arco'")
    variables: list[str] = Field(min_length=1, description="Variable names the source lists")
    lat_bounds: tuple[float, float] = Field(description="[south, north] in degrees")
    lon_bounds: tuple[float, float] = Field(
        description="[west, east] in degrees, either -180..180 or 0..360"
    )
    start: date = Field(description="First day, inclusive")
    end: date = Field(description="Last day, inclusive")

    @model_validator(mode="after")
    def _dates_in_order_and_box_on_the_globe(self) -> FetchRequest:
        if self.end < self.start:
            raise ValueError(f"end {self.end} is before start {self.start}")
        BBox.parse(self.lat_bounds, self.lon_bounds)
        return self

    @property
    def bbox(self) -> BBox:
        return BBox.parse(self.lat_bounds, self.lon_bounds)

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1


@dataclass(frozen=True)
class Variable:
    description: str
    units: str


@dataclass(frozen=True)
class Fetched:
    path: Path
    source_version: str


@dataclass(frozen=True)
class Source:
    name: str
    version: str
    description: str
    hosts: tuple[str, ...]
    grid_deg: float
    variables: dict[str, Variable]
    fetch: Callable[[FetchRequest, Path, Path], Fetched]
    # The last day the source has data for, looked up from the source; None if it has no end.
    record_end: Callable[[], date] | None = None


@dataclass(frozen=True)
class FetchPlan:
    request: FetchRequest
    grid_points: int
    estimated_bytes: int


def plan(source: Source, request: FetchRequest) -> FetchPlan:
    """Check ``request`` against ``source`` and estimate its size from the catalog alone."""
    unknown = sorted(set(request.variables) - set(source.variables))
    if unknown:
        raise ValueError(
            f"{source.name} has no variable {', '.join(unknown)}; it offers {', '.join(source.variables)}"
        )
    grid_points = _points(request.lat_bounds, source.grid_deg) * _points(
        request.lon_bounds, source.grid_deg
    )
    values = len(request.variables) * request.days * grid_points
    return FetchPlan(request, grid_points, values * FLOAT32_BYTES)


def _points(bounds: tuple[float, float], spacing: float) -> int:
    return math.floor(abs(bounds[1] - bounds[0]) / spacing) + 1
