"""The GraphCast speed-ups apply only where they were checked and never stop a
forecast; the graph layout they sum with is checked here, the patches
themselves (which need JAX and a GPU) by envs/speedups_check.py."""

from __future__ import annotations

import logging

import numpy as np
import pytest

from ai_almanac.server.services.forecast_model_opt import graphcast_speedups
from ai_almanac.server.services.forecast_model_opt.graphcast_speedups import Speedup


class GraphCastOperational:
    """Stands in for earth2studio's model: only its class name is read."""


@pytest.fixture
def weathernext(monkeypatch: pytest.MonkeyPatch):
    def set_version(value: str, *, verified: str | None = None) -> None:
        monkeypatch.setattr(graphcast_speedups, "version", lambda package: value)
        if verified:
            monkeypatch.setitem(graphcast_speedups.VERIFIED, "GraphCastOperational", {verified})

    return set_version


def test_nothing_applies_until_a_weathernext_version_is_verified(weathernext, monkeypatch) -> None:
    weathernext("0.3.1.dev0")
    monkeypatch.delitem(graphcast_speedups.VERIFIED, "GraphCastOperational", raising=False)

    assert graphcast_speedups.apply(GraphCastOperational()) == []


def test_an_unverified_version_applies_nothing_and_says_so(
    weathernext, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    weathernext("0.4.0", verified="0.3.1.dev0")
    monkeypatch.setitem(
        graphcast_speedups.SPEEDUPS,
        "GraphCastOperational",
        (Speedup("never", lambda model: pytest.fail("ran"), True),),
    )

    with caplog.at_level(logging.WARNING):
        assert graphcast_speedups.apply(GraphCastOperational()) == []

    assert "weathernext 0.4.0 is not verified" in caplog.text


@pytest.mark.parametrize(("setting", "inexact"), [(None, True), ("1", False)])
def test_a_failing_speed_up_is_skipped_and_exact_only_drops_the_inexact_ones(
    weathernext, monkeypatch, caplog: pytest.LogCaptureFixture, setting, inexact
) -> None:
    weathernext("0.3.1.dev0", verified="0.3.1.dev0")

    def broken(model):
        raise AttributeError("weathernext moved this")

    monkeypatch.setitem(
        graphcast_speedups.SPEEDUPS,
        "GraphCastOperational",
        (
            Speedup("rounds differently", lambda model: True, False),
            Speedup("broken", broken, True),
            Speedup("exact", lambda model: True, True),
        ),
    )
    if setting is None:
        monkeypatch.delenv(graphcast_speedups.EXACT_ONLY_ENV, raising=False)
    else:
        monkeypatch.setenv(graphcast_speedups.EXACT_ONLY_ENV, setting)

    with caplog.at_level(logging.ERROR):
        applied = graphcast_speedups.apply(GraphCastOperational())

    assert applied == (["rounds differently", "exact"] if inexact else ["exact"])
    assert "GraphCast speed-up 'broken' not applied" in caplog.text


def _sum(layout, data: np.ndarray) -> np.ndarray:
    """_fixed_order_segment_sum's arithmetic, in numpy."""
    groups, where = layout
    rows = np.concatenate([data, [0.0]])
    sums = np.concatenate([rows[slots].sum(axis=1) for slots in groups] + [[0.0]])
    return sums[:-1] if where is None else sums[where]


def test_each_nodes_in_edges_are_summed_in_edge_order_grouped_by_in_degree() -> None:
    # Edges 0..6 into nodes 2, 0, 2, 1, 0, 2, 2; node 3 receives none.
    receivers = np.array([2, 0, 2, 1, 0, 2, 2])

    groups, where = graphcast_speedups._in_edges(receivers, 4)

    # In-degrees 1, 2 and 4 fall in different bands; padding points one past
    # the last edge, at the zero row the sum appends.
    assert [g.tolist() for g in groups] == [[[3]], [[1, 4]], [[0, 2, 5, 6]]]
    assert where.tolist() == [1, 0, 2, 3]
    data = np.arange(1.0, 8.0)
    assert (
        _sum((groups, where), data).tolist()
        == np.bincount(receivers, weights=data, minlength=4).tolist()
    )


def test_an_even_graph_is_one_group_in_node_order() -> None:
    # Three edges into each node, as mesh2grid has.
    receivers = np.repeat(np.arange(5), 3)[np.random.default_rng(0).permutation(15)]

    groups, where = graphcast_speedups._in_edges(receivers, 5)

    assert len(groups) == 1 and where is None
    data = np.random.default_rng(1).normal(size=15)
    np.testing.assert_allclose(_sum((groups, where), data), np.bincount(receivers, weights=data))


def test_a_very_uneven_graph_pads_at_most_twice_its_edges() -> None:
    # One hub with 1000 in-edges, and 1000 nodes with one each.
    receivers = np.concatenate([np.zeros(1000, dtype=np.int64), np.arange(1, 1001)])

    groups, _ = graphcast_speedups._in_edges(receivers, 1001)

    assert sum(g.size for g in groups) <= 2 * receivers.size


def test_stray_ids_are_left_to_the_stock_sum() -> None:
    assert graphcast_speedups._in_edges(np.array([0, 3]), 3) is None
