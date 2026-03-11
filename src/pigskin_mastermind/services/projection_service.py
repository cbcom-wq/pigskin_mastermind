"""Projection services for generating player projections."""

from typing import Dict, Any, Optional
from abc import ABC, abstractmethod
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.algorithm_coefficients import (
    AlgorithmCoefficients,
    PositionCoefficients,
)
from pigskin_mastermind.models.projection_criteria import (
    PlayerProjectionCriteria,
    YearlyProjectionCriteria,
    WeeklyProjectionCriteria,
)


class ProjectionService(ABC):
    """
    Abstract base class for projection services.

    Provides interface for generating player projections based on various criteria.

    An optional :class:`PositionCoefficients` (or single
    :class:`AlgorithmCoefficients`) can be supplied at construction time.
    When provided the tuned values are used instead of the hard-coded
    defaults.  If ``None`` (the default) the original constants are used,
    preserving full backward compatibility.
    """

    def __init__(
        self,
        coefficients: Optional[PositionCoefficients | AlgorithmCoefficients] = None,
    ) -> None:
        if coefficients is None:
            self._pos_coeffs: Optional[PositionCoefficients] = None
        elif isinstance(coefficients, AlgorithmCoefficients):
            self._pos_coeffs = PositionCoefficients.from_global(coefficients)
        else:
            self._pos_coeffs = coefficients

    def _get_coeffs(self, position: Optional[str] = None) -> AlgorithmCoefficients:
        """Return coefficients for *position*, or global defaults."""
        if self._pos_coeffs is None:
            return AlgorithmCoefficients()
        return self._pos_coeffs.get_for_position(position or "")

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

    def _apply_base_criteria(
        self,
        criteria: PlayerProjectionCriteria,
        *,
        position: Optional[str] = None,
    ) -> float:
        """
        Apply base criteria common to all projection types.

        When *position* is supplied and tuned coefficients were provided at
        construction time, the position-specific values are used.
        Otherwise the original hard-coded constants are used.

        Args:
            criteria: Base projection criteria
            position: Player position for coefficient lookup

        Returns:
            Base score from common criteria
        """
        coeffs = self._get_coeffs(position)

        # Start with historical average, scaled by the tunable baseline weight
        base_score = criteria.historical_average_points * coeffs.baseline_weight

        # Adjust for player skill level
        skill_adjustment = (criteria.player_skill_level - 50) * coeffs.skill_multiplier
        base_score += skill_adjustment

        # Adjust for team offense level
        offense_adjustment = (criteria.team_offense_level - 50) * coeffs.offense_multiplier
        base_score += offense_adjustment

        # Adjust for opponent defense level (worse defense = higher score)
        defense_adjustment = (criteria.opponent_defense_level - 50) * coeffs.defense_multiplier
        base_score += defense_adjustment

        # Adjust for touch percentage (more touches = higher projection)
        touch_adjustment = criteria.positional_touch_percentage * coeffs.touch_multiplier
        base_score += touch_adjustment

        # Adjust for recent trends
        trend_adjustment = criteria.recent_trend_score * coeffs.trend_multiplier
        base_score += trend_adjustment

        # Adjust for fantasy points per touch efficiency
        # Skip when no touch data exists (0.0 means no data, not truly 0 efficiency)
        if criteria.fantasy_points_per_touch != 0:
            efficiency_adjustment = (
                criteria.fantasy_points_per_touch - coeffs.efficiency_baseline
            ) * coeffs.efficiency_multiplier
            efficiency_adjustment = max(
                -coeffs.efficiency_cap,
                min(coeffs.efficiency_cap, efficiency_adjustment),
            )
            base_score += efficiency_adjustment

        # Adjust for injury risk (higher risk = lower projection)
        injury_penalty = criteria.injury_risk_score * coeffs.injury_multiplier
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
        base_score = self._apply_base_criteria(criteria, position=player.position)

        # Apply yearly-specific adjustments
        coeffs = self._get_coeffs(player.position)

        # Age deviation impact (peak age = 0 deviation)
        # Post-peak players are penalized; pre-peak players get a small boost
        dev = criteria.age_deviation_from_optimum
        if dev > 0:
            age_penalty = dev * coeffs.age_post_peak_multiplier
        else:
            age_penalty = dev * coeffs.age_pre_peak_multiplier
        base_score += age_penalty

        # Coaching stability impact (stable coaching = better performance)
        coaching_adjustment = (
            (criteria.coaching_stability_score - 50) * coeffs.coaching_multiplier
        )
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
        base_score = self._apply_base_criteria(criteria, position=player.position)

        # Apply weekly-specific adjustments
        coeffs = self._get_coeffs(player.position)

        # Opposing defense rank impact (rank 1 = best defense, 32 = worst)
        # Better opponent defense (lower rank) = lower projection
        defense_rank_adjustment = (
            criteria.opposing_defense_vs_position_rank - 16
        ) * coeffs.defense_rank_multiplier
        base_score += defense_rank_adjustment

        # Offensive momentum impact (hot/cold streak)
        momentum_adjustment = (
            criteria.offensive_momentum_score * coeffs.momentum_multiplier
        )
        base_score += momentum_adjustment

        # Weather impact (bad weather can hurt certain positions more)
        weather_adjustment = (
            criteria.weather_impact_score * coeffs.weather_multiplier
        )
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
