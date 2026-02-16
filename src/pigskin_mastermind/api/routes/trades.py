from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam, DBPlayer
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.services.decision_tools import TradeAnalyzer

router = APIRouter(prefix="/trades", tags=["trades"])


class TradeRequest(BaseModel):
    team_id: int
    gives: List[int]
    receives: List[int]


@router.get("")
async def trade_page(request: Request, db: Session = Depends(get_db)):
    """Trade analyzer page"""
    from pigskin_mastermind.api.main import templates
    teams = db.query(DBTeam).all()
    return templates.TemplateResponse(
        "trades/analyzer.html",
        {"request": request, "teams": teams}
    )


@router.post("/analyze")
async def analyze_trade(
    request: Request,
    trade: TradeRequest,
    db: Session = Depends(get_db)
):
    """Analyze a trade"""
    from pigskin_mastermind.api.main import templates
    db_team = db.query(DBTeam).filter(DBTeam.id == trade.team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    gives_players = db.query(DBPlayer).filter(DBPlayer.id.in_(trade.gives)).all()
    receives_players = db.query(DBPlayer).filter(DBPlayer.id.in_(trade.receives)).all()

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
