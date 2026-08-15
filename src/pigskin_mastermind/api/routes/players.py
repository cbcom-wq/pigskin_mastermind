"""Player search and listing API routes."""

from datetime import datetime
from fastapi import APIRouter, Depends, Request, Query
from sqlalchemy.orm import Session
from typing import Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBPlayer, DBTeam, DBPlayerSeasonStats, DBPlayerGameLog
from pigskin_mastermind.services.stats_service import StatsService
from pigskin_mastermind.services.player_news_service import PlayerNewsService

router = APIRouter(tags=["players"])


def _real_players():
    """Filter excluding importer placeholder rows.

    nfl_data_py's seasonal feed has no name or position column, so older
    imports wrote ``Unknown`` for both. ``pigskin players merge-identities``
    resolves those rows, but until it runs they should not surface in search.
    """
    return (DBPlayer.name != "Unknown") & (DBPlayer.position != "Unknown")


@router.get("/players/{player_id}")
async def player_detail_page(
    request: Request,
    player_id: int,
    back: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Player detail page showing stats, game logs, and projections."""
    from pigskin_mastermind.api.main import templates

    player = db.query(DBPlayer).filter_by(id=player_id).first()
    if not player:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/players", status_code=302)

    stats_svc = StatsService(db)

    # On-demand: fetch full weekly stats from ESPN for this player
    # if we don't already have comprehensive game logs for them.
    # Players imported via team sync often only have data for weeks
    # they were rostered, not their full NFL season.
    MIN_GAME_LOGS = 6  # below this, data is likely incomplete
    has_season_stats = db.query(DBPlayerSeasonStats).filter_by(player_id=player_id).first()
    game_log_count = (
        db.query(DBPlayerGameLog).filter_by(player_id=player_id).count()
    )
    needs_import = (not has_season_stats) or (game_log_count < MIN_GAME_LOGS)
    if needs_import:
        try:
            from pigskin_mastermind.services.espn_sync import ESPNSyncService
            from pigskin_mastermind.models.database import DBLeague
            league = db.query(DBLeague).first()
            if league and league.espn_s2 and league.swid:
                sync_svc = ESPNSyncService(db)
                sync_svc.fetch_player_full_stats(
                    db_player_id=player_id,
                    league_id=league.league_id,
                    espn_s2=league.espn_s2,
                    swid=league.swid,
                    year=league.year,
                )
            elif player.stats:
                # Fallback: parse whatever JSON we already have
                sync_svc = ESPNSyncService(db)
                sync_svc._populate_single_player_stats(
                    player, year=league.year if league else 2025,
                )
        except Exception:
            pass  # Non-critical — page still renders

    # Season stats from stats service
    player_stats = stats_svc.get_player_stats(player_id)
    seasons = player_stats.get("seasons", [])

    # Game logs (last 30 games)
    game_logs = stats_svc.get_player_game_logs(player_id, limit=30)

    # Recent trend
    trend = stats_svc.get_recent_performance(player_id, num_weeks=4)
    if trend.get("num_weeks", 0) == 0:
        trend = None

    # Fantasy team name (if rostered)
    fantasy_team = player.team.name if player.team else None

    # On-demand player news (ESPN)
    news_svc = PlayerNewsService(db)
    news_items = news_svc.get_player_news(player)

    # Most recent season carries the headline rates and the ADP block; the most
    # recent season that actually has an ADP may be a different (future) one.
    latest_season = seasons[0] if seasons else None
    adp_season = next((s for s in seasons if s.get("adp") is not None), None)

    return templates.TemplateResponse(
        "players/details.html",
        {
            "request": request,
            "player": player,
            "seasons": seasons,
            "latest_season": latest_season,
            "adp_season": adp_season,
            "game_logs": game_logs,
            "trend": trend,
            "fantasy_team": fantasy_team,
            "news_items": news_items,
            "back_url": back,
        },
    )


@router.get("/players/{player_id}/simulation")
async def player_simulation_page(
    request: Request,
    player_id: int,
    year: Optional[int] = Query(None),
    week: Optional[int] = Query(None, ge=1, le=22),
    back: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Full-page player game simulation view."""
    from pigskin_mastermind.api.main import templates

    player = db.query(DBPlayer).filter_by(id=player_id).first()
    if not player:
        from fastapi.responses import RedirectResponse
        return RedirectResponse("/players", status_code=302)

    return templates.TemplateResponse(
        "players/simulation.html",
        {
            "request": request,
            "player": player,
            "default_year": year or datetime.utcnow().year,
            "default_week": week or 1,
            "back_url": back,
        },
    )


@router.get("/players")
async def player_search_page(request: Request, db: Session = Depends(get_db)):
    """Full player search page."""
    from pigskin_mastermind.api.main import templates
    nfl_teams = [
        row[0] for row in
        db.query(DBPlayer.nfl_team)
        .filter(_real_players())
        .distinct().order_by(DBPlayer.nfl_team).all()
        if row[0]
    ]
    return templates.TemplateResponse(
        "players/search.html",
        {"request": request, "nfl_teams": nfl_teams}
    )


@router.get("/api/players/search")
async def search_players(
    request: Request,
    q: str = Query("", min_length=0),
    context: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """Search players and return HTML fragments for HTMX.

    Used by both the global search modal and the trade analyzer receive-search.
    Results are ordered by ADP (ascending) to match mock draft rankings.
    """
    from pigskin_mastermind.api.main import templates
    from datetime import datetime

    current_year = datetime.utcnow().year

    query = (
        db.query(DBPlayer, DBPlayerSeasonStats)
        .outerjoin(
            DBPlayerSeasonStats,
            (DBPlayerSeasonStats.player_id == DBPlayer.id)
            & (DBPlayerSeasonStats.year == current_year)
        )
        .filter(_real_players())
    )
    if q:
        search = f"%{q}%"
        query = query.filter(
            (DBPlayer.name.ilike(search))
            | (DBPlayer.position.ilike(search))
            | (DBPlayer.nfl_team.ilike(search))
        )
    # Order by ADP ascending (NULLs last), then by projected_points descending
    # NOTE: legacy mixed-unit column. The draft pool reads player_projections
    # (services/projection_refresh.py) instead; this route has not been migrated.
    query = query.order_by(DBPlayerSeasonStats.adp.asc().nullslast(), DBPlayer.projected_points.desc())
    results = query.limit(20).all()

    if context == "trade-receive":
        return templates.TemplateResponse(
            "players/_trade_result.html",
            {"request": request, "players": [p for p, _ in results], "side": "receive"},
        )

    return templates.TemplateResponse(
        "players/_search_result.html",
        {
            "request": request,
            "results": [
                {
                    "player": p,
                    "adp": season.adp if season else None,
                    "subtitle": f"{p.nfl_team} — {p.team.name}" if p.team else p.nfl_team,
                }
                for p, season in results
            ],
        },
    )


@router.get("/api/players/list")
async def list_players(
    request: Request,
    q: str = Query("", min_length=0),
    position: Optional[str] = Query(None),
    nfl_team: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """List players with filters, returning HTML table rows for HTMX."""
    from pigskin_mastermind.api.main import templates

    query = db.query(DBPlayer).outerjoin(DBTeam).filter(_real_players())

    if q:
        search = f"%{q}%"
        query = query.filter(DBPlayer.name.ilike(search))
    if position:
        query = query.filter(DBPlayer.position == position)
    if nfl_team:
        query = query.filter(DBPlayer.nfl_team == nfl_team)

    # NOTE: legacy mixed-unit column. The draft pool reads player_projections
    # (services/projection_refresh.py) instead; this route has not been migrated.
    players = query.order_by(DBPlayer.projected_points.desc()).limit(100).all()

    return templates.TemplateResponse(
        "players/_list_row.html",
        {"request": request, "players": players},
    )
