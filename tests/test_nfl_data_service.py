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


class TestImportADPFromCSV:
    def _seed_players(self, db):
        """Create two players for ADP matching tests."""
        qb = DBPlayer(player_id="nfl_GSIS001", name="Test QB", position="QB", nfl_team="KC")
        rb = DBPlayer(player_id="nfl_GSIS002", name="Test RB", position="RB", nfl_team="KC")
        db.add_all([qb, rb])
        db.commit()
        return qb, rb

    def test_import_by_name_creates_season_stats(self, db):
        self._seed_players(db)
        csv_data = "name,position,adp\nTest QB,QB,5.5\nTest RB,RB,12.0\n"
        import io
        service = NFLDataService(db)
        count = service.import_adp_from_csv(io.StringIO(csv_data), year=2024)

        assert count == 2
        qb = db.query(DBPlayer).filter_by(name="Test QB").first()
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=qb.id, year=2024).first()
        assert season is not None
        assert season.adp == pytest.approx(5.5)
        assert season.adp_source == 'csv'

    def test_import_by_player_id(self, db):
        qb, _ = self._seed_players(db)
        csv_data = "player_id,name,position,adp\nGSIS001,Test QB,QB,3.2\n"
        import io
        service = NFLDataService(db)
        count = service.import_adp_from_csv(io.StringIO(csv_data), year=2024)

        assert count == 1
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=qb.id, year=2024).first()
        assert season.adp == pytest.approx(3.2)

    def test_import_updates_existing_season_stats(self, db):
        qb, _ = self._seed_players(db)
        existing = DBPlayerSeasonStats(
            player_id=qb.id, year=2024, games_played=16,
            fantasy_points_total=300.0, fantasy_points_avg=18.75,
        )
        db.add(existing)
        db.commit()

        csv_data = "name,position,adp\nTest QB,QB,7.0\n"
        import io
        service = NFLDataService(db)
        count = service.import_adp_from_csv(io.StringIO(csv_data), year=2024)

        assert count == 1
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=qb.id, year=2024).first()
        assert season.adp == pytest.approx(7.0)
        assert season.games_played == 16  # existing data preserved

    def test_import_skips_missing_name(self, db):
        self._seed_players(db)
        csv_data = "name,position,adp\n,QB,5.5\n"
        import io
        service = NFLDataService(db)
        count = service.import_adp_from_csv(io.StringIO(csv_data), year=2024)
        assert count == 0

    def test_import_skips_invalid_adp(self, db):
        self._seed_players(db)
        csv_data = "name,position,adp\nTest QB,QB,not_a_number\n"
        import io
        service = NFLDataService(db)
        count = service.import_adp_from_csv(io.StringIO(csv_data), year=2024)
        assert count == 0

    def test_import_skips_unknown_player(self, db):
        self._seed_players(db)
        csv_data = "name,position,adp\nUnknown Player,WR,50.0\n"
        import io
        service = NFLDataService(db)
        count = service.import_adp_from_csv(io.StringIO(csv_data), year=2024)
        assert count == 0

    def test_import_custom_adp_source_label(self, db):
        self._seed_players(db)
        csv_data = "name,position,adp\nTest RB,RB,15.0\n"
        import io
        service = NFLDataService(db)
        service.import_adp_from_csv(io.StringIO(csv_data), year=2024, adp_source='fantasypros')
        rb = db.query(DBPlayer).filter_by(name="Test RB").first()
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=rb.id, year=2024).first()
        assert season.adp_source == 'fantasypros'

    def test_import_from_file_path(self, db, tmp_path):
        self._seed_players(db)
        csv_file = tmp_path / "adp.csv"
        csv_file.write_text("name,position,adp\nTest QB,QB,4.0\n")
        service = NFLDataService(db)
        count = service.import_adp_from_csv(str(csv_file), year=2024)
        assert count == 1


def _make_pbp_df(gsis_id='GSIS001'):
    """Create a minimal PBP DataFrame for testing."""
    return pd.DataFrame([
        {
            'play_id': 1,
            'game_id': 'test_game',
            'week': 3,
            'season': 2024,
            'qtr': 1,
            'down': 1,
            'ydstogo': 10,
            'yardline_100': 75,
            'quarter_seconds_remaining': 600,
            'game_seconds_remaining': 3000,
            'posteam': 'KC',
            'defteam': 'DEN',
            'home_team': 'KC',
            'away_team': 'DEN',
            'desc': 'Test QB pass to Test WR for 12 yards',
            'play_type': 'pass',
            'yards_gained': 12,
            'air_yards': 8.0,
            'yards_after_catch': 4.0,
            'epa': 0.5,
            'passer_player_id': gsis_id,
            'passer_player_name': 'Test QB',
            'rusher_player_id': None,
            'rusher_player_name': None,
            'receiver_player_id': 'GSIS003',
            'receiver_player_name': 'Test WR',
            'sack': 0,
            'touchdown': 0,
            'interception': 0,
            'first_down_rush': 0,
            'first_down_pass': 1,
            'penalty': 0,
            'penalty_team': None,
            'penalty_yards': 0,
            'total_home_score': 7,
            'total_away_score': 0,
        },
        {
            'play_id': 2,
            'game_id': 'test_game',
            'week': 3,
            'season': 2024,
            'qtr': 2,
            'down': 2,
            'ydstogo': 5,
            'yardline_100': 40,
            'quarter_seconds_remaining': 300,
            'game_seconds_remaining': 1800,
            'posteam': 'KC',
            'defteam': 'DEN',
            'home_team': 'KC',
            'away_team': 'DEN',
            'desc': 'Test QB sacked for -7 yards',
            'play_type': 'pass',
            'yards_gained': -7,
            'air_yards': None,
            'yards_after_catch': None,
            'epa': -1.2,
            'passer_player_id': gsis_id,
            'passer_player_name': 'Test QB',
            'rusher_player_id': None,
            'rusher_player_name': None,
            'receiver_player_id': None,
            'receiver_player_name': None,
            'sack': 1,
            'touchdown': 0,
            'interception': 0,
            'first_down_rush': 0,
            'first_down_pass': 0,
            'penalty': 0,
            'penalty_team': None,
            'penalty_yards': 0,
            'total_home_score': 7,
            'total_away_score': 0,
        },
        # A play NOT involving the player
        {
            'play_id': 3,
            'game_id': 'test_game',
            'week': 3,
            'season': 2024,
            'qtr': 3,
            'down': 1,
            'ydstogo': 10,
            'yardline_100': 60,
            'quarter_seconds_remaining': 800,
            'game_seconds_remaining': 900,
            'posteam': 'DEN',
            'defteam': 'KC',
            'home_team': 'KC',
            'away_team': 'DEN',
            'desc': 'Other RB runs for 5 yards',
            'play_type': 'run',
            'yards_gained': 5,
            'air_yards': None,
            'yards_after_catch': None,
            'epa': 0.1,
            'passer_player_id': None,
            'passer_player_name': None,
            'rusher_player_id': 'OTHER_PLAYER',
            'rusher_player_name': 'Other RB',
            'receiver_player_id': None,
            'receiver_player_name': None,
            'sack': 0,
            'touchdown': 0,
            'interception': 0,
            'first_down_rush': 0,
            'first_down_pass': 0,
            'penalty': 0,
            'penalty_team': None,
            'penalty_yards': 0,
            'total_home_score': 7,
            'total_away_score': 7,
        },
    ])


class TestGetPlayByPlay:
    def _seed_player(self, db, gsis_id='GSIS001'):
        player = DBPlayer(
            player_id=f"nfl_{gsis_id}",
            name='Test QB',
            position='QB',
            nfl_team='KC',
        )
        db.add(player)
        db.commit()
        return player

    def test_returns_only_player_plays(self, db):
        player = self._seed_player(db)
        nfl_data_service_module.nfl.import_pbp_data.return_value = _make_pbp_df('GSIS001')

        service = NFLDataService(db)
        plays = service.get_play_by_play(player.id, year=2024, week=3)

        # Only plays 1 and 2 involve GSIS001; play 3 involves a different player
        assert len(plays) == 2
        play_ids = {p['play_id'] for p in plays}
        assert play_ids == {1, 2}

    def test_sorted_earliest_first(self, db):
        player = self._seed_player(db)
        nfl_data_service_module.nfl.import_pbp_data.return_value = _make_pbp_df('GSIS001')

        service = NFLDataService(db)
        plays = service.get_play_by_play(player.id, year=2024, week=3)

        # game_seconds_remaining: play 1 = 3000 > play 2 = 1800 → play 1 first
        assert plays[0]['play_id'] == 1
        assert plays[1]['play_id'] == 2

    def test_empty_when_no_matching_plays(self, db):
        player = self._seed_player(db, gsis_id='NOBODY')
        nfl_data_service_module.nfl.import_pbp_data.return_value = _make_pbp_df('GSIS001')

        service = NFLDataService(db)
        plays = service.get_play_by_play(player.id, year=2024, week=3)

        assert plays == []

    def test_empty_when_wrong_week(self, db):
        player = self._seed_player(db)
        nfl_data_service_module.nfl.import_pbp_data.return_value = _make_pbp_df('GSIS001')

        service = NFLDataService(db)
        plays = service.get_play_by_play(player.id, year=2024, week=99)

        assert plays == []

    def test_raises_value_error_for_unknown_player(self, db):
        service = NFLDataService(db)
        with pytest.raises(ValueError, match="not found"):
            service.get_play_by_play(player_db_id=9999, year=2024, week=1)

    def test_raises_import_error_when_nfl_missing(self, db):
        player = self._seed_player(db)
        original = nfl_data_service_module.nfl
        nfl_data_service_module.nfl = None
        try:
            service = NFLDataService(db)
            with pytest.raises(ImportError, match="nfl_data_py"):
                service.get_play_by_play(player.id, year=2024, week=1)
        finally:
            nfl_data_service_module.nfl = original

    def test_nan_values_converted_to_none(self, db):
        player = self._seed_player(db)
        nfl_data_service_module.nfl.import_pbp_data.return_value = _make_pbp_df('GSIS001')

        service = NFLDataService(db)
        plays = service.get_play_by_play(player.id, year=2024, week=3)

        # air_yards for the sack play (play_id=2) was None in the fixture;
        # it must come back as Python None, not float NaN.
        sack_play = next(p for p in plays if p['play_id'] == 2)
        assert sack_play['air_yards'] is None

    def test_player_id_without_nfl_prefix(self, db):
        """Players stored without 'nfl_' prefix should still resolve."""
        player = DBPlayer(
            player_id='GSIS001',  # no nfl_ prefix
            name='Test QB',
            position='QB',
            nfl_team='KC',
        )
        db.add(player)
        db.commit()
        nfl_data_service_module.nfl.import_pbp_data.return_value = _make_pbp_df('GSIS001')

        service = NFLDataService(db)
        plays = service.get_play_by_play(player.id, year=2024, week=3)
        assert len(plays) == 2
