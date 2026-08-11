"""Tests for ADPService – Fantasy Football Calculator ADP integration."""

import json
import pytest
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBPlayer,
    DBPlayerProjection,
    DBPlayerSeasonStats,
)
from pigskin_mastermind.services.adp_service import ADPService

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def db():
    session = TestSessionLocal()
    yield session
    session.close()


def _seed_players(db):
    """Create a handful of players for matching tests."""
    players = [
        DBPlayer(player_id="espn_101", name="Saquon Barkley", position="RB", nfl_team="PHI"),
        DBPlayer(player_id="espn_102", name="Bijan Robinson", position="RB", nfl_team="ATL"),
        DBPlayer(player_id="espn_103", name="Ja'Marr Chase", position="WR", nfl_team="CIN"),
        DBPlayer(player_id="espn_104", name="Patrick Mahomes", position="QB", nfl_team="KC"),
        DBPlayer(player_id="espn_105", name="Brandon Aubrey", position="K", nfl_team="DAL"),
        DBPlayer(player_id="espn_106", name="Denver Defense", position="DEF", nfl_team="DEN"),
    ]
    db.add_all(players)
    db.commit()
    return players


def _ffc_response(players_list=None):
    """Return a minimal FFC-like JSON response."""
    if players_list is None:
        players_list = [
            {"player_id": 2860, "name": "Saquon Barkley", "position": "RB", "team": "PHI", "adp": 1.6,
             "adp_formatted": "1.02", "times_drafted": 19, "high": 1, "low": 3, "stdev": 0.8, "bye": 9},
            {"player_id": 5670, "name": "Bijan Robinson", "position": "RB", "team": "ATL", "adp": 2.0,
             "adp_formatted": "1.02", "times_drafted": 27, "high": 1, "low": 3, "stdev": 0.8, "bye": 5},
            {"player_id": 5177, "name": "Ja'Marr Chase", "position": "WR", "team": "CIN", "adp": 3.8,
             "adp_formatted": "1.04", "times_drafted": 75, "high": 1, "low": 7, "stdev": 1.7, "bye": 10},
            {"player_id": 2462, "name": "Patrick Mahomes", "position": "QB", "team": "KC", "adp": 49.5,
             "adp_formatted": "5.02", "times_drafted": 12, "high": 38, "low": 57, "stdev": 6.1, "bye": 10},
            {"player_id": 6162, "name": "Brandon Aubrey", "position": "PK", "team": "DAL", "adp": 137.0,
             "adp_formatted": "12.05", "times_drafted": 71, "high": 87, "low": 157, "stdev": 20.1, "bye": 10},
            {"player_id": 1309, "name": "Denver Defense", "position": "DEF", "team": "DEN", "adp": 109.4,
             "adp_formatted": "10.01", "times_drafted": 47, "high": 81, "low": 126, "stdev": 12.2, "bye": 12},
            {"player_id": 9999, "name": "Unknown Guy", "position": "WR", "team": "FA", "adp": 200.0,
             "adp_formatted": "15.01", "times_drafted": 5, "high": 190, "low": 210, "stdev": 5.0, "bye": 0},
        ]
    return json.dumps({
        "status": "Success",
        "meta": {"type": "PPR", "teams": 12, "rounds": 15, "total_drafts": 518,
                 "start_date": "2025-08-30", "end_date": "2025-09-01"},
        "players": players_list,
    }).encode("utf-8")


# ---------------------------------------------------------------------------
# fetch_ffc_adp
# ---------------------------------------------------------------------------


class TestFetchFFCAdp:
    def test_returns_players_on_success(self, db):
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.fetch_ffc_adp(year=2025, scoring="ppr", num_teams=12)

        assert result is not None
        assert len(result) == 7
        assert result[0]["name"] == "Saquon Barkley"
        assert result[0]["adp"] == 1.6

    def test_normalizes_pk_to_k(self, db):
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.fetch_ffc_adp(year=2025)

        kicker = [p for p in result if p["name"] == "Brandon Aubrey"]
        assert len(kicker) == 1
        assert kicker[0]["position"] == "K"

    def test_returns_none_on_network_error(self, db):
        svc = ADPService(db)
        with patch("pigskin_mastermind.services.adp_service.urlopen", side_effect=OSError("timeout")):
            result = svc.fetch_ffc_adp(year=2025)
        assert result is None

    def test_returns_none_on_empty_players(self, db):
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "Success", "players": []}).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.fetch_ffc_adp(year=2025)
        assert result is None

    def test_invalid_scoring_raises(self, db):
        svc = ADPService(db)
        with pytest.raises(ValueError, match="Invalid scoring format"):
            svc.fetch_ffc_adp(scoring="invalid")


# ---------------------------------------------------------------------------
# import_from_ffc
# ---------------------------------------------------------------------------


class TestImportFromFFC:
    def test_import_matches_and_stores_adp(self, db):
        _seed_players(db)
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.import_from_ffc(year=2025, scoring="ppr", num_teams=12)

        assert result["imported"] == 7  # 6 matched + 1 unknown created
        assert result["created"] == 1
        assert result["skipped"] == 0
        assert result["total"] == 7
        assert result["source"] == "fantasyfootballcalculator"
        assert result["last_updated"] is not None

    def test_import_skips_unmatched_when_create_missing_disabled(self, db):
        _seed_players(db)
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.import_from_ffc(year=2025, create_missing=False)

        assert result["imported"] == 6
        assert result["created"] == 0
        assert result["skipped"] == 1
        assert db.query(DBPlayer).filter_by(name="Unknown Guy").first() is None

    def test_import_creates_minimal_player_with_ffc_id(self, db):
        _seed_players(db)
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            svc.import_from_ffc(year=2025)

        created = db.query(DBPlayer).filter_by(name="Unknown Guy").first()
        assert created is not None
        assert created.player_id == "ffc_9999"
        assert created.position == "WR"
        assert created.nfl_team == "FA"
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=created.id, year=2025).first()
        assert season.adp == pytest.approx(200.0)

    def test_import_persists_variance_fields(self, db):
        _seed_players(db)
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            svc.import_from_ffc(year=2025, num_teams=12)

        rb = db.query(DBPlayer).filter_by(name="Saquon Barkley").first()
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=rb.id, year=2025).first()
        assert season.adp_stdev == pytest.approx(0.8)
        assert season.adp_high == pytest.approx(1.0)
        assert season.adp_low == pytest.approx(3.0)
        assert season.adp_times_drafted == 19

    def test_import_converts_round_pick_high_low(self, db):
        """FFC formats high/low as round.pick strings in some payloads."""
        _seed_players(db)
        svc = ADPService(db)
        payload = [
            {"player_id": 2462, "name": "Patrick Mahomes", "position": "QB", "team": "KC",
             "adp": 49.5, "adp_formatted": "5.02", "times_drafted": 12,
             "high": "4.02", "low": "5.09", "stdev": 6.1, "bye": 10},
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response(payload)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            svc.import_from_ffc(year=2025, num_teams=12)

        qb = db.query(DBPlayer).filter_by(name="Patrick Mahomes").first()
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=qb.id, year=2025).first()
        assert season.adp_high == pytest.approx(38.0)  # (4-1)*12 + 2
        assert season.adp_low == pytest.approx(57.0)  # (5-1)*12 + 9

    def test_normalized_name_matching_avoids_duplicates(self, db):
        db.add(DBPlayer(player_id="espn_201", name="A.J. Brown", position="WR", nfl_team="PHI"))
        db.commit()
        svc = ADPService(db)
        payload = [
            {"player_id": 4321, "name": "AJ Brown", "position": "WR", "team": "PHI",
             "adp": 10.0, "adp_formatted": "1.10", "times_drafted": 40,
             "high": 5, "low": 15, "stdev": 2.0, "bye": 9},
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response(payload)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.import_from_ffc(year=2025)

        assert result["created"] == 0
        assert db.query(DBPlayer).filter(DBPlayer.position == "WR").count() == 1
        matched = db.query(DBPlayer).filter_by(name="A.J. Brown").first()
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=matched.id, year=2025).first()
        assert season.adp == pytest.approx(10.0)

    def test_import_updates_team_for_existing_player(self, db):
        """The reported bug: a player who changed teams kept his old one.

        Would fail before the fix — import_from_ffc set nfl_team only inside
        _create_minimal_player, so already-existing rows were never corrected.
        """
        _seed_players(db)
        svc = ADPService(db)
        payload = [
            {"player_id": 2860, "name": "Saquon Barkley", "position": "RB", "team": "NE",
             "adp": 1.6, "adp_formatted": "1.02", "times_drafted": 19,
             "high": 1, "low": 3, "stdev": 0.8, "bye": 9},
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response(payload)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.import_from_ffc(year=2025)

        rb = db.query(DBPlayer).filter_by(name="Saquon Barkley").first()
        assert rb.nfl_team == "NE"
        assert result["team_changes"] == [
            {"name": "Saquon Barkley", "old": "PHI", "new": "NE"}
        ]

    def test_import_reports_no_change_when_team_is_same(self, db):
        _seed_players(db)
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()  # every team matches the seed
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.import_from_ffc(year=2025)

        assert result["team_changes"] == []

    def test_import_does_not_overwrite_real_team_with_free_agent(self, db):
        """A source reporting FA for an unsigned player must not wipe a good team."""
        _seed_players(db)
        svc = ADPService(db)
        payload = [
            {"player_id": 2860, "name": "Saquon Barkley", "position": "RB", "team": "FA",
             "adp": 1.6, "adp_formatted": "1.02", "times_drafted": 19,
             "high": 1, "low": 3, "stdev": 0.8, "bye": 9},
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response(payload)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.import_from_ffc(year=2025)

        rb = db.query(DBPlayer).filter_by(name="Saquon Barkley").first()
        assert rb.nfl_team == "PHI"
        assert result["team_changes"] == []

    def test_import_canonicalizes_wsh_without_reporting_a_change(self, db):
        """WSH and WAS are the same franchise — not a team change."""
        db.add(DBPlayer(player_id="espn_301", name="Jayden Daniels", position="QB",
                        nfl_team="WSH"))
        db.commit()
        svc = ADPService(db)
        payload = [
            {"player_id": 7777, "name": "Jayden Daniels", "position": "QB", "team": "WAS",
             "adp": 40.0, "adp_formatted": "4.04", "times_drafted": 30,
             "high": 30, "low": 50, "stdev": 4.0, "bye": 14},
        ]
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response(payload)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.import_from_ffc(year=2025)

        qb = db.query(DBPlayer).filter_by(name="Jayden Daniels").first()
        assert qb.nfl_team == "WAS"  # canonicalized in place
        assert result["team_changes"] == []  # but not a roster move

    def test_canonicalize_stored_teams_fixes_rows_no_source_returns(self, db):
        """Players neither source ships must not stay on a stale spelling."""
        db.add_all([
            DBPlayer(player_id="espn_401", name="Benched Guy", position="RB", nfl_team="WSH"),
            DBPlayer(player_id="espn_402", name="Other Guy", position="WR", nfl_team="JAC"),
            DBPlayer(player_id="espn_403", name="Fine Guy", position="TE", nfl_team="KC"),
            DBPlayer(player_id="espn_404", name="Loose Guy", position="QB", nfl_team="FA"),
        ])
        db.commit()
        svc = ADPService(db)

        fixed = svc.canonicalize_stored_teams()

        assert fixed == 2
        assert db.query(DBPlayer).filter_by(player_id="espn_401").first().nfl_team == "WAS"
        assert db.query(DBPlayer).filter_by(player_id="espn_402").first().nfl_team == "JAX"
        assert db.query(DBPlayer).filter_by(player_id="espn_403").first().nfl_team == "KC"
        # FA is not a spelling problem — leave it alone.
        assert db.query(DBPlayer).filter_by(player_id="espn_404").first().nfl_team == "FA"

    def test_import_creates_season_stats_rows(self, db):
        _seed_players(db)
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            svc.import_from_ffc(year=2025)

        rb = db.query(DBPlayer).filter_by(name="Saquon Barkley").first()
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=rb.id, year=2025).first()
        assert season is not None
        assert season.adp == pytest.approx(1.6)
        assert season.adp_source == "fantasyfootballcalculator"

    def test_import_updates_existing_season(self, db):
        players = _seed_players(db)
        rb = players[0]  # Saquon Barkley
        existing = DBPlayerSeasonStats(player_id=rb.id, year=2025, games_played=16,
                                       fantasy_points_total=300.0, adp=99.0, adp_source="old")
        db.add(existing)
        db.commit()

        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            svc.import_from_ffc(year=2025)

        season = db.query(DBPlayerSeasonStats).filter_by(player_id=rb.id, year=2025).first()
        assert season.adp == pytest.approx(1.6)
        assert season.adp_source == "fantasyfootballcalculator"
        assert season.games_played == 16  # preserved

    def test_import_returns_error_on_fetch_failure(self, db):
        svc = ADPService(db)
        with patch("pigskin_mastermind.services.adp_service.urlopen", side_effect=OSError("down")):
            result = svc.import_from_ffc(year=2025)
        assert result["imported"] == 0
        assert "error" in result

    def test_pk_position_maps_to_k_on_import(self, db):
        """FFC uses PK for kickers; repo uses K. Verify the mapping works for matching."""
        _seed_players(db)
        svc = ADPService(db)
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            svc.import_from_ffc(year=2025)

        k = db.query(DBPlayer).filter_by(name="Brandon Aubrey").first()
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=k.id, year=2025).first()
        assert season is not None
        assert season.adp == pytest.approx(137.0)


# ---------------------------------------------------------------------------
# get_adp lookup
# ---------------------------------------------------------------------------


class TestGetAdp:
    def test_returns_adp_for_known_player(self, db):
        players = _seed_players(db)
        rb = players[0]
        db.add(DBPlayerSeasonStats(player_id=rb.id, year=2025, adp=1.6,
                                    adp_source="fantasyfootballcalculator"))
        db.commit()

        svc = ADPService(db)
        adp = svc.get_adp("Saquon Barkley", "RB")
        assert adp == pytest.approx(1.6)

    def test_returns_none_for_unknown_player(self, db):
        _seed_players(db)
        svc = ADPService(db)
        assert svc.get_adp("Nobody Here", "WR") is None

    def test_case_insensitive_name_match(self, db):
        players = _seed_players(db)
        rb = players[0]
        db.add(DBPlayerSeasonStats(player_id=rb.id, year=2025, adp=1.6,
                                    adp_source="fantasyfootballcalculator"))
        db.commit()

        svc = ADPService(db)
        assert svc.get_adp("saquon barkley", "RB") == pytest.approx(1.6)

    def test_year_filter(self, db):
        players = _seed_players(db)
        rb = players[0]
        db.add(DBPlayerSeasonStats(player_id=rb.id, year=2024, adp=5.0,
                                    adp_source="fantasyfootballcalculator"))
        db.add(DBPlayerSeasonStats(player_id=rb.id, year=2025, adp=1.6,
                                    adp_source="fantasyfootballcalculator"))
        db.commit()

        svc = ADPService(db)
        assert svc.get_adp("Saquon Barkley", "RB", year=2024) == pytest.approx(5.0)
        assert svc.get_adp("Saquon Barkley", "RB", year=2025) == pytest.approx(1.6)

    def test_returns_none_when_no_adp_stored(self, db):
        players = _seed_players(db)
        rb = players[0]
        db.add(DBPlayerSeasonStats(player_id=rb.id, year=2025))
        db.commit()

        svc = ADPService(db)
        assert svc.get_adp("Saquon Barkley", "RB") is None


# ---------------------------------------------------------------------------
# get_adp_metadata
# ---------------------------------------------------------------------------


class TestGetAdpMetadata:
    def test_returns_latest_timestamp_and_count(self, db):
        from datetime import datetime

        players = _seed_players(db)
        older = datetime(2025, 7, 1, 8, 0, 0)
        newer = datetime(2025, 7, 20, 9, 30, 0)
        db.add(DBPlayerSeasonStats(player_id=players[0].id, year=2025, adp=1.6,
                                   adp_source="fantasyfootballcalculator", updated_at=older))
        db.add(DBPlayerSeasonStats(player_id=players[1].id, year=2025, adp=2.0,
                                   adp_source="fantasyfootballcalculator", updated_at=newer))
        # Different source and different year must be excluded
        db.add(DBPlayerSeasonStats(player_id=players[2].id, year=2025, adp=3.0,
                                   adp_source="csv"))
        db.add(DBPlayerSeasonStats(player_id=players[3].id, year=2024, adp=4.0,
                                   adp_source="fantasyfootballcalculator"))
        db.commit()

        meta = ADPService(db).get_adp_metadata(year=2025)
        assert meta["year"] == 2025
        assert meta["count"] == 2
        assert meta["last_updated"] == newer.isoformat()

    def test_returns_none_when_no_data(self, db):
        meta = ADPService(db).get_adp_metadata(year=2025)
        assert meta["last_updated"] is None
        assert meta["count"] == 0


# ---------------------------------------------------------------------------
# get_all_adp
# ---------------------------------------------------------------------------


class TestGetAllAdp:
    def test_returns_ordered_list(self, db):
        players = _seed_players(db)
        db.add(DBPlayerSeasonStats(player_id=players[0].id, year=2025, adp=1.6,
                                    adp_source="fantasyfootballcalculator"))
        db.add(DBPlayerSeasonStats(player_id=players[1].id, year=2025, adp=2.0,
                                    adp_source="fantasyfootballcalculator"))
        db.add(DBPlayerSeasonStats(player_id=players[2].id, year=2025, adp=3.8,
                                    adp_source="fantasyfootballcalculator"))
        db.commit()

        svc = ADPService(db)
        results = svc.get_all_adp(year=2025)
        assert len(results) == 3
        assert results[0]["adp"] == pytest.approx(1.6)
        assert results[1]["adp"] == pytest.approx(2.0)
        assert results[2]["adp"] == pytest.approx(3.8)

    def test_position_filter(self, db):
        players = _seed_players(db)
        db.add(DBPlayerSeasonStats(player_id=players[0].id, year=2025, adp=1.6,
                                    adp_source="fantasyfootballcalculator"))
        db.add(DBPlayerSeasonStats(player_id=players[2].id, year=2025, adp=3.8,
                                    adp_source="fantasyfootballcalculator"))
        db.commit()

        svc = ADPService(db)
        results = svc.get_all_adp(year=2025, position="WR")
        assert len(results) == 1
        assert results[0]["name"] == "Ja'Marr Chase"

    def test_excludes_non_ffc_sources(self, db):
        players = _seed_players(db)
        db.add(DBPlayerSeasonStats(player_id=players[0].id, year=2025, adp=5.0,
                                    adp_source="espn"))
        db.commit()

        svc = ADPService(db)
        results = svc.get_all_adp(year=2025)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# get_adp_for_draft_pool
# ---------------------------------------------------------------------------


class TestGetAdpForDraftPool:
    def test_returns_draft_pool_format(self, db):
        players = _seed_players(db)
        for i, p in enumerate(players[:3]):
            db.add(DBPlayerSeasonStats(player_id=p.id, year=2025, adp=float(i + 1),
                                        adp_source="fantasyfootballcalculator"))
        db.commit()

        svc = ADPService(db)
        pool = svc.get_adp_for_draft_pool(year=2025)
        assert len(pool) == 3
        # Check required keys
        for p in pool:
            assert "id" in p
            assert "name" in p
            assert "position" in p
            assert "nfl_team" in p
            assert "projected_points" in p
            assert "adp_rank" in p
        # Verify ordering
        assert pool[0]["adp_rank"] < pool[1]["adp_rank"]

    def test_empty_when_no_data(self, db):
        svc = ADPService(db)
        pool = svc.get_adp_for_draft_pool(year=2025)
        assert pool == []


def test_import_espn_projections_writes_season_rows(db, monkeypatch):
    """Board totals land as source='espn' season rows, matched by identity."""
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
    from pigskin_mastermind.services import adp_service as adp_mod

    existing = DBPlayer(
        player_id="espn_4242", espn_id="4242",
        name="Bijan Robinson", position="RB", nfl_team="ATL",
    )
    db.add(existing)
    db.commit()

    monkeypatch.setattr(adp_mod, "fetch_espn_adp", lambda year, limit: [
        {"id": "espn_4242", "name": "Bijan Robinson", "position": "RB",
         "nfl_team": "ATL", "projected_points": 370.8, "adp_rank": 2.6},
    ])

    result = adp_mod.ADPService(db).import_espn_projections(year=2026)

    assert result["imported"] == 1
    row = db.query(DBPlayerProjection).filter_by(
        player_id=existing.id, year=2026, week=None, source="espn",
    ).one()
    assert row.projected_points == 370.8


def test_import_espn_projections_skips_zero_totals(db, monkeypatch):
    """A 0.0 totalRating is 'ESPN has no opinion', not 'worth zero points'."""
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
    from pigskin_mastermind.services import adp_service as adp_mod

    db.add(DBPlayer(player_id="espn_9", espn_id="9", name="Deep Bench",
                    position="WR", nfl_team="NYJ"))
    db.commit()
    monkeypatch.setattr(adp_mod, "fetch_espn_adp", lambda year, limit: [
        {"id": "espn_9", "name": "Deep Bench", "position": "WR",
         "nfl_team": "NYJ", "projected_points": 0.0, "adp_rank": 300.0},
    ])

    result = adp_mod.ADPService(db).import_espn_projections(year=2026)

    assert result["imported"] == 0
    assert db.query(DBPlayerProjection).count() == 0


def test_import_espn_projections_is_idempotent(db, monkeypatch):
    """Re-running updates in place rather than violating the unique index."""
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
    from pigskin_mastermind.services import adp_service as adp_mod

    db.add(DBPlayer(player_id="espn_4242", espn_id="4242",
                    name="Bijan Robinson", position="RB", nfl_team="ATL"))
    db.commit()
    board = [{"id": "espn_4242", "name": "Bijan Robinson", "position": "RB",
              "nfl_team": "ATL", "projected_points": 370.8, "adp_rank": 2.6}]
    monkeypatch.setattr(adp_mod, "fetch_espn_adp", lambda year, limit: board)

    svc = adp_mod.ADPService(db)
    svc.import_espn_projections(year=2026)
    board[0]["projected_points"] = 355.0
    svc.import_espn_projections(year=2026)

    rows = db.query(DBPlayerProjection).filter_by(source="espn").all()
    assert len(rows) == 1
    assert rows[0].projected_points == 355.0


class TestDraftPoolProjections:
    """The pool must serve season totals from player_projections only."""

    def _seed_with_adp(self, db):
        players = _seed_players(db)
        for i, p in enumerate(players[:3]):
            db.add(DBPlayerSeasonStats(
                player_id=p.id, year=2026, adp=float(i + 1),
                adp_source="fantasyfootballcalculator",
            ))
        db.commit()
        return players

    def test_persisted_zero_falls_through_to_season_total(self, db):
        """A persisted 0.0 is projection_service's clamp for 'no signal', not
        a real forecast of zero. It must not suppress the season-total
        fallback."""
        players = self._seed_with_adp(db)
        db.add(DBPlayerProjection(
            player_id=players[0].id, year=2026, week=None, source="blend",
            projected_points=0.0,
        ))
        db.add(DBPlayerSeasonStats(
            player_id=players[0].id, year=2025, games_played=17,
            fantasy_points_total=104.0, fantasy_points_avg=6.1,
        ))
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        entry = next(p for p in pool if p["db_id"] == players[0].id)
        assert entry["projected_points"] == pytest.approx(104.0)

    def test_fallback_excludes_in_progress_target_season(self, db):
        """order_by(year.desc()).first() with no year filter can pick the
        target season's own partial-year total, which is not scale-stable
        the way a per-game average was. The fallback must only look at
        seasons strictly before the one being projected."""
        players = self._seed_with_adp(db)
        # _seed_with_adp already wrote the 2026 row (that's what carries the
        # ADP that puts this player in the pool) — turn it into an
        # in-progress season: 4 games in, so its total is a partial number
        # that would badly understate a season projection.
        row_2026 = db.query(DBPlayerSeasonStats).filter_by(
            player_id=players[0].id, year=2026,
        ).one()
        row_2026.games_played = 4
        row_2026.fantasy_points_total = 40.0
        row_2026.fantasy_points_avg = 10.0
        db.add(DBPlayerSeasonStats(
            player_id=players[0].id, year=2025, games_played=17,
            fantasy_points_total=300.0, fantasy_points_avg=17.6,
        ))
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        entry = next(p for p in pool if p["db_id"] == players[0].id)
        assert entry["projected_points"] == pytest.approx(300.0)

    def test_prefers_blend_row(self, db):
        players = self._seed_with_adp(db)
        db.add(DBPlayerProjection(
            player_id=players[0].id, year=2026, week=None, source="model",
            projected_points=300.0,
        ))
        db.add(DBPlayerProjection(
            player_id=players[0].id, year=2026, week=None, source="blend",
            projected_points=355.0,
        ))
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        entry = next(p for p in pool if p["db_id"] == players[0].id)
        assert entry["projected_points"] == pytest.approx(355.0)

    def test_ignores_db_player_projected_points(self, db):
        """The mixed-unit column must not reach the pool."""
        players = self._seed_with_adp(db)
        players[0].projected_points = 19.5  # a per-game value
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        entry = next(p for p in pool if p["db_id"] == players[0].id)
        assert entry["projected_points"] != pytest.approx(19.5)

    def test_falls_back_to_last_season_total(self, db):
        players = self._seed_with_adp(db)
        db.add(DBPlayerSeasonStats(
            player_id=players[0].id, year=2025, games_played=16,
            fantasy_points_total=280.0, fantasy_points_avg=17.5,
        ))
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        entry = next(p for p in pool if p["db_id"] == players[0].id)
        # The season TOTAL, not the 17.5 per-game average.
        assert entry["projected_points"] == pytest.approx(280.0)

    def test_no_entry_lands_in_the_per_game_band(self, db):
        """A per-game leak shows up as a ~16 beside a ~300."""
        players = self._seed_with_adp(db)
        db.add(DBPlayerProjection(
            player_id=players[0].id, year=2026, week=None, source="blend",
            projected_points=355.0,
        ))
        db.add(DBPlayerSeasonStats(
            player_id=players[1].id, year=2025, games_played=16,
            fantasy_points_total=280.0, fantasy_points_avg=17.5,
        ))
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        # Band kept consistent with the one in
        # test_player_profile_import.py::test_draft_pool_projection_units_stay_comparable.
        assert not [p for p in pool if 0 < p["projected_points"] < 40]
