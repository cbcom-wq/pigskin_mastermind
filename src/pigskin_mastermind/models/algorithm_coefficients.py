"""Tunable algorithm coefficients for the projection system.

Each field corresponds to a multiplier or baseline used in the projection
calculation pipeline (``_apply_base_criteria``, ``WeeklyProjectionService``,
and ``YearlyProjectionService``).  By varying these values the automated
algorithm-honing system can explore many parameter combinations and compare
projected scores against actual outcomes.
"""

from dataclasses import dataclass, asdict, fields
from typing import Dict, Any


@dataclass
class AlgorithmCoefficients:
    """Complete set of tunable projection-algorithm parameters.

    Base criteria (shared by weekly and yearly):
        skill_multiplier: Weight for player skill level adjustment.
        offense_multiplier: Weight for team offense level adjustment.
        defense_multiplier: Weight for opponent defense level adjustment.
        touch_multiplier: Weight for positional touch percentage.
        trend_multiplier: Weight for recent performance trend.
        efficiency_multiplier: Scaling factor for per-touch efficiency.
        efficiency_baseline: Centre-point subtracted before scaling efficiency.
        efficiency_cap: Max absolute value for the efficiency adjustment.
        injury_multiplier: Weight for injury risk penalty (negative value).

    Weekly-specific:
        defense_rank_multiplier: Weight for opposing defense positional rank.
        momentum_multiplier: Weight for offensive momentum score.
        weather_multiplier: Weight for weather impact score.

    Yearly-specific:
        age_post_peak_multiplier: Penalty per year past peak age.
        age_pre_peak_multiplier: Adjustment per year before peak age.
        coaching_multiplier: Weight for coaching stability score.
    """

    # ── Base criteria ─────────────────────────────────────────────────
    skill_multiplier: float = 0.1
    offense_multiplier: float = 0.06
    defense_multiplier: float = 0.04
    touch_multiplier: float = 0.05
    trend_multiplier: float = 0.03
    efficiency_multiplier: float = 2.0
    efficiency_baseline: float = 0.5
    efficiency_cap: float = 5.0
    injury_multiplier: float = -0.05

    # ── Weekly-specific ───────────────────────────────────────────────
    defense_rank_multiplier: float = 0.15
    momentum_multiplier: float = 0.02
    weather_multiplier: float = 0.015

    # ── Yearly-specific ───────────────────────────────────────────────
    age_post_peak_multiplier: float = -0.5
    age_pre_peak_multiplier: float = -0.1
    coaching_multiplier: float = 0.04

    # ── Serialisation helpers ─────────────────────────────────────────

    def to_dict(self) -> Dict[str, float]:
        """Return a plain dictionary of all coefficient values."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AlgorithmCoefficients":
        """Construct from a dictionary, ignoring unknown keys."""
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in valid})
