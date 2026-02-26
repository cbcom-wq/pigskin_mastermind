"""Player search and listing API routes."""

from datetime import datetime
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBPlayer, DBTeam, DBPlayerSeasonStats
from pigskin_mastermind.services.stats_service import StatsService

router = APIRouter(tags=["players"])


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
    # if we don't already have game logs for them
    has_season_stats = db.query(DBPlayerSeasonStats).filter_by(player_id=player_id).first()
    if not has_season_stats:
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

    return templates.TemplateResponse(
        "players/details.html",
        {
            "request": request,
            "player": player,
            "seasons": seasons,
            "game_logs": game_logs,
            "trend": trend,
            "fantasy_team": fantasy_team,
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
        db.query(DBPlayer.nfl_team).distinct().order_by(DBPlayer.nfl_team).all()
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
    """
    from pigskin_mastermind.api.main import templates

    query = db.query(DBPlayer)
    if q:
        search = f"%{q}%"
        query = query.filter(
            (DBPlayer.name.ilike(search))
            | (DBPlayer.position.ilike(search))
            | (DBPlayer.nfl_team.ilike(search))
        )
    players = query.order_by(DBPlayer.projected_points.desc()).limit(20).all()

    if context == "trade-receive":
        return _render_trade_search_results(players)

    # Default: global search modal results
    if not players:
        return HTMLResponse(
            '<div class="px-4 py-6 text-center text-sm text-slate-400">No players found</div>'
        )

    html_parts = []
    for p in players:
        team_name = ""
        if p.team:
            team_name = p.team.name
        img_html = ''
        if p.headshot_url:
            img_html = f'<img src="{p.headshot_url}" alt="" class="w-8 h-8 rounded-full object-cover bg-slate-100" onerror="this.style.display=\'none\'" />'
        else:
            img_html = '<div class="w-8 h-8 rounded-full bg-slate-200"></div>'
        html_parts.append(
            f'<a href="/players/{p.id}" class="flex items-center justify-between px-4 py-2.5 hover:bg-slate-50 transition-colors cursor-pointer">'
            f'  <div class="flex items-center gap-3">'
            f'    {img_html}'
            f'    <span class="inline-flex items-center justify-center w-10 h-6 rounded text-xs font-bold badge-{p.position.lower()}">{p.position}</span>'
            f'    <div>'
            f'      <p class="text-sm font-medium text-slate-800">{p.name}</p>'
            f'      <p class="text-xs text-slate-400">{p.nfl_team}{(" — " + team_name) if team_name else ""}</p>'
            f'    </div>'
            f'  </div>'
            f'  <div class="text-right">'
            f'    <p class="text-sm font-semibold text-slate-700">{p.projected_points:.1f}</p>'
            f'    <p class="text-[10px] text-slate-400">projected</p>'
            f'  </div>'
            f'</a>'
        )
    return HTMLResponse("\n".join(html_parts))


def _render_trade_search_results(players):
    """Render player search results for the trade analyzer receive column."""
    if not players:
        return HTMLResponse(
            '<p class="text-sm text-slate-400 text-center py-4">No players found</p>'
        )

    html_parts = []
    for p in players:
        img_html = f'<img src="{p.headshot_url}" alt="" class="w-7 h-7 rounded-full object-cover bg-slate-200 flex-shrink-0" onerror="this.style.display=\'none\'" />' if p.headshot_url else ''
        html_parts.append(
            f'<div class="flex items-center justify-between p-2 rounded-lg hover:bg-slate-50 transition-colors">'
            f'  <div class="flex items-center gap-2">'
            f'    {img_html}'
            f'    <span class="inline-flex items-center justify-center w-9 h-5 rounded text-[10px] font-bold badge-{p.position.lower()}">{p.position}</span>'
            f'    <div>'
            f'      <p class="text-sm font-medium text-slate-700">{p.name}</p>'
            f'      <p class="text-xs text-slate-400">{p.nfl_team} &middot; {p.projected_points:.1f} pts</p>'
            f'    </div>'
            f'  </div>'
            f'  <button type="button"'
            f'    onclick="toggleReceivePlayer({p.id}, \'{p.name}\', \'{p.position}\', {p.projected_points:.1f})"'
            f'    data-player-receive="{p.id}"'
            f'    class="px-2 py-1 text-xs font-medium rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200 transition-colors">'
            f'    Select'
            f'  </button>'
            f'</div>'
        )
    return HTMLResponse("\n".join(html_parts))


@router.get("/api/players/list")
async def list_players(
    request: Request,
    q: str = Query("", min_length=0),
    position: Optional[str] = Query(None),
    nfl_team: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """List players with filters, returning HTML table rows for HTMX."""
    query = db.query(DBPlayer).outerjoin(DBTeam)

    if q:
        search = f"%{q}%"
        query = query.filter(DBPlayer.name.ilike(search))
    if position:
        query = query.filter(DBPlayer.position == position)
    if nfl_team:
        query = query.filter(DBPlayer.nfl_team == nfl_team)

    players = query.order_by(DBPlayer.projected_points.desc()).limit(100).all()

    if not players:
        return HTMLResponse(
            '<tr><td colspan="6" class="px-6 py-8 text-center text-sm text-slate-400">No players found</td></tr>'
        )

    html_parts = []
    for p in players:
        team_name = p.team.name if p.team else "—"
        img_html = ''
        if p.headshot_url:
            img_html = f'<img src="{p.headshot_url}" alt="" class="w-8 h-8 rounded-full object-cover bg-slate-100 inline-block mr-2 align-middle" onerror="this.style.display=\'none\'" />'
        html_parts.append(
            f'<tr class="hover:bg-slate-50 transition-colors cursor-pointer" onclick="window.location=\'/players/{p.id}\'" >'
            f'  <td class="px-6 py-3 text-sm font-medium">{img_html}<a href="/players/{p.id}" class="text-field-700 hover:text-field-900 hover:underline">{p.name}</a></td>'
            f'  <td class="px-6 py-3">'
            f'    <span class="inline-flex items-center justify-center w-10 h-6 rounded text-xs font-bold badge-{p.position.lower()}">{p.position}</span>'
            f'  </td>'
            f'  <td class="px-6 py-3 text-sm text-slate-600">{p.nfl_team}</td>'
            f'  <td class="px-6 py-3 text-sm text-slate-600">{team_name}</td>'
            f'  <td class="px-6 py-3 text-sm font-semibold text-slate-700 text-right">{p.projected_points:.1f}</td>'
            f'  <td class="px-6 py-3 text-sm font-semibold text-slate-700 text-right">{p.actual_points:.1f}</td>'
            f'</tr>'
        )
    return HTMLResponse("\n".join(html_parts))
