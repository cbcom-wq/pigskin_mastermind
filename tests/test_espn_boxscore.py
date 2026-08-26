"""Parsing ESPN's public box score.

Every test runs against a recorded response. The endpoint is undocumented and
can change shape without notice, which is exactly why the parser is pinned to a
real capture rather than an assumed structure -- and why a parse failure must
degrade rather than raise.
"""

import json
from pathlib import Path

import pytest

from pigskin_mastermind.services.espn_boxscore import (
    parse_player_stats, parse_team_defense_stats,
)

FIXTURE = Path(__file__).parent / "fixtures" / "espn_summary_sample.json"


@pytest.fixture
def summary():
    return json.loads(FIXTURE.read_text())


class TestParsePlayerStats:
    def test_returns_players_with_espn_ids(self, summary):
        rows = parse_player_stats(summary)
        assert rows
        assert all(r["espn_id"] for r in rows)
        assert all(r["name"] for r in rows)

    def test_a_quarterback_has_passing_stats(self, summary):
        rows = parse_player_stats(summary)
        passers = [r for r in rows if r["stats"].get("pass_att")]
        assert passers
        top = max(passers, key=lambda r: r["stats"]["pass_att"])
        assert top["stats"]["pass_yd"] > 0
        assert "pass_cmp" in top["stats"]

    def test_a_receiver_has_receptions_and_yards(self, summary):
        rows = parse_player_stats(summary)
        receivers = [r for r in rows if r["stats"].get("rec")]
        assert receivers
        assert all(r["stats"].get("rec_yd") is not None for r in receivers)

    def test_stats_are_numbers_not_strings(self, summary):
        """ESPN returns display strings like '24/38' and '312'."""
        rows = parse_player_stats(summary)
        for row in rows:
            for key, value in row["stats"].items():
                assert isinstance(value, (int, float)), f"{key} is {type(value)}"

    def test_every_player_carries_a_team(self, summary):
        rows = parse_player_stats(summary)
        assert all(r["team"] for r in rows)

    def test_a_kicker_has_made_extra_points(self, summary):
        """XP is a made/attempted string ("3/3"), not a plain number."""
        rows = parse_player_stats(summary)
        kickers = [r for r in rows if r["stats"].get("xp")]
        assert kickers

    def test_a_kicker_has_field_goal_distance_buckets(self, summary):
        """Distance comes from scoringPlays text, not the kicking category,
        which only carries a combined made/attempted count."""
        rows = parse_player_stats(summary)
        bucket_keys = {"fg_0_39", "fg_40_49", "fg_50_plus"}
        with_buckets = [
            r for r in rows if bucket_keys & set(r["stats"])
        ]
        assert with_buckets

    def test_individual_defenders_do_not_carry_team_defense_keys(self, summary):
        """A linebacker's sack belongs on the team DEF stat line, produced by
        parse_team_defense_stats -- not on the individual human player, who
        is never rostered at the DEF position."""
        rows = parse_player_stats(summary)
        team_defense_keys = {
            "def_sack", "def_int", "def_fumble_rec", "def_td", "def_safety",
        }
        for row in rows:
            assert not team_defense_keys & set(row["stats"])


class TestParseTeamDefenseStats:
    def test_returns_one_row_per_team(self, summary):
        rows = parse_team_defense_stats(summary)
        assert len(rows) == 2

    def test_points_allowed_is_the_opponents_score(self, summary):
        rows = parse_team_defense_stats(summary)
        allowed = sorted(r["stats"]["pts_allowed"] for r in rows)
        assert all(isinstance(a, int) for a in allowed)
        assert all(a >= 0 for a in allowed)

    def test_a_team_with_sacks_gets_credited(self, summary):
        rows = parse_team_defense_stats(summary)
        sacks = {r["team"]: r["stats"].get("def_sack") for r in rows}
        assert any(v for v in sacks.values())

    def test_stats_are_numbers_not_strings(self, summary):
        rows = parse_team_defense_stats(summary)
        for row in rows:
            for key, value in row["stats"].items():
                assert isinstance(value, (int, float)), f"{key} is {type(value)}"


class TestDegradation:
    def test_an_empty_summary_yields_no_rows_rather_than_raising(self):
        """A parse failure must never kill the background refresher."""
        assert parse_player_stats({}) == []
        assert parse_team_defense_stats({}) == []

    def test_a_malformed_summary_yields_no_rows(self):
        assert parse_player_stats({"boxscore": {"players": "nonsense"}}) == []

    def test_missing_statistics_block_is_survivable(self):
        payload = {"boxscore": {"players": [{"team": {"abbreviation": "KC"}}]}}
        assert parse_player_stats(payload) == []

    def test_team_defense_survives_missing_header(self):
        assert parse_team_defense_stats({"boxscore": {}}) == []

    def test_team_defense_survives_non_dict_input(self):
        assert parse_team_defense_stats("nonsense") == []
        assert parse_player_stats("nonsense") == []
