from fastapi import APIRouter, Depends, Request, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
import uuid
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("")
async def list_teams(request: Request, db: Session = Depends(get_db)):
    """List all teams"""
    from pigskin_mastermind.api.main import templates
    teams = db.query(DBTeam).all()
    return templates.TemplateResponse(
        "teams/list.html",
        {"request": request, "teams": teams}
    )


@router.get("/new")
async def new_team_form(request: Request):
    """Show new team form"""
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
    """Create a new team"""
    team = DBTeam(
        team_id=str(uuid.uuid4()),
        name=name,
        owner=owner,
        league_id=league_id
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    return RedirectResponse(url="/teams", status_code=303)


@router.delete("/{team_id}")
async def delete_team(team_id: int, db: Session = Depends(get_db)):
    """Delete a team"""
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if team:
        db.delete(team)
        db.commit()
    return ""
