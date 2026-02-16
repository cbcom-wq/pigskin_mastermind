from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam, DBPlayer
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.services.decision_tools import LineupOptimizer

router = APIRouter(prefix="/lineups", tags=["lineups"])


def _db_team_to_domain(db_team, db_players):
    """Convert DB models to domain models for service layer."""
    players = [
        Player(
            player_id=p.player_id,
            name=p.name,
            position=p.position,
            team=p.nfl_team,
            projected_points=p.projected_points,
            actual_points=p.actual_points,
            stats=p.stats or {}
        )
        for p in db_players
    ]
    return Team(
        team_id=db_team.team_id,
        name=db_team.name,
        owner=db_team.owner,
        players=players,
        record={"wins": db_team.wins, "losses": db_team.losses, "ties": db_team.ties},
        total_points=db_team.total_points,
        league_id=db_team.league_id
    )


@router.get("")
async def lineup_page(request: Request, db: Session = Depends(get_db)):
    """Lineup optimizer page"""
    from pigskin_mastermind.api.main import templates
    teams = db.query(DBTeam).all()
    return templates.TemplateResponse(
        "lineups/optimizer.html",
        {"request": request, "teams": teams}
    )


@router.post("/{team_id}/optimize")
async def optimize_lineup(
    request: Request,
    team_id: int,
    db: Session = Depends(get_db)
):
    """Optimize lineup for a team"""
    from pigskin_mastermind.api.main import templates
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    db_players = db.query(DBPlayer).filter(DBPlayer.team_id == team_id).all()
    team = _db_team_to_domain(db_team, db_players)

    optimizer = LineupOptimizer()
    result = optimizer.optimize_lineup(team)

    return templates.TemplateResponse(
        "lineups/_lineup_result.html",
        {"request": request, "result": result, "team": team}
    )
