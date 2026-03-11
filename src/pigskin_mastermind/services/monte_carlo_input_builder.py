"""Build FantasyPlayerInput from existing projection criteria.

Translates ``WeeklyProjectionCriteria`` (0–100 / raw scale) into the
normalised 0–1 inputs consumed by the Monte Carlo simulation engine.
This lets the Monte Carlo module reuse the data-gathering pipeline in
``ProjectionCriteriaBuilder`` without duplicating DB queries.
"""

import logging
from typing import Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.monte_carlo import FantasyPlayerInput
from pigskin_mastermind.models.projection_criteria import WeeklyProjectionCriteria
from pigskin_mastermind.models.database import DBPlayer
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder,
)

logger = logging.getLogger(__name__)


class MonteCarloInputBuilder:
    """Converts existing projection criteria into Monte Carlo inputs.

    Two usage patterns are supported:

    1. **From criteria** – call ``from_weekly_criteria()`` when you already
       have a ``WeeklyProjectionCriteria`` instance (e.g. from the tuner).

    2. **From database** – call ``build_for_player()`` to auto-derive
       criteria via ``ProjectionCriteriaBuilder`` and convert in one step.
    """

    def __init__(self, db: Optional[Session] = None) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def from_weekly_criteria(
        criteria: WeeklyProjectionCriteria,
        *,
        player_name: str = "",
        position: str = "",
    ) -> FantasyPlayerInput:
        """Convert a ``WeeklyProjectionCriteria`` to ``FantasyPlayerInput``.

        The projection criteria use 0–100 scales (and –100 to 100 for
        trend/momentum/weather).  The Monte Carlo model expects 0–1.
        This method handles the normalisation.

        Parameters
        ----------
        criteria : WeeklyProjectionCriteria
            Criteria built by ``ProjectionCriteriaBuilder``.
        player_name : str
            Display name (optional, for labelling results).
        position : str
            Player position code (QB, RB, WR, TE, …).

        Returns
        -------
        FantasyPlayerInput
        """
        return FantasyPlayerInput(
            player_name=player_name,
            position=position,
            # 0–100 → 0–1
            player_skill_level=_norm_0_100(criteria.player_skill_level),
            team_offense_level=_norm_0_100(criteria.team_offense_level),
            opponent_defense_level=_norm_0_100(criteria.opponent_defense_level),
            positional_touch_percentage=_norm_0_100(
                criteria.positional_touch_percentage
            ),
            # –100 to 100 → 0–1 (midpoint 0 → 0.5)
            recent_trend_score=_norm_neg100_100(criteria.recent_trend_score),
            # Raw values passed through unchanged
            historical_average_points=max(
                0.1, criteria.historical_average_points
            ),
            fantasy_points_per_touch=max(0.01, criteria.fantasy_points_per_touch),
            # 0–100 → 0–1
            injury_risk_score=_norm_0_100(criteria.injury_risk_score),
            # 1–32 kept as-is
            opposing_defense_vs_position_rank=(
                criteria.opposing_defense_vs_position_rank
            ),
            # –100 to 100 → 0–1
            offensive_momentum_score=_norm_neg100_100(
                criteria.offensive_momentum_score
            ),
            weather_impact_score=_norm_neg100_100(criteria.weather_impact_score),
        )

    def build_for_player(
        self,
        player_id: int,
        year: int,
        week: int,
        *,
        opponent_team: Optional[str] = None,
        overrides: Optional[dict] = None,
    ) -> FantasyPlayerInput:
        """Build Monte Carlo input directly from DB data.

        Uses ``ProjectionCriteriaBuilder`` to gather stats, then converts
        into normalised ``FantasyPlayerInput``.

        Parameters
        ----------
        player_id : int
            Database primary key for the player.
        year : int
            NFL season year.
        week : int
            NFL week number.
        opponent_team : str or None
            Opponent abbreviation (auto-derived when possible).
        overrides : dict or None
            Manual overrides forwarded to the criteria builder.

        Returns
        -------
        FantasyPlayerInput
        """
        if self.db is None:
            raise RuntimeError(
                "MonteCarloInputBuilder requires a DB session for "
                "build_for_player(); pass db= to the constructor."
            )

        builder = ProjectionCriteriaBuilder(self.db)
        criteria = builder.build_weekly_criteria(
            player_id=player_id,
            year=year,
            week=week,
            opponent_team=opponent_team,
            overrides=overrides,
        )

        # Look up display info
        db_player = self.db.query(DBPlayer).filter_by(id=player_id).first()
        name = db_player.name if db_player else ""
        pos = db_player.position if db_player else ""

        return self.from_weekly_criteria(
            criteria, player_name=name, position=pos,
        )


# ------------------------------------------------------------------
# Normalisation helpers
# ------------------------------------------------------------------

def _norm_0_100(value: float) -> float:
    """Normalise a 0–100 value to 0–1, clamped."""
    return max(0.0, min(1.0, value / 100.0))


def _norm_neg100_100(value: float) -> float:
    """Normalise a –100 to 100 value to 0–1 (midpoint → 0.5), clamped."""
    return max(0.0, min(1.0, (value + 100.0) / 200.0))
