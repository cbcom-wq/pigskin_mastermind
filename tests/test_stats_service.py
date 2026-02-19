"""Tests for StatsService queries and aggregation."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBTeam, DBPlayerSeasonStats,
    DBPlayerGameLog, DBNFLTeamStats
)
from pigskin_mastermind.services.stats_service import StatsService

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
def sample_data(db):
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.flush()

    player = DBPlayer(
        player_id="p1", name="Patrick Mahomes", position="QB",
        nfl_team="KC", team_id=team.id
    )
    db.add(player)
    db.flush()

    # Season stats
    season = DBPlayerSeasonStats(
        player_id=player.id, year=2024, games_played=16,
        pass_yd=4500, pass_td=35, pass_int=10,
        rush_yd=300, rush_td=3,
        rec=0, rec_yd=0, rec_td=0, targets=0,
        fantasy_points_total=350.0, fantasy_points_avg=21.875,
        fantasy_points_per_touch=1.1,
    )
    db.add(season)

    # Game logs
    for week in range(1, 17):
        log = DBPlayerGameLog(
            player_id=player.id, year=2024, week=week,
            opponent=f"OPP{week}",
            pass_yd=250 + (week * 10), pass_td=2, pass_int=1,
            rush_yd=20, rush_td=0,
            fantasy_points=18.0 + week,
        )
        db.add(log)

    # NFL team defense stats
    db.add(DBNFLTeamStats(
        nfl_team="KC", year=2024, week=None,
        total_yards=6000, pass_yards=4000, rush_yards=2000,
        points_scored=450, points_allowed=300,
        def_rank_vs_qb=5, def_rank_vs_rb=12,
        def_rank_vs_wr=8, def_rank_vs_te=20,
    ))

    db.commit()
    return player


class TestGetPlayerStats:
    def test_basic_stats(self, db, sample_data):
        service = StatsService(db)
        result = service.get_player_stats(sample_data.id)
        assert result['name'] == "Patrick Mahomes"
        assert result['position'] == "QB"
        assert len(result['seasons']) == 1
        assert result['seasons'][0]['pass_yd'] == 4500

    def test_season_stats_includes_targets_and_pass_fields(self, db, sample_data):
        service = StatsService(db)
        result = service.get_player_stats(sample_data.id)
        season = result['seasons'][0]
        assert 'targets' in season
        assert 'pass_att' in season
        assert 'pass_cmp' in season
        assert 'rush_att' in season
        assert 'pass_rating' in season

    def test_filter_by_year(self, db, sample_data):
        service = StatsService(db)
        result = service.get_player_stats(sample_data.id, year=2024)
        assert len(result['seasons']) == 1
        assert result['seasons'][0]['year'] == 2024

    def test_nonexistent_player(self, db):
        service = StatsService(db)
        result = service.get_player_stats(999)
        assert result == {}


class TestGetPlayerGameLogs:
    def test_all_logs(self, db, sample_data):
        service = StatsService(db)
        logs = service.get_player_game_logs(sample_data.id)
        assert len(logs) == 16

    def test_limited_logs(self, db, sample_data):
        service = StatsService(db)
        logs = service.get_player_game_logs(sample_data.id, limit=4)
        assert len(logs) == 4
        # Should be most recent first
        assert logs[0]['week'] == 16

    def test_filter_by_year(self, db, sample_data):
        service = StatsService(db)
        logs = service.get_player_game_logs(sample_data.id, year=2023)
        assert len(logs) == 0


class TestGetRecentPerformance:
    def test_recent_trend(self, db, sample_data):
        service = StatsService(db)
        result = service.get_recent_performance(sample_data.id, num_weeks=4)
        assert result['num_weeks'] == 4
        assert result['recent_avg_points'] > 0
        assert 'trend_vs_season' in result

    def test_no_data(self, db):
        team = DBTeam(team_id="t2", name="T2", owner="O")
        db.add(team)
        db.flush()
        player = DBPlayer(player_id="p99", name="Nobody", position="WR", nfl_team="FA")
        db.add(player)
        db.commit()

        service = StatsService(db)
        result = service.get_recent_performance(player.id)
        assert result['num_weeks'] == 0


class TestGetSeasonAverages:
    def test_averages(self, db, sample_data):
        service = StatsService(db)
        result = service.get_season_averages(sample_data.id, 2024)
        assert result['games_played'] == 16
        assert result['pass_yd_per_game'] == round(4500 / 16, 1)
        assert result['fantasy_points_avg'] == 21.88

    def test_missing_season(self, db, sample_data):
        service = StatsService(db)
        result = service.get_season_averages(sample_data.id, 2023)
        assert result == {}


class TestGetTeamDefenseRankings:
    def test_defense_rankings(self, db, sample_data):
        service = StatsService(db)
        result = service.get_team_defense_rankings("KC", 2024)
        assert result['nfl_team'] == "KC"
        assert result['def_rank_vs_qb'] == 5
        assert result['def_rank_vs_rb'] == 12

    def test_missing_team(self, db):
        service = StatsService(db)
        result = service.get_team_defense_rankings("ZZZ", 2024)
        assert result == {}


class TestComparePlayers:
    def test_compare_two_players(self, db, sample_data):
        # Add a second player
        player2 = DBPlayer(
            player_id="p2", name="Josh Allen", position="QB",
            nfl_team="BUF"
        )
        db.add(player2)
        db.flush()
        db.add(DBPlayerSeasonStats(
            player_id=player2.id, year=2024, games_played=16,
            pass_yd=4200, pass_td=30, pass_int=12,
            fantasy_points_total=320.0, fantasy_points_avg=20.0,
        ))
        db.commit()

        service = StatsService(db)
        result = service.compare_players([sample_data.id, player2.id], year=2024)
        assert len(result['players']) == 2
        assert result['players'][0]['name'] == "Patrick Mahomes"
        assert result['players'][1]['name'] == "Josh Allen"


class TestGetYearlyRankings:
    def _add_player(self, db, player_id, name, position, nfl_team, year,
                    fantasy_points_total, adp=None, adp_source=None):
        player = DBPlayer(player_id=player_id, name=name, position=position, nfl_team=nfl_team)
        db.add(player)
        db.flush()
        season = DBPlayerSeasonStats(
            player_id=player.id, year=year, games_played=16,
            fantasy_points_total=fantasy_points_total,
            fantasy_points_avg=fantasy_points_total / 16,
            adp=adp,
            adp_source=adp_source,
        )
        db.add(season)
        db.commit()
        return player

    def test_rankings_sorted_by_adp(self, db):
        self._add_player(db, "p1", "Player B", "RB", "KC", 2024, 300.0, adp=5.0)
        self._add_player(db, "p2", "Player A", "QB", "KC", 2024, 350.0, adp=2.0)

        service = StatsService(db)
        rankings = service.get_yearly_rankings(2024)

        assert rankings[0]['name'] == "Player A"  # lower ADP ranked first
        assert rankings[1]['name'] == "Player B"
        assert rankings[0]['rank'] == 1
        assert rankings[1]['rank'] == 2

    def test_no_adp_falls_back_to_fantasy_points(self, db):
        self._add_player(db, "p1", "Low Points", "WR", "KC", 2024, 100.0, adp=None)
        self._add_player(db, "p2", "High Points", "WR", "KC", 2024, 300.0, adp=None)

        service = StatsService(db)
        rankings = service.get_yearly_rankings(2024)

        assert rankings[0]['name'] == "High Points"
        assert rankings[1]['name'] == "Low Points"

    def test_adp_players_ranked_before_no_adp(self, db):
        self._add_player(db, "p1", "No ADP", "RB", "KC", 2024, 400.0, adp=None)
        self._add_player(db, "p2", "Has ADP", "QB", "KC", 2024, 200.0, adp=10.0)

        service = StatsService(db)
        rankings = service.get_yearly_rankings(2024)

        assert rankings[0]['name'] == "Has ADP"
        assert rankings[1]['name'] == "No ADP"

    def test_filter_by_position(self, db):
        self._add_player(db, "p1", "QB Guy", "QB", "KC", 2024, 350.0, adp=3.0)
        self._add_player(db, "p2", "RB Guy", "RB", "KC", 2024, 300.0, adp=1.0)

        service = StatsService(db)
        rankings = service.get_yearly_rankings(2024, position="QB")

        assert len(rankings) == 1
        assert rankings[0]['name'] == "QB Guy"

    def test_returns_empty_for_missing_year(self, db):
        service = StatsService(db)
        rankings = service.get_yearly_rankings(1990)
        assert rankings == []

    def test_ranking_includes_expected_fields(self, db):
        self._add_player(db, "p1", "Test Player", "WR", "KC", 2024, 200.0, adp=15.0, adp_source='csv')

        service = StatsService(db)
        rankings = service.get_yearly_rankings(2024)

        r = rankings[0]
        assert 'rank' in r
        assert 'player_id' in r
        assert 'name' in r
        assert 'position' in r
        assert 'nfl_team' in r
        assert 'adp' in r
        assert 'adp_source' in r
        assert 'fantasy_points_total' in r
        assert 'fantasy_points_avg' in r
        assert 'games_played' in r
        assert r['adp_source'] == 'csv'
