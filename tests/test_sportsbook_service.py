"""Tests for SportsbookService."""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBSportsbookOdds
from pigskin_mastermind.services.sportsbook_service import SportsbookService

# ---------------------------------------------------------------------------
# In-memory test DB
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Sample Odds API responses
# ---------------------------------------------------------------------------

SAMPLE_EVENTS = [
    {
        "id": "event_abc123",
        "sport_key": "americanfootball_nfl",
        "sport_title": "NFL",
        "commence_time": "2025-01-15T18:00:00Z",
        "home_team": "Kansas City Chiefs",
        "away_team": "Houston Texans",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "h2h",
                        "outcomes": [
                            {"name": "Kansas City Chiefs", "price": -350},
                            {"name": "Houston Texans", "price": 280},
                        ],
                    },
                    {
                        "key": "spreads",
                        "outcomes": [
                            {"name": "Kansas City Chiefs", "price": -110, "point": -7.5},
                            {"name": "Houston Texans", "price": -110, "point": 7.5},
                        ],
                    },
                ],
            }
        ],
    }
]

SAMPLE_PROPS = [
    {
        "id": "event_abc123",
        "sport_key": "americanfootball_nfl",
        "sport_title": "NFL",
        "commence_time": "2025-01-15T18:00:00Z",
        "home_team": "Kansas City Chiefs",
        "away_team": "Houston Texans",
        "bookmakers": [
            {
                "key": "draftkings",
                "markets": [
                    {
                        "key": "player_pass_yds",
                        "outcomes": [
                            {
                                "name": "Over",
                                "description": "Patrick Mahomes",
                                "price": -115,
                                "point": 279.5,
                            },
                            {
                                "name": "Under",
                                "description": "Patrick Mahomes",
                                "price": -115,
                                "point": 279.5,
                            },
                        ],
                    }
                ],
            }
        ],
    }
]


# ---------------------------------------------------------------------------
# Tests – _events_to_rows
# ---------------------------------------------------------------------------

def test_events_to_rows_game_odds(db):
    service = SportsbookService(db, api_key="test")
    rows = service._events_to_rows(SAMPLE_EVENTS, "americanfootball_nfl")
    assert len(rows) == 4  # 2 h2h + 2 spreads outcomes
    markets = {r["market"] for r in rows}
    assert markets == {"h2h", "spreads"}
    bookmakers = {r["bookmaker"] for r in rows}
    assert bookmakers == {"draftkings"}


def test_events_to_rows_player_props(db):
    service = SportsbookService(db, api_key="test")
    rows = service._events_to_rows(SAMPLE_PROPS, "americanfootball_nfl")
    assert len(rows) == 2
    assert rows[0]["market"] == "player_pass_yds"
    assert rows[0]["description"] == "Patrick Mahomes"
    assert rows[0]["point"] == 279.5


# ---------------------------------------------------------------------------
# Tests – _upsert_rows
# ---------------------------------------------------------------------------

def test_upsert_rows_inserts(db):
    service = SportsbookService(db, api_key="test")
    rows = service._events_to_rows(SAMPLE_EVENTS, "americanfootball_nfl")
    count = service._upsert_rows(rows)
    assert count == 4
    assert db.query(DBSportsbookOdds).count() == 4


def test_upsert_rows_updates_on_conflict(db):
    service = SportsbookService(db, api_key="test")
    rows = service._events_to_rows(SAMPLE_EVENTS, "americanfootball_nfl")
    service._upsert_rows(rows)

    # Mutate price and upsert again
    for r in rows:
        r["price"] = 999
    service._upsert_rows(rows)

    # Should still only have 4 rows
    assert db.query(DBSportsbookOdds).count() == 4
    for row in db.query(DBSportsbookOdds).all():
        assert row.price == 999


def test_upsert_rows_empty(db):
    service = SportsbookService(db, api_key="test")
    count = service._upsert_rows([])
    assert count == 0


# ---------------------------------------------------------------------------
# Tests – import_game_odds (mocked HTTP)
# ---------------------------------------------------------------------------

def test_import_game_odds(db):
    service = SportsbookService(db, api_key="test")
    with patch.object(service, "_fetch_odds", return_value=SAMPLE_EVENTS):
        count = service.import_game_odds()
    assert count == 4
    assert db.query(DBSportsbookOdds).count() == 4


def test_import_player_props(db):
    service = SportsbookService(db, api_key="test")
    with patch.object(service, "_fetch_event_odds", return_value=SAMPLE_PROPS):
        count = service.import_player_props(event_id="event_abc123")
    assert count == 2


# ---------------------------------------------------------------------------
# Tests – get_game_odds queries
# ---------------------------------------------------------------------------

def _seed_db(db):
    service = SportsbookService(db, api_key="test")
    all_rows = service._events_to_rows(SAMPLE_EVENTS + SAMPLE_PROPS, "americanfootball_nfl")
    service._upsert_rows(all_rows)
    return service


def test_get_game_odds_no_filter(db):
    service = _seed_db(db)
    results = service.get_game_odds()
    # h2h, spreads, and player_pass_yds rows
    assert len(results) >= 4


def test_get_game_odds_by_market(db):
    service = _seed_db(db)
    results = service.get_game_odds(market="h2h")
    assert all(r["market"] == "h2h" for r in results)
    assert len(results) == 2


def test_get_game_odds_by_home_team(db):
    service = _seed_db(db)
    results = service.get_game_odds(home_team="Kansas City")
    assert len(results) >= 1
    assert all("Kansas City" in r["home_team"] for r in results)


# ---------------------------------------------------------------------------
# Tests – get_player_props queries
# ---------------------------------------------------------------------------

def test_get_player_props_no_filter(db):
    service = _seed_db(db)
    results = service.get_player_props()
    assert all("player_" in r["market"] for r in results)


def test_get_player_props_by_player_name(db):
    service = _seed_db(db)
    results = service.get_player_props(player_name="Mahomes")
    assert len(results) == 2
    for r in results:
        assert "Mahomes" in (r.get("description") or "")


def test_get_player_props_by_market(db):
    service = _seed_db(db)
    results = service.get_player_props(market="player_pass_yds")
    assert all(r["market"] == "player_pass_yds" for r in results)


# ---------------------------------------------------------------------------
# Tests – _row_to_dict
# ---------------------------------------------------------------------------

def test_row_to_dict(db):
    service = SportsbookService(db, api_key="test")
    row = DBSportsbookOdds(
        id=1,
        event_id="ev1",
        sport_key="americanfootball_nfl",
        sport_title="NFL",
        commence_time=datetime(2025, 1, 15, 18, 0, 0),
        home_team="Chiefs",
        away_team="Texans",
        bookmaker="draftkings",
        market="h2h",
        outcome_name="Chiefs",
        price=-350,
        point=None,
        description=None,
        fetched_at=datetime(2025, 1, 14, 0, 0, 0),
    )
    d = service._row_to_dict(row)
    assert d["event_id"] == "ev1"
    assert d["price"] == -350
    assert d["point"] is None
    assert "2025-01-15" in d["commence_time"]
