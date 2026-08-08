"""Projection criteria models for player projections."""

from dataclasses import dataclass


@dataclass
class PlayerProjectionCriteria:
    """
    Base criteria for player projections (both weekly and yearly).

    These criteria apply to both weekly and yearly projection algorithms.

    Attributes:
        player_skill_level: Rating of player's overall skill (0-100)
        team_offense_level: Rating of player's team offense strength (0-100)
        opponent_defense_level: Rating of opposing defense
        (higher = worse defense, better for offense) (0-100)
        positional_touch_percentage: Percentage of team's touches at this position (0-100)
        recent_trend_score: Score based on recent performance trends (-100 to 100)
        historical_average_points: Historical average fantasy points per game
        fantasy_points_per_touch: Average fantasy points per touch/target
        injury_risk_score: Score representing injury history/risk (0-100, lower is better)
    """

    player_skill_level: float = 50.0
    team_offense_level: float = 50.0
    opponent_defense_level: float = 50.0
    positional_touch_percentage: float = 0.0
    recent_trend_score: float = 0.0
    historical_average_points: float = 0.0
    fantasy_points_per_touch: float = 0.0
    injury_risk_score: float = 0.0

    def __post_init__(self):
        """Validate criteria values."""
        # Validate skill level (0-100)
        if not 0 <= self.player_skill_level <= 100:
            raise ValueError("player_skill_level must be between 0 and 100")

        # Validate team offense level (0-100)
        if not 0 <= self.team_offense_level <= 100:
            raise ValueError("team_offense_level must be between 0 and 100")

        # Validate opponent defense level (0-100)
        if not 0 <= self.opponent_defense_level <= 100:
            raise ValueError("opponent_defense_level must be between 0 and 100")

        # Validate touch percentage (0-100)
        if not 0 <= self.positional_touch_percentage <= 100:
            raise ValueError("positional_touch_percentage must be between 0 and 100")

        # Validate recent trend score (-100 to 100)
        if not -100 <= self.recent_trend_score <= 100:
            raise ValueError("recent_trend_score must be between -100 and 100")

        # Validate injury risk score (0-100)
        if not 0 <= self.injury_risk_score <= 100:
            raise ValueError("injury_risk_score must be between 0 and 100")


@dataclass
class YearlyProjectionCriteria(PlayerProjectionCriteria):
    """
    Criteria specific to yearly projections.

    Extends base criteria with yearly-specific factors.

    Attributes:
        age_deviation_from_optimum: Years from optimal age for position (-10 to 10, 0 is optimal)
        coaching_stability_score: Score representing coaching staff stability (0-100)
        expected_games: Games the player is expected to play this season (0-17).
            The projection formula produces a per-game *rate*; multiplying by
            this is what turns it into a season total. Availability is a real
            part of season-long value — a 20 ppg player who plays 12 games is
            worth less than a 17 ppg player who plays all 17.
    """

    age_deviation_from_optimum: float = 0.0
    coaching_stability_score: float = 50.0
    expected_games: float = 17.0

    def __post_init__(self):
        """Validate yearly criteria values."""
        super().__post_init__()

        # Validate age deviation (-10 to 10)
        if not -10 <= self.age_deviation_from_optimum <= 10:
            raise ValueError("age_deviation_from_optimum must be between -10 and 10")

        # Validate coaching stability (0-100)
        if not 0 <= self.coaching_stability_score <= 100:
            raise ValueError("coaching_stability_score must be between 0 and 100")

        # Validate expected games (0-17 — the modern regular season)
        if not 0 <= self.expected_games <= 17:
            raise ValueError("expected_games must be between 0 and 17")


@dataclass
class WeeklyProjectionCriteria(PlayerProjectionCriteria):
    """
    Criteria specific to weekly projections.

    Extends base criteria with weekly-specific factors.

    Attributes:
        opposing_defense_vs_position_rank: Rank of opposing defense against this
        position (1-32)
        offensive_momentum_score: Score representing team's recent offensive
        momentum (-100 to 100)
        weather_impact_score: Score representing weather conditions impact
        (-100 to 100, 0 is neutral)
        home_field: 1.0 at home, -1.0 on the road, 0.0 when unknown. Requires
        the NFL schedule; 0.0 means "no information", not "neutral site".
        is_available: False when the player cannot play at all — bye week, OUT,
        or IR. A player who is not playing scores exactly zero, which is a
        different statement from "risky", and the injury_risk_score nudge
        cannot express it.
    """

    opposing_defense_vs_position_rank: int = 16
    offensive_momentum_score: float = 0.0
    weather_impact_score: float = 0.0
    home_field: float = 0.0
    is_available: bool = True

    def __post_init__(self):
        """Validate weekly criteria values."""
        super().__post_init__()

        # Validate home field indicator
        if not -1 <= self.home_field <= 1:
            raise ValueError("home_field must be between -1 and 1")

        # Validate opposing defense rank (1-32 for NFL teams)
        if not 1 <= self.opposing_defense_vs_position_rank <= 32:
            raise ValueError(
                "opposing_defense_vs_position_rank must be between 1 and 32"
            )

        # Validate offensive momentum (-100 to 100)
        if not -100 <= self.offensive_momentum_score <= 100:
            raise ValueError("offensive_momentum_score must be between -100 and 100")

        # Validate weather impact (-100 to 100)
        if not -100 <= self.weather_impact_score <= 100:
            raise ValueError("weather_impact_score must be between -100 and 100")
