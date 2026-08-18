"""Bye weeks must never count as games played.

A bye is stored as a game log with an empty stat line and 0.0 fantasy points,
because both ESPN import paths write a row for every week a player is rostered.
Aggregating those rows with ``len(logs)`` inflates ``games_played`` by one and
deflates ``fantasy_points_avg`` by ~5% for every player with a bye — and that
average feeds ProjectionBaselines, so the error propagates into every
projection built on it.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats, DBNFLGame,
)
from pigskin_mastermind.services.nfl_schedule import (
    ScheduleIndex, is_bye_row, purge_bye_week_game_logs,
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


YEAR = 2025
BYE_WEEK = 14


def _seed_schedule(db, team="NE", bye_week=BYE_WEEK, weeks=18):
    """A full 18-week REG schedule for *team* with exactly one bye."""
    for week in range(1, weeks + 1):
        if week == bye_week:
            continue
        db.add(DBNFLGame(
            year=YEAR, week=week, game_type="REG",
            home_team=team, away_team="BUF",
            home_score=24, away_score=17,
        ))
    db.commit()


def _seed_player_with_bye(db, team="NE", position="QB", points=24.0):
    """A player with 18 game logs, one of which (week 14) is the bye."""
    player = DBPlayer(
        player_id="espn_1", name="Bye Haver", position=position, nfl_team=team,
    )
    db.add(player)
    db.commit()

    for week in range(1, 19):
        is_bye = week == BYE_WEEK
        db.add(DBPlayerGameLog(
            player_id=player.id, year=YEAR, week=week,
            pass_att=0 if is_bye else 30,
            pass_cmp=0 if is_bye else 20,
            pass_yd=0 if is_bye else 250,
            pass_td=0 if is_bye else 2,
            fantasy_points=0.0 if is_bye else points,
            source="espn_weekly",
        ))
    db.commit()
    return player


class TestScheduleIndex:
    def test_week_with_a_game_is_not_a_bye(self, db):
        _seed_schedule(db)
        index = ScheduleIndex(db)
        assert index.played("NE", YEAR, 13) is True
        assert index.is_bye("NE", YEAR, 13) is False

    def test_missing_week_is_a_bye(self, db):
        _seed_schedule(db)
        index = ScheduleIndex(db)
        assert index.played("NE", YEAR, BYE_WEEK) is False
        assert index.is_bye("NE", YEAR, BYE_WEEK) is True

    def test_no_schedule_means_no_bye_can_be_claimed(self, db):
        """Without a schedule every week looks missing — must not guess."""
        index = ScheduleIndex(db)
        assert index.has_schedule(YEAR) is False
        assert index.is_bye("NE", YEAR, BYE_WEEK) is False

    def test_unknown_team_is_never_a_bye(self, db):
        _seed_schedule(db)
        index = ScheduleIndex(db)
        assert index.is_bye(None, YEAR, BYE_WEEK) is False
        assert index.is_bye("FA", YEAR, BYE_WEEK) is False

    def test_team_spelling_is_normalized(self, db):
        """ESPN says WSH, the schedule says WAS."""
        db.add(DBNFLGame(
            year=YEAR, week=5, game_type="REG",
            home_team="WAS", away_team="DAL",
        ))
        db.commit()
        index = ScheduleIndex(db)
        assert index.played("WSH", YEAR, 5) is True


class TestIsByeRow:
    def test_empty_row_on_a_missing_week_is_a_bye(self, db):
        _seed_schedule(db)
        index = ScheduleIndex(db)
        log = DBPlayerGameLog(
            player_id=1, year=YEAR, week=BYE_WEEK, fantasy_points=0.0,
        )
        assert is_bye_row(index, "NE", log) is True

    def test_row_with_a_stat_line_is_never_a_bye(self, db):
        """Guards a stale DBPlayer.nfl_team: a traded player's real game on his
        old team must not be deleted because his *new* team was on bye."""
        _seed_schedule(db)
        index = ScheduleIndex(db)
        log = DBPlayerGameLog(
            player_id=1, year=YEAR, week=BYE_WEEK,
            rush_att=12, rush_yd=58, fantasy_points=0.0,
        )
        assert is_bye_row(index, "NE", log) is False

    def test_genuine_zero_point_game_is_not_a_bye(self, db):
        """A K or DEF can score exactly 0.0 in a game that really happened."""
        _seed_schedule(db)
        index = ScheduleIndex(db)
        log = DBPlayerGameLog(
            player_id=1, year=YEAR, week=13, fantasy_points=0.0,
        )
        assert is_bye_row(index, "NE", log) is False


class TestComputeSeasonStatsFromGameLogs:
    """The reported bug: nfl_data_service aggregation."""

    def test_games_played_excludes_the_bye(self, db):
        from pigskin_mastermind.services.nfl_data_service import NFLDataService

        _seed_schedule(db)
        player = _seed_player_with_bye(db, points=24.0)

        NFLDataService(db).compute_season_stats_from_game_logs(YEAR)

        season = db.query(DBPlayerSeasonStats).filter_by(
            player_id=player.id, year=YEAR,
        ).first()
        assert season.games_played == 17
        assert season.fantasy_points_total == pytest.approx(17 * 24.0)
        assert season.fantasy_points_avg == pytest.approx(24.0)

    def test_counting_stats_are_unaffected_by_the_bye(self, db):
        from pigskin_mastermind.services.nfl_data_service import NFLDataService

        _seed_schedule(db)
        player = _seed_player_with_bye(db)

        NFLDataService(db).compute_season_stats_from_game_logs(YEAR)

        season = db.query(DBPlayerSeasonStats).filter_by(
            player_id=player.id, year=YEAR,
        ).first()
        assert season.pass_att == 17 * 30
        assert season.pass_yd == 17 * 250

    def test_no_schedule_leaves_the_old_count(self, db):
        """Without a schedule the aggregation cannot identify a bye, so it must
        not silently drop a week it merely suspects."""
        from pigskin_mastermind.services.nfl_data_service import NFLDataService

        player = _seed_player_with_bye(db)
        NFLDataService(db).compute_season_stats_from_game_logs(YEAR)

        season = db.query(DBPlayerSeasonStats).filter_by(
            player_id=player.id, year=YEAR,
        ).first()
        assert season.games_played == 18

    def test_kicker_zero_point_game_still_counts(self, db):
        from pigskin_mastermind.services.nfl_data_service import NFLDataService

        _seed_schedule(db)
        player = DBPlayer(
            player_id="espn_k", name="Shanked It", position="K", nfl_team="NE",
        )
        db.add(player)
        db.commit()
        for week in (11, 12, 13):
            db.add(DBPlayerGameLog(
                player_id=player.id, year=YEAR, week=week,
                fantasy_points=0.0 if week == 12 else 9.0,
            ))
        db.commit()

        NFLDataService(db).compute_season_stats_from_game_logs(YEAR)

        season = db.query(DBPlayerSeasonStats).filter_by(
            player_id=player.id, year=YEAR,
        ).first()
        assert season.games_played == 3
        assert season.fantasy_points_avg == pytest.approx(18.0 / 3)


class TestEspnAggregateSeasonStats:
    """The same `len(logs)` bug lives in espn_sync._aggregate_season_stats."""

    def test_games_played_excludes_the_bye(self, db):
        from pigskin_mastermind.models.database import DBLeague, DBTeam
        from pigskin_mastermind.services.espn_sync import ESPNSyncService

        _seed_schedule(db)
        league = DBLeague(league_id="L1", name="Test", year=YEAR)
        team = DBTeam(
            team_id="T1", name="Fantasy Team", owner="Tester",
            league_id="L1", espn_team_id="7",
        )
        db.add_all([league, team])
        db.commit()

        player = _seed_player_with_bye(db, points=24.0)
        player.team_id = team.id
        db.commit()

        service = ESPNSyncService(db)
        service._aggregate_season_stats("L1", 7, YEAR)

        season = db.query(DBPlayerSeasonStats).filter_by(
            player_id=player.id, year=YEAR,
        ).first()
        assert season.games_played == 17
        assert season.fantasy_points_avg == pytest.approx(24.0)


class TestPromotionSkipsByeWeeks:
    """ESPN rosters a player every week; only the weeks they played are games."""

    def _seed_weekly(self, db, weeks, team="NE"):
        from pigskin_mastermind.models.database import (
            DBLeague, DBTeam, DBWeeklyPlayerStats, DBWeeklyTeamStats,
        )
        league = DBLeague(league_id="L1", name="Test", year=YEAR)
        team_row = DBTeam(
            team_id="T1", name="Fantasy Team", owner="Tester",
            league_id="L1", espn_team_id="7",
        )
        player = DBPlayer(
            player_id="espn_1", name="Bye Haver", position="QB", nfl_team=team,
        )
        db.add_all([league, team_row, player])
        db.commit()

        for week in weeks:
            is_bye = week == BYE_WEEK
            wts = DBWeeklyTeamStats(team_id=team_row.id, week=week)
            db.add(wts)
            db.commit()
            db.add(DBWeeklyPlayerStats(
                weekly_team_stats_id=wts.id,
                player_id=player.id,
                week=week,
                actual_points=0.0 if is_bye else 22.0,
                stats={} if is_bye else {
                    'breakdown': {'passingYards': 250, 'passingTouchdowns': 2},
                },
            ))
        db.commit()
        return player

    def test_bye_week_never_becomes_a_game_log(self, db):
        from pigskin_mastermind.services.nfl_data_service import NFLDataService

        _seed_schedule(db)
        player = self._seed_weekly(db, weeks=[12, 13, BYE_WEEK, 15])

        NFLDataService(db)._promote_weekly_stats_to_game_logs(YEAR)

        weeks = {
            g.week for g in db.query(DBPlayerGameLog).filter_by(
                player_id=player.id, year=YEAR,
            )
        }
        assert weeks == {12, 13, 15}

    def test_without_a_schedule_every_week_is_still_promoted(self, db):
        from pigskin_mastermind.services.nfl_data_service import NFLDataService

        player = self._seed_weekly(db, weeks=[12, 13, BYE_WEEK, 15])

        NFLDataService(db)._promote_weekly_stats_to_game_logs(YEAR)

        assert db.query(DBPlayerGameLog).filter_by(player_id=player.id).count() == 4


class TestPurgeByeWeekGameLogs:
    def test_dry_run_reports_without_deleting(self, db):
        _seed_schedule(db)
        _seed_player_with_bye(db)

        removed = purge_bye_week_game_logs(db, YEAR, dry_run=True)

        assert removed == 1
        assert db.query(DBPlayerGameLog).count() == 18

    def test_apply_deletes_only_the_bye_row(self, db):
        _seed_schedule(db)
        player = _seed_player_with_bye(db)

        removed = purge_bye_week_game_logs(db, YEAR, dry_run=False)

        assert removed == 1
        assert db.query(DBPlayerGameLog).count() == 17
        weeks = {
            g.week for g in db.query(DBPlayerGameLog).filter_by(
                player_id=player.id,
            )
        }
        assert BYE_WEEK not in weeks

    def test_purge_is_a_noop_without_a_schedule(self, db):
        _seed_player_with_bye(db)
        assert purge_bye_week_game_logs(db, YEAR, dry_run=False) == 0
        assert db.query(DBPlayerGameLog).count() == 18
