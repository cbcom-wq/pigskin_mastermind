"""Tests for player news feature."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pigskin_mastermind.models.database import Base, DBPlayer, DBPlayerNews
from pigskin_mastermind.services.player_news_service import PlayerNewsService


@pytest.fixture
def db():
    """In-memory SQLite session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def player(db):
    """A test player with an ESPN ID."""
    p = DBPlayer(
        player_id="espn_12345",
        name="Patrick Mahomes",
        position="QB",
        nfl_team="KC",
        espn_id="12345",
    )
    db.add(p)
    db.commit()
    return p


def test_db_player_news_roundtrip(db, player):
    """DBPlayerNews rows can be inserted, queried, and are unique on
    (player_id, espn_headline_id)."""
    news = DBPlayerNews(
        player_id=player.id,
        espn_headline_id="art_001",
        headline="Mahomes throws 5 TDs",
        description="In a dominant performance...",
        source_url="https://espn.com/article/001",
        published_at=datetime(2026, 8, 10, 14, 0),
        fetched_at=datetime.utcnow(),
    )
    db.add(news)
    db.commit()

    rows = db.query(DBPlayerNews).filter_by(player_id=player.id).all()
    assert len(rows) == 1
    assert rows[0].headline == "Mahomes throws 5 TDs"
    assert rows[0].espn_headline_id == "art_001"


# Sample ESPN API response payload for mocking
ESPN_RESPONSE = {
    "articles": [
        {
            "id": 99001,
            "headline": "Mahomes leads Chiefs to victory",
            "description": "Patrick Mahomes threw for 300 yards and 3 TDs.",
            "published": "2026-08-10T14:30:00Z",
            "links": {"web": {"href": "https://www.espn.com/nfl/story/_/id/99001"}},
        },
        {
            "id": 99002,
            "headline": "Chiefs prep for Week 2",
            "description": "Kansas City focuses on run game.",
            "published": "2026-08-09T10:00:00Z",
            "links": {"web": {"href": "https://www.espn.com/nfl/story/_/id/99002"}},
        },
    ]
}


@patch("pigskin_mastermind.services.player_news_service.requests.get")
def test_get_player_news_fetches_on_cache_miss(mock_get, db, player):
    """First call for a player hits ESPN and caches the results."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = ESPN_RESPONSE
    mock_get.return_value = mock_resp

    svc = PlayerNewsService(db)
    news = svc.get_player_news(player)

    assert len(news) == 2
    assert news[0].headline == "Mahomes leads Chiefs to victory"
    assert news[1].headline == "Chiefs prep for Week 2"
    mock_get.assert_called_once()

    # Verify persisted in DB
    assert db.query(DBPlayerNews).filter_by(player_id=player.id).count() == 2


@patch("pigskin_mastermind.services.player_news_service.requests.get")
def test_get_player_news_serves_cache_when_fresh(mock_get, db, player):
    """Second call within TTL returns cached rows without hitting ESPN."""
    # Pre-populate cache
    db.add(
        DBPlayerNews(
            player_id=player.id,
            espn_headline_id="cached_1",
            headline="Cached headline",
            description="From earlier fetch",
            fetched_at=datetime.utcnow(),  # fresh
        )
    )
    db.commit()

    svc = PlayerNewsService(db)
    news = svc.get_player_news(player)

    assert len(news) == 1
    assert news[0].headline == "Cached headline"
    mock_get.assert_not_called()


@patch("pigskin_mastermind.services.player_news_service.requests.get")
def test_get_player_news_refetches_when_stale(mock_get, db, player):
    """Cached rows older than max_age_minutes trigger a fresh ESPN call."""
    db.add(
        DBPlayerNews(
            player_id=player.id,
            espn_headline_id="old_1",
            headline="Old headline",
            description="Stale",
            fetched_at=datetime.utcnow() - timedelta(minutes=60),
        )
    )
    db.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = ESPN_RESPONSE
    mock_get.return_value = mock_resp

    svc = PlayerNewsService(db)
    news = svc.get_player_news(player, max_age_minutes=30)

    # Old row still exists, plus 2 new ones
    assert len(news) >= 2
    mock_get.assert_called_once()


def test_get_player_news_no_espn_id(db):
    """Player without espn_id returns empty list, no API call."""
    p = DBPlayer(
        player_id="nfl_99999",
        name="No ESPN ID",
        position="WR",
        nfl_team="NYG",
        espn_id=None,
    )
    db.add(p)
    db.commit()

    svc = PlayerNewsService(db)
    news = svc.get_player_news(p)
    assert news == []


@patch("pigskin_mastermind.services.player_news_service.requests.get")
def test_get_player_news_network_error_returns_cached(mock_get, db, player):
    """Network failure returns stale cache instead of raising."""
    db.add(
        DBPlayerNews(
            player_id=player.id,
            espn_headline_id="stale_1",
            headline="Stale but usable",
            description="Still good enough",
            fetched_at=datetime.utcnow() - timedelta(hours=2),
        )
    )
    db.commit()

    mock_get.side_effect = Exception("Connection refused")

    svc = PlayerNewsService(db)
    news = svc.get_player_news(player, max_age_minutes=30)

    assert len(news) == 1
    assert news[0].headline == "Stale but usable"


from pigskin_mastermind.api.main import _timeago


def test_timeago_minutes():
    assert _timeago(datetime.utcnow() - timedelta(minutes=5)) == "5 minutes ago"


def test_timeago_hours():
    assert _timeago(datetime.utcnow() - timedelta(hours=3)) == "3 hours ago"


def test_timeago_days():
    assert _timeago(datetime.utcnow() - timedelta(days=2)) == "2 days ago"


def test_timeago_just_now():
    assert _timeago(datetime.utcnow() - timedelta(seconds=30)) == "just now"


def test_timeago_none():
    assert _timeago(None) == ""
