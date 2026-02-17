"""Tests for new stats database models."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from datetime import datetime

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBTeam, DBPlayerSeasonStats, DBNFLTeamStats, DBPlayerGameLog
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


@pytest.fixture
def sample_player(db):
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.flush()
    player = DBPlayer(
        player_id="p1", name="Test Player", position="QB",
        nfl_team="KC", team_id=team.id
    )
    db.add(player)
    db.commit()
    return player


class TestDBPlayerSeasonStats:
    def test_create_season_stats(self, db, sample_player):
        season = DBPlayerSeasonStats(
            player_id=sample_player.id,
            year=2024,
            games_played=16,
            pass_yd=4500,
            pass_td=35,
            pass_int=10,
            fantasy_points_total=350.5,
            fantasy_points_avg=21.9,
        )
        db.add(season)
        db.commit()

        result = db.query(DBPlayerSeasonStats).filter_by(
            player_id=sample_player.id, year=2024
        ).first()
        assert result is not None
        assert result.games_played == 16
        assert result.pass_yd == 4500
        assert result.pass_td == 35
        assert result.fantasy_points_total == 350.5

    def test_unique_constraint(self, db, sample_player):
        s1 = DBPlayerSeasonStats(player_id=sample_player.id, year=2024)
        db.add(s1)
        db.commit()

        s2 = DBPlayerSeasonStats(player_id=sample_player.id, year=2024)
        db.add(s2)
        with pytest.raises(Exception):
            db.commit()

    def test_relationship_to_player(self, db, sample_player):
        season = DBPlayerSeasonStats(player_id=sample_player.id, year=2024)
        db.add(season)
        db.commit()
        db.refresh(sample_player)

        assert len(sample_player.season_stats) == 1
        assert sample_player.season_stats[0].year == 2024

    def test_advanced_fields(self, db, sample_player):
        season = DBPlayerSeasonStats(
            player_id=sample_player.id, year=2024,
            snap_count=900, snap_pct=0.85,
            air_yards=1200.5, yac=450.3, wopr=0.42,
            source='nfl_data_py'
        )
        db.add(season)
        db.commit()

        result = db.query(DBPlayerSeasonStats).first()
        assert result.snap_count == 900
        assert result.snap_pct == 0.85
        assert result.wopr == 0.42
        assert result.source == 'nfl_data_py'


class TestDBNFLTeamStats:
    def test_create_team_stats(self, db):
        stat = DBNFLTeamStats(
            nfl_team="KC", year=2024, week=None,
            total_yards=6000, pass_yards=4000, rush_yards=2000,
            points_scored=450, points_allowed=300,
            def_rank_vs_qb=5, def_rank_vs_rb=12,
        )
        db.add(stat)
        db.commit()

        result = db.query(DBNFLTeamStats).filter_by(nfl_team="KC").first()
        assert result.total_yards == 6000
        assert result.def_rank_vs_qb == 5

    def test_weekly_vs_season(self, db):
        # Season total
        db.add(DBNFLTeamStats(nfl_team="KC", year=2024, week=None, points_scored=450))
        # Weekly
        db.add(DBNFLTeamStats(nfl_team="KC", year=2024, week=1, points_scored=30))
        db.add(DBNFLTeamStats(nfl_team="KC", year=2024, week=2, points_scored=24))
        db.commit()

        season = db.query(DBNFLTeamStats).filter_by(nfl_team="KC", week=None).first()
        assert season.points_scored == 450

        weekly = db.query(DBNFLTeamStats).filter(
            DBNFLTeamStats.nfl_team == "KC",
            DBNFLTeamStats.week.isnot(None)
        ).all()
        assert len(weekly) == 2


class TestDBPlayerGameLog:
    def test_create_game_log(self, db, sample_player):
        log = DBPlayerGameLog(
            player_id=sample_player.id, year=2024, week=1,
            opponent="DET",
            pass_yd=300, pass_td=3, pass_int=1,
            rush_yd=25, rush_td=0,
            fantasy_points=28.5,
        )
        db.add(log)
        db.commit()

        result = db.query(DBPlayerGameLog).first()
        assert result.opponent == "DET"
        assert result.pass_yd == 300
        assert result.fantasy_points == 28.5

    def test_unique_constraint(self, db, sample_player):
        db.add(DBPlayerGameLog(
            player_id=sample_player.id, year=2024, week=1
        ))
        db.commit()

        db.add(DBPlayerGameLog(
            player_id=sample_player.id, year=2024, week=1
        ))
        with pytest.raises(Exception):
            db.commit()

    def test_active_game_tracking(self, db, sample_player):
        log = DBPlayerGameLog(
            player_id=sample_player.id, year=2024, week=1,
            is_active_game=True, fantasy_points=12.5,
        )
        db.add(log)
        db.commit()

        result = db.query(DBPlayerGameLog).filter_by(is_active_game=True).first()
        assert result is not None
        assert result.fantasy_points == 12.5

    def test_relationship_to_player(self, db, sample_player):
        db.add(DBPlayerGameLog(
            player_id=sample_player.id, year=2024, week=1
        ))
        db.add(DBPlayerGameLog(
            player_id=sample_player.id, year=2024, week=2
        ))
        db.commit()
        db.refresh(sample_player)

        assert len(sample_player.game_logs) == 2

    def test_cascade_delete(self, db, sample_player):
        db.add(DBPlayerGameLog(
            player_id=sample_player.id, year=2024, week=1
        ))
        db.add(DBPlayerSeasonStats(
            player_id=sample_player.id, year=2024
        ))
        db.commit()

        db.delete(sample_player)
        db.commit()

        assert db.query(DBPlayerGameLog).count() == 0
        assert db.query(DBPlayerSeasonStats).count() == 0
