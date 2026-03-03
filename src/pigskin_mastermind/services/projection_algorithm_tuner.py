"""Automated projection-algorithm honing system.

The ``ProjectionAlgorithmTuner`` explores many coefficient variations,
evaluates each against historical game-log data (actual fantasy points),
and produces an analysis ranking the best-performing parameter sets.

Results are persisted as JSON so that knowledge accumulates across runs.
"""

from __future__ import annotations

import itertools
import json
import math
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.algorithm_coefficients import AlgorithmCoefficients
from pigskin_mastermind.models.database import (
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
    DBNFLTeamStats,
)
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.projection_criteria import (
    WeeklyProjectionCriteria,
)
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder,
)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class VariationResult:
    """Accuracy metrics for a single coefficient variation."""

    coefficients: Dict[str, float]
    mae: float  # Mean Absolute Error
    rmse: float  # Root Mean Square Error
    sample_count: int  # number of player-week samples evaluated
    per_position_mae: Dict[str, float] = field(default_factory=dict)


@dataclass
class TuningRunResult:
    """Full output of one tuning run."""

    run_id: str
    timestamp: str
    year: int
    weeks: List[int]
    player_count: int
    sample_count: int
    variations_tested: int
    best: VariationResult
    top_variations: List[VariationResult]
    default_result: VariationResult

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TuningRunResult":
        data["best"] = VariationResult(**data["best"])
        data["top_variations"] = [VariationResult(**v) for v in data["top_variations"]]
        data["default_result"] = VariationResult(**data["default_result"])
        return cls(**data)


# ---------------------------------------------------------------------------
# Projection calculation with custom coefficients
# ---------------------------------------------------------------------------


def _calculate_weekly_projection(
    criteria: WeeklyProjectionCriteria,
    coeffs: AlgorithmCoefficients,
) -> float:
    """Re-implement the projection formula using the supplied coefficients.

    This mirrors ``ProjectionService._apply_base_criteria`` plus
    ``WeeklyProjectionService.calculate_projection`` but substitutes
    hard-coded constants with the values from *coeffs*.
    """
    base_score = criteria.historical_average_points

    base_score += (criteria.player_skill_level - 50) * coeffs.skill_multiplier
    base_score += (criteria.team_offense_level - 50) * coeffs.offense_multiplier
    base_score += (criteria.opponent_defense_level - 50) * coeffs.defense_multiplier
    base_score += criteria.positional_touch_percentage * coeffs.touch_multiplier
    base_score += criteria.recent_trend_score * coeffs.trend_multiplier

    if criteria.fantasy_points_per_touch != 0:
        eff = (criteria.fantasy_points_per_touch - coeffs.efficiency_baseline) * coeffs.efficiency_multiplier
        eff = max(-coeffs.efficiency_cap, min(coeffs.efficiency_cap, eff))
        base_score += eff

    base_score += criteria.injury_risk_score * coeffs.injury_multiplier

    # Weekly-specific adjustments
    base_score += (criteria.opposing_defense_vs_position_rank - 16) * coeffs.defense_rank_multiplier
    base_score += criteria.offensive_momentum_score * coeffs.momentum_multiplier
    base_score += criteria.weather_impact_score * coeffs.weather_multiplier

    return max(0.0, base_score)


# ---------------------------------------------------------------------------
# Variation generators
# ---------------------------------------------------------------------------

# Default scaling factors: each coefficient will be tested at
# *default_value × factor* for every factor in this list.
_DEFAULT_SCALE_FACTORS: List[float] = [0.5, 0.75, 1.0, 1.25, 1.5]

# Coefficients that are explored during tuning (weekly-focused).
_TUNABLE_WEEKLY_FIELDS: List[str] = [
    "skill_multiplier",
    "offense_multiplier",
    "defense_multiplier",
    "touch_multiplier",
    "trend_multiplier",
    "injury_multiplier",
    "defense_rank_multiplier",
    "momentum_multiplier",
    "weather_multiplier",
]


def generate_variations(
    base: Optional[AlgorithmCoefficients] = None,
    fields_to_vary: Optional[List[str]] = None,
    scale_factors: Optional[List[float]] = None,
    max_combinations: int = 500,
) -> List[AlgorithmCoefficients]:
    """Create coefficient variations by scaling selected fields.

    For each field in *fields_to_vary* the generator multiplies the
    default value by each entry in *scale_factors*.  One-at-a-time
    variations are produced first; if the total is below
    *max_combinations* pairwise combinations are added as well.

    Args:
        base: Starting coefficients (defaults used if ``None``).
        fields_to_vary: Which coefficient names to vary.
        scale_factors: Multipliers applied to each field's default value.
        max_combinations: Hard cap on total variations returned.

    Returns:
        List of ``AlgorithmCoefficients`` instances to evaluate.
    """
    if base is None:
        base = AlgorithmCoefficients()
    if fields_to_vary is None:
        fields_to_vary = list(_TUNABLE_WEEKLY_FIELDS)
    if scale_factors is None:
        scale_factors = list(_DEFAULT_SCALE_FACTORS)

    base_dict = base.to_dict()
    seen: set = set()
    variations: List[AlgorithmCoefficients] = []

    def _add(d: Dict[str, float]) -> None:
        key = tuple(sorted(d.items()))
        if key not in seen and len(variations) < max_combinations:
            seen.add(key)
            variations.append(AlgorithmCoefficients.from_dict(d))

    # Always include the base
    _add(base_dict)

    # --- One-at-a-time variations ---
    for fname in fields_to_vary:
        default_val = base_dict[fname]
        for sf in scale_factors:
            variant = dict(base_dict)
            variant[fname] = round(default_val * sf, 6)
            _add(variant)

    # --- Pairwise combinations (if budget allows) ---
    if len(variations) < max_combinations:
        for f1, f2 in itertools.combinations(fields_to_vary, 2):
            for sf1, sf2 in itertools.product(scale_factors, repeat=2):
                if sf1 == 1.0 and sf2 == 1.0:
                    continue  # skip no-change
                variant = dict(base_dict)
                variant[f1] = round(base_dict[f1] * sf1, 6)
                variant[f2] = round(base_dict[f2] * sf2, 6)
                _add(variant)
                if len(variations) >= max_combinations:
                    break
            if len(variations) >= max_combinations:
                break

    return variations


# ---------------------------------------------------------------------------
# Core tuner
# ---------------------------------------------------------------------------


class ProjectionAlgorithmTuner:
    """Automated algorithm-honing system.

    Given a database session with populated player game logs, the tuner:

    1. Builds weekly projection criteria for each player/week using the
       existing ``ProjectionCriteriaBuilder``.
    2. For every coefficient variation, calculates projected points and
       compares them to the actual ``fantasy_points`` in the game log.
    3. Ranks variations by accuracy (lowest MAE/RMSE wins).
    4. Persists results as JSON so knowledge accumulates across runs.
    """

    def __init__(self, db: Session, results_dir: Optional[str] = None):
        self.db = db
        self.builder = ProjectionCriteriaBuilder(db)
        self.results_dir = results_dir or os.path.join(
            os.path.expanduser("~"), ".pigskin_mastermind", "tuning_results"
        )

    # ----- public API --------------------------------------------------------

    def run(
        self,
        year: int,
        weeks: Optional[List[int]] = None,
        player_ids: Optional[List[int]] = None,
        positions: Optional[List[str]] = None,
        variations: Optional[List[AlgorithmCoefficients]] = None,
        max_variations: int = 500,
        top_n: int = 10,
    ) -> TuningRunResult:
        """Execute a full tuning run.

        Args:
            year: Season year whose game-log data is used for evaluation.
            weeks: Specific weeks to evaluate (default: all available).
            player_ids: Restrict to these DB player IDs (default: all with
                game-log data for the given *year*).
            positions: Restrict to these positions (e.g. ``['QB', 'RB']``).
            variations: Pre-built coefficient variations.  If ``None``,
                :func:`generate_variations` is called automatically.
            max_variations: Cap passed to :func:`generate_variations`.
            top_n: Number of best variations to include in the report.

        Returns:
            ``TuningRunResult`` with accuracy metrics and the best
            coefficient sets found.
        """
        # 1. Gather evaluation samples: (criteria, actual_points, position)
        samples = self._gather_samples(year, weeks, player_ids, positions)
        if not samples:
            raise ValueError(
                "No valid evaluation samples found for the given filters."
            )

        # 2. Build or receive variations
        if variations is None:
            variations = generate_variations(max_combinations=max_variations)

        # 3. Evaluate each variation
        results: List[VariationResult] = []
        for coeffs in variations:
            result = self._evaluate(coeffs, samples)
            results.append(result)

        # 4. Rank by MAE (lower is better)
        results.sort(key=lambda r: r.mae)

        # 5. Locate the default-coefficient result for comparison
        default_coeffs = AlgorithmCoefficients()
        default_result = self._evaluate(default_coeffs, samples)

        unique_players = {s[2] for s in samples}  # player_id stored at idx 2

        run_result = TuningRunResult(
            run_id=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            year=year,
            weeks=sorted({s[3] for s in samples}),  # week stored at idx 3
            player_count=len(unique_players),
            sample_count=len(samples),
            variations_tested=len(variations),
            best=results[0],
            top_variations=results[:top_n],
            default_result=default_result,
        )

        return run_result

    def save_result(self, result: TuningRunResult) -> str:
        """Persist a tuning-run result as JSON.

        Returns the path to the saved file.
        """
        os.makedirs(self.results_dir, exist_ok=True)
        path = os.path.join(self.results_dir, f"tuning_{result.run_id}.json")
        with open(path, "w") as fh:
            json.dump(result.to_dict(), fh, indent=2)
        return path

    def load_results(self) -> List[TuningRunResult]:
        """Load all previously saved tuning results."""
        loaded: List[TuningRunResult] = []
        if not os.path.isdir(self.results_dir):
            return loaded
        for fname in sorted(os.listdir(self.results_dir)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self.results_dir, fname)
            with open(path) as fh:
                data = json.load(fh)
            loaded.append(TuningRunResult.from_dict(data))
        return loaded

    def generate_analysis_report(self, result: TuningRunResult) -> Dict[str, Any]:
        """Build a human-readable analysis from a tuning-run result.

        Returns a dictionary containing summary text, improvement
        metrics, and per-position breakdowns.
        """
        default = result.default_result
        best = result.best

        mae_improvement = default.mae - best.mae
        rmse_improvement = default.rmse - best.rmse
        mae_pct = (mae_improvement / default.mae * 100) if default.mae else 0.0

        coefficient_changes: Dict[str, Dict[str, float]] = {}
        default_coeffs = AlgorithmCoefficients().to_dict()
        for key, new_val in best.coefficients.items():
            old_val = default_coeffs.get(key, 0.0)
            if abs(new_val - old_val) > 1e-9:
                coefficient_changes[key] = {
                    "default": round(old_val, 6),
                    "tuned": round(new_val, 6),
                    "change_pct": round(
                        ((new_val - old_val) / abs(old_val) * 100) if old_val else 0.0,
                        2,
                    ),
                }

        return {
            "summary": {
                "year": result.year,
                "weeks_evaluated": result.weeks,
                "players_evaluated": result.player_count,
                "total_samples": result.sample_count,
                "variations_tested": result.variations_tested,
            },
            "default_accuracy": {
                "mae": round(default.mae, 4),
                "rmse": round(default.rmse, 4),
            },
            "best_accuracy": {
                "mae": round(best.mae, 4),
                "rmse": round(best.rmse, 4),
            },
            "improvement": {
                "mae_reduction": round(mae_improvement, 4),
                "rmse_reduction": round(rmse_improvement, 4),
                "mae_improvement_pct": round(mae_pct, 2),
            },
            "best_coefficients": best.coefficients,
            "coefficient_changes": coefficient_changes,
            "per_position_mae": best.per_position_mae,
            "top_variations_summary": [
                {
                    "rank": i + 1,
                    "mae": round(v.mae, 4),
                    "rmse": round(v.rmse, 4),
                    "sample_count": v.sample_count,
                }
                for i, v in enumerate(result.top_variations)
            ],
        }

    # ----- internals ---------------------------------------------------------

    def _gather_samples(
        self,
        year: int,
        weeks: Optional[List[int]],
        player_ids: Optional[List[int]],
        positions: Optional[List[str]],
    ) -> List[Tuple[WeeklyProjectionCriteria, float, int, int, str]]:
        """Return list of (criteria, actual_points, player_id, week, position).

        Only game-log entries where the player was active and has a
        corresponding season-stats record are included.
        """
        query = self.db.query(DBPlayerGameLog).filter_by(year=year)
        if weeks:
            query = query.filter(DBPlayerGameLog.week.in_(weeks))
        if player_ids:
            query = query.filter(DBPlayerGameLog.player_id.in_(player_ids))

        logs = query.all()

        # Optionally filter by position
        position_set = set(positions) if positions else None

        samples: List[Tuple[WeeklyProjectionCriteria, float, int, int, str]] = []
        for log in logs:
            player = self.db.get(DBPlayer, log.player_id)
            if not player:
                continue
            if position_set and player.position not in position_set:
                continue
            try:
                criteria = self.builder.build_weekly_criteria(
                    log.player_id, log.week, year
                )
            except (ValueError, Exception):
                continue
            samples.append(
                (criteria, log.fantasy_points or 0.0, log.player_id, log.week, player.position)
            )
        return samples

    @staticmethod
    def _evaluate(
        coeffs: AlgorithmCoefficients,
        samples: List[Tuple[WeeklyProjectionCriteria, float, int, int, str]],
    ) -> VariationResult:
        """Compute accuracy metrics for a set of coefficients."""
        errors: List[float] = []
        position_errors: Dict[str, List[float]] = {}

        for criteria, actual, _pid, _week, pos in samples:
            projected = _calculate_weekly_projection(criteria, coeffs)
            err = abs(projected - actual)
            errors.append(err)
            position_errors.setdefault(pos, []).append(err)

        n = len(errors)
        mae = sum(errors) / n if n else 0.0
        rmse = math.sqrt(sum(e * e for e in errors) / n) if n else 0.0

        per_pos_mae: Dict[str, float] = {}
        for pos, errs in position_errors.items():
            per_pos_mae[pos] = round(sum(errs) / len(errs), 4) if errs else 0.0

        return VariationResult(
            coefficients=coeffs.to_dict(),
            mae=round(mae, 4),
            rmse=round(rmse, 4),
            sample_count=n,
            per_position_mae=per_pos_mae,
        )
