"""Data models for Monte Carlo fantasy football simulation."""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List

import numpy as np


@dataclass
class FantasyPlayerInput:
    """
    Input features for Monte Carlo fantasy simulation.

    All normalized metrics feed the simulation pipeline to estimate
    touches, efficiency, touchdown probability, and context modifiers.

    Attributes:
        player_name: Display name for reporting.
        position: Player position (QB, RB, WR, TE, K, DEF).
        player_skill_level: Composite skill rating (0-1 normalized).
        team_offense_level: Team offensive strength (0-1 normalized).
        opponent_defense_level: Opponent defense strength (0-1 normalized,
            higher = tougher defense).
        positional_touch_percentage: Share of team touches for this
            position (0-1 normalized).
        recent_trend_score: Momentum from recent games (0-1 normalized,
            0.5 = neutral).
        historical_average_points: Average 0.5 PPR fantasy points per game.
        fantasy_points_per_touch: Historical fantasy points per touch/target.
        injury_risk_score: Probability of reduced usage due to injury (0-1).
        opposing_defense_vs_position_rank: Opponent rank vs this position
            (1 = best defense, 32 = worst).
        offensive_momentum_score: Team offensive momentum (0-1 normalized,
            0.5 = neutral).
        weather_impact_score: Weather effect on scoring (0-1 normalized,
            0.5 = neutral / no impact).
    """

    player_name: str = ""
    position: str = ""
    player_skill_level: float = 0.5
    team_offense_level: float = 0.5
    opponent_defense_level: float = 0.5
    positional_touch_percentage: float = 0.5
    recent_trend_score: float = 0.5
    historical_average_points: float = 10.0
    fantasy_points_per_touch: float = 1.0
    injury_risk_score: float = 0.0
    opposing_defense_vs_position_rank: int = 16
    offensive_momentum_score: float = 0.5
    weather_impact_score: float = 0.5

    def __post_init__(self):
        """Validate input ranges."""
        for attr in [
            "player_skill_level",
            "team_offense_level",
            "opponent_defense_level",
            "positional_touch_percentage",
            "recent_trend_score",
            "offensive_momentum_score",
            "weather_impact_score",
        ]:
            val = getattr(self, attr)
            if not 0.0 <= val <= 1.0:
                raise ValueError(f"{attr} must be between 0.0 and 1.0, got {val}")

        if self.injury_risk_score < 0.0 or self.injury_risk_score > 1.0:
            raise ValueError(
                f"injury_risk_score must be between 0.0 and 1.0, "
                f"got {self.injury_risk_score}"
            )

        if not 1 <= self.opposing_defense_vs_position_rank <= 32:
            raise ValueError(
                "opposing_defense_vs_position_rank must be between 1 and 32"
            )

        if self.fantasy_points_per_touch <= 0:
            raise ValueError("fantasy_points_per_touch must be positive")

        if self.historical_average_points < 0:
            raise ValueError("historical_average_points cannot be negative")


@dataclass
class MonteCarloResult:
    """
    Output of a Monte Carlo fantasy simulation.

    Contains summary statistics from the simulation distribution
    plus the raw simulation array for further analysis.

    Attributes:
        player_name: Player display name.
        position: Player position.
        expected_points: Mean of simulated outcomes.
        median_points: 50th percentile of simulated outcomes.
        floor: 10th percentile (conservative estimate).
        ceiling: 90th percentile (optimistic estimate).
        boom_probability: Fraction of simulations exceeding 25 points.
        bust_probability: Fraction of simulations below 8 points.
        std_dev: Standard deviation of the distribution.
        simulations: Raw array of all simulated fantasy scores.
        simulation_count: Number of simulations run.
    """

    player_name: str = ""
    position: str = ""
    expected_points: float = 0.0
    median_points: float = 0.0
    floor: float = 0.0
    ceiling: float = 0.0
    boom_probability: float = 0.0
    bust_probability: float = 0.0
    std_dev: float = 0.0
    simulations: np.ndarray = field(default_factory=lambda: np.array([]))
    simulation_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """Convert to serializable dictionary (excludes raw simulations)."""
        return {
            "player_name": self.player_name,
            "position": self.position,
            "expected_points": round(self.expected_points, 2),
            "median_points": round(self.median_points, 2),
            "floor": round(self.floor, 2),
            "ceiling": round(self.ceiling, 2),
            "boom_probability": round(self.boom_probability, 4),
            "bust_probability": round(self.bust_probability, 4),
            "std_dev": round(self.std_dev, 2),
            "simulation_count": self.simulation_count,
        }

    def to_detailed_dict(self) -> Dict[str, Any]:
        """Convert to dictionary including percentile distribution."""
        result = self.to_dict()
        if len(self.simulations) > 0:
            result["percentiles"] = {
                f"p{p}": round(float(np.percentile(self.simulations, p)), 2)
                for p in [5, 10, 25, 50, 75, 90, 95]
            }
            result["histogram"] = self._build_histogram()
        return result

    def _build_histogram(self, bins: int = 20) -> List[Dict[str, Any]]:
        """Build a histogram of simulation results for visualization."""
        if len(self.simulations) == 0:
            return []
        counts, edges = np.histogram(self.simulations, bins=bins)
        return [
            {
                "bin_start": round(float(edges[i]), 2),
                "bin_end": round(float(edges[i + 1]), 2),
                "count": int(counts[i]),
                "frequency": round(float(counts[i]) / len(self.simulations), 4),
            }
            for i in range(len(counts))
        ]
