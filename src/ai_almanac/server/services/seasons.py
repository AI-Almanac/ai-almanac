"""The rainy season a workspace works in: when onset is searched for, and which issue
dates a forecast covers.

A season starts and ends in one calendar year. Zero-padded ``MM-DD`` strings compare
lexically in calendar order, which the checks below rely on.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from datetime import date, datetime
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, computed_field, model_validator

from ai_almanac.settings import get_packaged_seasons

# The fallback season, for a workspace that sets none and works where no preset is
# the usual one. The dates match the blend's DEFAULT_CUTOFF_MONTH_DAY /
# DEFAULT_REF_ONSET_MONTH_DAY and the blending app's issue_end_month_day.
DEFAULT_ONSET_SEARCH_START = "05-01"
DEFAULT_REFERENCE_ONSET = "06-01"
DEFAULT_LAST_ISSUE_DATE = "07-31"


def parse_month_day(value: str) -> str:
    """A zero-padded ``MM-DD`` that exists in every year; Feb 29 is refused because a
    season builds a date from it for each year."""
    try:
        datetime.strptime(f"2001-{value}", "%Y-%m-%d")
        valid = len(value) == 5
    except ValueError:
        valid = False
    if not valid:
        raise ValueError(
            f"must be a calendar date in MM-DD form (e.g. 05-01, not Feb 29), got {value!r}"
        )
    return value


MonthDay = Annotated[str, AfterValidator(parse_month_day)]


class SeasonWindow(BaseModel):
    """A workspace's rainy season, e.g. Ethiopia's Kiremt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = "Rainy season"
    onset_search_start: MonthDay = DEFAULT_ONSET_SEARCH_START
    reference_onset: MonthDay = DEFAULT_REFERENCE_ONSET
    last_issue_date: MonthDay = DEFAULT_LAST_ISSUE_DATE

    @model_validator(mode="after")
    def _ordered_within_one_year(self) -> Self:
        if self.reference_onset < self.onset_search_start:
            raise ValueError(
                f"the reference onset date {self.reference_onset} is before the onset search "
                f"start {self.onset_search_start}; onset cannot be found before the search begins"
            )
        if self.last_issue_date <= self.onset_search_start:
            raise ValueError(
                f"the last issue date {self.last_issue_date} must come after the onset search "
                f"start {self.onset_search_start}; a season that crosses the new year is not "
                "supported yet"
            )
        return self

    def issue_dates_in(self, year: int) -> tuple[date, date]:
        """The season's first and last issue dates in ``year``."""
        return _on(year, self.onset_search_start), _on(year, self.last_issue_date)


class SeasonPreset(BaseModel):
    """A season people already work in, and the countries where it is the usual one."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    window: SeasonWindow
    countries: tuple[str, ...] = ()


def parse_preset(entry: Mapping) -> SeasonPreset:
    window = {key: value for key, value in entry.items() if key not in ("id", "countries")}
    return SeasonPreset(
        id=entry["id"],
        window=SeasonWindow.model_validate(window),
        countries=tuple(str(code).upper() for code in entry.get("countries") or ()),
    )


@functools.cache
def season_presets() -> tuple[SeasonPreset, ...]:
    return tuple(parse_preset(entry) for entry in get_packaged_seasons())


def default_season(country: str | None = None) -> SeasonWindow:
    """The season a workspace with none of its own works in: the preset that is usual
    in its country (Ethiopia's Kiremt), else the fallback season."""
    usual = next((preset for preset in season_presets() if country in preset.countries), None)
    return usual.window if usual else SeasonWindow()


def _on(year: int, month_day: str) -> date:
    return date.fromisoformat(f"{year:04d}-{month_day}")


def _spoken(day: date) -> str:
    return f"{day.day} {day:%B %Y}"


def spoken_date(month_day: str) -> str:
    """``06-01`` → ``1 June``."""
    day = datetime.strptime(f"2001-{month_day}", "%Y-%m-%d")
    return f"{day.day} {day:%B}"


SeasonPhase = Literal["upcoming", "in_season", "ended"]


class SeasonState(BaseModel):
    """Where one season stands on a given day: the season a forecast plans."""

    model_config = ConfigDict(frozen=True)

    window: SeasonWindow
    phase: SeasonPhase
    year: int
    first_issue_date: date
    last_issue_date: date
    # The latest issue date the season has reached; None before it starts.
    issued_through: date | None
    days_until_start: int | None = None
    # When the season after this one opens; set once this one has ended.
    next_season_start: date | None = None
    # An ended season the request named by year, forecast again on purpose.
    re_forecast: bool = False

    @computed_field
    @property
    def summary(self) -> str:
        season = f"{self.window.name} {self.year}"
        if self.phase == "upcoming":
            return (
                f"{season} has not started: the first issue date is "
                f"{_spoken(self.first_issue_date)}, {self.days_until_start} days away."
            )
        if self.phase == "in_season":
            return (
                f"{season} is under way: issue dates from {_spoken(self.first_issue_date)} "
                f"to {_spoken(self.issued_through)} so far, the last on "
                f"{_spoken(self.last_issue_date)}."
            )
        if self.re_forecast:
            return (
                f"Re-forecast of {season}, which ran from {_spoken(self.first_issue_date)} "
                f"to {_spoken(self.last_issue_date)}."
            )
        return (
            f"{season} ended on {_spoken(self.last_issue_date)}; the first "
            f"{self.next_season_start.year} issue date is {_spoken(self.next_season_start)}."
        )

    @property
    def refusal(self) -> str | None:
        """Why this season can't be forecast now, in the user's terms; None when it can."""
        if self.phase == "upcoming":
            return self.summary
        if self.phase == "ended" and not self.re_forecast:
            return f"{self.summary} Ask for {self.year} by year to re-forecast it."
        return None


def resolve_season(window: SeasonWindow, today: date, year: int | None = None) -> SeasonState:
    """The season a forecast made on ``today`` plans: this year's, unless ``year``
    names another. Pure; the clock is ``today``."""
    season_year = today.year if year is None else year
    first, last = window.issue_dates_in(season_year)
    common = {
        "window": window,
        "year": season_year,
        "first_issue_date": first,
        "last_issue_date": last,
    }
    if today < first:
        return SeasonState(
            **common, phase="upcoming", issued_through=None, days_until_start=(first - today).days
        )
    if today <= last:
        return SeasonState(**common, phase="in_season", issued_through=today)
    return SeasonState(
        **common,
        phase="ended",
        issued_through=last,
        next_season_start=_next_season_start(window, today),
        re_forecast=year is not None,
    )


def _next_season_start(window: SeasonWindow, today: date) -> date:
    this_year = _on(today.year, window.onset_search_start)
    return this_year if today < this_year else _on(today.year + 1, window.onset_search_start)


def blend_season_window(blend_params: Mapping, requested: SeasonWindow | None) -> SeasonWindow:
    """The season a blend forecast runs over. The onset search start and reference
    onset are what the blend was trained on, so they always come from the blend;
    the name and last issue date come from the request when one is given.
    Raises ValueError when the combination is not a valid season."""
    trained = {
        "onset_search_start": blend_params.get("cutoff_month_day") or DEFAULT_ONSET_SEARCH_START,
        # mok_month_day is the pre-rename spelling older blends still carry.
        "reference_onset": blend_params.get("ref_onset_month_day")
        or blend_params.get("mok_month_day")
        or DEFAULT_REFERENCE_ONSET,
    }
    if requested is not None:
        return SeasonWindow(
            name=requested.name, last_issue_date=requested.last_issue_date, **trained
        )
    return SeasonWindow(
        last_issue_date=blend_params.get("issue_end_month_day") or DEFAULT_LAST_ISSUE_DATE,
        **trained,
    )
