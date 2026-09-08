"""The dashboard view model.

Every rule here is one the page gets wrong silently if it breaks: a score from
the wrong year, an empty roster, a lineup nobody flagged as unset.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBLeague,
    DBNFLGame,
    DBTeam,
)
from pigskin_mastermind.services import dashboard

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
WEEK = 1
SUNDAY_EARLY = datetime(2026, 9, 13, 13, 0)
SUNDAY_LATE = datetime(2026, 9, 13, 16, 25)
WEDNESDAY = datetime(2026, 9, 9, 10, 0)
MID_EARLY_GAME = datetime(2026, 9, 13, 14, 30)

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1, "BENCH": 6}


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


def add_schedule(db, year=YEAR, week=WEEK):
    """Two early games and one late game, none played."""
    db.add(
        DBNFLGame(
            year=year,
            week=week,
            home_team="CHI",
            away_team="DET",
            kickoff_at=SUNDAY_EARLY,
            total_line=48.5,
            spread_line=1.5,
        )
    )
    db.add(
        DBNFLGame(
            year=year,
            week=week,
            home_team="MIN",
            away_team="GB",
            kickoff_at=SUNDAY_EARLY,
            total_line=44.5,
            spread_line=-2.5,
        )
    )
    db.add(
        DBNFLGame(
            year=year,
            week=week,
            home_team="KC",
            away_team="LAC",
            kickoff_at=SUNDAY_LATE,
            total_line=45.0,
            spread_line=3.0,
        )
    )
    db.commit()


def add_league(db, league_id, name, kind, year=YEAR, week=WEEK):
    lg = DBLeague(
        league_id=league_id,
        name=name,
        year=year,
        kind=kind,
        current_week=week,
        roster_slots=SLOTS,
    )
    db.add(lg)
    db.commit()
    return lg


def add_team(
    db,
    league,
    name,
    is_user=True,
    espn_team_id=None,
    wins=0,
    losses=0,
    ties=0,
    points=0.0,
):
    t = DBTeam(
        team_id=f"{league.league_id}-{name}",
        name=name,
        owner="Brandon",
        league_id=league.league_id,
        is_user_team=is_user,
        espn_team_id=espn_team_id,
        wins=wins,
        losses=losses,
        ties=ties,
        total_points=points,
    )
    db.add(t)
    db.commit()
    return t


class TestResolveScope:
    def test_uses_the_newest_non_archive_league(self, db):
        add_league(db, "old", "Old", "archive", year=2025)
        add_league(db, "cur", "Current", "season", year=YEAR, week=4)
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 4)

    def test_ignores_an_archive_league_even_when_it_is_newest(self, db):
        add_league(db, "cur", "Current", "season", year=YEAR, week=2)
        add_league(db, "arc", "Archived", "archive", year=2030)
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 2)

    def test_falls_back_to_the_calendar_season_with_no_leagues(self, db):
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 1)

    def test_missing_current_week_reads_as_week_one(self, db):
        lg = add_league(db, "cur", "Current", "season")
        lg.current_week = None
        db.commit()
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 1)


class TestWeekContext:
    def test_counts_games(self, db):
        add_schedule(db)
        ctx = dashboard.build_week_context(db, YEAR, WEEK, WEDNESDAY)
        assert ctx.games_total == 3
        assert ctx.games_in_progress == 0
        assert ctx.games_final == 0

    def test_not_live_on_a_wednesday(self, db):
        add_schedule(db)
        ctx = dashboard.build_week_context(db, YEAR, WEEK, WEDNESDAY)
        assert ctx.games_live is False
        assert ctx.next_kickoff == SUNDAY_EARLY

    def test_live_during_the_early_window(self, db):
        add_schedule(db)
        ctx = dashboard.build_week_context(db, YEAR, WEEK, MID_EARLY_GAME)
        assert ctx.games_live is True
        assert ctx.games_in_progress == 2
        assert ctx.next_kickoff == SUNDAY_LATE

    def test_a_scored_game_is_final_not_in_progress(self, db):
        add_schedule(db)
        game = db.query(DBNFLGame).filter_by(home_team="CHI").first()
        game.home_score, game.away_score = 20, 17
        db.commit()
        ctx = dashboard.build_week_context(db, YEAR, WEEK, MID_EARLY_GAME)
        assert ctx.games_final == 1
        assert ctx.games_in_progress == 1

    def test_no_schedule_is_not_live(self, db):
        ctx = dashboard.build_week_context(db, YEAR, WEEK, MID_EARLY_GAME)
        assert ctx.games_total == 0
        assert ctx.games_live is False
        assert ctx.next_kickoff is None
