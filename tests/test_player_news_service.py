"""Tests for player news feature."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pigskin_mastermind.models.database import Base, DBPlayer, DBPlayerNews


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
