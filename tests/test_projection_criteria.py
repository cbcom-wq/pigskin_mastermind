"""Tests for projection criteria models."""

import pytest
from pigskin_mastermind.models.projection_criteria import (
    PlayerProjectionCriteria,
    YearlyProjectionCriteria,
    WeeklyProjectionCriteria,
)


def test_base_criteria_creation():
    """Test creating base projection criteria."""
    criteria = PlayerProjectionCriteria(
        player_skill_level=75.0,
        team_offense_level=60.0,
        opponent_defense_level=55.0,
        positional_touch_percentage=25.0,
        recent_trend_score=10.0,
        historical_average_points=15.5,
        fantasy_points_per_touch=0.8,
        injury_risk_score=20.0,
    )
    
    assert criteria.player_skill_level == 75.0
    assert criteria.team_offense_level == 60.0
    assert criteria.opponent_defense_level == 55.0
    assert criteria.positional_touch_percentage == 25.0
    assert criteria.recent_trend_score == 10.0
    assert criteria.historical_average_points == 15.5
    assert criteria.fantasy_points_per_touch == 0.8
    assert criteria.injury_risk_score == 20.0


def test_base_criteria_defaults():
    """Test default values for base criteria."""
    criteria = PlayerProjectionCriteria()
    
    assert criteria.player_skill_level == 50.0
    assert criteria.team_offense_level == 50.0
    assert criteria.opponent_defense_level == 50.0
    assert criteria.positional_touch_percentage == 0.0
    assert criteria.recent_trend_score == 0.0
    assert criteria.historical_average_points == 0.0
    assert criteria.fantasy_points_per_touch == 0.0
    assert criteria.injury_risk_score == 0.0


def test_base_criteria_validation_skill_level():
    """Test validation of skill level."""
    with pytest.raises(ValueError, match="player_skill_level must be between 0 and 100"):
        PlayerProjectionCriteria(player_skill_level=150.0)
    
    with pytest.raises(ValueError, match="player_skill_level must be between 0 and 100"):
        PlayerProjectionCriteria(player_skill_level=-10.0)


def test_base_criteria_validation_team_offense():
    """Test validation of team offense level."""
    with pytest.raises(ValueError, match="team_offense_level must be between 0 and 100"):
        PlayerProjectionCriteria(team_offense_level=150.0)


def test_base_criteria_validation_opponent_defense():
    """Test validation of opponent defense level."""
    with pytest.raises(ValueError, match="opponent_defense_level must be between 0 and 100"):
        PlayerProjectionCriteria(opponent_defense_level=-5.0)


def test_base_criteria_validation_touch_percentage():
    """Test validation of touch percentage."""
    with pytest.raises(ValueError, match="positional_touch_percentage must be between 0 and 100"):
        PlayerProjectionCriteria(positional_touch_percentage=150.0)


def test_base_criteria_validation_trend_score():
    """Test validation of trend score."""
    with pytest.raises(ValueError, match="recent_trend_score must be between -100 and 100"):
        PlayerProjectionCriteria(recent_trend_score=150.0)
    
    with pytest.raises(ValueError, match="recent_trend_score must be between -100 and 100"):
        PlayerProjectionCriteria(recent_trend_score=-150.0)


def test_base_criteria_validation_injury_risk():
    """Test validation of injury risk score."""
    with pytest.raises(ValueError, match="injury_risk_score must be between 0 and 100"):
        PlayerProjectionCriteria(injury_risk_score=150.0)


def test_yearly_criteria_creation():
    """Test creating yearly projection criteria."""
    criteria = YearlyProjectionCriteria(
        player_skill_level=80.0,
        team_offense_level=70.0,
        age_deviation_from_optimum=-2.0,
        coaching_stability_score=85.0,
    )
    
    assert criteria.player_skill_level == 80.0
    assert criteria.team_offense_level == 70.0
    assert criteria.age_deviation_from_optimum == -2.0
    assert criteria.coaching_stability_score == 85.0


def test_yearly_criteria_defaults():
    """Test default values for yearly criteria."""
    criteria = YearlyProjectionCriteria()
    
    assert criteria.age_deviation_from_optimum == 0.0
    assert criteria.coaching_stability_score == 50.0


def test_yearly_criteria_validation_age_deviation():
    """Test validation of age deviation."""
    with pytest.raises(ValueError, match="age_deviation_from_optimum must be between -10 and 10"):
        YearlyProjectionCriteria(age_deviation_from_optimum=15.0)
    
    with pytest.raises(ValueError, match="age_deviation_from_optimum must be between -10 and 10"):
        YearlyProjectionCriteria(age_deviation_from_optimum=-15.0)


def test_yearly_criteria_validation_coaching_stability():
    """Test validation of coaching stability."""
    with pytest.raises(ValueError, match="coaching_stability_score must be between 0 and 100"):
        YearlyProjectionCriteria(coaching_stability_score=150.0)


def test_weekly_criteria_creation():
    """Test creating weekly projection criteria."""
    criteria = WeeklyProjectionCriteria(
        player_skill_level=75.0,
        team_offense_level=65.0,
        opposing_defense_vs_position_rank=25,
        offensive_momentum_score=15.0,
        weather_impact_score=-20.0,
    )
    
    assert criteria.player_skill_level == 75.0
    assert criteria.team_offense_level == 65.0
    assert criteria.opposing_defense_vs_position_rank == 25
    assert criteria.offensive_momentum_score == 15.0
    assert criteria.weather_impact_score == -20.0


def test_weekly_criteria_defaults():
    """Test default values for weekly criteria."""
    criteria = WeeklyProjectionCriteria()
    
    assert criteria.opposing_defense_vs_position_rank == 16
    assert criteria.offensive_momentum_score == 0.0
    assert criteria.weather_impact_score == 0.0


def test_weekly_criteria_validation_defense_rank():
    """Test validation of opposing defense rank."""
    with pytest.raises(ValueError, match="opposing_defense_vs_position_rank must be between 1 and 32"):
        WeeklyProjectionCriteria(opposing_defense_vs_position_rank=0)
    
    with pytest.raises(ValueError, match="opposing_defense_vs_position_rank must be between 1 and 32"):
        WeeklyProjectionCriteria(opposing_defense_vs_position_rank=33)


def test_weekly_criteria_validation_momentum():
    """Test validation of offensive momentum."""
    with pytest.raises(ValueError, match="offensive_momentum_score must be between -100 and 100"):
        WeeklyProjectionCriteria(offensive_momentum_score=150.0)


def test_weekly_criteria_validation_weather():
    """Test validation of weather impact."""
    with pytest.raises(ValueError, match="weather_impact_score must be between -100 and 100"):
        WeeklyProjectionCriteria(weather_impact_score=-150.0)
