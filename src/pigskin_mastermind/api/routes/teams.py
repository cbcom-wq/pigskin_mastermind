"""Teams routes: listing, CRUD, detail page."""

from fastapi import APIRouter, Depends, Request, Form, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
import uuid
import json

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam, DBPlayer, DBWeeklyTeamStats, DBWeeklyPlayerStats

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
                _POSITION_ORDER.get(row[1].position, 7),
                -row[0].actual_points,
            ),
        )

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
