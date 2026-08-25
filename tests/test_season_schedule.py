"""Schedule generation invariants.

A malformed schedule is silent until someone notices in October that they
played the same team four times, so every structural property is asserted here.
"""

import pytest

from pigskin_mastermind.services.season_schedule import (
    SUPPORTED_BRACKETS, clamp_playoff_teams, playoff_rounds,
    regular_season_schedule, round_robin_pairings,
)


class TestRoundRobin:
    def test_one_rotation_is_n_minus_one_rounds(self):
        rounds = round_robin_pairings(12)
        assert len(rounds) == 11
        assert all(len(r) == 6 for r in rounds)

    def test_everyone_plays_exactly_once_per_round(self):
        for round_pairs in round_robin_pairings(12):
            seen = [t for pair in round_pairs for t in pair]
            assert sorted(seen) == list(range(12))

    def test_nobody_plays_themselves(self):
        for round_pairs in round_robin_pairings(12):
            assert all(home != away for home, away in round_pairs)

    def test_every_pair_meets_exactly_once_in_a_rotation(self):
        met = set()
        for round_pairs in round_robin_pairings(10):
            for home, away in round_pairs:
                key = frozenset((home, away))
                assert key not in met
                met.add(key)
        assert len(met) == 45  # 10 choose 2

    def test_odd_team_counts_are_refused(self):
        """A round robin over an odd count leaves someone idle every week, and
        an idle week is neither a win, a loss, nor a bye."""
        with pytest.raises(ValueError, match="even"):
            round_robin_pairings(11)

    def test_two_teams_is_a_valid_rotation(self):
        assert round_robin_pairings(2) == [[(0, 1)]]


class TestRegularSeasonSchedule:
    def test_produces_the_requested_number_of_weeks(self):
        assert len(regular_season_schedule(12, 14)) == 14

    def test_everyone_plays_every_week(self):
        for week_pairs in regular_season_schedule(12, 14):
            seen = [t for pair in week_pairs for t in pair]
            assert sorted(seen) == list(range(12))

    def test_repeat_cycles_flip_home_and_away(self):
        """Weeks 12-14 repeat rounds 1-3 for a 12-team league; the venue must
        alternate rather than handing the same team home field twice."""
        weeks = regular_season_schedule(12, 14)
        assert weeks[11] == [(away, home) for home, away in weeks[0]]

    def test_home_and_away_are_balanced_within_a_rotation(self):
        weeks = regular_season_schedule(10, 9)
        home_counts = {t: 0 for t in range(10)}
        for week_pairs in weeks:
            for home, _away in week_pairs:
                home_counts[home] += 1
        assert max(home_counts.values()) - min(home_counts.values()) <= 1


class TestPlayoffBracket:
    def test_supported_brackets_descend(self):
        assert SUPPORTED_BRACKETS == (6, 4, 2)

    def test_six_team_bracket_spans_three_weeks_ending_at_seventeen(self):
        rounds = playoff_rounds(6, 15)
        assert [r["week"] for r in rounds] == [15, 16, 17]
        assert [r["round_name"] for r in rounds] == [
            "quarterfinal", "semifinal", "final",
        ]
        assert [r["games"] for r in rounds] == [2, 2, 1]

    def test_four_team_bracket_leaves_week_fifteen_to_the_regular_season(self):
        rounds = playoff_rounds(4, 15)
        assert [r["week"] for r in rounds] == [16, 17]
        assert [r["round_name"] for r in rounds] == ["semifinal", "final"]

    def test_two_team_bracket_is_the_final_only(self):
        rounds = playoff_rounds(2, 15)
        assert [r["week"] for r in rounds] == [17]
        assert rounds[0]["games"] == 1

    def test_clamping_never_exceeds_the_league_size(self):
        assert clamp_playoff_teams(12, 6) == 6
        assert clamp_playoff_teams(4, 6) == 4
        assert clamp_playoff_teams(2, 6) == 2

    def test_clamping_respects_a_smaller_request(self):
        assert clamp_playoff_teams(12, 4) == 4
        assert clamp_playoff_teams(12, 2) == 2

    def test_an_unsupported_request_falls_to_the_next_bracket_down(self):
        assert clamp_playoff_teams(12, 5) == 4
        assert clamp_playoff_teams(12, 8) == 6
