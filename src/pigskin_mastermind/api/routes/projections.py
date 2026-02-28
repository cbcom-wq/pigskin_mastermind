"""API routes for sportsbook-based fantasy scoring projections."""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.services.sportsbook_projection_service import (
    SportsbookProjectionService,
)

router = APIRouter(prefix="/api/projections/sportsbook", tags=["projections"])


@router.get("/player")
async def project_player(
    player_name: str = Query(..., description="Full or partial player name"),
    event_id: Optional[str] = Query(None, description="Restrict to a specific event / game"),
    league_id: Optional[str] = Query(None, description="League ID for custom scoring settings"),
    bookmaker: Optional[str] = Query(None, description="Restrict to one bookmaker"),
    db: Session = Depends(get_db),
):
    """Return a sportsbook-derived weekly fantasy projection for a player.

    The projection is built entirely from stored player-prop betting lines.
    Each prop market (passing yards, rushing TDs, etc.) is converted to
    fantasy points using the league's scoring settings (or 0.5-PPR defaults),
    and all categories are summed for the total.
    """
    service = SportsbookProjectionService(db)
    result = service.project_player(
        player_name,
        event_id=event_id,
        league_id=league_id,
        bookmaker=bookmaker,
    )
    if not result["categories"]:
        raise HTTPException(
            status_code=404,
            detail=f"No player props found for '{player_name}'",
        )
    return result


@router.get("/event/{event_id}")
async def project_event(
    event_id: str,
    league_id: Optional[str] = Query(None, description="League ID for custom scoring settings"),
    bookmaker: Optional[str] = Query(None, description="Restrict to one bookmaker"),
    db: Session = Depends(get_db),
):
    """Return sportsbook-derived projections for every player with props in an event."""
    service = SportsbookProjectionService(db)
    results = service.project_event(
        event_id,
        league_id=league_id,
        bookmaker=bookmaker,
    )
    if not results:
        raise HTTPException(
            status_code=404,
            detail=f"No player props found for event '{event_id}'",
        )
    return results
