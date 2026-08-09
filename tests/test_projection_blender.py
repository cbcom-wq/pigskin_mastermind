"""Weighted consensus across projection sources."""

import pytest

from pigskin_mastermind.services.projection_blender import (
    blend,
    SEASON_WEIGHTS,
    WEEKLY_WEIGHTS,
)


def test_all_sources_present_uses_nominal_weights():
    r = blend({"model": 300.0, "espn": 200.0, "adp": 100.0}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(0.5 * 300 + 0.3 * 200 + 0.2 * 100)


def test_missing_source_renormalizes_rather_than_dragging_to_zero():
    """Dropping ESPN must not pull the blend down by 30%.

    A naive implementation multiplies by 0.5 and 0.2 and returns 200 for a
    player both remaining sources call ~300 -- the failure mode this test
    exists to prevent.
    """
    r = blend({"model": 300.0, "adp": 300.0}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(300.0)
    assert sum(r.weights_used.values()) == pytest.approx(1.0)
    assert set(r.weights_used) == {"model", "adp"}


def test_single_source_returns_that_source_exactly():
    r = blend({"model": 271.4}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(271.4)
    assert r.weights_used == {"model": 1.0}


def test_none_values_are_absent_not_zero():
    r = blend({"model": 300.0, "espn": None, "adp": None}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(300.0)


def test_no_sources_returns_none():
    assert blend({}, SEASON_WEIGHTS) is None
    assert blend({"model": None}, SEASON_WEIGHTS) is None


def test_unknown_source_is_ignored():
    """A source with no weight cannot silently contribute."""
    r = blend({"model": 300.0, "vibes": 999.0}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(300.0)
    assert "vibes" not in r.weights_used


def test_weekly_weights_favour_the_market():
    assert WEEKLY_WEIGHTS["sportsbook"] > WEEKLY_WEIGHTS["model"]
    assert sum(WEEKLY_WEIGHTS.values()) == pytest.approx(1.0)
    assert sum(SEASON_WEIGHTS.values()) == pytest.approx(1.0)


def test_weekly_with_only_model_returns_model():
    """Today's real weekly case: no live odds, ESPN weekly unusable."""
    r = blend({"model": 17.5}, WEEKLY_WEIGHTS)
    assert r.points == pytest.approx(17.5)
    assert r.weights_used == {"model": 1.0}


def test_zero_is_a_real_value_not_a_missing_one():
    """A bye-week 0.0 must count, or the gate is silently discarded."""
    r = blend({"model": 0.0, "sportsbook": 10.0}, WEEKLY_WEIGHTS)
    assert r.points == pytest.approx((0.35 * 0.0 + 0.45 * 10.0) / (0.35 + 0.45))
