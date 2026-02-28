"""Teams routes: listing, CRUD, detail page."""

from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, Request, Form, Query, Body
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from pydantic import BaseModel
import uuid
import json

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import (
    DBTeam, DBPlayer, DBLeague, DBWeeklyTeamStats, DBWeeklyPlayerStats,
    DBNFLTeamStats, DBPlayerGameLog,
)


class SlotChange(BaseModel):
    player_id: int
    new_slot: str


class SlotSwapRequest(BaseModel):
    changes: List[SlotChange]

router = APIRouter(prefix="/teams", tags=["teams"])

# Position display order: QB, RB, WR, TE, FLEX, DEF/DST, K; unknown positions last
_POSITION_ORDER = {'QB': 0, 'RB': 1, 'WR': 2, 'TE': 3, 'FLEX': 4, 'DEF': 5, 'DST': 5, 'K': 6}
_BENCH_SLOTS = {'BE', 'IR'}


def _toast_response(message: str, type: str = "success"):
    """Create an empty response with an HX-Trigger header to show a toast."""
    response = HTMLResponse("")
    trigger = json.dumps({"showToast": {"message": message, "type": type}})
    response.headers["HX-Trigger"] = trigger
    return response


@router.get("")
async def list_teams(request: Request, db: Session = Depends(get_db)):
    """List all teams."""
    from pigskin_mastermind.api.main import templates
    teams = db.query(DBTeam).filter(DBTeam.is_user_team == True).order_by(DBTeam.created_at.desc()).all()
    return templates.TemplateResponse(
        "teams/list.html",
        {"request": request, "teams": teams}
    )


@router.get("/new")
async def new_team_form(request: Request):
    """Show new team form modal fragment."""
    from pigskin_mastermind.api.main import templates
    return templates.TemplateResponse(
        "teams/_team_form.html",
        {"request": request}
    )


@router.post("")
async def create_team(
    name: str = Form(...),
    owner: str = Form(...),
    league_id: str = Form(None),
    db: Session = Depends(get_db)
):
    """Create a new team."""
    team = DBTeam(
        team_id=str(uuid.uuid4()),
        name=name,
        owner=owner,
        league_id=league_id or None
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    return RedirectResponse(url="/teams", status_code=303)


@router.get("/{team_db_id}")
async def team_detail(
    request: Request,
    team_db_id: int,
    week: int = Query(None),
    db: Session = Depends(get_db)
):
    """Team detail page with roster. Supports weekly view via ?week=N."""
    from pigskin_mastermind.api.main import templates
    team = db.query(DBTeam).filter(DBTeam.id == team_db_id).first()
    if not team:
        return RedirectResponse(url="/teams", status_code=303)

    # Get available weeks for this team
    available_weeks = (
        db.query(DBWeeklyTeamStats.week)
        .filter(DBWeeklyTeamStats.team_id == team_db_id)
        .order_by(DBWeeklyTeamStats.week)
        .all()
    )
    available_weeks = [w[0] for w in available_weeks]

    weekly_team = None
    weekly_players = []
    matchups = {}

    if week and week in available_weeks:
        # Fetch weekly team stats
        weekly_team = db.query(DBWeeklyTeamStats).filter_by(
            team_id=team_db_id, week=week
        ).first()

        # Fetch weekly player stats with player details
        weekly_players = sorted(
            db.query(DBWeeklyPlayerStats, DBPlayer)
            .join(DBPlayer, DBWeeklyPlayerStats.player_id == DBPlayer.id)
            .filter(DBWeeklyPlayerStats.weekly_team_stats_id == weekly_team.id)
            .all(),
            key=lambda row: (
                1 if row[0].slot_position in _BENCH_SLOTS else 0,
                _POSITION_ORDER.get(row[0].slot_position, _POSITION_ORDER.get(row[1].position, 7)),
                -row[0].actual_points,
            ),
        )

        # Build matchup data for each player
        year = _get_league_year(db, team)
        matchups = _build_matchup_data(db, weekly_players, team_db_id, week, year)

    # Current roster (season view): starters sorted by position order, then projected points
    players = sorted(
        db.query(DBPlayer).filter(DBPlayer.team_id == team_db_id).all(),
        key=lambda p: (_POSITION_ORDER.get(p.position, 7), -p.projected_points),
    )

    return templates.TemplateResponse(
        "teams/detail.html",
        {
            "request": request,
            "team": team,
            "players": players,
            "available_weeks": available_weeks,
            "selected_week": week,
            "weekly_team": weekly_team,
            "weekly_players": weekly_players,
            "matchups": matchups,
        }
    )


@router.delete("/{team_db_id}")
async def delete_team(team_db_id: int, db: Session = Depends(get_db)):
    """Delete a team and its players."""
    team = db.query(DBTeam).filter(DBTeam.id == team_db_id).first()
    if team:
        # Delete associated players first
        db.query(DBPlayer).filter(DBPlayer.team_id == team_db_id).delete()
        db.delete(team)
        db.commit()
    return _toast_response("Team deleted", "info")


@router.get("/{team_db_id}/simulation")
async def team_simulation_page(
    request: Request,
    team_db_id: int,
    week: int = Query(..., ge=1, le=22),
    back: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Full-page team game simulation view for one week."""
    from pigskin_mastermind.api.main import templates

    team = db.query(DBTeam).filter(DBTeam.id == team_db_id).first()
    if not team:
        return RedirectResponse(url="/teams", status_code=303)

    # Determine year from league or fallback to current
    default_year = datetime.utcnow().year
    if team.league_id:
        league = db.query(DBLeague).filter_by(league_id=team.league_id).first()
        if league and league.year:
            default_year = league.year

    # Get active players for the roster sidebar
    active_players = []
    weekly_team = (
        db.query(DBWeeklyTeamStats)
        .filter_by(team_id=team_db_id, week=week)
        .first()
    )
    if weekly_team:
        rows = (
            db.query(DBWeeklyPlayerStats, DBPlayer)
            .join(DBPlayer, DBWeeklyPlayerStats.player_id == DBPlayer.id)
            .filter(DBWeeklyPlayerStats.weekly_team_stats_id == weekly_team.id)
            .all()
        )
        active_players = [
            {
                "id": player.id,
                "name": player.name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "headshot_url": player.headshot_url,
                "slot_position": wp.slot_position,
                "is_active": wp.slot_position not in _BENCH_SLOTS,
            }
            for wp, player in sorted(
                rows,
                key=lambda r: (
                    1 if r[0].slot_position in _BENCH_SLOTS else 0,
                    _POSITION_ORDER.get(r[1].position, 7),
                ),
            )
        ]

    return templates.TemplateResponse(
        "teams/simulation.html",
        {
            "request": request,
            "team": team,
            "week": week,
            "default_year": default_year,
            "roster": active_players,
            "back_url": back,
        },
    )


def _build_matchup_data(db: Session, weekly_players, team_db_id: int, week: int, year: int):
    """Build a dict mapping player_id → matchup info (opponent, def_rank, etc.)."""
    matchups = {}
    for wp, player in weekly_players:
        opponent = None
        def_rank = None

        # Try game log first for opponent
        game_log = (
            db.query(DBPlayerGameLog)
            .filter_by(player_id=player.id, year=year, week=week)
            .first()
        )
        if game_log and game_log.opponent:
            opponent = game_log.opponent

        # Fall back to weekly team stats opponent_name
        if not opponent:
            wt = (
                db.query(DBWeeklyTeamStats)
                .filter_by(team_id=team_db_id, week=week)
                .first()
            )
            if wt and wt.opponent_name:
                opponent = wt.opponent_name

        if opponent:
            pos_lower = player.position.lower()
            rank_field = f'def_rank_vs_{pos_lower}'
            team_def = (
                db.query(DBNFLTeamStats)
                .filter_by(nfl_team=opponent, year=year, week=None)
                .first()
            )
            if team_def:
                def_rank = getattr(team_def, rank_field, None)

        matchups[player.id] = {
            'opponent': opponent,
            'def_rank': def_rank,
        }
    return matchups


def _get_league_year(db: Session, team) -> int:
    """Get the year from the team's league or fall back to current year."""
    if team.league_id:
        league = db.query(DBLeague).filter_by(league_id=team.league_id).first()
        if league and league.year:
            return league.year
    return datetime.utcnow().year


def _render_weekly_lineup(request, db, team, team_db_id, week):
    """Fetch weekly lineup data and render the _weekly_lineup.html fragment."""
    from pigskin_mastermind.api.main import templates

    weekly_team = db.query(DBWeeklyTeamStats).filter_by(
        team_id=team_db_id, week=week
    ).first()

    weekly_players = sorted(
        db.query(DBWeeklyPlayerStats, DBPlayer)
        .join(DBPlayer, DBWeeklyPlayerStats.player_id == DBPlayer.id)
        .filter(DBWeeklyPlayerStats.weekly_team_stats_id == weekly_team.id)
        .all(),
        key=lambda row: (
            1 if row[0].slot_position in _BENCH_SLOTS else 0,
            _POSITION_ORDER.get(row[0].slot_position, _POSITION_ORDER.get(row[1].position, 7)),
            -row[0].actual_points,
        ),
    )

    year = _get_league_year(db, team)
    matchups = _build_matchup_data(db, weekly_players, team_db_id, week, year)

    return templates.TemplateResponse(
        "teams/_weekly_lineup.html",
        {
            "request": request,
            "team": team,
            "selected_week": week,
            "weekly_team": weekly_team,
            "weekly_players": weekly_players,
            "matchups": matchups,
        },
    )


@router.put("/{team_db_id}/weekly/{week}/slots")
async def update_weekly_slots(
    request: Request,
    team_db_id: int,
    week: int,
    body: SlotSwapRequest,
    db: Session = Depends(get_db),
):
    """Update player slot positions for a weekly lineup."""
    team = db.query(DBTeam).filter(DBTeam.id == team_db_id).first()
    if not team:
        return HTMLResponse("Team not found", status_code=404)

    weekly_team = db.query(DBWeeklyTeamStats).filter_by(
        team_id=team_db_id, week=week
    ).first()
    if not weekly_team:
        return HTMLResponse("Weekly data not found", status_code=404)

    # Apply each slot change
    for change in body.changes:
        wp = (
            db.query(DBWeeklyPlayerStats)
            .filter_by(
                player_id=change.player_id,
                weekly_team_stats_id=weekly_team.id,
            )
            .first()
        )
        if wp:
            wp.slot_position = change.new_slot

    db.commit()

    return _render_weekly_lineup(request, db, team, team_db_id, week)


@router.put("/{team_db_id}/weekly/{week}/reset-slots")
async def reset_weekly_slots(
    request: Request,
    team_db_id: int,
    week: int,
    db: Session = Depends(get_db),
):
    """Reset all slot positions back to the original ESPN import values."""
    team = db.query(DBTeam).filter(DBTeam.id == team_db_id).first()
    if not team:
        return HTMLResponse("Team not found", status_code=404)

    weekly_team = db.query(DBWeeklyTeamStats).filter_by(
        team_id=team_db_id, week=week
    ).first()
    if not weekly_team:
        return HTMLResponse("Weekly data not found", status_code=404)

    # Copy espn_slot_position back to slot_position for all players this week
    (
        db.query(DBWeeklyPlayerStats)
        .filter_by(weekly_team_stats_id=weekly_team.id)
        .filter(DBWeeklyPlayerStats.espn_slot_position.isnot(None))
        .update(
            {DBWeeklyPlayerStats.slot_position: DBWeeklyPlayerStats.espn_slot_position},
            synchronize_session='fetch',
        )
    )
    db.commit()

    return _render_weekly_lineup(request, db, team, team_db_id, week)
