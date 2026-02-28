"""Tests for SportsBookService with mocked HTTP requests."""

import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBSportsbookOdds
from pigskin_mastermind.services.sportsbook_service import (
    SportsBookService,
    GAME_MARKETS,
    PLAYER_PROP_MARKETS,
    _parse_iso,
)

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


def _sample_game_odds_response():
    """Sample response from The Odds API /v4/sports/{sport}/odds."""
    return [
        {
            "id": "evt_001",
            "sport_key": "americanfootball_nfl",
            "home_team": "Kansas City Chiefs",
            "away_team": "Denver Broncos",
            "commence_time": "2026-09-10T20:20:00Z",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "title": "DraftKings",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Kansas City Chiefs", "price": -200},
                                {"name": "Denver Broncos", "price": 170},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Kansas City Chiefs", "price": -110, "point": -4.5},
                                {"name": "Denver Broncos", "price": -110, "point": 4.5},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": -110, "point": 48.5},
                                {"name": "Under", "price": -110, "point": 48.5},
                            ],
                        },
                    ],
                },
            ],
        },
    ]


def _sample_player_props_response():
    """Sample response from The Odds API event player props endpoint."""
    return {
        "id": "evt_001",
        "home_team": "Kansas City Chiefs",
        "away_team": "Denver Broncos",
        "commence_time": "2026-09-10T20:20:00Z",
        "bookmakers": [
            {
                "key": "fanduel",
                "title": "FanDuel",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "outcomes": [
                            {"name": "Over", "description": "Patrick Mahomes", "price": -115, "point": 274.5},
                            {"name": "Under", "description": "Patrick Mahomes", "price": -105, "point": 274.5},
                        ],
                    },
                    {
                        "key": "player_rush_yds",
                        "outcomes": [
                            {"name": "Over", "description": "Isiah Pacheco", "price": -120, "point": 64.5},
                            {"name": "Under", "description": "Isiah Pacheco", "price": 100, "point": 64.5},
                        ],
                    },
                ],
            },
        ],
    }


class TestSportsBookServiceInit:
    def test_api_key_from_param(self, db):
        service = SportsBookService(db, api_key="test_key")
        assert service.api_key == "test_key"

    def test_api_key_from_env(self, db, monkeypatch):
        monkeypatch.setenv("ODDS_API_KEY", "env_key")
        service = SportsBookService(db)
        assert service.api_key == "env_key"

    def test_api_key_missing(self, db, monkeypatch):
        monkeypatch.delenv("ODDS_API_KEY", raising=False)
        service = SportsBookService(db)
        assert service.api_key == ""


class TestFetchGameOdds:
    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_fetch_returns_json(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_game_odds_response()
        mock_resp.headers = {"x-requests-remaining": "499"}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        result = service.fetch_game_odds()

        assert len(result) == 1
        assert result[0]["id"] == "evt_001"
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert "apiKey" in call_args.kwargs.get("params", call_args[1].get("params", {}))

    def test_fetch_raises_without_api_key(self, db, monkeypatch):
        monkeypatch.delenv("ODDS_API_KEY", raising=False)
        service = SportsBookService(db)
        with pytest.raises(RuntimeError, match="ODDS_API_KEY"):
            service.fetch_game_odds()


class TestFetchPlayerProps:
    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_fetch_returns_json(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_player_props_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        result = service.fetch_player_props("evt_001")

        assert result["id"] == "evt_001"
        mock_get.assert_called_once()

    def test_fetch_raises_without_api_key(self, db, monkeypatch):
        monkeypatch.delenv("ODDS_API_KEY", raising=False)
        service = SportsBookService(db)
        with pytest.raises(RuntimeError, match="ODDS_API_KEY"):
            service.fetch_player_props("evt_001")


class TestImportGameOdds:
    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_import_creates_rows(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_game_odds_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        count = service.import_game_odds()

        # 1 event × 1 bookmaker × (2 h2h + 2 spreads + 2 totals) = 6
        assert count == 6
        assert db.query(DBSportsbookOdds).count() == 6

    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_import_upserts_existing(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_game_odds_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        service.import_game_odds()
        # Import again — should upsert, not duplicate
        count = service.import_game_odds()

        assert count == 6
        assert db.query(DBSportsbookOdds).count() == 6

    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_import_maps_fields_correctly(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_game_odds_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        service.import_game_odds()

        spread = db.query(DBSportsbookOdds).filter_by(
            market="spreads", outcome_name="Kansas City Chiefs"
        ).first()
        assert spread is not None
        assert spread.price == -110
        assert spread.point == -4.5
        assert spread.bookmaker == "draftkings"
        assert spread.home_team == "Kansas City Chiefs"
        assert spread.away_team == "Denver Broncos"
        assert spread.player_name is None


class TestImportPlayerProps:
    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_import_creates_rows(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_player_props_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        count = service.import_player_props("evt_001")

        # 1 bookmaker × (2 pass_yds + 2 rush_yds) = 4
        assert count == 4
        assert db.query(DBSportsbookOdds).count() == 4

    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_import_stores_player_name(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_player_props_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        service.import_player_props("evt_001")

        mahomes_rows = db.query(DBSportsbookOdds).filter(
            DBSportsbookOdds.player_name == "Patrick Mahomes"
        ).all()
        assert len(mahomes_rows) == 2  # Over and Under
        assert all(r.market == "player_pass_yds" for r in mahomes_rows)


class TestGetGameOdds:
    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_query_all(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_game_odds_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        service.import_game_odds()

        results = service.get_game_odds()
        assert len(results) == 6

    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_query_by_team(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_game_odds_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        service.import_game_odds()

        results = service.get_game_odds(team="Chiefs")
        assert len(results) == 6  # All rows involve Chiefs

        results = service.get_game_odds(team="Eagles")
        assert len(results) == 0

    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_query_by_market(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_game_odds_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        service.import_game_odds()

        results = service.get_game_odds(market="h2h")
        assert len(results) == 2


class TestGetPlayerOdds:
    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_query_by_player(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_player_props_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        service.import_player_props("evt_001")

        results = service.get_player_odds(player_name="Mahomes")
        assert len(results) == 2
        assert all(r["player_name"] == "Patrick Mahomes" for r in results)

    @patch("pigskin_mastermind.services.sportsbook_service.requests.get")
    def test_query_by_market(self, mock_get, db):
        mock_resp = MagicMock()
        mock_resp.json.return_value = _sample_player_props_response()
        mock_resp.headers = {}
        mock_get.return_value = mock_resp

        service = SportsBookService(db, api_key="test_key")
        service.import_player_props("evt_001")

        results = service.get_player_odds(market="player_rush_yds")
        assert len(results) == 2
        assert all(r["player_name"] == "Isiah Pacheco" for r in results)


class TestParseISO:
    def test_parses_z_suffix(self):
        dt = _parse_iso("2026-09-10T20:20:00Z")
        assert dt is not None
        assert dt.year == 2026
        assert dt.month == 9
        assert dt.day == 10

    def test_returns_none_for_empty(self):
        assert _parse_iso(None) is None
        assert _parse_iso("") is None

    def test_returns_none_for_invalid(self):
        assert _parse_iso("not-a-date") is None


class TestConstants:
    def test_game_markets(self):
        assert "h2h" in GAME_MARKETS
        assert "spreads" in GAME_MARKETS
        assert "totals" in GAME_MARKETS

    def test_player_prop_markets(self):
        assert "player_pass_yds" in PLAYER_PROP_MARKETS
        assert "player_rush_yds" in PLAYER_PROP_MARKETS
        assert "player_receptions" in PLAYER_PROP_MARKETS
