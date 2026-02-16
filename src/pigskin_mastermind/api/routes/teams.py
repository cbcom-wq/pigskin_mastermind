"""Teams routes: listing, CRUD, detail page."""

from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
import uuid
import json

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam, DBPlayer

router = APIRouter(prefix="/teams", tags=["teams"])


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
    teams = db.query(DBTeam).order_by(DBTeam.created_at.desc()).all()
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
async def team_detail(request: Request, team_db_id: int, db: Session = Depends(get_db)):
    """Team detail page with roster."""
    from pigskin_mastermind.api.main import templates
    team = db.query(DBTeam).filter(DBTeam.id == team_db_id).first()
    if not team:
        return RedirectResponse(url="/teams", status_code=303)

    players = db.query(DBPlayer).filter(DBPlayer.team_id == team_db_id).order_by(
        DBPlayer.position, DBPlayer.projected_points.desc()
    ).all()

    return templates.TemplateResponse(
        "teams/detail.html",
        {"request": request, "team": team, "players": players}
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
