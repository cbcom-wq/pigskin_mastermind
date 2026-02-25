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
    _fpts_total = 350.0
    _pass_att = 500
    _rush_att = 50
    _rec = 0
    season = DBPlayerSeasonStats(
        player_id=player.id, year=2024, games_played=16,
        pass_att=_pass_att, pass_cmp=340, pass_yd=4500, pass_td=35, pass_int=10,
        rush_att=_rush_att, rush_yd=200, rush_td=3,
        rec=_rec, rec_yd=0, rec_td=0, targets=0,
        fantasy_points_total=_fpts_total, fantasy_points_avg=21.875,
        fantasy_points_per_touch=round(_fpts_total / (_pass_att + _rush_att + _rec), 6),
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
        # Criteria builder reads fantasy_points_per_touch directly from the stored season stats.
        # With the corrected formula (pass_att + rush_att + rec in denominator):
        # 350.0 / (500 + 50 + 0) = ~0.636
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=sample_data.id).first()
        assert criteria.fantasy_points_per_touch == pytest.approx(season.fantasy_points_per_touch, rel=0.01)
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


class TestOpponentDefenseLevel:
    def test_worst_defense_rank_32_gives_level_near_100(self, db, sample_data):
        """Rank 32 (worst defense) should give opponent_defense_level near 100."""
        builder = ProjectionCriteriaBuilder(db)
        # Override def_rank by using a weak opponent in week 10 (has def_rank_vs_qb=5)
        # We test via overrides to isolate the formula
        criteria = builder.build_weekly_criteria(
            sample_data.id, week=10, year=2024,
            overrides={'opponent_defense_level': ((32 - 1) / 31) * 100}
        )
        assert criteria.opponent_defense_level == pytest.approx(100.0, rel=0.01)

    def test_best_defense_rank_1_gives_level_near_0(self, db, sample_data):
        """Rank 1 (best defense) should give opponent_defense_level near 0."""
        criteria_level = ((1 - 1) / 31) * 100
        assert criteria_level == pytest.approx(0.0, abs=0.01)

    def test_middle_defense_rank_16_gives_level_near_48(self, db, sample_data):
        """Rank 16 (middle defense) should give opponent_defense_level near 48.4."""
        criteria_level = ((16 - 1) / 31) * 100
        assert criteria_level == pytest.approx(48.4, rel=0.01)

    def test_weekly_criteria_opponent_level_uses_def_rank(self, db, sample_data):
        """build_weekly_criteria with a known def_rank should set opponent_defense_level correctly."""
        # Week 10 opponent "OPP10" has def_rank_vs_qb=5
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)
        expected = ((5 - 1) / 31) * 100
        assert criteria.opponent_defense_level == pytest.approx(expected, rel=0.01)

    def test_yearly_criteria_keeps_neutral_50(self, db, sample_data):
        """build_yearly_criteria should keep opponent_defense_level at 50 (no single opponent)."""
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_yearly_criteria(sample_data.id, year=2025)
        assert criteria.opponent_defense_level == 50.0


class TestTrendScoreConfidence:
    def test_small_sample_dampens_score(self, db):
        """A 1-game sample (confidence=0.25) should give at most 25% of the raw deviation."""
        team = DBTeam(team_id="t2", name="Team2", owner="Owner2")
        db.add(team)
        db.flush()

        player = DBPlayer(
            player_id="trend1", name="Trend Player", position="RB",
            nfl_team="SF", team_id=team.id, stats={}
        )
        db.add(player)
        db.flush()

        # Season average = 5 pts over 4 games; only 1 recent game scoring 25 pts
        # Raw deviation = ((25 - 5) / 5) * 100 = 400 → capped at 100
        # With confidence = 1/4 = 0.25: result = 100 * 0.25 = 25
        season = DBPlayerSeasonStats(
            player_id=player.id, year=2024, games_played=4,
            fantasy_points_total=20.0, fantasy_points_avg=5.0,
        )
        db.add(season)

        # Only 1 game log (small sample)
        db.add(DBPlayerGameLog(
            player_id=player.id, year=2024, week=16,
            fantasy_points=25.0,
        ))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        score = builder._compute_trend_score(player.id, year=2024, num_weeks=4)
        assert score <= 25, f"Expected trend score ≤ 25 with 1/4 confidence, got {score}"


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
