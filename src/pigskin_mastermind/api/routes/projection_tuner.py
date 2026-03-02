"""API routes for the Projection Algorithm Tuner developer tool."""

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, Dict, List, Any

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBPlayer, DBPlayerGameLog
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
