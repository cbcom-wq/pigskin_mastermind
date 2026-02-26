"""Trade analyzer routes."""

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import HTMLResponse
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
    if not team_id:
        return HTMLResponse(
            '<p class="text-sm text-slate-400 text-center py-8">Select a team above to see your players</p>'
        )

    # Verify this is a user's team
    team = db.query(DBTeam).filter(DBTeam.id == team_id, DBTeam.is_user_team == True).first()
    if not team:
        return HTMLResponse(
            '<p class="text-sm text-slate-400 text-center py-8">Team not found or not claimed</p>'
        )

    players = db.query(DBPlayer).filter(
        DBPlayer.team_id == team_id
    ).order_by(DBPlayer.position, DBPlayer.projected_points.desc()).all()

    if not players:
        return HTMLResponse(
            '<p class="text-sm text-slate-400 text-center py-8">No players on this team</p>'
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
            f'    onclick="toggleGivePlayer({p.id}, \'{p.name}\', \'{p.position}\', {p.projected_points:.1f})"'
            f'    data-player-give="{p.id}"'
            f'    class="px-2 py-1 text-xs font-medium rounded-lg bg-slate-100 text-slate-700 hover:bg-slate-200 transition-colors">'
            f'    Select'
            f'  </button>'
            f'</div>'
        )
    return HTMLResponse("\n".join(html_parts))


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
