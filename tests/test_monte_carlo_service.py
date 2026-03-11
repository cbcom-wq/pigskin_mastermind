"""Tests for the Monte Carlo fantasy simulation engine."""

import pytest
import numpy as np

from pigskin_mastermind.models.monte_carlo import (
    FantasyPlayerInput,
    MonteCarloResult,
)
from pigskin_mastermind.services.monte_carlo_service import (
    FantasySimulationEngine,
    BOOM_THRESHOLD,
    BUST_THRESHOLD,
)
from pigskin_mastermind.services.monte_carlo_input_builder import (
    MonteCarloInputBuilder,
    _norm_0_100,
    _norm_neg100_100,
)
from pigskin_mastermind.models.projection_criteria import WeeklyProjectionCriteria


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def sample_player() -> FantasyPlayerInput:
    """A typical RB2 input for testing."""
    return FantasyPlayerInput(
        player_name="Test RB",
        position="RB",
        player_skill_level=0.65,
        team_offense_level=0.6,
        opponent_defense_level=0.45,
        positional_touch_percentage=0.55,
        recent_trend_score=0.6,
        historical_average_points=14.0,
        fantasy_points_per_touch=1.2,
        injury_risk_score=0.1,
        opposing_defense_vs_position_rank=20,
        offensive_momentum_score=0.55,
        weather_impact_score=0.5,
    )


@pytest.fixture
def engine() -> FantasySimulationEngine:
    """Engine with fixed seed for deterministic tests."""
    return FantasySimulationEngine(simulations=10_000, seed=42)


# ------------------------------------------------------------------
# FantasyPlayerInput validation
# ------------------------------------------------------------------


class TestFantasyPlayerInput:
    """Validation tests for the input dataclass."""

    def test_valid_input(self, sample_player):
        """Valid inputs should not raise."""
        assert sample_player.player_name == "Test RB"

    def test_skill_level_out_of_range(self):
        with pytest.raises(ValueError, match="player_skill_level"):
            FantasyPlayerInput(player_skill_level=1.5)

    def test_negative_skill_level(self):
        with pytest.raises(ValueError, match="player_skill_level"):
            FantasyPlayerInput(player_skill_level=-0.1)

    def test_injury_score_out_of_range(self):
        with pytest.raises(ValueError, match="injury_risk_score"):
            FantasyPlayerInput(injury_risk_score=1.1)

    def test_defense_rank_out_of_range(self):
        with pytest.raises(ValueError, match="opposing_defense_vs_position_rank"):
            FantasyPlayerInput(opposing_defense_vs_position_rank=0)

    def test_defense_rank_too_high(self):
        with pytest.raises(ValueError, match="opposing_defense_vs_position_rank"):
            FantasyPlayerInput(opposing_defense_vs_position_rank=33)

    def test_zero_fpts_per_touch(self):
        with pytest.raises(ValueError, match="fantasy_points_per_touch"):
            FantasyPlayerInput(fantasy_points_per_touch=0)

    def test_negative_historical_points(self):
        with pytest.raises(ValueError, match="historical_average_points"):
            FantasyPlayerInput(historical_average_points=-5)


# ------------------------------------------------------------------
# MonteCarloResult serialisation
# ------------------------------------------------------------------


class TestMonteCarloResult:
    """Tests for result serialisation methods."""

    def test_to_dict_keys(self, sample_player, engine):
        result = engine.simulate(sample_player)
        d = result.to_dict()
        expected_keys = {
            "player_name", "position", "expected_points", "median_points",
            "floor", "ceiling", "boom_probability", "bust_probability",
            "std_dev", "simulation_count",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_excludes_simulations(self, sample_player, engine):
        result = engine.simulate(sample_player)
        d = result.to_dict()
        assert "simulations" not in d

    def test_to_detailed_dict_has_percentiles(self, sample_player, engine):
        result = engine.simulate(sample_player)
        d = result.to_detailed_dict()
        assert "percentiles" in d
        assert "histogram" in d
        assert "p50" in d["percentiles"]

    def test_histogram_structure(self, sample_player, engine):
        result = engine.simulate(sample_player)
        d = result.to_detailed_dict()
        hist = d["histogram"]
        assert len(hist) > 0
        first_bin = hist[0]
        assert "bin_start" in first_bin
        assert "bin_end" in first_bin
        assert "count" in first_bin
        assert "frequency" in first_bin


# ------------------------------------------------------------------
# FantasySimulationEngine
# ------------------------------------------------------------------


class TestSimulationEngine:
    """Core simulation engine tests."""

    def test_deterministic_with_seed(self, sample_player):
        """Same seed must produce identical results."""
        engine_a = FantasySimulationEngine(simulations=5000, seed=123)
        engine_b = FantasySimulationEngine(simulations=5000, seed=123)

        result_a = engine_a.simulate(sample_player)
        result_b = engine_b.simulate(sample_player)

        np.testing.assert_array_equal(result_a.simulations, result_b.simulations)
        assert result_a.expected_points == result_b.expected_points

    def test_different_seeds_differ(self, sample_player):
        """Different seeds should produce different results."""
        result_a = FantasySimulationEngine(simulations=5000, seed=1).simulate(
            sample_player
        )
        result_b = FantasySimulationEngine(simulations=5000, seed=2).simulate(
            sample_player
        )
        assert result_a.expected_points != result_b.expected_points

    def test_simulation_count(self, sample_player, engine):
        """Output should contain the requested number of simulations."""
        result = engine.simulate(sample_player)
        assert result.simulation_count == 10_000
        assert len(result.simulations) == 10_000

    def test_non_negative_scores(self, sample_player, engine):
        """All simulated scores must be >= 0."""
        result = engine.simulate(sample_player)
        assert np.all(result.simulations >= 0)

    def test_floor_less_than_ceiling(self, sample_player, engine):
        """10th percentile must be below 90th percentile."""
        result = engine.simulate(sample_player)
        assert result.floor < result.ceiling

    def test_median_between_floor_and_ceiling(self, sample_player, engine):
        """Median should fall between floor and ceiling."""
        result = engine.simulate(sample_player)
        assert result.floor <= result.median_points <= result.ceiling

    def test_boom_bust_probabilities_valid(self, sample_player, engine):
        """Probabilities must be between 0 and 1."""
        result = engine.simulate(sample_player)
        assert 0.0 <= result.boom_probability <= 1.0
        assert 0.0 <= result.bust_probability <= 1.0

    def test_expected_points_positive(self, sample_player, engine):
        """A reasonable player should have positive expected points."""
        result = engine.simulate(sample_player)
        assert result.expected_points > 0

    def test_std_dev_positive(self, sample_player, engine):
        """Standard deviation should be positive for a real player."""
        result = engine.simulate(sample_player)
        assert result.std_dev > 0

    def test_invalid_simulation_count(self):
        """simulations < 1 should raise."""
        with pytest.raises(ValueError, match="simulations must be >= 1"):
            FantasySimulationEngine(simulations=0)

    def test_high_injury_risk_lowers_projection(self, sample_player):
        """A player with high injury risk should project lower on average."""
        engine = FantasySimulationEngine(simulations=10_000, seed=42)

        healthy = sample_player
        from dataclasses import replace

        injured = replace(healthy, injury_risk_score=0.8)

        result_healthy = engine.simulate(healthy)
        result_injured = engine.simulate(injured)

        assert result_injured.expected_points < result_healthy.expected_points

    def test_better_matchup_increases_projection(self, sample_player):
        """Facing a weaker defense should raise the projection."""
        engine = FantasySimulationEngine(simulations=10_000, seed=42)
        from dataclasses import replace

        # Tough matchup: rank 3 (elite defense)
        tough = replace(
            sample_player,
            opposing_defense_vs_position_rank=3,
            opponent_defense_level=0.85,
        )
        # Easy matchup: rank 30 (weak defense)
        easy = replace(
            sample_player,
            opposing_defense_vs_position_rank=30,
            opponent_defense_level=0.2,
        )

        result_tough = engine.simulate(tough)
        result_easy = engine.simulate(easy)

        assert result_easy.expected_points > result_tough.expected_points

    def test_star_player_booms_more(self):
        """A star RB1 should have higher boom probability than a flex."""
        engine = FantasySimulationEngine(simulations=10_000, seed=42)

        star = FantasyPlayerInput(
            player_name="Star RB",
            position="RB",
            player_skill_level=0.9,
            team_offense_level=0.85,
            opponent_defense_level=0.3,
            positional_touch_percentage=0.7,
            recent_trend_score=0.7,
            historical_average_points=22.0,
            fantasy_points_per_touch=1.5,
            injury_risk_score=0.05,
            opposing_defense_vs_position_rank=28,
            offensive_momentum_score=0.7,
            weather_impact_score=0.5,
        )

        flex = FantasyPlayerInput(
            player_name="Flex RB",
            position="RB",
            player_skill_level=0.35,
            team_offense_level=0.4,
            opponent_defense_level=0.6,
            positional_touch_percentage=0.3,
            recent_trend_score=0.45,
            historical_average_points=7.0,
            fantasy_points_per_touch=0.8,
            injury_risk_score=0.15,
            opposing_defense_vs_position_rank=8,
            offensive_momentum_score=0.4,
            weather_impact_score=0.5,
        )

        star_result = engine.simulate(star)
        flex_result = engine.simulate(flex)

        assert star_result.boom_probability > flex_result.boom_probability
        assert star_result.expected_points > flex_result.expected_points

    def test_zero_historical_points(self):
        """Player with minimal history should still produce valid output."""
        engine = FantasySimulationEngine(simulations=1000, seed=42)
        player = FantasyPlayerInput(
            historical_average_points=0.1,
            fantasy_points_per_touch=0.5,
        )
        result = engine.simulate(player)
        assert result.simulation_count == 1000
        assert result.expected_points >= 0


# ------------------------------------------------------------------
# MonteCarloInputBuilder
# ------------------------------------------------------------------


class TestMonteCarloInputBuilder:
    """Tests for the criteria-to-input translation layer."""

    def test_normalisation_helpers(self):
        assert _norm_0_100(0) == 0.0
        assert _norm_0_100(100) == 1.0
        assert _norm_0_100(50) == 0.5
        assert _norm_0_100(150) == 1.0  # clamped

        assert _norm_neg100_100(-100) == 0.0
        assert _norm_neg100_100(0) == 0.5
        assert _norm_neg100_100(100) == 1.0

    def test_from_weekly_criteria_default(self):
        """Default criteria should convert without error."""
        criteria = WeeklyProjectionCriteria()
        player_input = MonteCarloInputBuilder.from_weekly_criteria(
            criteria, player_name="Test", position="WR",
        )
        assert player_input.player_name == "Test"
        assert player_input.position == "WR"
        assert 0.0 <= player_input.player_skill_level <= 1.0
        assert 0.0 <= player_input.team_offense_level <= 1.0

    def test_from_weekly_criteria_full(self):
        """Full criteria values should normalise correctly."""
        criteria = WeeklyProjectionCriteria(
            player_skill_level=75.0,
            team_offense_level=70.0,
            opponent_defense_level=40.0,
            positional_touch_percentage=30.0,
            recent_trend_score=20.0,
            historical_average_points=15.0,
            fantasy_points_per_touch=1.2,
            injury_risk_score=10.0,
            opposing_defense_vs_position_rank=8,
            offensive_momentum_score=30.0,
            weather_impact_score=-10.0,
        )
        player_input = MonteCarloInputBuilder.from_weekly_criteria(
            criteria, player_name="RB Star", position="RB",
        )
        assert player_input.player_skill_level == pytest.approx(0.75)
        assert player_input.team_offense_level == pytest.approx(0.70)
        assert player_input.opponent_defense_level == pytest.approx(0.40)
        assert player_input.recent_trend_score == pytest.approx(0.60)
        assert player_input.historical_average_points == 15.0
        assert player_input.fantasy_points_per_touch == 1.2
        assert player_input.injury_risk_score == pytest.approx(0.10)
        assert player_input.opposing_defense_vs_position_rank == 8
        assert player_input.offensive_momentum_score == pytest.approx(0.65)
        # weather: (-10 + 100) / 200 = 0.45
        assert player_input.weather_impact_score == pytest.approx(0.45)

    def test_from_criteria_clamps_minimum_points(self):
        """Ensure historical_average_points floors at 0.1."""
        criteria = WeeklyProjectionCriteria(
            historical_average_points=0.0,
            fantasy_points_per_touch=0.5,
        )
        player_input = MonteCarloInputBuilder.from_weekly_criteria(criteria)
        assert player_input.historical_average_points == 0.1

    def test_from_criteria_clamps_minimum_fpt(self):
        """Ensure fantasy_points_per_touch floors at 0.01."""
        criteria = WeeklyProjectionCriteria(
            historical_average_points=5.0,
            fantasy_points_per_touch=0.0,
        )
        player_input = MonteCarloInputBuilder.from_weekly_criteria(criteria)
        assert player_input.fantasy_points_per_touch == 0.01

    def test_build_for_player_requires_db(self):
        """build_for_player without a DB session should raise."""
        builder = MonteCarloInputBuilder()
        with pytest.raises(RuntimeError, match="requires a DB session"):
            builder.build_for_player(player_id=1, year=2025, week=1)


# ------------------------------------------------------------------
# Integration: full pipeline from criteria → simulation
# ------------------------------------------------------------------


class TestEndToEnd:
    """Verify the full criteria → input → simulation pipeline."""

    def test_criteria_to_simulation(self):
        """Build input from criteria, run simulation, check output."""
        criteria = WeeklyProjectionCriteria(
            player_skill_level=70.0,
            team_offense_level=65.0,
            opponent_defense_level=45.0,
            positional_touch_percentage=40.0,
            recent_trend_score=15.0,
            historical_average_points=16.0,
            fantasy_points_per_touch=1.3,
            injury_risk_score=5.0,
            opposing_defense_vs_position_rank=22,
            offensive_momentum_score=10.0,
            weather_impact_score=0.0,
        )

        player_input = MonteCarloInputBuilder.from_weekly_criteria(
            criteria, player_name="Pipeline Test", position="WR",
        )

        engine = FantasySimulationEngine(simulations=5000, seed=99)
        result = engine.simulate(player_input)

        assert result.player_name == "Pipeline Test"
        assert result.position == "WR"
        assert result.simulation_count == 5000
        assert result.expected_points > 0
        assert result.floor < result.ceiling
        assert 0 <= result.boom_probability <= 1
        assert 0 <= result.bust_probability <= 1

    def test_small_simulation_count(self):
        """Even 100 simulations should produce valid structure."""
        player = FantasyPlayerInput(
            player_name="Quick Test",
            position="TE",
            historical_average_points=8.0,
            fantasy_points_per_touch=0.9,
        )
        engine = FantasySimulationEngine(simulations=100, seed=7)
        result = engine.simulate(player)
        assert result.simulation_count == 100
        assert len(result.simulations) == 100
        d = result.to_dict()
        assert isinstance(d["expected_points"], float)
