"""Tests for ProjectionRefreshService — writing persisted projections."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerProjection, DBPlayerSeasonStats,
)
from pigskin_mastermind.models.projection_criteria import YearlyProjectionCriteria
from pigskin_mastermind.services.projection_refresh import ProjectionRefreshService


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


class StubBuilder:
    """Returns fixed criteria so tests never touch the real builder."""

    def __init__(self, ppg=20.0, games=16.0):
        self.ppg = ppg
        self.games = games

    def ensure_players_stats(self, player_ids, year):
        return None

    def build_yearly_criteria(self, player_id, year):
        return YearlyProjectionCriteria(
            historical_average_points=self.ppg,
            expected_games=self.games,
        )


def _seed_pool_player(db, name="Bijan Robinson", position="RB", adp=1.8):
    player = DBPlayer(
        player_id=f"espn_{name.replace(' ', '')}", name=name,
        position=position, nfl_team="ATL",
    )
    db.add(player)
    db.flush()
    db.add(DBPlayerSeasonStats(
        player_id=player.id, year=2026, adp=adp,
        adp_source="fantasyfootballcalculator",
    ))
    db.commit()
    return player


def test_writes_model_row_at_season_scale(db):
    player = _seed_pool_player(db)
    svc = ProjectionRefreshService(db, builder=StubBuilder(ppg=20.0, games=16.0))

    result = svc.refresh_season(2026)

    assert result["model"] == 1
    row = db.query(DBPlayerProjection).filter_by(
        player_id=player.id, year=2026, week=None, source="model",
    ).one()
    # 20 ppg x 16 games = a season TOTAL, not a per-game rate.
    assert row.projected_points == pytest.approx(320.0, abs=1.0)
    assert row.expected_games == pytest.approx(16.0)


def test_rerunning_updates_rather_than_duplicating(db):
    player = _seed_pool_player(db)
    ProjectionRefreshService(db, builder=StubBuilder(ppg=20.0)).refresh_season(2026)
    ProjectionRefreshService(db, builder=StubBuilder(ppg=10.0)).refresh_season(2026)

    rows = db.query(DBPlayerProjection).filter_by(
        player_id=player.id, year=2026, source="model",
    ).all()
    assert len(rows) == 1
    assert rows[0].projected_points == pytest.approx(160.0, abs=1.0)


def test_skips_non_fantasy_positions(db):
    player = _seed_pool_player(db, name="Some Guy", position="Unknown")
    result = ProjectionRefreshService(db, builder=StubBuilder()).refresh_season(2026)

    assert result["model"] == 0
    assert result["skipped"] == 1
    assert db.query(DBPlayerProjection).count() == 0
