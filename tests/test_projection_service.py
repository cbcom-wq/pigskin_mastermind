"""Tests for projection services."""

import pytest
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.algorithm_coefficients import (
    AlgorithmCoefficients,
    PositionCoefficients,
)
from pigskin_mastermind.models.projection_criteria import (
    YearlyProjectionCriteria,
    WeeklyProjectionCriteria,
)
from pigskin_mastermind.services.projection_service import (
    YearlyProjectionService,
    WeeklyProjectionService,
)


def test_yearly_projection_service_basic():
    """Test basic yearly projection calculation."""
    player = Player(
        player_id="p1",
        name="Test Player",
        position="RB",
        team="KC",
    )

    criteria = YearlyProjectionCriteria(
        player_skill_level=75.0,
        team_offense_level=70.0,
        opponent_defense_level=60.0,
        positional_touch_percentage=30.0,
        recent_trend_score=10.0,
        historical_average_points=15.0,
        fantasy_points_per_touch=0.7,
        injury_risk_score=10.0,
        age_deviation_from_optimum=-1.0,
        coaching_stability_score=80.0,
    )

    service = YearlyProjectionService()
    projection = service.calculate_projection(player, criteria)

    # Projection should be positive
    assert projection > 0
    # Should be influenced by historical average
    assert projection >= 10.0  # At least somewhat close to historical average


def test_yearly_projection_service_with_defaults():
    """Test yearly projection with default criteria."""
    player = Player(
        player_id="p1",
        name="Test Player",
        position="QB",
        team="KC",
    )

    criteria = YearlyProjectionCriteria()
    service = YearlyProjectionService()
    projection = service.calculate_projection(player, criteria)

    # With all defaults (averages), projection should be non-negative
    assert projection >= 0


def test_yearly_projection_high_skill_increases_projection():
    """Test that higher skill level increases projection."""
    player = Player(
        player_id="p1",
        name="Test Player",
        position="WR",
        team="KC",
    )

    # Low skill criteria
    low_skill_criteria = YearlyProjectionCriteria(
        player_skill_level=30.0,
        historical_average_points=10.0,
    )

    # High skill criteria
    high_skill_criteria = YearlyProjectionCriteria(
        player_skill_level=90.0,
        historical_average_points=10.0,
    )

    service = YearlyProjectionService()
    low_projection = service.calculate_projection(player, low_skill_criteria)
    high_projection = service.calculate_projection(player, high_skill_criteria)

    # Higher skill should result in higher projection
    assert high_projection > low_projection


def test_yearly_projection_age_deviation_penalty():
    """Test that age deviation from optimum reduces projection."""
    player = Player(
        player_id="p1",
        name="Test Player",
        position="RB",
        team="KC",
    )

    # At optimal age
    optimal_criteria = YearlyProjectionCriteria(
        age_deviation_from_optimum=0.0,
        historical_average_points=15.0,
    )

    # Far from optimal age
    non_optimal_criteria = YearlyProjectionCriteria(
        age_deviation_from_optimum=5.0,
        historical_average_points=15.0,
    )

    service = YearlyProjectionService()
    optimal_projection = service.calculate_projection(player, optimal_criteria)
    non_optimal_projection = service.calculate_projection(player, non_optimal_criteria)

    # Optimal age should result in higher projection
    assert optimal_projection > non_optimal_projection


def test_yearly_projection_report_generation():
    """Test generating a detailed yearly projection report."""
    player = Player(
        player_id="p1",
        name="Patrick Mahomes",
        position="QB",
        team="KC",
    )

    criteria = YearlyProjectionCriteria(
        player_skill_level=95.0,
        team_offense_level=90.0,
        historical_average_points=25.0,
        age_deviation_from_optimum=0.0,
        coaching_stability_score=95.0,
    )

    service = YearlyProjectionService()
    report = service.generate_projection_report(player, criteria)

    # Check report structure
    assert report["player_id"] == "p1"
    assert report["player_name"] == "Patrick Mahomes"
    assert report["position"] == "QB"
    assert report["team"] == "KC"
    assert report["projection_type"] == "yearly"
    assert "projected_points" in report
    assert "criteria_used" in report
    assert report["criteria_used"]["player_skill_level"] == 95.0


def test_weekly_projection_service_basic():
    """Test basic weekly projection calculation."""
    player = Player(
        player_id="p1",
        name="Test Player",
        position="WR",
        team="KC",
    )

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

    service = WeeklyProjectionService()
    projection = service.calculate_projection(player, criteria)

    # Projection should be positive
    assert projection > 0


def test_weekly_projection_service_with_defaults():
    """Test weekly projection with default criteria."""
    player = Player(
        player_id="p1",
        name="Test Player",
        position="TE",
        team="KC",
    )

    criteria = WeeklyProjectionCriteria()
    service = WeeklyProjectionService()
    projection = service.calculate_projection(player, criteria)

    # With all defaults, projection should be non-negative
    assert projection >= 0


def test_weekly_projection_weak_defense_increases_projection():
    """Test that weaker opposing defense increases projection."""
    player = Player(
        player_id="p1",
        name="Test Player",
        position="RB",
        team="KC",
    )

    # Strong defense (low rank number = strong)
    strong_defense_criteria = WeeklyProjectionCriteria(
        opposing_defense_vs_position_rank=1,
        historical_average_points=10.0,
    )

    # Weak defense (high rank number = weak)
    weak_defense_criteria = WeeklyProjectionCriteria(
        opposing_defense_vs_position_rank=32,
        historical_average_points=10.0,
    )

    service = WeeklyProjectionService()
    strong_defense_projection = service.calculate_projection(
        player, strong_defense_criteria
    )
    weak_defense_projection = service.calculate_projection(
        player, weak_defense_criteria
    )

    # Weaker opposing defense should result in higher projection
    assert weak_defense_projection > strong_defense_projection


def test_weekly_projection_momentum_impact():
    """Test that offensive momentum impacts projection."""
    player = Player(
        player_id="p1",
        name="Test Player",
        position="QB",
        team="KC",
    )

    # Negative momentum
    negative_momentum_criteria = WeeklyProjectionCriteria(
        offensive_momentum_score=-50.0,
        historical_average_points=20.0,
    )

    # Positive momentum
    positive_momentum_criteria = WeeklyProjectionCriteria(
        offensive_momentum_score=50.0,
        historical_average_points=20.0,
    )

    service = WeeklyProjectionService()
    negative_projection = service.calculate_projection(
        player, negative_momentum_criteria
    )
    positive_projection = service.calculate_projection(
        player, positive_momentum_criteria
    )

    # Positive momentum should result in higher projection
    assert positive_projection > negative_projection


def test_weekly_projection_report_generation():
    """Test generating a detailed weekly projection report."""
    player = Player(
        player_id="p2",
        name="Travis Kelce",
        position="TE",
        team="KC",
    )

    criteria = WeeklyProjectionCriteria(
        player_skill_level=90.0,
        team_offense_level=85.0,
        historical_average_points=15.0,
        opposing_defense_vs_position_rank=30,
        offensive_momentum_score=25.0,
        weather_impact_score=0.0,
    )

    service = WeeklyProjectionService()
    report = service.generate_projection_report(player, criteria)

    # Check report structure
    assert report["player_id"] == "p2"
    assert report["player_name"] == "Travis Kelce"
    assert report["position"] == "TE"
    assert report["team"] == "KC"
    assert report["projection_type"] == "weekly"
    assert "projected_points" in report
    assert "criteria_used" in report
    assert report["criteria_used"]["opposing_defense_vs_position_rank"] == 30


def test_yearly_projection_young_player_not_penalized():
    """Young players (pre-peak) get a boost; old players (post-peak) get a penalty."""
    player = Player(player_id="p1", name="Test Player", position="QB", team="KC")
    base_kwargs = dict(historical_average_points=20.0)

    young_criteria = YearlyProjectionCriteria(age_deviation_from_optimum=-7.0, **base_kwargs)
    peak_criteria = YearlyProjectionCriteria(age_deviation_from_optimum=0.0, **base_kwargs)
    old_criteria = YearlyProjectionCriteria(age_deviation_from_optimum=7.0, **base_kwargs)

    service = YearlyProjectionService()
    young = service.calculate_projection(player, young_criteria)
    peak = service.calculate_projection(player, peak_criteria)
    old = service.calculate_projection(player, old_criteria)

    assert young > peak, "Young player should score higher than peak-age player"
    assert peak > old, "Peak-age player should score higher than past-peak player"


def test_yearly_projection_zero_efficiency_no_penalty():
    """Players with no touch data (0.0) should not be penalized vs a neutral (0.5) player."""
    player = Player(player_id="p1", name="Test Player", position="WR", team="KC")
    base_kwargs = dict(historical_average_points=10.0)

    no_data_criteria = YearlyProjectionCriteria(fantasy_points_per_touch=0.0, **base_kwargs)
    neutral_criteria = YearlyProjectionCriteria(fantasy_points_per_touch=0.5, **base_kwargs)

    service = YearlyProjectionService()
    no_data = service.calculate_projection(player, no_data_criteria)
    neutral = service.calculate_projection(player, neutral_criteria)

    assert no_data >= neutral, "Zero touch data should not be penalized below neutral efficiency"


def test_projection_non_negative():
    """Test that projections are always non-negative."""
    player = Player(
        player_id="p1",
        name="Test Player",
        position="K",
        team="KC",
    )

    # Very negative criteria
    negative_criteria = WeeklyProjectionCriteria(
        player_skill_level=10.0,
        team_offense_level=10.0,
        opponent_defense_level=10.0,
        historical_average_points=0.0,
        injury_risk_score=90.0,
        offensive_momentum_score=-100.0,
        weather_impact_score=-100.0,
    )

    service = WeeklyProjectionService()
    projection = service.calculate_projection(player, negative_criteria)

    # Even with very negative criteria, projection should not be negative
    assert projection >= 0


# ---------------------------------------------------------------------------
# Per-position coefficient tests
# ---------------------------------------------------------------------------


def test_weekly_service_with_position_coefficients():
    """Service with PositionCoefficients uses position-specific values."""
    qb_coeffs = AlgorithmCoefficients(skill_multiplier=0.2)
    rb_coeffs = AlgorithmCoefficients(skill_multiplier=0.05)
    pos_coeffs = PositionCoefficients(
        default=AlgorithmCoefficients(),
        by_position={"QB": qb_coeffs, "RB": rb_coeffs},
    )

    service = WeeklyProjectionService(coefficients=pos_coeffs)

    criteria = WeeklyProjectionCriteria(
        player_skill_level=80.0,
        historical_average_points=15.0,
    )

    qb = Player(player_id="qb1", name="QB", position="QB", team="KC")
    rb = Player(player_id="rb1", name="RB", position="RB", team="KC")

    qb_proj = service.calculate_projection(qb, criteria)
    rb_proj = service.calculate_projection(rb, criteria)

    # QB has higher skill_multiplier so should get a bigger boost
    assert qb_proj > rb_proj


def test_yearly_service_with_position_coefficients():
    """Yearly service also resolves per-position coefficients."""
    pos_coeffs = PositionCoefficients(
        default=AlgorithmCoefficients(),
        by_position={
            "QB": AlgorithmCoefficients(age_post_peak_multiplier=-1.0),
            "RB": AlgorithmCoefficients(age_post_peak_multiplier=-2.0),
        },
    )

    service = YearlyProjectionService(coefficients=pos_coeffs)

    criteria = YearlyProjectionCriteria(
        historical_average_points=20.0,
        age_deviation_from_optimum=3.0,  # 3 years past peak
    )

    qb = Player(player_id="qb1", name="QB", position="QB", team="KC")
    rb = Player(player_id="rb1", name="RB", position="RB", team="KC")

    qb_proj = service.calculate_projection(qb, criteria)
    rb_proj = service.calculate_projection(rb, criteria)

    # RB has steeper post-peak penalty
    assert qb_proj > rb_proj


def test_service_no_coefficients_uses_defaults():
    """Service without coefficients matches hard-coded default behaviour."""
    service_none = WeeklyProjectionService()
    service_default = WeeklyProjectionService(
        coefficients=AlgorithmCoefficients()
    )

    criteria = WeeklyProjectionCriteria(
        player_skill_level=70.0,
        team_offense_level=60.0,
        historical_average_points=12.0,
    )
    player = Player(player_id="p1", name="P", position="WR", team="KC")

    proj_none = service_none.calculate_projection(player, criteria)
    proj_default = service_default.calculate_projection(player, criteria)

    assert abs(proj_none - proj_default) < 0.001


def test_service_with_single_algorithm_coefficients():
    """Passing AlgorithmCoefficients wraps to PositionCoefficients."""
    custom = AlgorithmCoefficients(skill_multiplier=0.3)
    service = WeeklyProjectionService(coefficients=custom)

    criteria = WeeklyProjectionCriteria(
        player_skill_level=80.0,
        historical_average_points=10.0,
    )
    player = Player(player_id="p1", name="P", position="WR", team="KC")

    proj = service.calculate_projection(player, criteria)
    # Skill adjustment: (80-50) × 0.3 = 9.0 + 10.0 baseline = ~19
    assert proj > 15.0


def test_baseline_weight_scales_historical_average():
    """baseline_weight should scale the historical_average_points anchor."""
    player = Player(player_id="p1", name="P", position="QB", team="KC")

    criteria = WeeklyProjectionCriteria(
        historical_average_points=20.0,
    )

    # Default baseline_weight=1.0
    default_service = WeeklyProjectionService(
        coefficients=AlgorithmCoefficients(baseline_weight=1.0)
    )
    # Reduced baseline_weight=0.5
    half_service = WeeklyProjectionService(
        coefficients=AlgorithmCoefficients(baseline_weight=0.5)
    )
    # Boosted baseline_weight=1.5
    boosted_service = WeeklyProjectionService(
        coefficients=AlgorithmCoefficients(baseline_weight=1.5)
    )

    proj_default = default_service.calculate_projection(player, criteria)
    proj_half = half_service.calculate_projection(player, criteria)
    proj_boosted = boosted_service.calculate_projection(player, criteria)

    # Lower weight → lower projection; higher weight → higher projection
    assert proj_half < proj_default
    assert proj_boosted > proj_default


def test_baseline_weight_zero_removes_baseline():
    """baseline_weight=0 should remove historical average from projection."""
    player = Player(player_id="p1", name="P", position="RB", team="KC")

    criteria = WeeklyProjectionCriteria(
        historical_average_points=20.0,
    )

    zero_service = WeeklyProjectionService(
        coefficients=AlgorithmCoefficients(baseline_weight=0.0)
    )
    default_service = WeeklyProjectionService(
        coefficients=AlgorithmCoefficients(baseline_weight=1.0)
    )

    proj_zero = zero_service.calculate_projection(player, criteria)
    proj_default = default_service.calculate_projection(player, criteria)

    # The 20-point baseline is removed, so the projection should be ~20 less
    assert proj_default - proj_zero == pytest.approx(20.0, abs=0.01)
