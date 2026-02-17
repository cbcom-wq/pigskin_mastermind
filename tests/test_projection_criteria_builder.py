"""Tests for ProjectionCriteriaBuilder auto-derivation from stats."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBTeam, DBPlayerSeasonStats,
    DBPlayerGameLog, DBNFLTeamStats
)
from pigskin_mastermind.models.projection_criteria import (
    WeeklyProjectionCriteria, YearlyProjectionCriteria
)
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder
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
def sample_data(db):
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.flush()

    player = DBPlayer(
        player_id="p1", name="Test QB", position="QB",
        nfl_team="KC", team_id=team.id,
        stats={'age': 28, 'injuryStatus': ''},
    )
    db.add(player)
    db.flush()

    # Season stats
    season = DBPlayerSeasonStats(
        player_id=player.id, year=2024, games_played=16,
        pass_yd=4500, pass_td=35, pass_int=10,
        rush_att=50, rush_yd=200, rush_td=3,
        rec=0, rec_yd=0, rec_td=0, targets=0,
        fantasy_points_total=350.0, fantasy_points_avg=21.875,
        fantasy_points_per_touch=7.0,
        snap_pct=0.95,
    )
    db.add(season)

    # Game logs for trend calculation
    for week in range(1, 17):
        pts = 20.0 + (week - 8)  # trending up over season
        db.add(DBPlayerGameLog(
            player_id=player.id, year=2024, week=week,
            opponent=f"OPP{week}",
            pass_yd=280, pass_td=2, pass_int=1,
            fantasy_points=pts,
        ))

    # Team defense stats
    db.add(DBNFLTeamStats(
        nfl_team="KC", year=2024, week=None,
        total_yards=6000, pass_yards=4000, rush_yards=2000,
        points_scored=450,
    ))

    # Opponent defense stats
    db.add(DBNFLTeamStats(
        nfl_team="OPP10", year=2024, week=None,
        def_rank_vs_qb=5,
    ))

    db.commit()
    return player


class TestBuildWeeklyCriteria:
    def test_builds_valid_criteria(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)

        assert isinstance(criteria, WeeklyProjectionCriteria)
        assert criteria.historical_average_points == 21.875
        assert criteria.fantasy_points_per_touch == 7.0
        assert 0 <= criteria.player_skill_level <= 100

    def test_opponent_defense_rank(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)

        # Week 10 opponent is "OPP10" which has def_rank_vs_qb=5
        assert criteria.opposing_defense_vs_position_rank == 5

    def test_default_defense_rank_when_no_data(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        # Week 1's opponent "OPP1" has no defense data
        criteria = builder.build_weekly_criteria(sample_data.id, week=1, year=2024)
        assert criteria.opposing_defense_vs_position_rank == 16  # default

    def test_trend_score_positive_for_improving(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=16, year=2024)

        # Last 4 weeks (13-16) average higher than season avg
        assert criteria.recent_trend_score > 0

    def test_overrides_applied(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(
            sample_data.id, week=10, year=2024,
            overrides={'weather_impact_score': -50.0, 'injury_risk_score': 80.0}
        )
        assert criteria.weather_impact_score == -50.0
        assert criteria.injury_risk_score == 80.0

    def test_player_not_found(self, db):
        builder = ProjectionCriteriaBuilder(db)
        with pytest.raises(ValueError, match="Player 999 not found"):
            builder.build_weekly_criteria(999, week=1, year=2024)

    def test_snap_pct_as_touch_percentage(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)
        assert criteria.positional_touch_percentage == pytest.approx(95.0, rel=0.01)


class TestBuildYearlyCriteria:
    def test_builds_valid_criteria(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        # Build for 2025 using 2024 data
        criteria = builder.build_yearly_criteria(sample_data.id, year=2025)

        assert isinstance(criteria, YearlyProjectionCriteria)
        assert criteria.historical_average_points == 21.875

    def test_age_deviation(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_yearly_criteria(sample_data.id, year=2025)

        # Player age 28, QB peak 29, deviation = -1
        assert criteria.age_deviation_from_optimum == -1.0

    def test_coaching_stability_default(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_yearly_criteria(sample_data.id, year=2025)
        # Manual override only, default 50
        assert criteria.coaching_stability_score == 50.0

    def test_overrides_applied(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_yearly_criteria(
            sample_data.id, year=2025,
            overrides={'coaching_stability_score': 80.0}
        )
        assert criteria.coaching_stability_score == 80.0


class TestInjuryRisk:
    def test_out_player(self, db):
        player = DBPlayer(
            player_id="inj1", name="Injured", position="RB",
            nfl_team="SF", stats={'injuryStatus': 'OUT'}
        )
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player)
        assert risk == 90.0

    def test_questionable_player(self, db):
        player = DBPlayer(
            player_id="inj2", name="Questionable", position="WR",
            nfl_team="GB", stats={'injuryStatus': 'QUESTIONABLE'}
        )
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player)
        assert risk == 40.0

    def test_healthy_player(self, db):
        player = DBPlayer(
            player_id="inj3", name="Healthy", position="TE",
            nfl_team="KC", stats={}
        )
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player)
        assert risk == 5.0
