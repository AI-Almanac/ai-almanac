"""Whether a blend is trained on a country's sub-districts rather than on grid cells.

The blend pipeline holds sub-district outlines for a few countries only. A blend is
in a country when its area names one, or when its observations are filed under a
region that is one.
"""

from __future__ import annotations

SUB_DISTRICT_COUNTRIES = frozenset({"ETH"})


def on_sub_districts(country: str | None) -> bool:
    return country in SUB_DISTRICT_COUNTRIES
