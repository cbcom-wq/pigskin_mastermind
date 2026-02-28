"""API routes for sportsbook betting odds and player props."""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.services.sportsbook_service import SportsbookService

router = APIRouter(prefix="/api/odds", tags=["odds"])


@router.get("/games")
async def get_game_odds(
    event_id: Optional[str] = Query(None, description="Filter by event ID"),
    home_team: Optional[str] = Query(None, description="Filter by home team (partial match)"),
    away_team: Optional[str] = Query(None, description="Filter by away team (partial match)"),
    market: Optional[str] = Query(None, description="Filter by market (h2h, spreads, totals)"),
    bookmaker: Optional[str] = Query(None, description="Filter by bookmaker key"),
    db: Session = Depends(get_db),
):
    """Return stored game odds (spreads, totals, moneylines)."""
    service = SportsbookService(db)
    return service.get_game_odds(
        event_id=event_id,
        home_team=home_team,
        away_team=away_team,
        market=market,
        bookmaker=bookmaker,
    )


@router.get("/games/{event_id}")
async def get_game_odds_by_event(
    event_id: str,
    market: Optional[str] = Query(None),
    bookmaker: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Return all stored odds for a specific event."""
    service = SportsbookService(db)
    results = service.get_game_odds(event_id=event_id, market=market, bookmaker=bookmaker)
    if not results:
        raise HTTPException(status_code=404, detail="No odds found for this event")
    return results


@router.get("/props")
async def get_player_props(
    player_name: Optional[str] = Query(None, description="Filter by player name (partial match)"),
    event_id: Optional[str] = Query(None, description="Filter by event ID"),
    market: Optional[str] = Query(None, description="Filter by prop market key"),
    bookmaker: Optional[str] = Query(None, description="Filter by bookmaker key"),
    db: Session = Depends(get_db),
):
    """Return stored player prop odds."""
    service = SportsbookService(db)
    return service.get_player_props(
        player_name=player_name,
        event_id=event_id,
        market=market,
        bookmaker=bookmaker,
    )


@router.post("/import/games")
async def import_game_odds(
    sport: str = Query("americanfootball_nfl", description="Sport key"),
    regions: str = Query("us", description="Comma-separated region codes"),
    markets: str = Query("h2h,spreads,totals", description="Comma-separated market keys"),
    bookmakers: Optional[str] = Query(None, description="Comma-separated bookmaker keys"),
    db: Session = Depends(get_db),
):
    """Fetch game odds from The Odds API and store them in the database.

    Requires the ``ODDS_API_KEY`` environment variable to be set.
    """
    service = SportsbookService(db)
    try:
        count = service.import_game_odds(
            sport=sport,
            regions=regions,
            markets=markets,
            bookmakers=bookmakers,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Odds API error: {exc}")
    return {"status": "success", "rows_upserted": count}


@router.post("/import/props")
async def import_player_props(
    event_id: str = Query(..., description="The Odds API event ID"),
    sport: str = Query("americanfootball_nfl", description="Sport key"),
    regions: str = Query("us", description="Comma-separated region codes"),
    markets: str = Query(
        "player_pass_yds,player_pass_tds,player_rush_yds,player_rush_tds,"
        "player_reception_yds,player_receptions",
        description="Comma-separated prop market keys",
    ),
    bookmakers: Optional[str] = Query(None, description="Comma-separated bookmaker keys"),
    db: Session = Depends(get_db),
):
    """Fetch player props for an event from The Odds API and store them.

    Requires the ``ODDS_API_KEY`` environment variable to be set.
    """
    service = SportsbookService(db)
    try:
        count = service.import_player_props(
            event_id=event_id,
            sport=sport,
            regions=regions,
            markets=markets,
            bookmakers=bookmakers,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Odds API error: {exc}")
    return {"status": "success", "rows_upserted": count}


@router.post("/seed")
async def seed_sample_props(
    all_stars: bool = Query(False, description="Include all built-in star players, not just roster"),
    clear: bool = Query(False, description="Delete previously seeded rows before inserting"),
    db: Session = Depends(get_db),
):
    """Seed realistic sample player-prop lines for offline / offseason testing.

    Inserts historically-plausible 2025 NFL Week 10 prop data from three
    bookmakers (DraftKings, FanDuel, BetMGM) so the sportsbook projection
    pipeline can be tested without a live API or active season.

    By default only seeds props for players already on your roster.
    Pass ``all_stars=true`` to include ~60 well-known NFL players.
    Pass ``clear=true`` to wipe previously seeded rows first.
    """
    from pigskin_mastermind.services.sportsbook_seed import seed_sample_props as do_seed

    try:
        count = do_seed(db, roster_only=not all_stars, clear_existing=clear)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Seed error: {exc}")
    return {"status": "success", "rows_seeded": count}
