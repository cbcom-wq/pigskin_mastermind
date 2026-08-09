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
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.algorithm_coefficients import (
    AlgorithmCoefficients,
    PositionCoefficients,
)
from pigskin_mastermind.models.database import (
    DBPlayer,
    DBPlayerGameLog,
)
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.projection_criteria import (
    WeeklyProjectionCriteria,
)
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder,
)
from pigskin_mastermind.services.projection_service import (
    WeeklyProjectionService,
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
class SampleComparisonRow:
    """Projected vs actual values for one player-week sample."""

    player_id: int
    player_name: str
    week: int
    position: str
    actual_points: float
    default_projected_points: float
    tuned_projected_points: float
    default_error: float
    tuned_error: float


@dataclass
class PositionTuningResult:
    """Tuning result for a single position within a per-position run."""

    position: str
    best: VariationResult
    top_variations: List[VariationResult]
    default_result: VariationResult
    sample_count: int
    variations_tested: int


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
    run_filters: Dict[str, Any] = field(default_factory=dict)
    sample_comparisons: List[SampleComparisonRow] = field(default_factory=list)
    # Per-position tuning results (populated by run_per_position)
    per_position_results: Dict[str, Any] = field(default_factory=dict)
    combined_coefficients: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TuningRunResult":
        payload = dict(data)
        payload["best"] = VariationResult(**payload["best"])
        payload["top_variations"] = [
            VariationResult(**v) for v in payload["top_variations"]
        ]
        payload["default_result"] = VariationResult(**payload["default_result"])
        payload["run_filters"] = payload.get("run_filters", {})
        payload["sample_comparisons"] = [
            SampleComparisonRow(**row) for row in payload.get("sample_comparisons", [])
        ]
        # Restore per_position_results
        raw_ppr = payload.get("per_position_results", {})
        restored_ppr: Dict[str, Any] = {}
        for pos, ppr_data in raw_ppr.items():
            if isinstance(ppr_data, dict) and "best" in ppr_data:
                restored_ppr[pos] = PositionTuningResult(
                    position=ppr_data["position"],
                    best=VariationResult(**ppr_data["best"]),
                    top_variations=[
                        VariationResult(**v) for v in ppr_data.get("top_variations", [])
                    ],
                    default_result=VariationResult(**ppr_data["default_result"]),
                    sample_count=ppr_data["sample_count"],
                    variations_tested=ppr_data["variations_tested"],
                )
            else:
                restored_ppr[pos] = ppr_data
        payload["per_position_results"] = restored_ppr
        payload["combined_coefficients"] = payload.get("combined_coefficients", {})
        return cls(**payload)


# ---------------------------------------------------------------------------
# Projection calculation with custom coefficients
# ---------------------------------------------------------------------------


# A stand-in player: WeeklyProjectionService only reads ``.position`` from it,
# and the coefficients handed to that service are already resolved for the
# position being evaluated, so the value here never affects the result.
_SCORING_PLAYER = Player(
    player_id="__tuner__",
    name="__tuner__",
    position="WR",
    team="FA",
)


def _calculate_weekly_projection(
    criteria: WeeklyProjectionCriteria,
    coeffs: AlgorithmCoefficients,
) -> float:
    """Score *criteria* with *coeffs* using the production formula.

    This deliberately delegates rather than re-implementing. A private copy of
    the formula lived here and silently fell out of step with
    ``WeeklyProjectionService`` — meaning the sweep optimised coefficients
    against arithmetic the app never actually ran.
    """
    return WeeklyProjectionService(coefficients=coeffs).calculate_projection(
        _SCORING_PLAYER,
        criteria,
    )


# ---------------------------------------------------------------------------
# Variation generators
# ---------------------------------------------------------------------------

# Default scaling factors: each coefficient will be tested at
# *default_value × factor* for every factor in this list.
_DEFAULT_SCALE_FACTORS: List[float] = [0.5, 0.75, 1.0, 1.25, 1.5]

# Coefficients that are explored during tuning (weekly-focused).
_TUNABLE_WEEKLY_FIELDS: List[str] = [
    "baseline_weight",
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
            raise ValueError("No valid evaluation samples found for the given filters.")

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

        best_result = results[0]
        best_coeffs = AlgorithmCoefficients.from_dict(best_result.coefficients)
        unique_players = {s[2] for s in samples}  # player_id stored at idx 2
        simulated_weeks = sorted({s[3] for s in samples})  # week stored at idx 3
        simulated_positions = sorted(
            {s[4] for s in samples}
        )  # position stored at idx 4
        sample_comparisons = self._build_sample_comparisons(
            samples,
            default_coeffs,
            best_coeffs,
        )
        run_filters = {
            "weeks": sorted(set(weeks)) if weeks else None,
            "player_ids": sorted(set(player_ids)) if player_ids else None,
            "positions": sorted(set(positions)) if positions else None,
            "simulated_weeks": simulated_weeks,
            "simulated_player_ids": sorted(unique_players),
            "simulated_positions": simulated_positions,
        }

        run_result = TuningRunResult(
            run_id=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            year=year,
            weeks=simulated_weeks,
            player_count=len(unique_players),
            sample_count=len(samples),
            variations_tested=len(variations),
            best=best_result,
            top_variations=results[:top_n],
            default_result=default_result,
            run_filters=run_filters,
            sample_comparisons=sample_comparisons,
        )

        return run_result

    def run_per_position(
        self,
        year: int,
        weeks: Optional[List[int]] = None,
        player_ids: Optional[List[int]] = None,
        positions: Optional[List[str]] = None,
        variations: Optional[List[AlgorithmCoefficients]] = None,
        max_variations: int = 500,
        top_n: int = 10,
    ) -> TuningRunResult:
        """Execute a tuning run that optimises coefficients per position.

        Instead of finding a single best coefficient set, this method
        tunes independently for each position, then combines the
        per-position bests into a :class:`PositionCoefficients` structure.

        Samples are gathered once and partitioned by position.  Each
        position group is tuned with its own variation search, then the
        combined result is evaluated across all samples using position-
        specific coefficients.

        Args:
            year: Season year whose game-log data is used for evaluation.
            weeks: Specific weeks to evaluate (default: all available).
            player_ids: Restrict to these DB player IDs.
            positions: Restrict to these positions (e.g. ``['QB', 'RB']``).
            variations: Pre-built coefficient variations.  If ``None``,
                :func:`generate_variations` is called automatically.
            max_variations: Cap passed to :func:`generate_variations`.
            top_n: Number of best variations per position.

        Returns:
            ``TuningRunResult`` with per-position tuning detail in
            ``per_position_results`` and the combined coefficient map in
            ``combined_coefficients``.
        """
        # 1. Gather all samples
        all_samples = self._gather_samples(year, weeks, player_ids, positions)
        if not all_samples:
            raise ValueError("No valid evaluation samples found for the given filters.")

        # 2. Partition samples by position
        samples_by_pos: Dict[str, List] = {}
        for sample in all_samples:
            pos = sample[4]  # position is at index 4
            samples_by_pos.setdefault(pos, []).append(sample)

        # 3. Build or receive variations
        if variations is None:
            variations = generate_variations(max_combinations=max_variations)

        # 4. Tune each position independently
        default_coeffs = AlgorithmCoefficients()
        per_position_results: Dict[str, PositionTuningResult] = {}
        best_per_position: Dict[str, AlgorithmCoefficients] = {}

        for pos, pos_samples in sorted(samples_by_pos.items()):
            results: List[VariationResult] = []
            for coeffs in variations:
                result = self._evaluate(coeffs, pos_samples)
                results.append(result)

            results.sort(key=lambda r: r.mae)
            pos_default = self._evaluate(default_coeffs, pos_samples)
            pos_best = results[0]

            per_position_results[pos] = PositionTuningResult(
                position=pos,
                best=pos_best,
                top_variations=results[:top_n],
                default_result=pos_default,
                sample_count=len(pos_samples),
                variations_tested=len(variations),
            )
            best_per_position[pos] = AlgorithmCoefficients.from_dict(
                pos_best.coefficients
            )

        # 5. Build PositionCoefficients from per-position bests
        pos_coeffs = PositionCoefficients(
            default=default_coeffs,
            by_position=best_per_position,
        )
        combined_dict = pos_coeffs.to_dict()

        # 6. Evaluate the combined per-position set across ALL samples
        combined_result = self._evaluate_with_position_coefficients(
            pos_coeffs, all_samples
        )
        default_all = self._evaluate(default_coeffs, all_samples)

        # 7. Build sample comparisons using per-position coefficients
        sample_comparisons = self._build_sample_comparisons_per_position(
            all_samples, default_coeffs, pos_coeffs
        )

        unique_players = {s[2] for s in all_samples}
        simulated_weeks = sorted({s[3] for s in all_samples})
        simulated_positions = sorted({s[4] for s in all_samples})

        run_filters = {
            "weeks": sorted(set(weeks)) if weeks else None,
            "player_ids": sorted(set(player_ids)) if player_ids else None,
            "positions": sorted(set(positions)) if positions else None,
            "simulated_weeks": simulated_weeks,
            "simulated_player_ids": sorted(unique_players),
            "simulated_positions": simulated_positions,
            "per_position": True,
        }

        return TuningRunResult(
            run_id=datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
            timestamp=datetime.now(timezone.utc).isoformat(),
            year=year,
            weeks=simulated_weeks,
            player_count=len(unique_players),
            sample_count=len(all_samples),
            variations_tested=len(variations),
            best=combined_result,
            top_variations=[combined_result],
            default_result=default_all,
            run_filters=run_filters,
            sample_comparisons=sample_comparisons,
            per_position_results={
                pos: asdict(r) for pos, r in per_position_results.items()
            },
            combined_coefficients=combined_dict,
        )

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
                        (
                            ((new_val - old_val) / abs(old_val) * 100)
                            if abs(old_val) > 1e-9
                            else 0.0
                        ),
                        2,
                    ),
                }

        # Per-position coefficient details (from run_per_position)
        per_position_detail: Dict[str, Any] = {}
        for pos, ppr in result.per_position_results.items():
            if isinstance(ppr, PositionTuningResult):
                pos_best = ppr.best
                pos_default = ppr.default_result
            elif isinstance(ppr, dict):
                pos_best = (
                    VariationResult(**ppr["best"])
                    if isinstance(ppr["best"], dict)
                    else ppr["best"]
                )
                pos_default = (
                    VariationResult(**ppr["default_result"])
                    if isinstance(ppr["default_result"], dict)
                    else ppr["default_result"]
                )
            else:
                continue
            pos_mae_imp = pos_default.mae - pos_best.mae
            pos_mae_pct = (
                (pos_mae_imp / pos_default.mae * 100) if pos_default.mae else 0.0
            )
            # Build per-position coefficient changes
            pos_changes: Dict[str, Dict[str, float]] = {}
            for key, new_val in pos_best.coefficients.items():
                old_val = default_coeffs.get(key, 0.0)
                if abs(new_val - old_val) > 1e-9:
                    pos_changes[key] = {
                        "default": round(old_val, 6),
                        "tuned": round(new_val, 6),
                        "change_pct": round(
                            (
                                ((new_val - old_val) / abs(old_val) * 100)
                                if abs(old_val) > 1e-9
                                else 0.0
                            ),
                            2,
                        ),
                    }
            per_position_detail[pos] = {
                "default_mae": round(pos_default.mae, 4),
                "tuned_mae": round(pos_best.mae, 4),
                "mae_reduction": round(pos_mae_imp, 4),
                "mae_improvement_pct": round(pos_mae_pct, 2),
                "sample_count": pos_best.sample_count,
                "best_coefficients": pos_best.coefficients,
                "coefficient_changes": pos_changes,
            }

        report: Dict[str, Any] = {
            "summary": {
                "year": result.year,
                "weeks_evaluated": result.weeks,
                "players_evaluated": result.player_count,
                "total_samples": result.sample_count,
                "variations_tested": result.variations_tested,
                "per_position": bool(result.per_position_results),
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
            "run_filters": result.run_filters,
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

        if per_position_detail:
            report["per_position_tuning"] = per_position_detail
        if result.combined_coefficients:
            report["combined_coefficients"] = result.combined_coefficients

        return report

    # ----- internals ---------------------------------------------------------

    def _gather_samples(
        self,
        year: int,
        weeks: Optional[List[int]],
        player_ids: Optional[List[int]],
        positions: Optional[List[str]],
    ) -> List[Tuple[WeeklyProjectionCriteria, float, int, int, str, str]]:
        """Return list of (criteria, actual_points, player_id, week, position,
        player_name).

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

        samples: List[Tuple[WeeklyProjectionCriteria, float, int, int, str, str]] = []
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
                (
                    criteria,
                    log.fantasy_points or 0.0,
                    log.player_id,
                    log.week,
                    player.position,
                    player.name or "",
                )
            )
        return samples

    @staticmethod
    def _evaluate(
        coeffs: AlgorithmCoefficients,
        samples: List[Tuple[WeeklyProjectionCriteria, float, int, int, str, str]],
    ) -> VariationResult:
        """Compute accuracy metrics for a set of coefficients."""
        errors: List[float] = []
        position_errors: Dict[str, List[float]] = {}

        for criteria, actual, _pid, _week, pos, _pname in samples:
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

    @staticmethod
    def _evaluate_with_position_coefficients(
        pos_coeffs: PositionCoefficients,
        samples: List[Tuple[WeeklyProjectionCriteria, float, int, int, str, str]],
    ) -> VariationResult:
        """Evaluate accuracy using per-position coefficients.

        Each sample is projected with the coefficients matching its
        position, falling back to the global default.
        """
        errors: List[float] = []
        position_errors: Dict[str, List[float]] = {}

        for criteria, actual, _pid, _week, pos, _pname in samples:
            coeffs = pos_coeffs.get_for_position(pos)
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
            coefficients=pos_coeffs.default.to_dict(),
            mae=round(mae, 4),
            rmse=round(rmse, 4),
            sample_count=n,
            per_position_mae=per_pos_mae,
        )

    @staticmethod
    def _build_sample_comparisons_per_position(
        samples: List[Tuple[WeeklyProjectionCriteria, float, int, int, str, str]],
        default_coeffs: AlgorithmCoefficients,
        pos_coeffs: PositionCoefficients,
    ) -> List[SampleComparisonRow]:
        """Build comparison rows using per-position tuned coefficients."""
        rows: List[SampleComparisonRow] = []
        for criteria, actual, player_id, week, position, player_name in samples:
            default_projected = _calculate_weekly_projection(criteria, default_coeffs)
            tuned = pos_coeffs.get_for_position(position)
            tuned_projected = _calculate_weekly_projection(criteria, tuned)
            rows.append(
                SampleComparisonRow(
                    player_id=player_id,
                    player_name=player_name,
                    week=week,
                    position=position,
                    actual_points=round(actual, 4),
                    default_projected_points=round(default_projected, 4),
                    tuned_projected_points=round(tuned_projected, 4),
                    default_error=round(abs(default_projected - actual), 4),
                    tuned_error=round(abs(tuned_projected - actual), 4),
                )
            )
        rows.sort(key=lambda row: (row.week, row.player_id))
        return rows

    @staticmethod
    def _build_sample_comparisons(
        samples: List[Tuple[WeeklyProjectionCriteria, float, int, int, str, str]],
        default_coeffs: AlgorithmCoefficients,
        tuned_coeffs: AlgorithmCoefficients,
    ) -> List[SampleComparisonRow]:
        rows: List[SampleComparisonRow] = []
        for criteria, actual, player_id, week, position, player_name in samples:
            default_projected = _calculate_weekly_projection(criteria, default_coeffs)
            tuned_projected = _calculate_weekly_projection(criteria, tuned_coeffs)
            rows.append(
                SampleComparisonRow(
                    player_id=player_id,
                    player_name=player_name,
                    week=week,
                    position=position,
                    actual_points=round(actual, 4),
                    default_projected_points=round(default_projected, 4),
                    tuned_projected_points=round(tuned_projected, 4),
                    default_error=round(abs(default_projected - actual), 4),
                    tuned_error=round(abs(tuned_projected - actual), 4),
                )
            )
        rows.sort(key=lambda row: (row.week, row.player_id))
        return rows
