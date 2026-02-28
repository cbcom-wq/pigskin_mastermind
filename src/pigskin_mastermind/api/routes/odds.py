"""API routes for sportsbook odds data."""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from pigskin_mastermind.api.database import get_db

router = APIRouter(prefix="/api/odds", tags=["odds"])


@router.post("/import/game-odds")
async def import_game_odds(
    api_key: str = Query(..., description="The Odds API key"),
    markets: str = Query("h2h,spreads,totals", description="Comma-separated market keys"),
    db: Session = Depends(get_db),
):
    """Import current NFL game odds from The Odds API."""
    from pigskin_mastermind.services.sportsbook_service import SportsBookService

    market_list = [m.strip() for m in markets.split(",") if m.strip()]
    service = SportsBookService(db, api_key=api_key)
    try:
        count = service.import_game_odds(markets=market_list)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Odds API error: {exc}")
    return {"status": "success", "odds_rows": count}


@router.post("/import/player-props/{event_id}")
async def import_player_props(
    event_id: str,
    api_key: str = Query(..., description="The Odds API key"),
    markets: Optional[str] = Query(None, description="Comma-separated player prop markets"),
    db: Session = Depends(get_db),
):
    """Import player prop odds for a specific NFL event."""
    from pigskin_mastermind.services.sportsbook_service import SportsBookService

    market_list = None
    if markets:
        market_list = [m.strip() for m in markets.split(",") if m.strip()]
    service = SportsBookService(db, api_key=api_key)
    try:
        count = service.import_player_props(event_id, markets=market_list)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Odds API error: {exc}")
    return {"status": "success", "odds_rows": count}


@router.get("/games")
async def get_game_odds(
    team: Optional[str] = Query(None, description="Filter by NFL team name"),
    market: Optional[str] = Query(None, description="Filter by market (h2h, spreads, totals)"),
    db: Session = Depends(get_db),
):
    """Query stored game odds."""
    from pigskin_mastermind.services.sportsbook_service import SportsBookService

    service = SportsBookService(db)
    return service.get_game_odds(team=team, market=market)


@router.get("/player-props")
async def get_player_props(
    player_name: Optional[str] = Query(None, description="Player name (partial match)"),
    market: Optional[str] = Query(None, description="Prop market key"),
    db: Session = Depends(get_db),
):
    """Query stored player prop odds."""
    from pigskin_mastermind.services.sportsbook_service import SportsBookService

    service = SportsBookService(db)
    return service.get_player_odds(player_name=player_name, market=market)
