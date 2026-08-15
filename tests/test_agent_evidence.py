"""Assembly of the agent evidence pack."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer
from pigskin_mastermind.services.agent_evidence import build_evidence


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def player(db):
    p = DBPlayer(
        player_id="espn_1",
        name="Test Back",
        position="RB",
        nfl_team="ATL",
        espn_id="1",
        age=25,
        years_exp=3,
        bye_week=9,
    )
    db.add(p)
    db.commit()
    return p


def test_player_block_carries_identity_and_bio(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["player"]["db_id"] == player.id
    assert ev["player"]["name"] == "Test Back"
    assert ev["player"]["position"] == "RB"
    assert ev["player"]["nfl_team"] == "ATL"
    assert ev["player"]["espn_id"] == "1"
    assert ev["player"]["age"] == 25
    assert ev["player"]["bye_week"] == 9


def test_season_scope_when_no_week_given(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["context"]["scope"] == "season"
    assert ev["context"]["week"] is None
    assert ev["context"]["year"] == 2026


def test_weekly_scope_when_week_given(db, player):
    ev = build_evidence(db, player.id, 2026, week=5)
    assert ev["context"]["scope"] == "weekly"
    assert ev["context"]["week"] == 5


def test_unknown_player_raises(db):
    with pytest.raises(ValueError, match="not found"):
        build_evidence(db, 9999, 2026)
