"""Tests for utils.nfl_teams – canonical NFL team abbreviation handling."""

import pytest

from pigskin_mastermind.utils.nfl_teams import NFL_TEAMS, normalize_team


class TestCanonicalValues:
    def test_all_32_canonical_abbreviations_pass_through(self):
        assert len(NFL_TEAMS) == 32
        for team in NFL_TEAMS:
            assert normalize_team(team) == team

    def test_canonical_set_uses_was_not_wsh(self):
        # FFC, nfl_data_py, and mock_draft._ESPN_TEAM_MAP all spell it WAS.
        assert "WAS" in NFL_TEAMS
        assert "WSH" not in NFL_TEAMS


class TestAliases:
    @pytest.mark.parametrize(
        "alias,expected",
        [
            ("WSH", "WAS"),   # ESPN / vendored espn_api
            ("JAC", "JAX"),
            ("LA", "LAR"),
            ("STL", "LAR"),
            ("SD", "LAC"),
            ("OAK", "LV"),
            ("ARZ", "ARI"),
            ("BLT", "BAL"),
            ("CLV", "CLE"),
            ("HST", "HOU"),
            ("GNB", "GB"),
            ("KAN", "KC"),
            ("NWE", "NE"),
            ("NOR", "NO"),
            ("SFO", "SF"),
            ("TAM", "TB"),
            ("LVR", "LV"),
        ],
    )
    def test_alias_maps_to_canonical(self, alias, expected):
        assert normalize_team(alias) == expected


class TestNormalization:
    def test_lowercase_is_upcased(self):
        assert normalize_team("wsh") == "WAS"
        assert normalize_team("phi") == "PHI"

    def test_surrounding_whitespace_is_stripped(self):
        assert normalize_team("  KC  ") == "KC"
        assert normalize_team(" jac ") == "JAX"


class TestUnknownValues:
    @pytest.mark.parametrize("value", ["FA", "fa", "", "   ", None, "XXX", "FREE AGENT"])
    def test_returns_none(self, value):
        assert normalize_team(value) is None

    def test_none_means_leave_existing_alone_not_write_none(self):
        # Documents the contract callers rely on: an unusable source value must
        # never be written over a good stored team.
        existing = "PHI"
        incoming = normalize_team("FA")
        assert incoming is None
        assert (incoming or existing) == "PHI"
