"""Leagues routes: listing leagues, viewing league details and teams."""

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session
import json

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBLeague, DBTeam

router = APIRouter(prefix="/leagues", tags=["leagues"])


def _toast_response(message: str, type: str = "success"):
    """Create an empty response with an HX-Trigger header to show a toast."""
    response = HTMLResponse("")
    trigger = json.dumps({"showToast": {"message": message, "type": type}})
    response.headers["HX-Trigger"] = trigger
    return response


@router.get("")
async def list_leagues(request: Request, db: Session = Depends(get_db)):
    """List all leagues."""
    from pigskin_mastermind.api.main import templates
    leagues = db.query(DBLeague).order_by(DBLeague.created_at.desc()).all()
    return templates.TemplateResponse(
        "leagues/list.html",
        {"request": request, "leagues": leagues}
    )


@router.get("/{league_id}")
async def league_detail(request: Request, league_id: str, db: Session = Depends(get_db)):
    """Show league detail page with all teams in the league."""
    from pigskin_mastermind.api.main import templates
    
    league = db.query(DBLeague).filter(DBLeague.league_id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    
    teams = db.query(DBTeam).filter(DBTeam.league_id == league_id).order_by(DBTeam.total_points.desc()).all()
    
    return templates.TemplateResponse(
        "leagues/detail.html",
        {"request": request, "league": league, "teams": teams}
    )


@router.post("/{league_id}/teams/{team_id}/claim")
async def claim_team(league_id: str, team_id: str, db: Session = Depends(get_db)):
    """Toggle the is_user_team status for a team."""
    team = db.query(DBTeam).filter(
        DBTeam.team_id == team_id,
        DBTeam.league_id == league_id
    ).first()
    
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    # Toggle the is_user_team status
    team.is_user_team = not team.is_user_team
    db.commit()
    
    message = f"{'Claimed' if team.is_user_team else 'Unclaimed'} team: {team.name}"
    return _toast_response(message, "success")


@router.post("/{league_id}/import-all-players")
async def import_all_players(
    league_id: str,
    year: int = 2024,
    db: Session = Depends(get_db)
):
    """Import all available players (including free agents) from ESPN for this league.
    
    This endpoint imports the complete player pool from ESPN, not just rostered players.
    This is essential for analyzing waiver wire options and free agents.
    """
    from pigskin_mastermind.services.espn_sync import ESPNSyncService
    
    league = db.query(DBLeague).filter(DBLeague.league_id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    
    try:
        service = ESPNSyncService(db)
        count = service.import_all_players(
            league_id=league_id,
            espn_s2=league.espn_s2,
            swid=league.swid,
            year=year,
            positions=['QB', 'RB', 'WR', 'TE', 'K', 'D/ST'],
            batch_size=500
        )
        
        return _toast_response(f"Successfully imported {count} players", "success")
    except Exception as e:
        return _toast_response(f"Error importing players: {str(e)}", "error")
