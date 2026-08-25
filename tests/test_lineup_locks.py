"""A player locks at his own game's kickoff.

Every function here takes ``now`` as a parameter. A lock that read the system
clock internally would only be testable on a Sunday afternoon.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBNFLGame
from pigskin_mastermind.services.lineup_locks import LockIndex, first_kickoff

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
THURSDAY = datetime(2026, 9, 10, 20, 15)
SUNDAY_EARLY = datetime(2026, 9, 13, 13, 0)
MONDAY = datetime(2026, 9, 14, 20, 15)


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


@pytest.fixture
def week_one(db):
    db.add(DBNFLGame(year=YEAR, week=1, home_team="KC", away_team="BAL",
                     kickoff_at=THURSDAY))
    db.add(DBNFLGame(year=YEAR, week=1, home_team="ATL", away_team="NO",
                     kickoff_at=SUNDAY_EARLY))
    db.add(DBNFLGame(year=YEAR, week=1, home_team="SF", away_team="SEA",
                     kickoff_at=MONDAY))
    db.commit()


class TestKickoff:
    def test_finds_a_home_team_kickoff(self, db, week_one):
        assert LockIndex(db).kickoff("KC", YEAR, 1) == THURSDAY

    def test_finds_an_away_team_kickoff(self, db, week_one):
        assert LockIndex(db).kickoff("BAL", YEAR, 1) == THURSDAY

    def test_normalizes_team_spellings(self, db):
        """ESPN says WSH, everyone else says WAS."""
        db.add(DBNFLGame(year=YEAR, week=1, home_team="WAS", away_team="NYG",
                         kickoff_at=SUNDAY_EARLY))
        db.commit()
        assert LockIndex(db).kickoff("WSH", YEAR, 1) == SUNDAY_EARLY

    def test_a_team_on_bye_has_no_kickoff(self, db, week_one):
        assert LockIndex(db).kickoff("DAL", YEAR, 1) is None

    def test_an_unknown_team_has_no_kickoff(self, db, week_one):
        assert LockIndex(db).kickoff("ZZZ", YEAR, 1) is None
        assert LockIndex(db).kickoff(None, YEAR, 1) is None


class TestIsLocked:
    def test_unlocked_before_kickoff(self, db, week_one):
        now = datetime(2026, 9, 10, 20, 14)
        assert LockIndex(db).is_locked("KC", YEAR, 1, now) is False

    def test_locked_exactly_at_kickoff(self, db, week_one):
        assert LockIndex(db).is_locked("KC", YEAR, 1, THURSDAY) is True

    def test_locked_after_kickoff(self, db, week_one):
        now = datetime(2026, 9, 10, 23, 0)
        assert LockIndex(db).is_locked("KC", YEAR, 1, now) is True

    def test_a_monday_player_is_still_free_on_sunday(self, db, week_one):
        """The whole point of a per-player lock rather than a weekly one."""
        now = datetime(2026, 9, 13, 16, 0)
        assert LockIndex(db).is_locked("KC", YEAR, 1, now) is True
        assert LockIndex(db).is_locked("SF", YEAR, 1, now) is False

    def test_a_team_on_bye_never_locks(self, db, week_one):
        assert LockIndex(db).is_locked("DAL", YEAR, 1, MONDAY) is False

    def test_a_game_with_no_kickoff_time_never_locks(self, db):
        """Schedules import without times sometimes; refuse to guess."""
        db.add(DBNFLGame(year=YEAR, week=2, home_team="KC", away_team="BAL",
                         kickoff_at=None))
        db.commit()
        assert LockIndex(db).is_locked("KC", YEAR, 2, MONDAY) is False


class TestFirstKickoff:
    def test_returns_the_earliest_game_of_the_week(self, db, week_one):
        assert first_kickoff(db, YEAR, 1) == THURSDAY

    def test_returns_none_when_the_week_is_not_scheduled(self, db, week_one):
        assert first_kickoff(db, YEAR, 9) is None


class TestCaching:
    def test_one_query_per_year_week(self, db, week_one):
        """The planner asks per player; re-querying each time would be dozens
        of round trips for one lineup."""
        index = LockIndex(db)
        index.kickoff("KC", YEAR, 1)
        loaded = dict(index._by_year_week)
        index.kickoff("ATL", YEAR, 1)
        assert dict(index._by_year_week) == loaded
