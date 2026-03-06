"""Tunable algorithm coefficients for the projection system.

Each field corresponds to a multiplier or baseline used in the projection
calculation pipeline (``_apply_base_criteria``, ``WeeklyProjectionService``,
and ``YearlyProjectionService``).  By varying these values the automated
algorithm-honing system can explore many parameter combinations and compare
projected scores against actual outcomes.

``PositionCoefficients`` wraps per-position overrides around a global
default so that QB/RB/WR/TE/K/DEF can each carry their own tuned values.
"""

from dataclasses import dataclass, asdict, field, fields
from typing import Any, Dict, List, Optional


# Positions that support individual tuning
TUNABLE_POSITIONS: List[str] = ["QB", "RB", "WR", "TE", "K", "DEF"]


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


# ---------------------------------------------------------------------------
# Position-specific coefficient wrapper
# ---------------------------------------------------------------------------


@dataclass
class PositionCoefficients:
    """Position-specific algorithm coefficients with global fallback.

    Wraps a *default* (global) set of coefficients plus optional per-position
    overrides.  When requesting coefficients for a position the position-specific
    set is returned if present; otherwise the global default is used.

    Creating from a single ``AlgorithmCoefficients`` via :meth:`from_global`
    copies the same values to every position – satisfying the requirement that
    existing (legacy) constants become the starting point for position-level
    tuning.
    """

    default: AlgorithmCoefficients = field(default_factory=AlgorithmCoefficients)
    by_position: Dict[str, AlgorithmCoefficients] = field(default_factory=dict)

    # ── lookup ────────────────────────────────────────────────────────

    def get_for_position(self, position: str) -> AlgorithmCoefficients:
        """Return coefficients for *position*, falling back to default."""
        return self.by_position.get(position, self.default)

    @property
    def positions(self) -> List[str]:
        """Positions that have explicit overrides."""
        return list(self.by_position.keys())

    # ── factories ─────────────────────────────────────────────────────

    @classmethod
    def from_global(
        cls, coeffs: Optional[AlgorithmCoefficients] = None,
    ) -> "PositionCoefficients":
        """Create from a single set, copying to every tunable position.

        This is the migration path: old global coefficients are replicated to
        every position so per-position tuning can diverge from there.
        """
        if coeffs is None:
            coeffs = AlgorithmCoefficients()
        by_pos = {
            pos: AlgorithmCoefficients.from_dict(coeffs.to_dict())
            for pos in TUNABLE_POSITIONS
        }
        return cls(
            default=AlgorithmCoefficients.from_dict(coeffs.to_dict()),
            by_position=by_pos,
        )

    # ── serialisation ─────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Serialize: ``{"default": {…}, "QB": {…}, …}``."""
        result: Dict[str, Any] = {"default": self.default.to_dict()}
        for pos, coeffs in sorted(self.by_position.items()):
            result[pos] = coeffs.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PositionCoefficients":
        """Deserialize, handling both legacy and position-keyed formats.

        *Legacy*: a flat ``{key: float}`` dict ➜ treated as global defaults
        and copied to every position via :meth:`from_global`.

        *New*: a nested dict with ``"default"`` and position keys.
        """
        if not data:
            return cls.from_global()

        # Legacy flat dict (no nested dicts) → treat as global coefficients
        if not any(isinstance(v, dict) for v in data.values()):
            return cls.from_global(AlgorithmCoefficients.from_dict(data))

        default_data = data.get("default", {})
        default = (
            AlgorithmCoefficients.from_dict(default_data)
            if default_data
            else AlgorithmCoefficients()
        )

        by_pos: Dict[str, AlgorithmCoefficients] = {}
        for key, val in data.items():
            if key != "default" and isinstance(val, dict):
                by_pos[key] = AlgorithmCoefficients.from_dict(val)

        return cls(default=default, by_position=by_pos)
