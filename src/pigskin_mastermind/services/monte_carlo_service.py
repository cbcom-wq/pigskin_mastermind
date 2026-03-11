"""Monte Carlo fantasy football simulation engine.

Predicts weekly fantasy points (0.5 PPR) by running thousands of
stochastic simulations.  Each iteration independently samples:

1. Touch volume   – Normal distribution around expected touches
2. Efficiency     – Normal distribution around adjusted points-per-touch
3. Touchdowns     – Poisson distribution based on skill/offense
4. Context        – Weather, injury, and momentum multipliers

The aggregated distribution provides expected value, floor/ceiling,
and boom/bust probabilities.
"""

import logging
from typing import Optional

import numpy as np

from pigskin_mastermind.models.monte_carlo import (
    FantasyPlayerInput,
    MonteCarloResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default simulation hyper-parameters
# ---------------------------------------------------------------------------

# Touch estimation
TOUCH_STD_FRACTION = 0.25  # σ as fraction of mean touches
TREND_TOUCH_WEIGHT = 0.2   # how much recent_trend_score scales touches
MOMENTUM_TOUCH_WEIGHT = 0.1  # how much offensive_momentum scales touches

# Efficiency estimation
MATCHUP_WEIGHT = 0.1       # weight of offense-defense gap on efficiency
POSITION_RANK_DIVISOR = 50  # divisor for positional matchup bonus
EFFICIENCY_STD_FRACTION = 0.2  # σ as fraction of mean efficiency

# Touchdown estimation
BASE_TD_LAMBDA = 0.6       # baseline expected TDs per game
SKILL_TD_WEIGHT = 0.3      # skill boost to TD rate
OFFENSE_TD_WEIGHT = 0.2    # team offense boost to TD rate

# Injury simulation
INJURY_USAGE_FACTOR = 0.5  # touch multiplier when injury event fires

# Boom / bust thresholds (0.5 PPR points)
BOOM_THRESHOLD = 25.0
BUST_THRESHOLD = 8.0

# Default number of simulations
DEFAULT_SIMULATIONS = 10_000


class FantasySimulationEngine:
    """Monte Carlo engine for weekly fantasy point projections.

    Parameters
    ----------
    simulations : int
        Number of Monte Carlo iterations (default 10,000).
    seed : int or None
        Random seed for reproducibility.  ``None`` uses entropy.
    """

    def __init__(
        self,
        simulations: int = DEFAULT_SIMULATIONS,
        seed: Optional[int] = None,
        # Touch volume hyper-parameters
        touch_std_fraction: float = TOUCH_STD_FRACTION,
        trend_touch_weight: float = TREND_TOUCH_WEIGHT,
        momentum_touch_weight: float = MOMENTUM_TOUCH_WEIGHT,
        # Efficiency hyper-parameters
        matchup_weight: float = MATCHUP_WEIGHT,
        position_rank_divisor: float = POSITION_RANK_DIVISOR,
        efficiency_std_fraction: float = EFFICIENCY_STD_FRACTION,
        # Touchdown hyper-parameters
        base_td_lambda: float = BASE_TD_LAMBDA,
        skill_td_weight: float = SKILL_TD_WEIGHT,
        offense_td_weight: float = OFFENSE_TD_WEIGHT,
        # Context / injury
        injury_usage_factor: float = INJURY_USAGE_FACTOR,
        # Boom / bust thresholds
        boom_threshold: float = BOOM_THRESHOLD,
        bust_threshold: float = BUST_THRESHOLD,
    ) -> None:
        if simulations < 1:
            raise ValueError("simulations must be >= 1")
        self.simulations = simulations
        self.seed = seed
        # Touch
        self.touch_std_fraction = touch_std_fraction
        self.trend_touch_weight = trend_touch_weight
        self.momentum_touch_weight = momentum_touch_weight
        # Efficiency
        self.matchup_weight = matchup_weight
        self.position_rank_divisor = position_rank_divisor
        self.efficiency_std_fraction = efficiency_std_fraction
        # Touchdowns
        self.base_td_lambda = base_td_lambda
        self.skill_td_weight = skill_td_weight
        self.offense_td_weight = offense_td_weight
        # Context
        self.injury_usage_factor = injury_usage_factor
        # Thresholds
        self.boom_threshold = boom_threshold
        self.bust_threshold = bust_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def simulate(self, player: FantasyPlayerInput) -> MonteCarloResult:
        """Run the full Monte Carlo simulation for a single player.

        Parameters
        ----------
        player : FantasyPlayerInput
            Normalised input features describing the player and context.

        Returns
        -------
        MonteCarloResult
            Summary statistics and raw simulation array.
        """
        rng = np.random.default_rng(self.seed)
        n = self.simulations

        # Step 1 – sample touches
        touches = self._sample_touches(player, rng, n)

        # Step 2 – sample efficiency per touch
        efficiency = self._sample_efficiency(player, rng, n)

        # Step 3 – sample touchdowns
        touchdowns = self._sample_touchdowns(player, rng, n)

        # Step 4 – apply context modifiers
        touches, efficiency = self._apply_context_modifiers(
            player, rng, n, touches, efficiency,
        )

        # Step 5 – compute fantasy scores
        scores = self._compute_scores(touches, efficiency, touchdowns)

        # Step 6 – aggregate
        return self._calculate_summary_stats(player, scores)

    # ------------------------------------------------------------------
    # Step 1 – Touch Volume
    # ------------------------------------------------------------------

    def _estimate_expected_touches(self, player: FantasyPlayerInput) -> float:
        """Derive expected touches from historical production / efficiency.

        base_touches = historical_average_points / fantasy_points_per_touch

        Then scaled by positional share, trend, and momentum.
        """
        base = player.historical_average_points / player.fantasy_points_per_touch

        expected = (
            base
            * player.positional_touch_percentage
            * (1.0 + (player.recent_trend_score - 0.5) * self.trend_touch_weight * 2)
            * (1.0 + (player.offensive_momentum_score - 0.5) * self.momentum_touch_weight * 2)
        )
        return max(0.0, expected)

    def _sample_touches(
        self,
        player: FantasyPlayerInput,
        rng: np.random.Generator,
        n: int,
    ) -> np.ndarray:
        """Sample touch counts from a normal distribution."""
        mean = self._estimate_expected_touches(player)
        std = mean * self.touch_std_fraction if mean > 0 else 0.5
        touches = rng.normal(loc=mean, scale=std, size=n)
        return np.maximum(touches, 0.0)  # clamp to 0

    # ------------------------------------------------------------------
    # Step 2 – Efficiency Per Touch
    # ------------------------------------------------------------------

    def _estimate_efficiency(self, player: FantasyPlayerInput) -> float:
        """Compute adjusted fantasy-points-per-touch with matchup factors.

        matchup_adjustment accounts for the gap between team offense
        and opponent defense.  position_matchup_bonus rewards facing
        a weaker positional defense (higher rank = weaker).
        """
        matchup_adjustment = (
            (player.team_offense_level - player.opponent_defense_level)
            * self.matchup_weight
        )

        # Rank 1 = best D, 32 = worst D.  Bonus is positive when facing
        # a weak positional defense (higher rank number = weaker D).
        position_matchup_bonus = (
            (player.opposing_defense_vs_position_rank - 17) / self.position_rank_divisor
        )

        efficiency = (
            player.fantasy_points_per_touch
            * (1.0 + matchup_adjustment)
            * (1.0 + position_matchup_bonus)
        )
        return max(0.0, efficiency)

    def _sample_efficiency(
        self,
        player: FantasyPlayerInput,
        rng: np.random.Generator,
        n: int,
    ) -> np.ndarray:
        """Sample per-touch efficiency from a normal distribution."""
        mean = self._estimate_efficiency(player)
        std = mean * self.efficiency_std_fraction if mean > 0 else 0.1
        return rng.normal(loc=mean, scale=std, size=n)

    # ------------------------------------------------------------------
    # Step 3 – Touchdown Probability
    # ------------------------------------------------------------------

    def _estimate_td_lambda(self, player: FantasyPlayerInput) -> float:
        """Compute Poisson λ for touchdown sampling.

        Baseline λ is modified upward by player skill and team offense.
        """
        lam = (
            self.base_td_lambda
            * (1.0 + player.player_skill_level * self.skill_td_weight)
            * (1.0 + player.team_offense_level * self.offense_td_weight)
        )
        return max(0.01, lam)  # guard against zero λ

    def _sample_touchdowns(
        self,
        player: FantasyPlayerInput,
        rng: np.random.Generator,
        n: int,
    ) -> np.ndarray:
        """Sample touchdowns from a Poisson distribution."""
        lam = self._estimate_td_lambda(player)
        return rng.poisson(lam=lam, size=n).astype(float)

    # ------------------------------------------------------------------
    # Step 4 – Context Modifiers
    # ------------------------------------------------------------------

    def _apply_context_modifiers(
        self,
        player: FantasyPlayerInput,
        rng: np.random.Generator,
        n: int,
        touches: np.ndarray,
        efficiency: np.ndarray,
    ) -> tuple:
        """Apply weather, injury, and momentum modifiers in-place.

        Weather multiplier:
            Scales efficiency.  A score of 0.5 is neutral (multiplier = 1.0).
            Values < 0.5 penalize; > 0.5 slightly boost.

        Injury risk:
            Each simulation independently rolls whether the player
            suffers reduced usage.  If triggered, touches are halved.

        Momentum:
            A slight efficiency multiplier from offensive momentum.
        """
        # Weather: map 0-1 score so 0.5 → 1.0 multiplier
        weather_mult = 1.0 + (player.weather_impact_score - 0.5) * 0.2
        efficiency = efficiency * weather_mult

        # Injury: stochastic usage reduction per simulation
        injury_mask = rng.random(size=n) < player.injury_risk_score
        touches = np.where(injury_mask, touches * self.injury_usage_factor, touches)

        # Momentum: slight efficiency nudge
        momentum_mult = (
            1.0 + (player.offensive_momentum_score - 0.5) * self.momentum_touch_weight * 2
        )
        efficiency = efficiency * momentum_mult

        return touches, efficiency

    # ------------------------------------------------------------------
    # Step 5 – Score Computation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_scores(
        touches: np.ndarray,
        efficiency: np.ndarray,
        touchdowns: np.ndarray,
    ) -> np.ndarray:
        """Compute 0.5 PPR fantasy scores for each simulation.

        points = touches * efficiency + touchdowns * 6

        The ``efficiency`` value already encodes per-touch fantasy points
        (yards component + reception component), so the product with
        touches yields base fantasy points.  TDs are added at the
        standard 6-point rate.
        """
        scores = touches * efficiency + touchdowns * 6.0
        return np.maximum(scores, 0.0)

    # ------------------------------------------------------------------
    # Step 6 – Summary Statistics
    # ------------------------------------------------------------------

    def _calculate_summary_stats(
        self,
        player: FantasyPlayerInput,
        scores: np.ndarray,
    ) -> MonteCarloResult:
        """Aggregate raw simulation scores into summary metrics."""
        n = len(scores)
        return MonteCarloResult(
            player_name=player.player_name,
            position=player.position,
            expected_points=float(np.mean(scores)),
            median_points=float(np.median(scores)),
            floor=float(np.percentile(scores, 10)),
            ceiling=float(np.percentile(scores, 90)),
            boom_probability=float(np.sum(scores > self.boom_threshold) / n),
            bust_probability=float(np.sum(scores < self.bust_threshold) / n),
            std_dev=float(np.std(scores)),
            simulations=scores,
            simulation_count=n,
        )
