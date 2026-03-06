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
from pigskin_mastermind.models.algorithm_coefficients import (
    PositionCoefficients,
    TUNABLE_POSITIONS,
)
from pigskin_mastermind.services.projection_algorithm_tuner import (
    ProjectionAlgorithmTuner,
    PositionTuningResult,
    SampleComparisonRow,
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

    def test_run_includes_sample_comparisons_and_filters(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run(
            year=2024,
            weeks=[10, 11],
            positions=["QB"],
            max_variations=5,
        )

        assert result.run_filters["weeks"] == [10, 11]
        assert result.run_filters["positions"] == ["QB"]
        assert result.run_filters["simulated_weeks"] == [10, 11]
        assert result.run_filters["simulated_positions"] == ["QB"]
        assert len(result.sample_comparisons) == result.sample_count

        sample = result.sample_comparisons[0]
        assert isinstance(sample, SampleComparisonRow)
        assert sample.player_id in result.run_filters["simulated_player_ids"]
        assert sample.week in result.run_filters["simulated_weeks"]
        assert sample.position in result.run_filters["simulated_positions"]
        assert sample.default_error == pytest.approx(
            abs(sample.default_projected_points - sample.actual_points), abs=1e-3
        )
        assert sample.tuned_error == pytest.approx(
            abs(sample.tuned_projected_points - sample.actual_points), abs=1e-3
        )


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
            assert loaded[0].run_filters == result.run_filters
            assert len(loaded[0].sample_comparisons) == result.sample_count
            loaded_sample = loaded[0].sample_comparisons[0]
            original_sample = result.sample_comparisons[0]
            assert loaded_sample.player_id == original_sample.player_id
            assert loaded_sample.week == original_sample.week
            assert loaded_sample.actual_points == original_sample.actual_points
            assert (
                loaded_sample.default_projected_points
                == original_sample.default_projected_points
            )
            assert (
                loaded_sample.tuned_projected_points
                == original_sample.tuned_projected_points
            )

    def test_load_legacy_result_without_new_fields(self, db):
        with tempfile.TemporaryDirectory() as tmpdir:
            tuner = ProjectionAlgorithmTuner(db, results_dir=tmpdir)
            variation = VariationResult(
                coefficients=AlgorithmCoefficients().to_dict(),
                mae=1.0,
                rmse=1.5,
                sample_count=4,
                per_position_mae={"QB": 1.0},
            )
            payload = {
                "run_id": "legacy_1",
                "timestamp": "2024-01-01T00:00:00+00:00",
                "year": 2024,
                "weeks": [1],
                "player_count": 1,
                "sample_count": 4,
                "variations_tested": 1,
                "best": asdict(variation),
                "top_variations": [asdict(variation)],
                "default_result": asdict(variation),
            }
            with open(os.path.join(tmpdir, "tuning_legacy_1.json"), "w") as f:
                json.dump(payload, f)

            loaded = tuner.load_results()
            assert len(loaded) == 1
            assert loaded[0].run_filters == {}
            assert loaded[0].sample_comparisons == []

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
        assert rebuilt.run_filters == result.run_filters
        assert len(rebuilt.sample_comparisons) == len(result.sample_comparisons)


# ---------------------------------------------------------------------------
# PositionCoefficients tests
# ---------------------------------------------------------------------------


class TestPositionCoefficients:
    def test_from_global_copies_to_all_positions(self):
        base = AlgorithmCoefficients(skill_multiplier=0.2)
        pc = PositionCoefficients.from_global(base)

        for pos in TUNABLE_POSITIONS:
            coeffs = pc.get_for_position(pos)
            assert coeffs.skill_multiplier == 0.2
            # Verify it's a copy, not the same object
            assert coeffs is not base

    def test_from_global_defaults(self):
        pc = PositionCoefficients.from_global()
        defaults = AlgorithmCoefficients()

        for pos in TUNABLE_POSITIONS:
            assert pc.get_for_position(pos).to_dict() == defaults.to_dict()

    def test_get_for_position_falls_back_to_default(self):
        pc = PositionCoefficients(
            default=AlgorithmCoefficients(skill_multiplier=0.3),
            by_position={"QB": AlgorithmCoefficients(skill_multiplier=0.5)},
        )
        assert pc.get_for_position("QB").skill_multiplier == 0.5
        assert pc.get_for_position("RB").skill_multiplier == 0.3
        assert pc.get_for_position("UNKNOWN").skill_multiplier == 0.3

    def test_to_dict_from_dict_roundtrip(self):
        pc = PositionCoefficients.from_global(
            AlgorithmCoefficients(skill_multiplier=0.15)
        )
        pc.by_position["QB"] = AlgorithmCoefficients(skill_multiplier=0.25)

        data = pc.to_dict()
        rebuilt = PositionCoefficients.from_dict(data)

        assert rebuilt.get_for_position("QB").skill_multiplier == 0.25
        assert rebuilt.get_for_position("RB").skill_multiplier == 0.15
        assert rebuilt.default.skill_multiplier == 0.15

    def test_from_dict_legacy_flat_format(self):
        """Legacy flat dict should be treated as global defaults."""
        legacy = {"skill_multiplier": 0.2, "offense_multiplier": 0.08}
        pc = PositionCoefficients.from_dict(legacy)

        for pos in TUNABLE_POSITIONS:
            assert pc.get_for_position(pos).skill_multiplier == 0.2
            assert pc.get_for_position(pos).offense_multiplier == 0.08

    def test_from_dict_empty(self):
        pc = PositionCoefficients.from_dict({})
        defaults = AlgorithmCoefficients()
        assert pc.default.to_dict() == defaults.to_dict()

    def test_positions_property(self):
        pc = PositionCoefficients(
            default=AlgorithmCoefficients(),
            by_position={
                "QB": AlgorithmCoefficients(),
                "WR": AlgorithmCoefficients(),
            },
        )
        assert sorted(pc.positions) == ["QB", "WR"]


# ---------------------------------------------------------------------------
# Per-position tuning run tests
# ---------------------------------------------------------------------------


class TestPerPositionTuning:
    def test_run_per_position_returns_result(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run_per_position(
            year=2024,
            max_variations=5,
            top_n=3,
        )
        assert isinstance(result, TuningRunResult)
        assert result.sample_count > 0
        assert result.per_position_results  # should have position keys
        assert result.combined_coefficients  # should have combined coefficients
        assert result.run_filters.get("per_position") is True

    def test_run_per_position_has_all_sampled_positions(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run_per_position(
            year=2024,
            max_variations=5,
        )
        # The populated_db has QB and RB
        assert "QB" in result.per_position_results
        assert "RB" in result.per_position_results

    def test_run_per_position_each_position_tuned_independently(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run_per_position(
            year=2024,
            max_variations=10,
        )
        # Each position result should have its own best coefficients
        qb_result = result.per_position_results["QB"]
        rb_result = result.per_position_results["RB"]

        if isinstance(qb_result, dict):
            qb_best = qb_result["best"]["coefficients"]
            rb_best = rb_result["best"]["coefficients"]
        else:
            qb_best = qb_result.best.coefficients
            rb_best = rb_result.best.coefficients

        # They CAN be the same if the optimal is the same, but the
        # structure should exist independently
        assert isinstance(qb_best, dict)
        assert isinstance(rb_best, dict)

    def test_run_per_position_combined_coefficients_structure(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run_per_position(
            year=2024,
            max_variations=5,
        )
        combined = result.combined_coefficients
        assert "default" in combined
        assert "QB" in combined
        assert "RB" in combined
        # Each entry should be a full coefficient dict
        assert "skill_multiplier" in combined["default"]
        assert "skill_multiplier" in combined["QB"]

    def test_run_per_position_best_at_least_as_good(self, populated_db):
        """Combined per-position tuning should be at least as good as global."""
        tuner = ProjectionAlgorithmTuner(populated_db)
        global_result = tuner.run(year=2024, max_variations=50)
        per_pos_result = tuner.run_per_position(year=2024, max_variations=50)

        # Per-position can optimise each position's samples independently,
        # so the combined MAE should be <= global MAE (or very close)
        assert per_pos_result.best.mae <= global_result.best.mae + 0.01

    def test_run_per_position_with_position_filter(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run_per_position(
            year=2024,
            positions=["QB"],
            max_variations=5,
        )
        assert "QB" in result.per_position_results
        assert "RB" not in result.per_position_results

    def test_run_per_position_sample_comparisons(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run_per_position(
            year=2024,
            max_variations=5,
        )
        assert len(result.sample_comparisons) == result.sample_count
        for sample in result.sample_comparisons:
            assert isinstance(sample, SampleComparisonRow)
            assert sample.tuned_error >= 0
            assert sample.default_error >= 0

    def test_run_per_position_save_and_load(self, populated_db):
        with tempfile.TemporaryDirectory() as tmpdir:
            tuner = ProjectionAlgorithmTuner(populated_db, results_dir=tmpdir)
            result = tuner.run_per_position(year=2024, max_variations=5)
            path = tuner.save_result(result)

            assert os.path.exists(path)
            loaded = tuner.load_results()
            assert len(loaded) == 1

            rebuilt = loaded[0]
            assert rebuilt.year == 2024
            assert rebuilt.combined_coefficients == result.combined_coefficients
            assert "QB" in rebuilt.per_position_results

            # After load, per_position_results are PositionTuningResult objects
            qb_rebuilt = rebuilt.per_position_results["QB"]
            assert isinstance(qb_rebuilt, PositionTuningResult)

            # The original result stores asdict() dicts
            qb_original = result.per_position_results["QB"]
            assert qb_rebuilt.best.mae == qb_original["best"]["mae"]

    def test_run_per_position_analysis_report(self, populated_db):
        tuner = ProjectionAlgorithmTuner(populated_db)
        result = tuner.run_per_position(year=2024, max_variations=10)
        report = tuner.generate_analysis_report(result)

        assert report["summary"]["per_position"] is True
        assert "per_position_tuning" in report
        assert "combined_coefficients" in report

        for pos in ["QB", "RB"]:
            pos_detail = report["per_position_tuning"][pos]
            assert "default_mae" in pos_detail
            assert "tuned_mae" in pos_detail
            assert "mae_reduction" in pos_detail
            assert "best_coefficients" in pos_detail
            assert "coefficient_changes" in pos_detail

    def test_run_per_position_raises_on_no_data(self, db):
        tuner = ProjectionAlgorithmTuner(db)
        with pytest.raises(ValueError, match="No valid evaluation samples"):
            tuner.run_per_position(year=2099)
