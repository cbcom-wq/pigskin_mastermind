"""Tests for NFLDataService with mocked nfl_data_py."""

import pytest
import types
import pandas as pd
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats, DBNFLTeamStats
)

# Ensure nfl_data_py module is mockable even when not installed
import pigskin_mastermind.services.nfl_data_service as nfl_data_service_module
nfl_data_service_module.nfl = MagicMock()

from pigskin_mastermind.services.nfl_data_service import NFLDataService

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


def _make_weekly_df():
    """Create a mock weekly data DataFrame."""
    return pd.DataFrame([
        {
            'player_id': 'GSIS001',
            'player_display_name': 'Test QB',
            'position': 'QB',
            'recent_team': 'KC',
            'opponent_team': 'DEN',
            'season': 2024,
            'week': 1,
            'attempts': 30,
            'completions': 22,
            'passing_yards': 280,
            'passing_tds': 2,
            'interceptions': 1,
            'carries': 3,
            'rushing_yards': 15,
            'rushing_tds': 0,
            'targets': 0,
            'receptions': 0,
            'receiving_yards': 0,
            'receiving_tds': 0,
            'rushing_fumbles': 0,
            'receiving_fumbles': 0,
            'rushing_fumbles_lost': 0,
            'receiving_fumbles_lost': 0,
            'fantasy_points_ppr': 22.5,
        },
        {
            'player_id': 'GSIS002',
            'player_display_name': 'Test RB',
            'position': 'RB',
            'recent_team': 'KC',
            'opponent_team': 'DEN',
            'season': 2024,
            'week': 1,
            'attempts': 0,
            'completions': 0,
            'passing_yards': 0,
            'passing_tds': 0,
            'interceptions': 0,
            'carries': 20,
            'rushing_yards': 95,
            'rushing_tds': 1,
            'targets': 4,
            'receptions': 3,
            'receiving_yards': 25,
            'receiving_tds': 0,
            'rushing_fumbles': 1,
            'receiving_fumbles': 0,
            'rushing_fumbles_lost': 0,
            'receiving_fumbles_lost': 0,
            'fantasy_points_ppr': 18.0,
        },
    ])


def _make_seasonal_df():
    """Create a mock seasonal data DataFrame."""
    return pd.DataFrame([
        {
            'player_id': 'GSIS001',
            'player_display_name': 'Test QB',
            'position': 'QB',
            'recent_team': 'KC',
            'season': 2024,
            'games': 16,
            'attempts': 500,
            'completions': 340,
            'passing_yards': 4500,
            'passing_tds': 35,
            'interceptions': 10,
            'carries': 50,
            'rushing_yards': 200,
            'rushing_tds': 3,
            'targets': 0,
            'receptions': 0,
            'receiving_yards': 0,
            'receiving_tds': 0,
            'rushing_fumbles_lost': 2,
            'fantasy_points_ppr': 350.0,
            'air_yards_share': None,
            'receiving_yards_after_catch': None,
            'wopr': None,
        },
    ])


class TestImportWeeklyStats:
    def test_import_creates_players_and_logs(self, db):
        nfl_data_service_module.nfl.import_weekly_data.return_value = _make_weekly_df()

        service = NFLDataService(db)
        count = service.import_weekly_stats([2024])

        assert count == 2
        assert db.query(DBPlayer).count() == 2
        assert db.query(DBPlayerGameLog).count() == 2

    def test_import_maps_stats_correctly(self, db):
        nfl_data_service_module.nfl.import_weekly_data.return_value = _make_weekly_df()

        service = NFLDataService(db)
        service.import_weekly_stats([2024])

        qb = db.query(DBPlayer).filter_by(name="Test QB").first()
        log = db.query(DBPlayerGameLog).filter_by(player_id=qb.id).first()
        assert log.pass_att == 30
        assert log.pass_cmp == 22
        assert log.pass_yd == 280
        assert log.pass_td == 2
        assert log.fantasy_points == 22.5

    def test_import_upserts(self, db):
        nfl_data_service_module.nfl.import_weekly_data.return_value = _make_weekly_df()

        service = NFLDataService(db)
        service.import_weekly_stats([2024])
        service.import_weekly_stats([2024])

        assert db.query(DBPlayerGameLog).count() == 2


class TestImportSeasonalStats:
    def test_import_creates_season_stats(self, db):
        # Pre-create the player
        player = DBPlayer(
            player_id="nfl_GSIS001", name="Test QB",
            position="QB", nfl_team="KC"
        )
        db.add(player)
        db.commit()

        nfl_data_service_module.nfl.import_seasonal_data.return_value = _make_seasonal_df()

        service = NFLDataService(db)
        count = service.import_seasonal_stats([2024])

        assert count == 1
        season = db.query(DBPlayerSeasonStats).first()
        assert season.pass_yd == 4500
        assert season.games_played == 16
        assert season.fantasy_points_avg == pytest.approx(350.0 / 16, rel=1e-2)


class TestImportTeamDefenseRankings:
    def test_import_computes_rankings(self, db):
        nfl_data_service_module.nfl.import_weekly_data.return_value = _make_weekly_df()

        service = NFLDataService(db)
        count = service.import_team_defense_rankings([2024])

        assert count > 0
        assert db.query(DBNFLTeamStats).count() > 0
