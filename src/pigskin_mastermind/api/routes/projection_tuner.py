"""API routes for the Projection Algorithm Tuner developer tool."""

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, Dict, List, Any

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats,
    DBNFLTeamStats, DBWeeklyPlayerStats,
)
from pigskin_mastermind.services.projection_tuner import (
    ProjectionTunerService,
    get_default_coefficients,
    get_coefficient_metadata,
    get_criteria_docs,
)

router = APIRouter(tags=["projection-tuner"])


# ── Pydantic request models ──────────────────────────────────────────────

class SimulatePlayerRequest(BaseModel):
    player_id: int
    year: int
    week: Optional[int] = None
    projection_type: str = "weekly"  # "weekly" or "yearly"
    coefficients: Optional[Dict[str, float]] = None


class SimulateBulkRequest(BaseModel):
    position: Optional[str] = None
    year: int
    week: Optional[int] = None
    projection_type: str = "weekly"
    coefficients: Optional[Dict[str, float]] = None


class CriteriaGridRequest(BaseModel):
    position: Optional[str] = None
    year: int
    week: Optional[int] = None
    projection_type: str = "weekly"
    player_ids: Optional[List[int]] = None  # None = all matching players
    limit: int = 50


# ── Page route ────────────────────────────────────────────────────────────

@router.get("/projection-tuner")
async def projection_tuner_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """Render the Projection Algorithm Tuner page."""
    from pigskin_mastermind.api.main import templates

    # Get available players for the dropdown
    players = (
        db.query(DBPlayer)
        .filter(DBPlayer.position.in_(["QB", "RB", "WR", "TE"]))
        .order_by(DBPlayer.position, DBPlayer.name)
        .all()
    )

    # Get available years from game logs
    years = (
        db.query(DBPlayerGameLog.year)
        .distinct()
        .order_by(DBPlayerGameLog.year.desc())
        .all()
    )
    available_years = [y[0] for y in years] if years else [2025]

    return templates.TemplateResponse(
        "projection_tuner.html",
        {
            "request": request,
            "players": players,
            "available_years": available_years,
            "coefficients": get_coefficient_metadata(),
            "criteria_docs": get_criteria_docs(),
            "defaults": get_default_coefficients(),
        },
    )


# ── API endpoints ─────────────────────────────────────────────────────────

@router.get("/api/projection-tuner/defaults")
async def get_defaults():
    """Return default coefficients and algorithm documentation."""
    return {
        "coefficients": get_coefficient_metadata(),
        "criteria_docs": get_criteria_docs(),
        "defaults": get_default_coefficients(),
    }


@router.post("/api/projection-tuner/simulate-player")
async def simulate_player(
    req: SimulatePlayerRequest,
    db: Session = Depends(get_db),
):
    """Run projection for a single player with custom coefficients."""
    service = ProjectionTunerService(db, coefficients=req.coefficients)
    player = db.query(DBPlayer).filter_by(id=req.player_id).first()
    if not player:
        return {"error": f"Player {req.player_id} not found"}

    try:
        if req.projection_type == "yearly":
            result = service.project_yearly(req.player_id, req.year)
        else:
            if req.week is None:
                return {"error": "week is required for weekly projections"}
            result = service.project_weekly(req.player_id, req.week, req.year)
    except ValueError as e:
        return {"error": str(e)}

    # Also compute with defaults for comparison
    default_service = ProjectionTunerService(db)
    try:
        if req.projection_type == "yearly":
            default_result = default_service.project_yearly(req.player_id, req.year)
        else:
            default_result = default_service.project_weekly(
                req.player_id, req.week, req.year
            )
    except (ValueError, Exception):
        default_result = None

    return {
        "player": {
            "id": player.id,
            "name": player.name,
            "position": player.position,
            "nfl_team": player.nfl_team,
        },
        "tuned": result,
        "default": default_result,
    }


@router.post("/api/projection-tuner/simulate-bulk")
async def simulate_bulk(
    req: SimulateBulkRequest,
    db: Session = Depends(get_db),
):
    """Run projections across a position group with custom coefficients."""
    tuned_service = ProjectionTunerService(db, coefficients=req.coefficients)
    default_service = ProjectionTunerService(db)

    try:
        if req.projection_type == "yearly":
            tuned = tuned_service.backtest_yearly(req.position, req.year)
            default = default_service.backtest_yearly(req.position, req.year)
        else:
            tuned = tuned_service.backtest_weekly(req.position, req.year, req.week)
            default = default_service.backtest_weekly(req.position, req.year, req.week)
    except (ValueError, Exception) as e:
        return {"error": str(e)}

    return {
        "tuned": tuned,
        "default": default,
        "position": req.position,
        "year": req.year,
        "week": req.week,
        "projection_type": req.projection_type,
    }


@router.post("/api/projection-tuner/criteria-grid")
async def criteria_grid(
    req: CriteriaGridRequest,
    db: Session = Depends(get_db),
):
    """Return a criteria spreadsheet for multiple players.

    Each row is a player; columns are the criteria fields plus projected/actual points.
    Used to assess whether auto-derived criteria values look sensible.
    """
    from pigskin_mastermind.services.projection_tuner import ProjectionTunerService

    # Build player list
    query = db.query(DBPlayer).filter(
        DBPlayer.position.in_(["QB", "RB", "WR", "TE"])
    )
    if req.position:
        query = query.filter(DBPlayer.position == req.position.upper())
    if req.player_ids:
        query = query.filter(DBPlayer.id.in_(req.player_ids))
    players = query.order_by(DBPlayer.position, DBPlayer.name).limit(req.limit).all()

    service = ProjectionTunerService(db)
    rows = []
    for player in players:
        try:
            if req.projection_type == "yearly":
                result = service.project_yearly(player.id, req.year)
            else:
                if req.week is None:
                    # No week specified — build criteria directly without projection
                    from pigskin_mastermind.services.projection_criteria_builder import (
                        ProjectionCriteriaBuilder,
                    )
                    builder = ProjectionCriteriaBuilder(db)
                    # Use week=1 as a placeholder for criteria derivation
                    criteria = builder.build_weekly_criteria(player.id, 1, req.year)
                    result = {
                        "criteria": service._criteria_to_dict(criteria),
                        "total": None,
                        "actual_points": None,
                    }
                else:
                    result = service.project_weekly(player.id, req.week, req.year)
        except (ValueError, Exception):
            continue

        rows.append({
            "player_id": player.id,
            "player_name": player.name,
            "position": player.position,
            "nfl_team": player.nfl_team,
            "projected": result.get("total"),
            "actual": result.get("actual_points"),
            "criteria": result.get("criteria", {}),
        })

    # Determine column order: base fields first, then type-specific
    base_cols = [
        "historical_average_points",
        "player_skill_level",
        "team_offense_level",
        "opponent_defense_level",
        "positional_touch_percentage",
        "recent_trend_score",
        "fantasy_points_per_touch",
        "injury_risk_score",
    ]
    weekly_cols = [
        "opposing_defense_vs_position_rank",
        "offensive_momentum_score",
        "weather_impact_score",
    ]
    yearly_cols = [
        "age_deviation_from_optimum",
        "coaching_stability_score",
    ]
    extra_cols = weekly_cols if req.projection_type == "weekly" else yearly_cols
    criteria_cols = base_cols + extra_cols

    # Compute per-column min/max for heat-map normalization
    col_stats: Dict[str, Any] = {}
    for col in criteria_cols:
        vals = [r["criteria"].get(col) for r in rows if r["criteria"].get(col) is not None]
        if vals:
            col_stats[col] = {"min": min(vals), "max": max(vals)}
        else:
            col_stats[col] = {"min": 0, "max": 0}

    return {
        "rows": rows,
        "criteria_cols": criteria_cols,
        "col_stats": col_stats,
        "projection_type": req.projection_type,
        "year": req.year,
        "week": req.week,
        "player_count": len(rows),
        "truncated": len(players) == req.limit,
    }


@router.get("/api/projection-tuner/players")
async def get_players_for_tuner(
    position: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Return players list, optionally filtered by position."""
    query = db.query(DBPlayer).filter(
        DBPlayer.position.in_(["QB", "RB", "WR", "TE"])
    )
    if position:
        query = query.filter(DBPlayer.position == position.upper())
    players = query.order_by(DBPlayer.name).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "position": p.position,
            "nfl_team": p.nfl_team,
        }
        for p in players
    ]


@router.get("/api/projection-tuner/diagnose/{player_id}")
async def diagnose_player(
    player_id: int,
    year: int = Query(..., description="Season year to diagnose"),
    db: Session = Depends(get_db),
):
    """Return a data-availability diagnostic for a player + year.

    Shows exactly which data sources are populated vs missing so developers
    understand why criteria values may be 0.
    """
    player = db.query(DBPlayer).filter_by(id=player_id).first()
    if not player:
        return {"error": f"Player {player_id} not found"}

    # ── 1. Season stats ───────────────────────────────────────────────
    season = (
        db.query(DBPlayerSeasonStats)
        .filter_by(player_id=player_id, year=year)
        .first()
    )
    season_check = {
        "found": season is not None,
        "year": year,
        "games_played": season.games_played if season else 0,
        "fantasy_points_total": season.fantasy_points_total if season else 0.0,
        "fantasy_points_avg": season.fantasy_points_avg if season else 0.0,
        "fantasy_points_per_touch": season.fantasy_points_per_touch if season else 0.0,
        "pass_att": season.pass_att if season else 0,
        "rush_att": season.rush_att if season else 0,
        "targets": season.targets if season else 0,
        "snap_pct": season.snap_pct if season else None,
    }

    # ── 2. Game logs ──────────────────────────────────────────────────
    logs = (
        db.query(DBPlayerGameLog)
        .filter_by(player_id=player_id, year=year)
        .order_by(DBPlayerGameLog.week)
        .all()
    )
    log_pts = [g.fantasy_points for g in logs]
    log_check = {
        "count": len(logs),
        "weeks": [g.week for g in logs],
        "fantasy_points_avg": round(sum(log_pts) / len(log_pts), 2) if log_pts else 0.0,
        "fantasy_points_range": (
            {"min": min(log_pts), "max": max(log_pts)} if log_pts else None
        ),
    }

    # ── 3. Weekly player stats (ESPN matchup data) ────────────────────
    weekly = (
        db.query(DBWeeklyPlayerStats)
        .filter(
            DBWeeklyPlayerStats.player_id == player_id,
            DBWeeklyPlayerStats.actual_points > 0,
        )
        .order_by(DBWeeklyPlayerStats.week)
        .all()
    )
    weekly_pts = [w.actual_points for w in weekly]
    weekly_check = {
        "count": len(weekly),
        "actual_points_avg": round(sum(weekly_pts) / len(weekly_pts), 2) if weekly_pts else 0.0,
        "projected_points_avg": (
            round(
                sum(w.projected_points for w in weekly) / len(weekly), 2
            )
            if weekly else 0.0
        ),
        "weeks_with_data": [w.week for w in weekly],
    }

    # ── 4. NFL team stats ─────────────────────────────────────────────
    team_stats = (
        db.query(DBNFLTeamStats)
        .filter_by(nfl_team=player.nfl_team, year=year, week=None)
        .first()
    )
    team_check = {
        "found": team_stats is not None,
        "nfl_team": player.nfl_team,
        "total_yards": team_stats.total_yards if team_stats else 0,
        "points_scored": team_stats.points_scored if team_stats else 0,
        "def_rank_vs_position": (
            getattr(team_stats, f"def_rank_vs_{player.position.lower()}", None)
            if team_stats else None
        ),
    }

    # ── 5. Player metadata ────────────────────────────────────────────
    player_stats = player.stats or {}
    player_check = {
        "injury_status": player_stats.get("injuryStatus", "—"),
        "age": player_stats.get("age", "—"),
        "stats_keys": list(player_stats.keys())[:10],
    }

    # ── 6. Criteria source explanations ──────────────────────────────
    def source_tag(primary_ok, fallback1_ok, fallback1_name, fallback2_ok=False, fallback2_name=""):
        if primary_ok:
            return {"status": "ok", "source": "season_stats"}
        if fallback1_ok:
            return {"status": "fallback", "source": fallback1_name}
        if fallback2_ok:
            return {"status": "fallback", "source": fallback2_name}
        return {"status": "missing", "source": "no data — will be 0"}

    has_season_avg = season and season.fantasy_points_avg and season.fantasy_points_avg > 0
    has_game_logs = len(logs) > 0
    has_weekly = len(weekly) > 0
    has_team_stats = team_stats is not None

    criteria_sources = {
        "historical_average_points": source_tag(
            has_season_avg, has_game_logs, "game_logs", has_weekly, "weekly_player_stats"
        ),
        "player_skill_level": source_tag(
            season is not None, False, "", False, ""
        ),
        "positional_touch_percentage": source_tag(
            season is not None, season and season.snap_pct is not None, "snap_pct fallback"
        ),
        "fantasy_points_per_touch": source_tag(
            season is not None and season.fantasy_points_total > 0, False, ""
        ),
        "recent_trend_score": source_tag(
            has_game_logs, has_weekly, "weekly_player_stats"
        ),
        "team_offense_level": source_tag(has_team_stats, False, ""),
        "opponent_defense_level": {"status": "ok" if has_team_stats else "missing", "source": "nfl_team_stats (opponent)"},
        "injury_risk_score": {
            "status": "ok" if player_stats.get("injuryStatus") is not None else "partial",
            "source": "player.stats.injuryStatus",
        },
    }

    # Highlight any zeros that will cascade
    warnings = []
    if criteria_sources["historical_average_points"]["status"] == "missing":
        warnings.append(
            "⚠️  historical_average_points = 0 — this is the baseline. "
            "All other criteria adjust around it, so 0 here means ~0 total projection. "
            "Run the NFL data import or sync ESPN weekly stats to populate data."
        )
    if not has_season_avg and not has_game_logs and not has_weekly:
        warnings.append(
            "⚠️  No fantasy points data found in season_stats, game_logs, OR weekly_player_stats. "
            f"Try: Settings → Sync ESPN data for {year}."
        )

    return {
        "player": {
            "id": player.id,
            "name": player.name,
            "position": player.position,
            "nfl_team": player.nfl_team,
        },
        "year": year,
        "checks": {
            "season_stats": season_check,
            "game_logs": log_check,
            "weekly_player_stats": weekly_check,
            "nfl_team_stats": team_check,
            "player_metadata": player_check,
        },
        "criteria_sources": criteria_sources,
        "warnings": warnings,
    }
