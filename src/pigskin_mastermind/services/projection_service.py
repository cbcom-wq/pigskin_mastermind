"""Projection services for generating player projections."""

from typing import Dict, Any
from abc import ABC, abstractmethod
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.projection_criteria import (
    PlayerProjectionCriteria,
    YearlyProjectionCriteria,
    WeeklyProjectionCriteria,
)


class ProjectionService(ABC):
    """
    Abstract base class for projection services.

    Provides interface for generating player projections based on various criteria.
    """

    @abstractmethod
    def calculate_projection(
        self,
        player: Player,
        criteria: PlayerProjectionCriteria,
    ) -> float:
        """
        Calculate projected fantasy points for a player.

        Args:
            player: Player instance
            criteria: Projection criteria to use

        Returns:
            Projected fantasy points
        """
        pass

    def _apply_base_criteria(self, criteria: PlayerProjectionCriteria) -> float:
        """
        Apply base criteria common to all projection types.

        This is a starting implementation that combines the various criteria
        into a base score. In a real implementation, this would be replaced
        with a more sophisticated algorithm or machine learning model.

        Args:
            criteria: Base projection criteria

        Returns:
            Base score from common criteria
        """
        # Start with historical average
        base_score = criteria.historical_average_points

        # Adjust for player skill level (normalized to -5 to +5 range)
        skill_adjustment = (criteria.player_skill_level - 50) * 0.1
        base_score += skill_adjustment

        # Adjust for team offense level (normalized to -3 to +3 range)
        offense_adjustment = (criteria.team_offense_level - 50) * 0.06
        base_score += offense_adjustment

        # Adjust for opponent defense level (worse defense = higher score)
        defense_adjustment = (criteria.opponent_defense_level - 50) * 0.04
        base_score += defense_adjustment

        # Adjust for touch percentage (more touches = higher projection)
        touch_adjustment = criteria.positional_touch_percentage * 0.05
        base_score += touch_adjustment

        # Adjust for recent trends (normalized to -3 to +3 range)
        trend_adjustment = criteria.recent_trend_score * 0.03
        base_score += trend_adjustment

        # Adjust for fantasy points per touch efficiency
        if criteria.fantasy_points_per_touch > 0:
            efficiency_adjustment = (criteria.fantasy_points_per_touch - 0.5) * 2
            base_score += efficiency_adjustment

        # Adjust for injury risk (higher risk = lower projection)
        injury_penalty = criteria.injury_risk_score * -0.05
        base_score += injury_penalty

        return max(0, base_score)  # Ensure non-negative


class YearlyProjectionService(ProjectionService):
    """
    Service for generating yearly (season-long) player projections.

    Uses yearly-specific criteria including age and coaching stability.
    """

    def calculate_projection(
        self,
        player: Player,
        criteria: YearlyProjectionCriteria,
    ) -> float:
        """
        Calculate yearly projected fantasy points for a player.

        Args:
            player: Player instance
            criteria: Yearly projection criteria

        Returns:
            Projected fantasy points for the season
        """
        # Start with base criteria calculation
        base_score = self._apply_base_criteria(criteria)

        # Apply yearly-specific adjustments

        # Age deviation impact (peak age = 0 deviation)
        # Players further from peak age get penalized
        age_penalty = abs(criteria.age_deviation_from_optimum) * -0.5
        base_score += age_penalty

        # Coaching stability impact (stable coaching = better performance)
        coaching_adjustment = (criteria.coaching_stability_score - 50) * 0.04
        base_score += coaching_adjustment

        return max(0, base_score)

    def generate_projection_report(
        self,
        player: Player,
        criteria: YearlyProjectionCriteria,
    ) -> Dict[str, Any]:
        """
        Generate a detailed projection report for a player.

        Args:
            player: Player instance
            criteria: Yearly projection criteria

        Returns:
            Dictionary containing projection and breakdown
        """
        projected_points = self.calculate_projection(player, criteria)

        return {
            "player_id": player.player_id,
            "player_name": player.name,
            "position": player.position,
            "team": player.team,
            "projected_points": round(projected_points, 2),
            "projection_type": "yearly",
            "criteria_used": {
                "player_skill_level": criteria.player_skill_level,
                "team_offense_level": criteria.team_offense_level,
                "opponent_defense_level": criteria.opponent_defense_level,
                "positional_touch_percentage": criteria.positional_touch_percentage,
                "recent_trend_score": criteria.recent_trend_score,
                "historical_average_points": criteria.historical_average_points,
                "fantasy_points_per_touch": criteria.fantasy_points_per_touch,
                "injury_risk_score": criteria.injury_risk_score,
                "age_deviation_from_optimum": criteria.age_deviation_from_optimum,
                "coaching_stability_score": criteria.coaching_stability_score,
            },
        }


class WeeklyProjectionService(ProjectionService):
    """
    Service for generating weekly player projections.

    Uses weekly-specific criteria including matchup and weather factors.
    """

    def calculate_projection(
        self,
        player: Player,
        criteria: WeeklyProjectionCriteria,
    ) -> float:
        """
        Calculate weekly projected fantasy points for a player.

        Args:
            player: Player instance
            criteria: Weekly projection criteria

        Returns:
            Projected fantasy points for the week
        """
        # Start with base criteria calculation
        base_score = self._apply_base_criteria(criteria)

        # Apply weekly-specific adjustments

        # Opposing defense rank impact (rank 1 = best defense, 32 = worst)
        # Better opponent defense (lower rank) = lower projection
        defense_rank_adjustment = (
            criteria.opposing_defense_vs_position_rank - 16
        ) * 0.15
        base_score += defense_rank_adjustment

        # Offensive momentum impact (hot/cold streak)
        momentum_adjustment = criteria.offensive_momentum_score * 0.02
        base_score += momentum_adjustment

        # Weather impact (bad weather can hurt certain positions more)
        weather_adjustment = criteria.weather_impact_score * 0.015
        base_score += weather_adjustment

        return max(0, base_score)

    def generate_projection_report(
        self,
        player: Player,
        criteria: WeeklyProjectionCriteria,
    ) -> Dict[str, Any]:
        """
        Generate a detailed projection report for a player.

        Args:
            player: Player instance
            criteria: Weekly projection criteria

        Returns:
            Dictionary containing projection and breakdown
        """
        projected_points = self.calculate_projection(player, criteria)

        return {
            "player_id": player.player_id,
            "player_name": player.name,
            "position": player.position,
            "team": player.team,
            "projected_points": round(projected_points, 2),
            "projection_type": "weekly",
            "criteria_used": {
                "player_skill_level": criteria.player_skill_level,
                "team_offense_level": criteria.team_offense_level,
                "opponent_defense_level": criteria.opponent_defense_level,
                "positional_touch_percentage": criteria.positional_touch_percentage,
                "recent_trend_score": criteria.recent_trend_score,
                "historical_average_points": criteria.historical_average_points,
                "fantasy_points_per_touch": criteria.fantasy_points_per_touch,
                "injury_risk_score": criteria.injury_risk_score,
                "opposing_defense_vs_position_rank": criteria.opposing_defense_vs_position_rank,
                "offensive_momentum_score": criteria.offensive_momentum_score,
                "weather_impact_score": criteria.weather_impact_score,
            },
        }
