"""Tests for ESPN stat name mapping."""

from pigskin_mastermind.services.espn_stats_mapper import (
    map_espn_breakdown_to_stats,
    map_espn_stat_ids_to_stats,
    _to_int,
)


class TestMapEspnBreakdownToStats:
    def test_passing_stats(self):
        breakdown = {
            'passingAttempts': 35,
            'passingCompletions': 24,
            'passingYards': 310,
            'passingTouchdowns': 3,
            'passingInterceptions': 1,
        }
        result = map_espn_breakdown_to_stats(breakdown)
        assert result['pass_att'] == 35
        assert result['pass_cmp'] == 24
        assert result['pass_yd'] == 310
        assert result['pass_td'] == 3
        assert result['pass_int'] == 1

    def test_rushing_stats(self):
        breakdown = {
            'rushingAttempts': 15,
            'rushingYards': 85,
            'rushingTouchdowns': 1,
        }
        result = map_espn_breakdown_to_stats(breakdown)
        assert result['rush_att'] == 15
        assert result['rush_yd'] == 85
        assert result['rush_td'] == 1

    def test_receiving_stats(self):
        breakdown = {
            'receivingTargets': 8,
            'receivingReceptions': 6,
            'receivingYards': 95,
            'receivingTouchdowns': 1,
        }
        result = map_espn_breakdown_to_stats(breakdown)
        assert result['targets'] == 8
        assert result['rec'] == 6
        assert result['rec_yd'] == 95
        assert result['rec_td'] == 1

    def test_2pt_conversions_sum(self):
        breakdown = {
            'passing2PtConversions': 1,
            'rushing2PtConversions': 1,
        }
        result = map_espn_breakdown_to_stats(breakdown)
        assert result['two_pt_conversions'] == 2

    def test_fumble_stats(self):
        breakdown = {
            'fumbles': 2,
            'lostFumbles': 1,
        }
        result = map_espn_breakdown_to_stats(breakdown)
        assert result['fumbles'] == 2
        assert result['fumbles_lost'] == 1

    def test_unknown_keys_ignored(self):
        breakdown = {
            'passingYards': 200,
            'unknownStat': 99,
            'passing40PlusYardTD': 1,
        }
        result = map_espn_breakdown_to_stats(breakdown)
        assert 'pass_yd' in result
        assert 'unknownStat' not in result
        assert len(result) == 1

    def test_empty_breakdown(self):
        result = map_espn_breakdown_to_stats({})
        assert result == {}

    def test_float_values_rounded(self):
        breakdown = {'passingYards': 310.7}
        result = map_espn_breakdown_to_stats(breakdown)
        assert result['pass_yd'] == 311


class TestMapEspnStatIdsToStats:
    def test_numeric_id_mapping(self):
        stat_dict = {
            0: 35.0,   # pass_att
            1: 24.0,   # pass_cmp
            3: 310.0,  # pass_yd
            4: 3.0,    # pass_td
            20: 1.0,   # pass_int
        }
        result = map_espn_stat_ids_to_stats(stat_dict)
        assert result['pass_att'] == 35
        assert result['pass_cmp'] == 24
        assert result['pass_yd'] == 310
        assert result['pass_td'] == 3
        assert result['pass_int'] == 1

    def test_string_id_keys(self):
        stat_dict = {'3': 250.0, '4': 2.0}
        result = map_espn_stat_ids_to_stats(stat_dict)
        assert result['pass_yd'] == 250
        assert result['pass_td'] == 2

    def test_receiving_ids(self):
        stat_dict = {
            41: 6.0,
            42: 95.0,
            43: 1.0,
            58: 8.0,
        }
        result = map_espn_stat_ids_to_stats(stat_dict)
        assert result['rec'] == 6
        assert result['rec_yd'] == 95
        assert result['rec_td'] == 1
        assert result['targets'] == 8


class TestToInt:
    def test_int_input(self):
        assert _to_int(5) == 5

    def test_float_input(self):
        assert _to_int(5.7) == 6

    def test_string_input(self):
        assert _to_int("10") == 10

    def test_none_input(self):
        assert _to_int(None) == 0

    def test_invalid_input(self):
        assert _to_int("abc") == 0


class TestKickingStatIds:
    """ESPN ids from the vendored PLAYER_STATS_MAP: 74/77/80 made by distance,
    85 missed, 86 made extra points."""

    def test_field_goals_map_by_distance_bucket(self):
        stats = map_espn_stat_ids_to_stats({80: 2, 77: 1, 74: 1})
        assert stats == {"fg_0_39": 2, "fg_40_49": 1, "fg_50_plus": 1}

    def test_missed_field_goals_and_extra_points(self):
        stats = map_espn_stat_ids_to_stats({85: 1, 86: 3})
        assert stats == {"fg_miss": 1, "xp": 3}


class TestDefensiveStatIds:
    """ESPN ids 94 TD, 95 INT, 96 fumble recovery, 98 safety, 99 sack,
    120 points allowed."""

    def test_defensive_counting_stats(self):
        stats = map_espn_stat_ids_to_stats({99: 3, 95: 2, 96: 1, 94: 1, 98: 1})
        assert stats == {
            "def_sack": 3, "def_int": 2, "def_fumble_rec": 1,
            "def_td": 1, "def_safety": 1,
        }

    def test_points_allowed_is_carried_through(self):
        assert map_espn_stat_ids_to_stats({120: 17}) == {"pts_allowed": 17}

    def test_breakdown_keys_map_too(self):
        """Box scores sometimes arrive keyed by name rather than id."""
        stats = map_espn_breakdown_to_stats({
            "defensiveSacks": 2, "madeFieldGoalsFromUnder40": 1,
        })
        assert stats == {"def_sack": 2, "fg_0_39": 1}
