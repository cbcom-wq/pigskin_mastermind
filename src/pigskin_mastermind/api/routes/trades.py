"""Trade analyzer routes."""

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam, DBPlayer
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.services.decision_tools import TradeAnalyzer
from pigskin_mastermind.services.season_league import roster_players

router = APIRouter(prefix="/trades", tags=["trades"])


class TradeRequest(BaseModel):
    team_id: int
    gives: List[int]
    receives: List[int]


@router.get("")
async def trade_page(request: Request, db: Session = Depends(get_db)):
    """Trade analyzer page."""
    from pigskin_mastermind.api.main import templates
    teams = db.query(DBTeam).filter(DBTeam.is_user_team == True).order_by(DBTeam.name).all()
    return templates.TemplateResponse(
        "trades/analyzer.html",
        {"request": request, "teams": teams}
    )


@router.get("/team-players")
async def team_players_for_trade(
    request: Request,
    team_id: int,
    db: Session = Depends(get_db)
):
    """Return HTML fragment with a team's players for the Give column."""
    from pigskin_mastermind.api.main import templates

    def _empty(message: str):
        return templates.TemplateResponse(
            "players/_trade_result.html",
            {"request": request, "players": [], "side": "give", "empty_message": message},
        )

    if not team_id:
        return _empty("Select a team above to see your players")

    # Verify this is a user's team
    team = db.query(DBTeam).filter(DBTeam.id == team_id, DBTeam.is_user_team == True).first()
    if not team:
        return _empty("Team not found or not claimed")

    # NOTE: legacy mixed-unit column. The draft pool reads player_projections
    # (services/projection_refresh.py) instead; this route has not been migrated.
    players = sorted(
        roster_players(db, team),
        key=lambda p: (p.position or "", -(p.projected_points or 0.0)),
    )

    if not players:
        return _empty("No players on this team")

    return templates.TemplateResponse(
        "players/_trade_result.html",
        {"request": request, "players": players, "side": "give"},
    )


@router.post("/analyze")
async def analyze_trade(
    request: Request,
    trade: TradeRequest,
    db: Session = Depends(get_db)
):
    """Analyze a trade and return HTML result fragment."""
    from pigskin_mastermind.api.main import templates

    db_team = db.query(DBTeam).filter(DBTeam.id == trade.team_id, DBTeam.is_user_team == True).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found or not claimed")

    gives_players = db.query(DBPlayer).filter(DBPlayer.id.in_(trade.gives)).all()
    receives_players = db.query(DBPlayer).filter(DBPlayer.id.in_(trade.receives)).all()

    # NOTE: legacy mixed-unit column. The draft pool reads player_projections
    # (services/projection_refresh.py) instead; this route has not been migrated.
    gives = [
        Player(
            player_id=p.player_id,
            name=p.name,
            position=p.position,
            team=p.nfl_team,
            projected_points=p.projected_points
        )
        for p in gives_players
    ]

    # NOTE: legacy mixed-unit column. The draft pool reads player_projections
    # (services/projection_refresh.py) instead; this route has not been migrated.
    receives = [
        Player(
            player_id=p.player_id,
            name=p.name,
            position=p.position,
            team=p.nfl_team,
            projected_points=p.projected_points
        )
        for p in receives_players
    ]

    team = Team(
        team_id=db_team.team_id,
        name=db_team.name,
        owner=db_team.owner
    )

    analyzer = TradeAnalyzer()
    result = analyzer.evaluate_trade_for_team(team, gives, receives)

    return templates.TemplateResponse(
        "trades/_trade_result.html",
        {"request": request, "result": result}
    )
