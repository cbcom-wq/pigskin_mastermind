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
        """XP is a made/attempted string ("3/3"), not a plain number.

        Cameron Dicker went 3/3 on PATs in the recorded game.
        """
        rows = parse_player_stats(summary)
        kickers = {r["name"]: r["stats"]["xp"] for r in rows if "xp" in r["stats"]}
        assert kickers["Cameron Dicker"] == 3

    def test_a_kicker_has_field_goal_distance_buckets(self, summary):
        """Distance comes from scoringPlays text, not the kicking category,
        which only carries a combined made/attempted count.

        Harrison Butker made field goals from 35, 59, and 27 yards in the
        recorded game: two under 40 and one 50+, none in the 40-49 band.
        """
        rows = parse_player_stats(summary)
        butker = next(r for r in rows if r["name"] == "Harrison Butker")
        assert butker["stats"]["fg_0_39"] == 2
        assert butker["stats"]["fg_50_plus"] == 1
        assert "fg_40_49" not in butker["stats"]

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


class TestDefensiveAttributionDirection:
    """The highest-risk logic in this module: which defense gets the credit.

    The recorded fixture has zero fumbles on both sides and symmetric-looking
    sacks, so it cannot distinguish correct attribution from inverted
    attribution. These use a hand-built payload with deliberately asymmetric
    values so a direction regression fails loudly.
    """

    @staticmethod
    def _payload():
        def team_block(abbr, sacks, ints, fumbles_lost):
            return {
                "team": {"abbreviation": abbr},
                "statistics": [
                    {"name": "defensive", "labels": ["SACKS"],
                     "totals": [str(sacks)], "athletes": []},
                    {"name": "interceptions", "labels": ["INT", "TD"],
                     "totals": [str(ints), "0"], "athletes": []},
                    {"name": "fumbles", "labels": ["FUM", "LOST", "REC"],
                     "totals": ["9", str(fumbles_lost), "7"], "athletes": []},
                ],
            }

        return {
            "header": {"competitions": [{"competitors": [
                {"team": {"abbreviation": "AAA"}, "score": "31",
                 "homeAway": "home"},
                {"team": {"abbreviation": "BBB"}, "score": "10",
                 "homeAway": "away"},
            ]}]},
            # AAA lost 3 fumbles and took 1 sack; BBB lost 0 and took 5.
            "boxscore": {"players": [
                team_block("AAA", sacks=5, ints=2, fumbles_lost=3),
                team_block("BBB", sacks=1, ints=0, fumbles_lost=0),
            ]},
        }

    def _by_team(self):
        rows = parse_team_defense_stats(self._payload())
        return {r["team"]: r["stats"] for r in rows}

    def test_fumble_recoveries_are_credited_to_the_opponent_of_the_loser(self):
        """AAA lost 3 fumbles, so BBB's defense recovered them -- not AAA's."""
        stats = self._by_team()
        assert stats["BBB"]["def_fumble_rec"] == 3
        assert stats["AAA"]["def_fumble_rec"] == 0

    def test_fumble_recoveries_do_not_read_the_teams_own_rec_column(self):
        """Both teams have REC=7, which conflates self-recovery with
        turnovers. If either team shows 7, the implementation regressed to
        reading REC."""
        stats = self._by_team()
        assert stats["AAA"]["def_fumble_rec"] != 7
        assert stats["BBB"]["def_fumble_rec"] != 7

    def test_sacks_and_interceptions_stay_with_their_own_defense(self):
        """Unlike fumbles, these belong to the team that recorded them."""
        stats = self._by_team()
        assert stats["AAA"]["def_sack"] == 5
        assert stats["BBB"]["def_sack"] == 1
        assert stats["AAA"]["def_int"] == 2
        assert stats["BBB"]["def_int"] == 0

    def test_points_allowed_is_the_opponents_score(self):
        stats = self._by_team()
        assert stats["AAA"]["pts_allowed"] == 10
        assert stats["BBB"]["pts_allowed"] == 31
