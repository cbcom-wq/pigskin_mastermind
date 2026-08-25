"""Weekly projection rows: written for the first time, read with a fallback.

The fallback matters more than it looks. A lineup manager that refuses to act
when a weekly row is missing would bench a real starter over a data gap.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBPlayer, DBPlayerProjection, DBRosterSpot, DBTeam,
)
from pigskin_mastermind.services.projection_refresh import (
    ProjectionRefreshService, weekly_projection_map,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
WEEK = 5


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
def rostered(db):
    league = DBLeague(league_id="s1", name="S", year=YEAR, kind="season")
    db.add(league)
    db.commit()
    team = DBTeam(team_id="s1-1", name="A", owner="B", league_id="s1")
    db.add(team)
    db.commit()
    players = []
    for i, pos in enumerate(["QB", "RB"]):
        p = DBPlayer(player_id=f"p{i}", name=f"P{i}", position=pos, nfl_team="ATL")
        db.add(p)
        players.append(p)
    db.commit()
    for p in players:
        db.add(DBRosterSpot(league_id=league.id, team_id=team.id,
                            player_id=p.id, acquired_via="draft"))
    db.commit()
    return league, team, players


class FakeBuilder:
    """The real builder issues dozens of queries per player."""

    def __init__(self, *_args, **_kwargs):
        pass

    def ensure_players_stats(self, *_args, **_kwargs):
        return None

    def build_weekly_criteria(self, player_id, week, year, **_kwargs):
        return object()


class FakeWeeklyService:
    def __init__(self, points=12.5):
        self.points = points

    def calculate_projection(self, player, criteria):
        return self.points


class TestRefreshWeek:
    def test_writes_one_weekly_row_per_rostered_player(self, db, rostered):
        league, _team, players = rostered
        service = ProjectionRefreshService(
            db, builder=FakeBuilder(), weekly_service=FakeWeeklyService(12.5),
        )
        result = service.refresh_week(YEAR, WEEK, league_id=league.id)

        assert result["model"] == 2
        rows = db.query(DBPlayerProjection).filter_by(year=YEAR, week=WEEK).all()
        assert len(rows) == 2
        assert all(r.source == "model" for r in rows)
        assert all(r.projected_points == 12.5 for r in rows)

    def test_rerunning_updates_rather_than_duplicating(self, db, rostered):
        league, _team, _players = rostered
        ProjectionRefreshService(
            db, builder=FakeBuilder(), weekly_service=FakeWeeklyService(12.5),
        ).refresh_week(YEAR, WEEK, league_id=league.id)
        ProjectionRefreshService(
            db, builder=FakeBuilder(), weekly_service=FakeWeeklyService(20.0),
        ).refresh_week(YEAR, WEEK, league_id=league.id)

        rows = db.query(DBPlayerProjection).filter_by(year=YEAR, week=WEEK).all()
        assert len(rows) == 2
        assert all(r.projected_points == 20.0 for r in rows)

    def test_a_weekly_row_does_not_disturb_the_season_row(self, db, rostered):
        league, _team, players = rostered
        db.add(DBPlayerProjection(player_id=players[0].id, year=YEAR, week=None,
                                  source="model", projected_points=250.0))
        db.commit()
        ProjectionRefreshService(
            db, builder=FakeBuilder(), weekly_service=FakeWeeklyService(12.5),
        ).refresh_week(YEAR, WEEK, league_id=league.id)

        season = db.query(DBPlayerProjection).filter_by(
            player_id=players[0].id, year=YEAR, week=None,
        ).one()
        assert season.projected_points == 250.0


class TestWeeklyProjectionMap:
    def test_reads_weekly_rows(self, db, rostered):
        _league, _team, players = rostered
        db.add(DBPlayerProjection(player_id=players[0].id, year=YEAR, week=WEEK,
                                  source="model", projected_points=18.0))
        db.commit()
        result = weekly_projection_map(db, [p.id for p in players], YEAR, WEEK)
        assert result[players[0].id] == 18.0

    def test_falls_back_to_season_total_over_expected_games(self, db, rostered):
        """A missing weekly row must not mean 'no projection' — that would
        bench a real starter over a data gap."""
        _league, _team, players = rostered
        db.add(DBPlayerProjection(player_id=players[1].id, year=YEAR, week=None,
                                  source="model", projected_points=170.0,
                                  expected_games=17.0))
        db.commit()
        result = weekly_projection_map(db, [p.id for p in players], YEAR, WEEK)
        assert result[players[1].id] == pytest.approx(10.0)

    def test_weekly_wins_over_the_season_fallback(self, db, rostered):
        _league, _team, players = rostered
        db.add(DBPlayerProjection(player_id=players[0].id, year=YEAR, week=None,
                                  source="model", projected_points=170.0,
                                  expected_games=17.0))
        db.add(DBPlayerProjection(player_id=players[0].id, year=YEAR, week=WEEK,
                                  source="model", projected_points=25.0))
        db.commit()
        result = weekly_projection_map(db, [players[0].id], YEAR, WEEK)
        assert result[players[0].id] == 25.0

    def test_a_player_with_nothing_stored_is_absent_not_zero(self, db, rostered):
        """Absent lets the caller decide; 0.0 asserts a forecast nobody made."""
        _league, _team, players = rostered
        assert weekly_projection_map(db, [players[0].id], YEAR, WEEK) == {}

    def test_missing_expected_games_does_not_divide_by_zero(self, db, rostered):
        _league, _team, players = rostered
        db.add(DBPlayerProjection(player_id=players[0].id, year=YEAR, week=None,
                                  source="model", projected_points=170.0,
                                  expected_games=None))
        db.commit()
        result = weekly_projection_map(db, [players[0].id], YEAR, WEEK)
        assert result[players[0].id] == pytest.approx(10.0)  # 170 / 17 default
