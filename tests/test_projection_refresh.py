"""Storage and read-path for persisted projections."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerProjection,
)


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def player(db):
    p = DBPlayer(player_id="espn_1", name="Test Back", position="RB", nfl_team="ATL")
    db.add(p)
    db.commit()
    return p


def test_season_and_weekly_rows_coexist(db, player):
    """week=NULL is the season row; it must not collide with week 1."""
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="blend",
        projected_points=280.0, expected_games=16.0,
    ))
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=1, source="blend",
        projected_points=17.5, expected_games=1.0,
    ))
    db.commit()
    assert db.query(DBPlayerProjection).count() == 2


def test_sources_coexist_for_one_scope(db, player):
    for src, pts in [("model", 300.0), ("espn", 260.0), ("adp", 275.0)]:
        db.add(DBPlayerProjection(
            player_id=player.id, year=2026, week=None,
            source=src, projected_points=pts, expected_games=16.0,
        ))
    db.commit()
    assert db.query(DBPlayerProjection).count() == 3


def test_duplicate_season_rows_rejected(db, player):
    """Two season rows with same (player_id, year, source) must be rejected."""
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="blend",
        projected_points=280.0, expected_games=16.0,
    ))
    db.commit()
    # Attempting a second season row with the same scope should fail
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="blend",
        projected_points=290.0, expected_games=16.0,
    ))
    with pytest.raises(IntegrityError):
        db.flush()
