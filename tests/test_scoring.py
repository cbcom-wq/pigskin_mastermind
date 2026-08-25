"""Scoring a stat line, including the two positions the old table could not.

DEFAULT_SCORING_SETTINGS was offense-only, so a kicker and a defense scored
exactly 0.0 forever. Nothing caught it because nothing in the app scored a
lineup from a stat line — the ESPN sync imports totals ESPN already computed.
"""

import pytest

from pigskin_mastermind.models.database import DEFAULT_SCORING_SETTINGS
from pigskin_mastermind.services.scoring import (
    DEFAULT_PTS_ALLOWED_TIERS, points_allowed_score, score_stat_line,
)


def test_offense_scoring_is_unchanged():
    """Half PPR: 100 rush yards, 1 TD, 5 catches for 50 = 10 + 6 + 2.5 + 5."""
    stats = {"rush_yd": 100, "rush_td": 1, "rec": 5, "rec_yd": 50}
    assert score_stat_line(stats, DEFAULT_SCORING_SETTINGS) == pytest.approx(23.5)


def test_unknown_stats_are_ignored():
    stats = {"rush_yd": 100, "snap_pct": 88.0, "helmet_color": 3}
    assert score_stat_line(stats, DEFAULT_SCORING_SETTINGS) == pytest.approx(10.0)


def test_kicker_scores_by_field_goal_distance():
    """Two short, one mid, one long, one miss = 6 + 4 + 5 - 1, plus 3 XP."""
    stats = {
        "fg_0_39": 2, "fg_40_49": 1, "fg_50_plus": 1, "fg_miss": 1, "xp": 3,
    }
    assert score_stat_line(stats, DEFAULT_SCORING_SETTINGS) == pytest.approx(17.0)


def test_defense_scores_sacks_turnovers_and_touchdowns():
    """3 sacks, 2 INT, 1 fumble rec, 1 TD, 1 safety, 3 points allowed."""
    stats = {
        "def_sack": 3, "def_int": 2, "def_fumble_rec": 1,
        "def_td": 1, "def_safety": 1, "pts_allowed": 3,
    }
    # 3 + 4 + 2 + 6 + 2 = 17, plus the 1-6 tier's 7 = 24
    assert score_stat_line(stats, DEFAULT_SCORING_SETTINGS) == pytest.approx(24.0)


@pytest.mark.parametrize("allowed,expected", [
    (0, 10.0), (1, 7.0), (6, 7.0), (7, 4.0), (13, 4.0),
    (14, 1.0), (20, 1.0), (21, 0.0), (27, 0.0),
    (28, -1.0), (34, -1.0), (35, -4.0), (70, -4.0),
])
def test_points_allowed_tier_boundaries(allowed, expected):
    """Every boundary, because off-by-one here is silent and permanent."""
    assert points_allowed_score(allowed) == expected


def test_points_allowed_is_a_step_not_a_multiplier():
    """A shutout must not be worth zero just because the stat value is zero."""
    assert score_stat_line({"pts_allowed": 0}, DEFAULT_SCORING_SETTINGS) == 10.0


def test_league_settings_override_defaults():
    """A full-PPR league scores receptions at 1.0, not the 0.5 default."""
    settings = dict(DEFAULT_SCORING_SETTINGS)
    settings["rec"] = 1.0
    assert score_stat_line({"rec": 6}, settings) == pytest.approx(6.0)


def test_custom_tiers_are_honoured():
    tiers = ((0, 20.0), (10, 5.0))
    assert points_allowed_score(0, tiers=tiers) == 20.0
    assert points_allowed_score(9, tiers=tiers) == 5.0
    assert points_allowed_score(11, tiers=tiers, floor=-9.0) == -9.0


def test_default_tiers_are_exposed_for_league_customisation():
    assert DEFAULT_PTS_ALLOWED_TIERS[0] == (0, 10.0)
    assert len(DEFAULT_PTS_ALLOWED_TIERS) == 6


def test_two_point_conversions_score_from_the_espn_mapper_key():
    """The mappers emit `two_pt_conversions`; a settings table that only knows
    `two_pt` silently scores every 2PT as zero."""
    from pigskin_mastermind.services.espn_stats_mapper import map_espn_stat_ids_to_stats
    stats = map_espn_stat_ids_to_stats({19: 2})
    assert stats == {"two_pt_conversions": 2}
    assert score_stat_line(stats, DEFAULT_SCORING_SETTINGS) == pytest.approx(4.0)
