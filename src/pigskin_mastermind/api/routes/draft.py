"""Mock draft routes: setup, interactive picking, and simulation."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Dict, List, Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBPlayer
from pigskin_mastermind.services.mock_draft import (
    DraftStrategy,
    MockDraftEngine,
    draft_engine,
)

router = APIRouter(prefix="/draft", tags=["draft"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class StartDraftRequest(BaseModel):
    num_teams: int = Field(10, ge=2, le=20)
    num_rounds: int = Field(15, ge=1, le=20)
    user_pick_position: int = Field(1, ge=1, le=20)
    ai_strategies: Optional[Dict[str, str]] = None
    use_db_players: bool = False
    position_by_round: Optional[Dict[str, str]] = None


class UserPickRequest(BaseModel):
    draft_id: str
    player_id: str


class SimulationRequest(BaseModel):
    num_teams: int = Field(10, ge=2, le=20)
    num_rounds: int = Field(15, ge=1, le=20)
    strategies: Optional[Dict[str, str]] = None
    num_simulations: int = Field(5, ge=1, le=20)
    use_db_players: bool = False
    position_by_round: Optional[Dict[str, str]] = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _load_db_players(db: Session) -> List[dict]:
    """Load all DB players and convert to draft pool format."""
    db_players = (
        db.query(DBPlayer)
        .order_by(DBPlayer.projected_points.desc())
        .all()
    )
    return [
        {
            "id": str(p.player_id),
            "name": p.name,
            "position": p.position,
            "nfl_team": p.nfl_team,
            "projected_points": p.projected_points or 0.0,
        }
        for p in db_players
        if p.position in ("QB", "RB", "WR", "TE", "K", "DEF")
    ]


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------


@router.get("")
async def draft_home(request: Request, db: Session = Depends(get_db)):
    """Draft setup / home page."""
    from pigskin_mastermind.api.main import templates

    strategies = [
        {"value": s.value, "label": s.value.replace("_", " ").title(), "desc": desc}
        for s, desc in DraftStrategy.descriptions().items()
    ]
    return templates.TemplateResponse(
        "draft/index.html",
        {"request": request, "strategies": strategies},
    )


@router.get("/simulate")
async def simulation_page(request: Request, db: Session = Depends(get_db)):
    """Draft simulation setup page."""
    from pigskin_mastermind.api.main import templates

    strategies = [
        {"value": s.value, "label": s.value.replace("_", " ").title(), "desc": desc}
        for s, desc in DraftStrategy.descriptions().items()
    ]
    return templates.TemplateResponse(
        "draft/simulate.html",
        {"request": request, "strategies": strategies},
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@router.post("/start")
async def start_draft(req: StartDraftRequest, db: Session = Depends(get_db)):
    """Create a new interactive mock draft and return its initial state."""
    player_pool = _load_db_players(db) if req.use_db_players else None

    # Convert position_by_round keys to int
    pbr: Optional[Dict[int, str]] = None
    if req.position_by_round:
        pbr = {int(k): v for k, v in req.position_by_round.items()}

    try:
        state = draft_engine.create_draft(
            num_teams=req.num_teams,
            num_rounds=req.num_rounds,
            user_pick_position=req.user_pick_position,
            ai_strategies=req.ai_strategies,
            player_pool=player_pool,
            position_by_round=pbr,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Auto-advance AI picks before user's first turn
    internal = draft_engine._drafts[state["draft_id"]]
    draft_engine._advance_ai_picks(internal)
    state = draft_engine._public_state(internal)

    return state


@router.get("/board/{draft_id}")
async def get_draft_board(
    request: Request, draft_id: str, db: Session = Depends(get_db)
):
    """Return the HTML draft board page for an ongoing interactive draft."""
    from pigskin_mastermind.api.main import templates

    state = draft_engine.get_draft(draft_id)
    if not state:
        raise HTTPException(status_code=404, detail="Draft not found")

    return templates.TemplateResponse(
        "draft/board.html",
        {"request": request, "state": state},
    )


@router.get("/state/{draft_id}")
async def get_draft_state(draft_id: str):
    """Return the current JSON state of a draft."""
    state = draft_engine.get_draft(draft_id)
    if not state:
        raise HTTPException(status_code=404, detail="Draft not found")
    return state


@router.post("/pick")
async def make_pick(req: UserPickRequest):
    """Register the user's pick and auto-advance AI picks."""
    try:
        state = draft_engine.make_user_pick(req.draft_id, req.player_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return state


@router.post("/run-simulation")
async def run_simulation(req: SimulationRequest, db: Session = Depends(get_db)):
    """Run automated draft simulations and return aggregated results."""
    player_pool = _load_db_players(db) if req.use_db_players else None

    pbr: Optional[Dict[int, str]] = None
    if req.position_by_round:
        pbr = {int(k): v for k, v in req.position_by_round.items()}

    engine = MockDraftEngine()
    results = engine.run_simulations(
        num_teams=req.num_teams,
        num_rounds=req.num_rounds,
        strategies=req.strategies,
        num_simulations=req.num_simulations,
        player_pool=player_pool,
        position_by_round=pbr,
    )
    return results


@router.get("/results")
async def simulation_results_page(request: Request):
    """Simulation results page (populated via JS after /run-simulation)."""
    from pigskin_mastermind.api.main import templates

    return templates.TemplateResponse(
        "draft/results.html",
        {"request": request},
    )
