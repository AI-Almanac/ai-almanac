"""What fetch's lookups hand the assistant: boxes in the shape a fetch takes, and a
record end that is not asked of an unreachable source on every call."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pytest

from ai_almanac.server.services import region_catalog
from ai_almanac.server.services.primitives import acquire


@pytest.mark.asyncio
async def test_a_region_resolves_to_the_bounds_a_fetch_takes(monkeypatch) -> None:
    async def regions():
        return [
            {
                "id": "ethiopia",
                "display_name": "Ethiopia",
                "lat_min": 3.0,
                "lat_max": 15.0,
                "lon_min": 33.0,
                "lon_max": 48.0,
            }
        ]

    monkeypatch.setattr(region_catalog, "list_regions", regions)

    region = await acquire.resolve_region.__wrapped__(None, "Ethiopia")

    assert region["lat_bounds"] == [3.0, 15.0]
    assert region["lon_bounds"] == [33.0, 48.0]
    assert "bbox" not in region


@dataclass
class _Unreachable:
    name: str = "unreachable-source"
    asked: list[int] = field(default_factory=list)

    def record_end(self) -> date:
        self.asked.append(1)
        raise OSError("bucket unreachable")


@pytest.mark.asyncio
async def test_an_unreachable_source_is_not_asked_again_on_every_call(monkeypatch) -> None:
    monkeypatch.setattr(acquire, "_record_ends", {})
    source = _Unreachable()

    first = await acquire._record_end(source)
    second = await acquire._record_end(source)

    assert first is None and second is None
    assert len(source.asked) == 1
