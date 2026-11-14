from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from ai_almanac.server.services.seasons import SeasonWindow, blend_season_window, resolve_season

KIREMT = SeasonWindow(name="Kiremt")


def test_in_april_the_season_is_upcoming_and_refused_with_its_first_issue_date():
    state = resolve_season(KIREMT, date(2026, 4, 15))

    assert state.phase == "upcoming"
    assert state.year == 2026
    assert state.days_until_start == 16
    assert state.refusal == (
        "Kiremt 2026 has not started: the first issue date is 1 May 2026, 16 days away."
    )


def test_in_june_the_season_is_under_way_through_today():
    state = resolve_season(KIREMT, date(2026, 6, 10))

    assert state.phase == "in_season"
    assert state.issued_through == date(2026, 6, 10)
    assert state.last_issue_date == date(2026, 7, 31)
    assert state.refusal is None


def test_in_november_the_season_has_ended_and_names_the_next_start():
    state = resolve_season(KIREMT, date(2026, 11, 5))

    assert state.phase == "ended"
    assert state.next_season_start == date(2027, 5, 1)
    assert state.summary == (
        "Kiremt 2026 ended on 31 July 2026; the first 2027 issue date is 1 May 2027."
    )
    assert "Ask for 2026 by year" in state.refusal


def test_an_ended_season_asked_for_by_year_is_a_re_forecast():
    state = resolve_season(KIREMT, date(2026, 11, 5), year=2024)

    assert state.phase == "ended"
    assert state.re_forecast is True
    assert state.issued_through == date(2024, 7, 31)
    assert state.summary.startswith("Re-forecast of Kiremt 2024")
    assert state.refusal is None


def test_a_future_year_asked_for_is_upcoming():
    assert resolve_season(KIREMT, date(2026, 11, 5), year=2027).phase == "upcoming"


def test_a_blend_forecast_keeps_the_onset_dates_the_blend_was_trained_on():
    window = blend_season_window(
        {"cutoff_month_day": "05-15", "ref_onset_month_day": "06-10"},
        SeasonWindow(name="Kiremt", onset_search_start="04-01", last_issue_date="08-15"),
    )

    assert window == SeasonWindow(
        name="Kiremt",
        onset_search_start="05-15",
        reference_onset="06-10",
        last_issue_date="08-15",
    )


def test_a_blend_with_no_season_given_runs_over_its_own_dates():
    window = blend_season_window({"issue_end_month_day": "08-31"}, None)

    assert (window.onset_search_start, window.last_issue_date) == ("05-01", "08-31")


def test_a_blend_whose_dates_cannot_make_a_season_is_refused():
    with pytest.raises(ValidationError, match="must come after the onset search start"):
        blend_season_window({"cutoff_month_day": "08-01", "ref_onset_month_day": "08-15"}, None)


def test_every_packaged_season_is_a_season_that_says_where_it_is_usual() -> None:
    from ai_almanac.server.services.seasons import season_presets

    presets = season_presets()

    assert presets, "at least one season is packaged"
    assert len({preset.id for preset in presets}) == len(presets)
    assert all(len(code) == 3 and code.isupper() for p in presets for code in p.countries)


def test_a_preset_is_its_window_plus_where_it_is_usual() -> None:
    from ai_almanac.server.services.seasons import parse_preset

    belg = parse_preset(
        {
            "id": "belg",
            "name": "Belg",
            "onset_search_start": "02-01",
            "reference_onset": "03-01",
            "last_issue_date": "04-30",
            "countries": ["eth"],
        }
    )

    assert (belg.window.name, belg.window.onset_search_start) == ("Belg", "02-01")
    assert belg.countries == ("ETH",)
