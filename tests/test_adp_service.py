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

        assert result["imported"] == 6  # 6 matched, 1 unknown skipped
        assert result["skipped"] == 1
        assert result["total"] == 7
        assert result["source"] == "fantasyfootballcalculator"

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
