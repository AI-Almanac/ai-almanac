"""The AIFS speed-ups apply only where they were checked, and never stop a
forecast: the patches themselves need a GPU and are checked by
envs/speedups_check.py in the forecast environment."""

from __future__ import annotations

import logging

import pytest

from ai_almanac.server.services.forecast_model_opt import aifs_speedups
from ai_almanac.server.services.forecast_model_opt.aifs_speedups import Speedup


class AIFS2ENS:
    """Stands in for earth2studio's model: only its class name is read."""


class AIFS2:
    pass


@pytest.fixture
def anemoi(monkeypatch: pytest.MonkeyPatch):
    def set_version(value: str) -> None:
        monkeypatch.setattr(aifs_speedups, "version", lambda package: value)

    return set_version


@pytest.fixture
def speedups(monkeypatch: pytest.MonkeyPatch):
    """Replace a model's speed-ups (and mark its anemoi version verified)."""

    def set_speedups(model_class: str, *entries: Speedup, verified: str = "0.11.2") -> None:
        monkeypatch.setitem(aifs_speedups.SPEEDUPS, model_class, entries)
        monkeypatch.setitem(aifs_speedups.VERIFIED, model_class, {verified})

    return set_speedups


def test_models_never_verified_are_left_alone(anemoi, monkeypatch: pytest.MonkeyPatch) -> None:
    anemoi("0.9.4")
    monkeypatch.delitem(aifs_speedups.VERIFIED, "AIFS2", raising=False)

    assert aifs_speedups.apply(AIFS2()) == []
    assert aifs_speedups.apply(object()) == []


def test_each_verified_model_has_its_own_speed_ups() -> None:
    for model_class in aifs_speedups.VERIFIED:
        assert aifs_speedups.SPEEDUPS[model_class]
    # What holds for one family is not assumed for the other.
    ens, single = ({s.name for s in aifs_speedups.SPEEDUPS[m]} for m in ("AIFS2ENS", "AIFS2"))
    assert "cached mapper graph work" in ens - single
    assert "Triton graph attention" in single - ens


def test_an_unverified_anemoi_version_applies_nothing_and_says_so(
    anemoi, speedups, caplog: pytest.LogCaptureFixture
) -> None:
    anemoi("0.12.0")
    speedups("AIFS2ENS", Speedup("never", lambda model: pytest.fail("ran"), True))

    with caplog.at_level(logging.WARNING):
        assert aifs_speedups.apply(AIFS2ENS()) == []

    assert "anemoi-models 0.12.0 is not verified" in caplog.text


def test_a_failing_speed_up_is_skipped_and_the_rest_still_apply(
    anemoi, speedups, caplog: pytest.LogCaptureFixture
) -> None:
    anemoi("0.9.4")

    def broken(model):
        raise AttributeError("anemoi moved this")

    speedups(
        "AIFS2",
        Speedup("first", lambda model: True, True),
        Speedup("broken", broken, True),
        Speedup("not here", lambda model: False, True),
        verified="0.9.4",
    )

    with caplog.at_level(logging.ERROR):
        assert aifs_speedups.apply(AIFS2()) == ["first"]

    assert "AIFS speed-up 'broken' not applied" in caplog.text


def test_the_pipeline_loads_the_speed_ups_from_beside_it() -> None:
    import sys

    from ai_almanac.server.services import forecast_pipeline

    sys.modules.pop("aifs_speedups", None)
    loaded = forecast_pipeline._speedups("aifs_speedups")

    assert loaded.VERIFIED == aifs_speedups.VERIFIED
    assert forecast_pipeline._speedups("aifs_speedups") is loaded
    assert "aifs_speedups" in forecast_pipeline.SPEEDUP_MODULES


@pytest.mark.parametrize(("setting", "inexact"), [(None, True), ("0", True), ("1", False)])
def test_the_inexact_speed_ups_apply_unless_exact_only_is_asked_for(
    anemoi, speedups, monkeypatch: pytest.MonkeyPatch, setting: str | None, inexact: bool
) -> None:
    anemoi("0.11.2")
    speedups(
        "AIFS2ENS",
        Speedup("rounds differently", lambda model: True, False),
        Speedup("exact", lambda model: True, True),
    )
    if setting is None:
        monkeypatch.delenv(aifs_speedups.EXACT_ONLY_ENV, raising=False)
    else:
        monkeypatch.setenv(aifs_speedups.EXACT_ONLY_ENV, setting)

    applied = aifs_speedups.apply(AIFS2ENS())

    assert applied == (["rounds differently", "exact"] if inexact else ["exact"])
