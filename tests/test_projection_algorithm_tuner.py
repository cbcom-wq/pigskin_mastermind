"""Tests for the projection algorithm tuner (automated honing system)."""

import json
import os
import tempfile
from dataclasses import asdict

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.algorithm_coefficients import AlgorithmCoefficients
from pigskin_mastermind.models.database import (
    Base,
    DBNFLTeamStats,
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
    DBTeam,
)
from pigskin_mastermind.models.projection_criteria import WeeklyProjectionCriteria
from pigskin_mastermind.services.projection_algorithm_tuner import (
    ProjectionAlgorithmTuner,
    TuningRunResult,
    VariationResult,
    _calculate_weekly_projection,
    generate_variations,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSession = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def db():
    session = TestSession()
    yield session
    session.close()


@pytest.fixture
def populated_db(db):
    """Seed enough data for the tuner to run meaningful evaluations."""
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.flush()

    # --- QB ---
    qb = DBPlayer(
        player_id="qb1", name="Test QB", position="QB",
        nfl_team="KC", team_id=team.id,
        stats={"age": 28, "injuryStatus": ""},
    )
    db.add(qb)
    db.flush()

    db.add(DBPlayerSeasonStats(
        player_id=qb.id, year=2024, games_played=16,
        pass_att=500, pass_cmp=340, pass_yd=4500, pass_td=35, pass_int=10,
        rush_att=50, rush_yd=200, rush_td=3,
        rec=0, rec_yd=0, rec_td=0, targets=0,
        fantasy_points_total=350.0, fantasy_points_avg=21.875,
        fantasy_points_per_touch=0.636,
        snap_pct=0.95,
    ))

    for week in range(1, 17):
        pts = 20.0 + (week - 8)
        db.add(DBPlayerGameLog(
            player_id=qb.id, year=2024, week=week,
            opponent=f"OPP{week}",
            pass_yd=280, pass_td=2, pass_int=1,
            fantasy_points=pts,
        ))

    # --- RB ---
    rb = DBPlayer(
        player_id="rb1", name="Test RB", position="RB",
        nfl_team="KC", team_id=team.id,
        stats={"age": 25, "injuryStatus": ""},
    )
    db.add(rb)
    db.flush()

    db.add(DBPlayerSeasonStats(
        player_id=rb.id, year=2024, games_played=16,
        rush_att=250, rush_yd=1200, rush_td=10,
        rec=40, rec_yd=300, rec_td=2, targets=50,
        fantasy_points_total=220.0, fantasy_points_avg=13.75,
        fantasy_points_per_touch=0.759,
        snap_pct=0.70,
    ))

    for week in range(1, 17):
        pts = 12.0 + (week % 5)
        db.add(DBPlayerGameLog(
            player_id=rb.id, year=2024, week=week,
            opponent=f"OPP{week}",
            rush_yd=75, rush_td=1 if week % 3 == 0 else 0,
            fantasy_points=pts,
        ))

    # Team-level stats (needed by criteria builder)
    db.add(DBNFLTeamStats(
        nfl_team="KC", year=2024, week=None,
        total_yards=6000, pass_yards=4000, rush_yards=2000,
        points_scored=450,
    ))

    db.commit()
    return db


# ---------------------------------------------------------------------------
# AlgorithmCoefficients tests
# ---------------------------------------------------------------------------


class TestAlgorithmCoefficients:
    def test_defaults_match_production_values(self):
        c = AlgorithmCoefficients()
        assert c.skill_multiplier == 0.1
        assert c.offense_multiplier == 0.06
        assert c.defense_multiplier == 0.04
        assert c.touch_multiplier == 0.05
        assert c.trend_multiplier == 0.03
        assert c.efficiency_multiplier == 2.0
        assert c.efficiency_baseline == 0.5
        assert c.efficiency_cap == 5.0
        assert c.injury_multiplier == -0.05
        assert c.defense_rank_multiplier == 0.15
        assert c.momentum_multiplier == 0.02
        assert c.weather_multiplier == 0.015
        assert c.age_post_peak_multiplier == -0.5
        assert c.age_pre_peak_multiplier == -0.1
        assert c.coaching_multiplier == 0.04

    def test_to_dict_roundtrip(self):
        original = AlgorithmCoefficients(skill_multiplier=0.2, touch_multiplier=0.08)
        rebuilt = AlgorithmCoefficients.from_dict(original.to_dict())
        assert rebuilt.skill_multiplier == 0.2
        assert rebuilt.touch_multiplier == 0.08
        assert rebuilt.offense_multiplier == 0.06  # unchanged default

    def test_from_dict_ignores_unknown_keys(self):
        data = {"skill_multiplier": 0.15, "unknown_field": 42}
        c = AlgorithmCoefficients.from_dict(data)
        assert c.skill_multiplier == 0.15
        assert not hasattr(c, "unknown_field")


# ---------------------------------------------------------------------------
# _calculate_weekly_projection tests
# ---------------------------------------------------------------------------


class TestCalculateWeeklyProjection:
    def test_with_default_coefficients_matches_service(self):
        """Projection with default coefficients should match the original service."""
        from pigskin_mastermind.models.player import Player
        from pigskin_mastermind.services.projection_service import WeeklyProjectionService

        criteria = WeeklyProjectionCriteria(
            player_skill_level=80.0,
            team_offense_level=75.0,
            opponent_defense_level=55.0,
            positional_touch_percentage=25.0,
            recent_trend_score=15.0,
            historical_average_points=12.0,
            fantasy_points_per_touch=0.8,
            injury_risk_score=5.0,
            opposing_defense_vs_position_rank=28,
            offensive_momentum_score=20.0,
            weather_impact_score=-10.0,
        )
        player = Player(player_id="p1", name="P", position="WR", team="KC")

        service_result = WeeklyProjectionService().calculate_projection(player, criteria)
        tuner_result = _calculate_weekly_projection(criteria, AlgorithmCoefficients())

        assert abs(service_result - tuner_result) < 0.001

    def test_higher_skill_multiplier_raises_projection(self):
        criteria = WeeklyProjectionCriteria(
            player_skill_level=80.0,
            historical_average_points=15.0,
        )
        default = _calculate_weekly_projection(criteria, AlgorithmCoefficients())
        boosted = _calculate_weekly_projection(
            criteria, AlgorithmCoefficients(skill_multiplier=0.2)
        )
        assert boosted > default

    def test_projection_always_non_negative(self):
        criteria = WeeklyProjectionCriteria(
            player_skill_level=10.0,
            team_offense_level=10.0,
            opponent_defense_level=10.0,
            historical_average_points=0.0,
            injury_risk_score=90.0,
            offensive_momentum_score=-100.0,
            weather_impact_score=-100.0,
        )
        result = _calculate_weekly_projection(criteria, AlgorithmCoefficients())
        assert result >= 0.0


# ---------------------------------------------------------------------------
# generate_variations tests
# ---------------------------------------------------------------------------


class TestGenerateVariations:
    def test_always_includes_base(self):
        variations = generate_variations()
        base = AlgorithmCoefficients()
        assert any(v.to_dict() == base.to_dict() for v in variations)

    def test_respects_max_combinations(self):
        variations = generate_variations(max_combinations=10)
        assert len(variations) <= 10

    def test_single_field_variations(self):
        variations = generate_variations(
            fields_to_vary=["skill_multiplier"],
            scale_factors=[0.5, 1.0, 2.0],
            max_combinations=100,
        )
        # base + 2 non-default scales for 1 field = 3
        assert len(variations) == 3

    def test_no_duplicate_variations(self):
        variations = generate_variations(max_combinations=200)
        keys = [tuple(sorted(v.to_dict().items())) for v in variations]
        assert len(keys) == len(set(keys))

    def test_custom_base(self):
        custom = AlgorithmCoefficients(skill_multiplier=0.2)
        variations = generate_variations(
            base=custom,
            fields_to_vary=["skill_multiplier"],
            scale_factors=[0.5, 1.0, 2.0],
            max_combinations=100,
        )
        # 0.2*0.5=0.1, 0.2*1.0=0.2, 0.2*2.0=0.4
        skill_values = {v.skill_multiplier for v in variations}
        assert 0.1 in skill_values
        assert 0.2 in skill_values
        assert 0.4 in skill_values


# ---------------------------------------------------------------------------
# ProjectionAlgorithmTuner integration tests
# ---------------------------------------------------------------------------


class TestTunerRun:
    def test_run_returns_result(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(
            year=2024,
            max_variations=5,
            top_n=3,
        )
        assert isinstance(result, TuningRunResult)
        assert result.sample_count > 0
        assert result.variations_tested == 5
        assert len(result.top_variations) <= 3
        assert result.best.mae >= 0
        assert result.best.rmse >= 0

    def test_run_with_specific_weeks(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(
            year=2024,
            weeks=[10, 11, 12],
            max_variations=3,
        )
        assert set(result.weeks).issubset({10, 11, 12})

    def test_run_with_position_filter(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(
            year=2024,
            positions=["QB"],
            max_variations=3,
        )
        # All per-position MAE keys should only contain QB
        assert set(result.best.per_position_mae.keys()) == {"QB"}

    def test_run_raises_on_no_data(self, db):
        tuner = ProjectionAlgorithmTuner(db)
        with pytest.raises(ValueError, match="No valid evaluation samples"):
            tuner.run(year=2099)

    def test_default_result_included(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(year=2024, max_variations=5)
        default = AlgorithmCoefficients()
        assert result.default_result.coefficients == default.to_dict()

    def test_best_is_at_least_as_good_as_default(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(year=2024, max_variations=50)
        assert result.best.mae <= result.default_result.mae


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------


class TestTunerPersistence:
    def test_save_and_load(self, populated_db):
        with tempfile.TemporaryDirectory() as tmpdir:
            tuner = ProjectionAlgorithmTuner(populated_db, results_dir=tmpdir)
            result = tuner.run(year=2024, max_variations=5)
            path = tuner.save_result(result)

            assert os.path.exists(path)
            with open(path) as f:
                data = json.load(f)
            assert data["year"] == 2024

            loaded = tuner.load_results()
            assert len(loaded) == 1
            assert loaded[0].year == 2024
            assert loaded[0].best.mae == result.best.mae

    def test_load_empty_dir(self, db):
        with tempfile.TemporaryDirectory() as tmpdir:
            tuner = ProjectionAlgorithmTuner(db, results_dir=tmpdir)
            assert tuner.load_results() == []

    def test_load_nonexistent_dir(self, db):
        tuner = ProjectionAlgorithmTuner(db, results_dir="/tmp/nonexistent_xyz")
        assert tuner.load_results() == []


# ---------------------------------------------------------------------------
# Analysis report tests
# ---------------------------------------------------------------------------


class TestAnalysisReport:
    def test_report_structure(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(year=2024, max_variations=10)
        report = tuner.generate_analysis_report(result)

        assert "summary" in report
        assert "default_accuracy" in report
        assert "best_accuracy" in report
        assert "improvement" in report
        assert "best_coefficients" in report
        assert "coefficient_changes" in report
        assert "per_position_mae" in report
        assert "top_variations_summary" in report

    def test_report_improvement_non_negative(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(year=2024, max_variations=50)
        report = tuner.generate_analysis_report(result)

        # Best should be at least as good as default
        assert report["improvement"]["mae_reduction"] >= 0

    def test_report_sample_counts(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(year=2024, max_variations=5)
        report = tuner.generate_analysis_report(result)

        assert report["summary"]["total_samples"] == result.sample_count
        assert report["summary"]["players_evaluated"] == result.player_count
        assert report["summary"]["variations_tested"] == result.variations_tested


# ---------------------------------------------------------------------------
# VariationResult / TuningRunResult serialisation
# ---------------------------------------------------------------------------


class TestResultSerialisation:
    def test_variation_result_dict_roundtrip(self):
        vr = VariationResult(
            coefficients=AlgorithmCoefficients().to_dict(),
            mae=3.5,
            rmse=4.2,
            sample_count=100,
            per_position_mae={"QB": 2.1, "RB": 4.9},
        )
        d = asdict(vr)
        rebuilt = VariationResult(**d)
        assert rebuilt.mae == 3.5
        assert rebuilt.per_position_mae["QB"] == 2.1

    def test_tuning_run_result_to_from_dict(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(year=2024, max_variations=5)
        data = result.to_dict()
        rebuilt = TuningRunResult.from_dict(data)

        assert rebuilt.year == result.year
        assert rebuilt.best.mae == result.best.mae
        assert rebuilt.sample_count == result.sample_count
