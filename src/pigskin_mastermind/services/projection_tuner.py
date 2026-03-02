"""Projection algorithm tuner — parameterized formula with breakdown & backtesting.

Provides metadata describing every coefficient and criteria in the projection
algorithm, plus a service that can run the projection with custom coefficients
and return a per-criteria contribution breakdown.
"""

import math
from typing import Dict, Any, List, Optional, Tuple

from sqlalchemy.orm import Session
from sqlalchemy import desc, func, or_

from pigskin_mastermind.models.database import (
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
    DBWeeklyPlayerStats,
)
from pigskin_mastermind.models.projection_criteria import (
    PlayerProjectionCriteria,
    WeeklyProjectionCriteria,
    YearlyProjectionCriteria,
)
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder,
)

# ---------------------------------------------------------------------------
# Coefficient metadata — single source of truth for defaults & documentation
# ---------------------------------------------------------------------------

# Each entry: key, display name, default value, min/max slider range,
# description, formula snippet, which projection type(s) it applies to.

COEFFICIENT_DEFS: List[Dict[str, Any]] = [
    # ── Base criteria (all projection types) ──────────────────────────────
    {
        "key": "skill_multiplier",
        "name": "Player Skill Multiplier",
        "default": 0.1,
        "min": 0.0,
        "max": 0.5,
        "step": 0.01,
        "group": "base",
        "description": (
            "Scales the player skill level (0–100) into a point adjustment. "
            "The formula centres on 50 (average), so a skill of 80 adds "
            "(80-50) × multiplier points."
        ),
        "formula": "(player_skill_level − 50) × multiplier",
    },
    {
        "key": "offense_multiplier",
        "name": "Team Offense Multiplier",
        "default": 0.06,
        "min": 0.0,
        "max": 0.3,
        "step": 0.01,
        "group": "base",
        "description": (
            "Scales the team offence rating (0–100). Higher values mean "
            "players on elite offences get a bigger boost."
        ),
        "formula": "(team_offense_level − 50) × multiplier",
    },
    {
        "key": "defense_multiplier",
        "name": "Opponent Defense Multiplier",
        "default": 0.04,
        "min": 0.0,
        "max": 0.3,
        "step": 0.01,
        "group": "base",
        "description": (
            "Scales the opponent defence level (0–100, higher = weaker D). "
            "Increases projections against weak defences."
        ),
        "formula": "(opponent_defense_level − 50) × multiplier",
    },
    {
        "key": "touch_multiplier",
        "name": "Touch Percentage Multiplier",
        "default": 0.05,
        "min": 0.0,
        "max": 0.3,
        "step": 0.01,
        "group": "base",
        "description": (
            "Scales the positional touch/target share (0–100%). "
            "Higher share → higher projection."
        ),
        "formula": "positional_touch_percentage × multiplier",
    },
    {
        "key": "trend_multiplier",
        "name": "Recent Trend Multiplier",
        "default": 0.03,
        "min": 0.0,
        "max": 0.2,
        "step": 0.005,
        "group": "base",
        "description": (
            "Scales the recent trend score (−100 to 100). Positive = "
            "hot streak boost, negative = cold streak penalty."
        ),
        "formula": "recent_trend_score × multiplier",
    },
    {
        "key": "efficiency_multiplier",
        "name": "Efficiency Multiplier",
        "default": 2.0,
        "min": 0.0,
        "max": 10.0,
        "step": 0.25,
        "group": "base",
        "description": (
            "Scales the fantasy-points-per-touch efficiency offset. "
            "Baseline is 0.5 fpts/touch; values above that boost, below penalise."
        ),
        "formula": "(fantasy_points_per_touch − 0.5) × multiplier  (clamped ±5)",
    },
    {
        "key": "efficiency_clamp",
        "name": "Efficiency Clamp (max abs)",
        "default": 5.0,
        "min": 1.0,
        "max": 20.0,
        "step": 0.5,
        "group": "base",
        "description": (
            "Caps the efficiency adjustment so outlier efficiency values "
            "don't dominate the projection."
        ),
        "formula": "clamp(efficiency_adjustment, −clamp, +clamp)",
    },
    {
        "key": "injury_multiplier",
        "name": "Injury Risk Multiplier",
        "default": 0.05,
        "min": 0.0,
        "max": 0.3,
        "step": 0.01,
        "group": "base",
        "description": (
            "Scales the injury risk score (0–100) into a negative adjustment. "
            "Higher multiplier means injury risk has more impact."
        ),
        "formula": "injury_risk_score × −multiplier",
    },
    # ── Weekly-specific ───────────────────────────────────────────────────
    {
        "key": "def_rank_multiplier",
        "name": "Def. Rank vs Position Multiplier",
        "default": 0.15,
        "min": 0.0,
        "max": 0.5,
        "step": 0.01,
        "group": "weekly",
        "description": (
            "Scales the opposing defence rank (1–32) for the player's "
            "position. Rank 1 = best D (penalty), 32 = worst D (boost). "
            "Centred on rank 16."
        ),
        "formula": "(opposing_defense_vs_position_rank − 16) × multiplier",
    },
    {
        "key": "momentum_multiplier",
        "name": "Offensive Momentum Multiplier",
        "default": 0.02,
        "min": 0.0,
        "max": 0.15,
        "step": 0.005,
        "group": "weekly",
        "description": (
            "Scales the team's offensive momentum score (−100 to 100) "
            "based on recent weeks' scoring trends."
        ),
        "formula": "offensive_momentum_score × multiplier",
    },
    {
        "key": "weather_multiplier",
        "name": "Weather Impact Multiplier",
        "default": 0.015,
        "min": 0.0,
        "max": 0.1,
        "step": 0.005,
        "group": "weekly",
        "description": (
            "Scales the weather impact score (−100 to 100). "
            "Bad weather (negative) hurts pass-heavy positions more."
        ),
        "formula": "weather_impact_score × multiplier",
    },
    # ── Yearly-specific ───────────────────────────────────────────────────
    {
        "key": "age_post_peak_multiplier",
        "name": "Age Post-Peak Penalty",
        "default": 0.5,
        "min": 0.0,
        "max": 2.0,
        "step": 0.05,
        "group": "yearly",
        "description": (
            "Penalty per year past the position's peak age. "
            "A QB at 32 (peak 29) with multiplier 0.5 loses 1.5 pts."
        ),
        "formula": "years_past_peak × −multiplier  (only when past peak)",
    },
    {
        "key": "age_pre_peak_multiplier",
        "name": "Age Pre-Peak Boost",
        "default": 0.1,
        "min": 0.0,
        "max": 1.0,
        "step": 0.05,
        "group": "yearly",
        "description": (
            "Boost per year before the position's peak age. "
            "Young players get a small upside bump."
        ),
        "formula": "years_before_peak × multiplier  (only when pre-peak)",
    },
    {
        "key": "coaching_multiplier",
        "name": "Coaching Stability Multiplier",
        "default": 0.04,
        "min": 0.0,
        "max": 0.2,
        "step": 0.01,
        "group": "yearly",
        "description": (
            "Scales the coaching stability score (0–100). Stable "
            "coaching (high score) boosts yearly projections."
        ),
        "formula": "(coaching_stability_score − 50) × multiplier",
    },
]

# Criteria documentation — explains how each criteria field is derived
CRITERIA_DOCS: List[Dict[str, Any]] = [
    {
        "field": "historical_average_points",
        "name": "Historical Avg Points",
        "group": "base",
        "range": "0+",
        "description": (
            "The player's historical per-game fantasy point average. "
            "For weekly projections this is the current-season average; "
            "for yearly it's a weighted multi-season average (60%/30%/10%)."
        ),
        "data_source": (
            "DBPlayerSeasonStats.fantasy_points_avg, falling back to "
            "mean(DBPlayerGameLog.fantasy_points) if season stat is 0."
        ),
        "role": "Baseline — the starting point before all adjustments.",
    },
    {
        "field": "player_skill_level",
        "name": "Player Skill Level",
        "group": "base",
        "range": "0–100",
        "description": (
            "Composite skill score blending fantasy points percentile (40%), "
            "efficiency percentile (20%), consistency (20%), and volume (20%) "
            "among all same-position players."
        ),
        "data_source": "ProjectionCriteriaBuilder._compute_skill_composite()",
        "role": "Adjusts projection up/down based on player talent.",
    },
    {
        "field": "team_offense_level",
        "name": "Team Offense Level",
        "group": "base",
        "range": "0–100",
        "description": (
            "Team's offensive strength, blending total yards percentile (50%) "
            "and points scored percentile (50%) across all NFL teams."
        ),
        "data_source": "ProjectionCriteriaBuilder._compute_team_offense_level()",
        "role": "Players on better offences get a boost.",
    },
    {
        "field": "opponent_defense_level",
        "name": "Opponent Defense Level",
        "group": "base",
        "range": "0–100",
        "description": (
            "How weak the opposing defence is (higher = weaker = better for offence). "
            "Derived from the opponent's rank against this position, normalised 0–100."
        ),
        "data_source": "Derived from DBNFLTeamStats.def_rank_vs_{position}.",
        "role": "Boosts projection against weak defences.",
    },
    {
        "field": "positional_touch_percentage",
        "name": "Touch/Target Share",
        "group": "base",
        "range": "0–100",
        "description": (
            "Player's share of their team's total touches/targets at their position. "
            "QBs use pass attempts, RBs use rush+targets, WR/TE use targets."
        ),
        "data_source": "ProjectionCriteriaBuilder._compute_touch_share()",
        "role": "More volume → higher projection.",
    },
    {
        "field": "recent_trend_score",
        "name": "Recent Trend Score",
        "group": "base",
        "range": "−100 to 100",
        "description": (
            "Recency-weighted comparison of last 4 weeks' average vs season average. "
            "Positive = trending up (hot streak), negative = cold streak."
        ),
        "data_source": "ProjectionCriteriaBuilder._compute_trend_score()",
        "role": "Captures momentum / regression signals.",
    },
    {
        "field": "fantasy_points_per_touch",
        "name": "Fantasy Pts per Touch",
        "group": "base",
        "range": "0+",
        "description": (
            "Position-aware efficiency metric. Fantasy points divided by "
            "touches/opportunities. Baseline expectation is ~0.5."
        ),
        "data_source": "ProjectionCriteriaBuilder._compute_position_efficiency()",
        "role": "Rewards efficient players, penalises inefficient ones.",
    },
    {
        "field": "injury_risk_score",
        "name": "Injury Risk Score",
        "group": "base",
        "range": "0–100",
        "description": (
            "Blended injury risk: 70% current injury status (OUT=90, Q=40, "
            "healthy=5) + 30% historical games-played rate over last 3 seasons."
        ),
        "data_source": "ProjectionCriteriaBuilder._compute_injury_risk()",
        "role": "Penalises injury-prone or currently injured players.",
    },
    {
        "field": "opposing_defense_vs_position_rank",
        "name": "Def. Rank vs Position",
        "group": "weekly",
        "range": "1–32",
        "description": (
            "The opposing team's rank defending this position. "
            "Rank 1 = toughest matchup, rank 32 = easiest."
        ),
        "data_source": "DBNFLTeamStats.def_rank_vs_{position} for the week's opponent.",
        "role": "Adjusts weekly projection for matchup difficulty.",
    },
    {
        "field": "offensive_momentum_score",
        "name": "Offensive Momentum",
        "group": "weekly",
        "range": "−100 to 100",
        "description": (
            "Team-level offensive momentum from recent weeks. "
            "Blends points deviation (60%) and yards deviation (40%)."
        ),
        "data_source": "ProjectionCriteriaBuilder._compute_momentum()",
        "role": "Hot offences get a weekly boost.",
    },
    {
        "field": "weather_impact_score",
        "name": "Weather Impact",
        "group": "weekly",
        "range": "−100 to 100",
        "description": (
            "Impact of game-day weather conditions. Negative = bad weather "
            "(hurts passing), positive = ideal conditions. Currently defaults to 0."
        ),
        "data_source": "Manual override only (default 0).",
        "role": "Adjusts for extreme weather games.",
    },
    {
        "field": "age_deviation_from_optimum",
        "name": "Age Deviation",
        "group": "yearly",
        "range": "−10 to 10",
        "description": (
            "Years from position peak age (QB=29, RB=25, WR/TE=27, K=32). "
            "Positive = past peak, negative = pre-peak."
        ),
        "data_source": "Player age from stats metadata vs POSITION_PEAK_AGES.",
        "role": "Post-peak penalty, pre-peak boost.",
    },
    {
        "field": "coaching_stability_score",
        "name": "Coaching Stability",
        "group": "yearly",
        "range": "0–100",
        "description": (
            "Rating of coaching staff continuity and quality. "
            "Currently manual-override only (default 50)."
        ),
        "data_source": "Manual override only.",
        "role": "Stable coaching boosts yearly projections.",
    },
]


def get_default_coefficients() -> Dict[str, float]:
    """Return a dict of coefficient key → default value."""
    return {c["key"]: c["default"] for c in COEFFICIENT_DEFS}


def get_coefficient_metadata() -> List[Dict[str, Any]]:
    """Return the full coefficient metadata list."""
    return COEFFICIENT_DEFS


def get_criteria_docs() -> List[Dict[str, Any]]:
    """Return the full criteria documentation list."""
    return CRITERIA_DOCS


# ---------------------------------------------------------------------------
# Player eligibility filter
# ---------------------------------------------------------------------------

MIN_AVG_POINTS = 5.0  # players averaging below this are excluded from the tuner


def active_players_query(db: Session, position: Optional[str] = None):
    """Return a SQLAlchemy query of 'active' skill-position players.

    A player is considered active if they average >= MIN_AVG_POINTS per game
    across any data we have (season stats or weekly ESPN stats).  Players who
    never registered meaningful fantasy production are hidden from the tuner to
    keep results clean.
    """
    # Subquery A: players with season-stats average >= threshold
    season_eligible = (
        db.query(DBPlayerSeasonStats.player_id)
        .filter(DBPlayerSeasonStats.fantasy_points_avg >= MIN_AVG_POINTS)
        .scalar_subquery()
    )

    # Subquery B: players whose ESPN weekly actual_points avg >= threshold
    # (only counting weeks where they actually played, i.e. actual_points > 0)
    weekly_eligible = (
        db.query(DBWeeklyPlayerStats.player_id)
        .filter(DBWeeklyPlayerStats.actual_points > 0)
        .group_by(DBWeeklyPlayerStats.player_id)
        .having(func.avg(DBWeeklyPlayerStats.actual_points) >= MIN_AVG_POINTS)
        .scalar_subquery()
    )

    query = db.query(DBPlayer).filter(
        DBPlayer.position.in_(["QB", "RB", "WR", "TE"]),
        or_(
            DBPlayer.id.in_(season_eligible),
            DBPlayer.id.in_(weekly_eligible),
        ),
    )

    if position:
        query = query.filter(DBPlayer.position == position.upper())

    return query


# ---------------------------------------------------------------------------
# ProjectionTunerService
# ---------------------------------------------------------------------------

class ProjectionTunerService:
    """Run projection formulas with custom coefficients and return breakdowns."""

    def __init__(self, db: Session, coefficients: Optional[Dict[str, float]] = None):
        self.db = db
        self.defaults = get_default_coefficients()
        self.coefficients = {**self.defaults}
        if coefficients:
            self.coefficients.update(coefficients)
        self.criteria_builder = ProjectionCriteriaBuilder(db)

    # ------------------------------------------------------------------
    # Core projection with breakdown
    # ------------------------------------------------------------------

    def project_weekly(
        self,
        player_id: int,
        week: int,
        year: int,
        criteria_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run weekly projection with custom coefficients, return breakdown."""
        criteria = self.criteria_builder.build_weekly_criteria(
            player_id, week, year, overrides=criteria_overrides,
        )
        breakdown = self._apply_base_breakdown(criteria)
        weekly_steps = self._apply_weekly_breakdown(criteria)
        breakdown["steps"].extend(weekly_steps)

        total = sum(s["value"] for s in breakdown["steps"])
        total = max(0, total)
        breakdown["total"] = round(total, 2)
        breakdown["projection_type"] = "weekly"
        breakdown["week"] = week
        breakdown["year"] = year

        # Actual points from game log
        actual = self._get_actual_points(player_id, week, year)
        breakdown["actual_points"] = actual
        breakdown["error"] = round(total - actual, 2) if actual is not None else None

        breakdown["criteria"] = self._criteria_to_dict(criteria)
        return breakdown

    def project_yearly(
        self,
        player_id: int,
        year: int,
        criteria_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run yearly projection with custom coefficients, return breakdown."""
        criteria = self.criteria_builder.build_yearly_criteria(
            player_id, year, overrides=criteria_overrides,
        )
        breakdown = self._apply_base_breakdown(criteria)
        yearly_steps = self._apply_yearly_breakdown(criteria)
        breakdown["steps"].extend(yearly_steps)

        total = sum(s["value"] for s in breakdown["steps"])
        total = max(0, total)
        breakdown["total"] = round(total, 2)
        breakdown["projection_type"] = "yearly"
        breakdown["year"] = year

        # Actual = season average from game logs
        actual = self._get_season_avg_actual(player_id, year)
        breakdown["actual_points"] = actual
        breakdown["error"] = round(total - actual, 2) if actual is not None else None

        breakdown["criteria"] = self._criteria_to_dict(criteria)
        return breakdown

    # ------------------------------------------------------------------
    # Backtesting
    # ------------------------------------------------------------------

    def backtest_weekly(
        self,
        position: Optional[str],
        year: int,
        week: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Run projections across players and compare to actuals.

        Args:
            position: Filter by position (e.g. 'QB'), or None for all.
            year: Season year.
            week: Specific week, or None to average across all weeks with data.

        Returns:
            Dict with mae, rmse, player_results list, and summary stats.
        """
        players = active_players_query(self.db, position).all()

        results: List[Dict[str, Any]] = []
        for player in players:
            weeks_to_test = self._get_available_weeks(player.id, year, week)
            if not weeks_to_test:
                continue

            for w in weeks_to_test:
                try:
                    proj = self.project_weekly(player.id, w, year)
                except (ValueError, Exception):
                    continue
                if proj["actual_points"] is None:
                    continue
                results.append({
                    "player_id": player.id,
                    "player_name": player.name,
                    "position": player.position,
                    "nfl_team": player.nfl_team,
                    "week": w,
                    "projected": proj["total"],
                    "actual": proj["actual_points"],
                    "error": proj["error"],
                })

        return self._summarise_backtest(results)

    def backtest_yearly(
        self,
        position: Optional[str],
        year: int,
    ) -> Dict[str, Any]:
        """Run yearly projections across players and compare to actuals."""
        players = active_players_query(self.db, position).all()

        results: List[Dict[str, Any]] = []
        for player in players:
            try:
                proj = self.project_yearly(player.id, year)
            except (ValueError, Exception):
                continue
            if proj["actual_points"] is None:
                continue
            results.append({
                "player_id": player.id,
                "player_name": player.name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "projected": proj["total"],
                "actual": proj["actual_points"],
                "error": proj["error"],
            })

        return self._summarise_backtest(results)

    # ------------------------------------------------------------------
    # Internal: breakdown builders
    # ------------------------------------------------------------------

    def _apply_base_breakdown(
        self, criteria: PlayerProjectionCriteria
    ) -> Dict[str, Any]:
        """Apply base criteria and return step-by-step breakdown."""
        c = self.coefficients
        steps = []

        # Historical average (baseline)
        steps.append({
            "label": "Historical Avg Points",
            "criteria_field": "historical_average_points",
            "criteria_value": criteria.historical_average_points,
            "coefficient_key": None,
            "coefficient_value": None,
            "formula": "baseline (no multiplier)",
            "value": round(criteria.historical_average_points, 2),
        })

        # Skill level
        skill_adj = (criteria.player_skill_level - 50) * c["skill_multiplier"]
        steps.append({
            "label": "Player Skill Adjustment",
            "criteria_field": "player_skill_level",
            "criteria_value": criteria.player_skill_level,
            "coefficient_key": "skill_multiplier",
            "coefficient_value": c["skill_multiplier"],
            "formula": f"({criteria.player_skill_level:.1f} − 50) × {c['skill_multiplier']}",
            "value": round(skill_adj, 2),
        })

        # Team offense
        off_adj = (criteria.team_offense_level - 50) * c["offense_multiplier"]
        steps.append({
            "label": "Team Offense Adjustment",
            "criteria_field": "team_offense_level",
            "criteria_value": criteria.team_offense_level,
            "coefficient_key": "offense_multiplier",
            "coefficient_value": c["offense_multiplier"],
            "formula": f"({criteria.team_offense_level:.1f} − 50) × {c['offense_multiplier']}",
            "value": round(off_adj, 2),
        })

        # Opponent defense
        def_adj = (criteria.opponent_defense_level - 50) * c["defense_multiplier"]
        steps.append({
            "label": "Opponent Defense Adjustment",
            "criteria_field": "opponent_defense_level",
            "criteria_value": criteria.opponent_defense_level,
            "coefficient_key": "defense_multiplier",
            "coefficient_value": c["defense_multiplier"],
            "formula": f"({criteria.opponent_defense_level:.1f} − 50) × {c['defense_multiplier']}",
            "value": round(def_adj, 2),
        })

        # Touch percentage
        touch_adj = criteria.positional_touch_percentage * c["touch_multiplier"]
        steps.append({
            "label": "Touch Share Adjustment",
            "criteria_field": "positional_touch_percentage",
            "criteria_value": criteria.positional_touch_percentage,
            "coefficient_key": "touch_multiplier",
            "coefficient_value": c["touch_multiplier"],
            "formula": f"{criteria.positional_touch_percentage:.1f} × {c['touch_multiplier']}",
            "value": round(touch_adj, 2),
        })

        # Recent trend
        trend_adj = criteria.recent_trend_score * c["trend_multiplier"]
        steps.append({
            "label": "Recent Trend Adjustment",
            "criteria_field": "recent_trend_score",
            "criteria_value": criteria.recent_trend_score,
            "coefficient_key": "trend_multiplier",
            "coefficient_value": c["trend_multiplier"],
            "formula": f"{criteria.recent_trend_score:.1f} × {c['trend_multiplier']}",
            "value": round(trend_adj, 2),
        })

        # Efficiency
        if criteria.fantasy_points_per_touch != 0:
            raw_eff = (criteria.fantasy_points_per_touch - 0.5) * c["efficiency_multiplier"]
            clamp = c["efficiency_clamp"]
            eff_adj = max(-clamp, min(clamp, raw_eff))
        else:
            eff_adj = 0.0
        steps.append({
            "label": "Efficiency Adjustment",
            "criteria_field": "fantasy_points_per_touch",
            "criteria_value": criteria.fantasy_points_per_touch,
            "coefficient_key": "efficiency_multiplier",
            "coefficient_value": c["efficiency_multiplier"],
            "formula": (
                f"clamp(({criteria.fantasy_points_per_touch:.2f} − 0.5) "
                f"× {c['efficiency_multiplier']}, ±{c['efficiency_clamp']})"
            ),
            "value": round(eff_adj, 2),
        })

        # Injury risk
        inj_adj = criteria.injury_risk_score * -c["injury_multiplier"]
        steps.append({
            "label": "Injury Risk Penalty",
            "criteria_field": "injury_risk_score",
            "criteria_value": criteria.injury_risk_score,
            "coefficient_key": "injury_multiplier",
            "coefficient_value": c["injury_multiplier"],
            "formula": f"{criteria.injury_risk_score:.1f} × −{c['injury_multiplier']}",
            "value": round(inj_adj, 2),
        })

        return {"steps": steps}

    def _apply_weekly_breakdown(
        self, criteria: WeeklyProjectionCriteria
    ) -> List[Dict[str, Any]]:
        """Return weekly-specific breakdown steps."""
        c = self.coefficients
        steps = []

        # Defense rank vs position
        rank_adj = (criteria.opposing_defense_vs_position_rank - 16) * c["def_rank_multiplier"]
        steps.append({
            "label": "Matchup: Def Rank vs Position",
            "criteria_field": "opposing_defense_vs_position_rank",
            "criteria_value": criteria.opposing_defense_vs_position_rank,
            "coefficient_key": "def_rank_multiplier",
            "coefficient_value": c["def_rank_multiplier"],
            "formula": f"({criteria.opposing_defense_vs_position_rank} − 16) × {c['def_rank_multiplier']}",
            "value": round(rank_adj, 2),
        })

        # Momentum
        mom_adj = criteria.offensive_momentum_score * c["momentum_multiplier"]
        steps.append({
            "label": "Offensive Momentum",
            "criteria_field": "offensive_momentum_score",
            "criteria_value": criteria.offensive_momentum_score,
            "coefficient_key": "momentum_multiplier",
            "coefficient_value": c["momentum_multiplier"],
            "formula": f"{criteria.offensive_momentum_score:.1f} × {c['momentum_multiplier']}",
            "value": round(mom_adj, 2),
        })

        # Weather
        wx_adj = criteria.weather_impact_score * c["weather_multiplier"]
        steps.append({
            "label": "Weather Impact",
            "criteria_field": "weather_impact_score",
            "criteria_value": criteria.weather_impact_score,
            "coefficient_key": "weather_multiplier",
            "coefficient_value": c["weather_multiplier"],
            "formula": f"{criteria.weather_impact_score:.1f} × {c['weather_multiplier']}",
            "value": round(wx_adj, 2),
        })

        return steps

    def _apply_yearly_breakdown(
        self, criteria: YearlyProjectionCriteria
    ) -> List[Dict[str, Any]]:
        """Return yearly-specific breakdown steps."""
        c = self.coefficients
        steps = []

        # Age deviation
        dev = criteria.age_deviation_from_optimum
        if dev > 0:
            age_adj = dev * -c["age_post_peak_multiplier"]
            formula = f"{dev:.1f} × −{c['age_post_peak_multiplier']} (past peak)"
            coeff_key = "age_post_peak_multiplier"
            coeff_val = c["age_post_peak_multiplier"]
        else:
            age_adj = dev * -c["age_pre_peak_multiplier"]
            formula = f"{dev:.1f} × −{c['age_pre_peak_multiplier']} (pre-peak)"
            coeff_key = "age_pre_peak_multiplier"
            coeff_val = c["age_pre_peak_multiplier"]

        steps.append({
            "label": "Age Deviation",
            "criteria_field": "age_deviation_from_optimum",
            "criteria_value": dev,
            "coefficient_key": coeff_key,
            "coefficient_value": coeff_val,
            "formula": formula,
            "value": round(age_adj, 2),
        })

        # Coaching stability
        coach_adj = (criteria.coaching_stability_score - 50) * c["coaching_multiplier"]
        steps.append({
            "label": "Coaching Stability",
            "criteria_field": "coaching_stability_score",
            "criteria_value": criteria.coaching_stability_score,
            "coefficient_key": "coaching_multiplier",
            "coefficient_value": c["coaching_multiplier"],
            "formula": f"({criteria.coaching_stability_score:.1f} − 50) × {c['coaching_multiplier']}",
            "value": round(coach_adj, 2),
        })

        return steps

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _get_actual_points(
        self, player_id: int, week: int, year: int
    ) -> Optional[float]:
        """Get actual fantasy points from game log."""
        log = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year, week=week)
            .first()
        )
        return round(log.fantasy_points, 2) if log else None

    def _get_season_avg_actual(
        self, player_id: int, year: int
    ) -> Optional[float]:
        """Get season average actual fantasy points."""
        logs = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year)
            .all()
        )
        if not logs:
            return None
        total = sum(g.fantasy_points for g in logs)
        return round(total / len(logs), 2)

    def _get_available_weeks(
        self, player_id: int, year: int, week: Optional[int] = None
    ) -> List[int]:
        """Get weeks with game log data for a player."""
        query = (
            self.db.query(DBPlayerGameLog.week)
            .filter_by(player_id=player_id, year=year)
        )
        if week is not None:
            query = query.filter_by(week=week)
        rows = query.order_by(DBPlayerGameLog.week).all()
        return [r[0] for r in rows]

    @staticmethod
    def _criteria_to_dict(criteria: PlayerProjectionCriteria) -> Dict[str, float]:
        """Convert a criteria dataclass to a plain dict."""
        d = {}
        for f in criteria.__dataclass_fields__:
            d[f] = getattr(criteria, f)
        return d

    @staticmethod
    def _summarise_backtest(results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Compute summary statistics for backtest results."""
        if not results:
            return {
                "player_count": 0,
                "sample_count": 0,
                "mae": None,
                "rmse": None,
                "results": [],
                "top_movers": [],
            }

        errors = [abs(r["error"]) for r in results]
        mae = sum(errors) / len(errors)
        rmse = math.sqrt(sum(e ** 2 for e in errors) / len(errors))

        # Sort by absolute error descending for "top movers"
        sorted_results = sorted(results, key=lambda r: abs(r["error"]), reverse=True)

        unique_players = {r["player_id"] for r in results}

        return {
            "player_count": len(unique_players),
            "sample_count": len(results),
            "mae": round(mae, 2),
            "rmse": round(rmse, 2),
            "results": results,
            "top_movers": sorted_results[:10],
        }
