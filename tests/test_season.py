"""Tests for the current_fantasy_season helper."""

from datetime import date

from pigskin_mastermind.utils.season import current_fantasy_season


def test_midsummer_is_current_year():
    assert current_fantasy_season(date(2026, 7, 26)) == 2026


def test_january_belongs_to_prior_season():
    assert current_fantasy_season(date(2027, 1, 15)) == 2026


def test_february_belongs_to_prior_season():
    assert current_fantasy_season(date(2027, 2, 28)) == 2026


def test_march_rolls_over_to_new_season():
    assert current_fantasy_season(date(2027, 3, 1)) == 2027


def test_december_is_current_year():
    assert current_fantasy_season(date(2026, 12, 31)) == 2026


def test_defaults_to_today():
    assert current_fantasy_season() >= 2026
