"""NFL game scoreboard and game detail routes."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request, Query, HTTPException
from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.utils.back_nav import resolve_back

router = APIRouter(tags=["games"])


@router.get("/games")
async def scoreboard_page(
    request: Request,
    year: Optional[int] = Query(None),
    week: Optional[int] = Query(None, ge=1, le=22),
    db: Session = Depends(get_db),
):
    """NFL scoreboard page – select a season week and view game cards."""
    from pigskin_mastermind.api.main import templates

    default_year = year or datetime.utcnow().year
    default_week = week or 1

    return templates.TemplateResponse(
        "games/scoreboard.html",
        {
            "request": request,
            "default_year": default_year,
            "default_week": default_week,
        },
    )


@router.get("/games/{game_id}")
async def game_detail_page(
    request: Request,
    game_id: str,
    year: int = Query(...),
    week: int = Query(..., ge=1, le=22),
    back: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Full game simulation view for a specific NFL game."""
    from pigskin_mastermind.api.main import templates

    return templates.TemplateResponse(
        "games/detail.html",
        {
            "request": request,
            "game_id": game_id,
            "year": year,
            "week": week,
            "back": resolve_back(back, f"/games?year={year}&week={week}", "Scores"),
        },
    )


# ── JSON API endpoints ──────────────────────────────────────────────────────

@router.get("/api/games/scoreboard")
async def get_scoreboard(
    year: int = Query(..., description="NFL season year"),
    week: int = Query(..., ge=1, le=22, description="Week number"),
    db: Session = Depends(get_db),
):
    """Return the list of games for a given week."""
    from pigskin_mastermind.services.nfl_data_service import NFLDataService

    service = NFLDataService(db)
    try:
        games = service.get_week_scoreboard(year=year, week=week)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {"year": year, "week": week, "games": games}


@router.get("/api/games/{game_id}/simulation")
async def get_game_simulation(
    game_id: str,
    year: int = Query(..., description="NFL season year"),
    week: int = Query(..., ge=1, le=22, description="Week number"),
    db: Session = Depends(get_db),
):
    """Return animation-ready simulation data for a full NFL game."""
    from pigskin_mastermind.services.nfl_game_simulation_service import (
        NFLGameSimulationService,
    )

    service = NFLGameSimulationService(db)
    try:
        return service.build_game_simulation(
            game_id=game_id, year=year, week=week,
        )
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
