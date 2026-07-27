"""Mock draft routes: setup, interactive picking, and simulation."""

import random

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import (
    DBLeague,
    DBPlayer,
    DBTeam,
    DBWeeklyPlayerStats,
    DBWeeklyTeamStats,
)
from pigskin_mastermind.services.adp_service import ADPService
from pigskin_mastermind.services.mock_draft import (
    AIProfile,
    DEFAULT_LINEUP_SLOTS,
    DraftStrategy,
    LINEUP_PRESETS,
    MockDraftEngine,
    draft_engine,
    fetch_espn_adp,
    randomize_ai_profiles,
)
from pigskin_mastermind.utils.season import current_fantasy_season

router = APIRouter(prefix="/draft", tags=["draft"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class StartDraftRequest(BaseModel):
    num_teams: int = Field(10, ge=2, le=20)
    num_rounds: int = Field(15, ge=1, le=20)
    user_pick_position: Optional[int] = Field(1, ge=1, le=20)
    randomize_user_pick_position: bool = False
    ai_strategies: Optional[Dict[str, str]] = None
    ai_profiles: Optional[Dict[str, Dict[str, float]]] = None
    ai_aggressiveness: float = Field(0.5, ge=0.0, le=1.0)
    use_espn_adp: bool = False
    use_ffc_adp: bool = False
    espn_adp_year: Optional[int] = Field(None, ge=2019, le=2035)
    ffc_scoring: str = Field("half-ppr", description="FFC scoring format slug (standard, half-ppr, ppr)")
    position_by_round: Optional[Dict[str, str]] = None
    player_pool: Optional[List[Dict[str, Any]]] = None
    lineup_slots: Optional[Dict[str, int]] = None


class RandomizeRequest(BaseModel):
    num_teams: int = Field(10, ge=2, le=20)
    user_pick_position: Optional[int] = Field(1, ge=1, le=20)
    overall_aggressiveness: float = Field(0.5, ge=0.0, le=1.0)


class UserPickRequest(BaseModel):
    draft_id: str
    player_id: str


class SimulationRequest(BaseModel):
    num_teams: int = Field(10, ge=2, le=20)
    num_rounds: int = Field(15, ge=1, le=20)
    strategies: Optional[Dict[str, str]] = None
    num_simulations: int = Field(5, ge=1, le=20)
    position_by_round: Optional[Dict[str, str]] = None
    player_pool: Optional[List[Dict[str, Any]]] = None
    lineup_slots: Optional[Dict[str, int]] = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _enrich_with_headshots(players: List[dict], db: Session) -> List[dict]:
    """Attach headshot_url to each player by matching on normalised name.

    Players already carrying a non-empty headshot_url are left unchanged.
    """
    # Only query if there are players that still need headshots
    needs = [p for p in players if not p.get("headshot_url")]
    if not needs:
        return players

    db_rows = db.query(DBPlayer.name, DBPlayer.headshot_url).filter(
        DBPlayer.headshot_url.isnot(None),
        DBPlayer.headshot_url != "",
    ).all()

    # Build a normalised name → URL lookup (lowercase, strip whitespace)
    headshot_map: Dict[str, str] = {
        row.name.strip().lower(): row.headshot_url
        for row in db_rows
        if row.headshot_url
    }

    for p in players:
        if not p.get("headshot_url"):
            p["headshot_url"] = headshot_map.get(p["name"].strip().lower(), "")

    return players


def _has_meaningful_adp(players: List[dict]) -> bool:
    """Return True when the player list has varied ADP values.

    ESPN returns a uniform default ADP (e.g. 170.0) for every player when
    season data is not yet available.  We consider data *meaningful* only
    when there are at least two distinct ``adp_rank`` values.
    """
    adp_values = {p.get("adp_rank") for p in players}
    return len(adp_values) > 1


def _fetch_espn_adp_with_fallback(
    year: int, limit: int = 300
) -> tuple[Optional[List[dict]], int]:
    """Fetch ESPN ADP data, falling back to the prior year if needed.

    When the requested season has only placeholder ADP values (all identical),
    the previous year's data is tried automatically.

    Returns:
        A ``(players, actual_year)`` tuple.  ``players`` is ``None`` when both
        the requested year *and* the fallback fail.
    """
    players = fetch_espn_adp(year=year, limit=limit)
    if players and not _has_meaningful_adp(players):
        fallback = fetch_espn_adp(year=year - 1, limit=limit)
        if fallback and _has_meaningful_adp(fallback):
            return fallback, year - 1
    return players, year


def _slot_to_lineup_key(slot_position: Optional[str]) -> Optional[str]:
    """Map stored weekly slot labels to draft lineup slot keys."""
    if not slot_position:
        return None
    slot = slot_position.strip().upper()
    slot_map = {
        "QB": "QB",
        "RB": "RB",
        "WR": "WR",
        "TE": "TE",
        "FLEX": "FLEX",
        "RB/WR/TE": "FLEX",
        "OP": "SUPERFLEX",
        "SUPERFLEX": "SUPERFLEX",
        "K": "K",
        "D/ST": "DEF",
        "DST": "DEF",
        "DEF": "DEF",
    }
    return slot_map.get(slot)


def _derive_roster_slots_from_imported_roster(
    league_identifier: str,
    db: Session,
) -> Optional[Dict[str, int]]:
    """Infer starting slot counts from locally stored weekly roster data.

    Prefers the user's team (``is_user_team``) and uses the most recent week
    with weekly player slot data.
    """
    teams = db.query(DBTeam).filter(DBTeam.league_id == league_identifier).all()
    if not teams:
        return None

    ordered_teams = sorted(
        teams,
        key=lambda team: (
            0 if team.is_user_team else 1,
            -int(team.last_synced_at.timestamp()) if team.last_synced_at else 0,
        ),
    )

    for team in ordered_teams:
        latest_weekly = (
            db.query(DBWeeklyTeamStats.id)
            .filter(DBWeeklyTeamStats.team_id == team.id)
            .order_by(DBWeeklyTeamStats.week.desc())
            .first()
        )
        if not latest_weekly:
            continue
        latest_weekly_id = latest_weekly[0]

        rows = (
            db.query(DBWeeklyPlayerStats.slot_position)
            .filter(DBWeeklyPlayerStats.weekly_team_stats_id == latest_weekly_id)
            .all()
        )
        counts: Dict[str, int] = {}
        for row in rows:
            key = _slot_to_lineup_key(row.slot_position)
            if key:
                counts[key] = counts.get(key, 0) + 1

        if counts:
            return counts

    return None


def _derive_scoring_format(scoring_settings: Optional[Dict[str, Any]]) -> str:
    """Derive an FFC-compatible scoring format slug from league scoring settings.

    Maps the ``rec`` (reception points) value to the corresponding ADP format:
    - 0  → ``standard``
    - 0 < rec < 1 → ``half-ppr``
    - ≥ 1 → ``ppr``
    """
    if not scoring_settings:
        return "half-ppr"
    rec = float(scoring_settings.get("rec", 0.5))
    if rec >= 1.0:
        return "ppr"
    if rec > 0:
        return "half-ppr"
    return "standard"


def _build_league_presets(db: Session) -> List[Dict[str, Any]]:
    """Build league preset payloads using saved slots or inferred local data."""
    leagues = db.query(DBLeague).order_by(DBLeague.created_at.desc()).all()
    league_presets: List[Dict[str, Any]] = []

    for league in leagues:
        inferred_slots = None
        if not league.roster_slots:
            inferred_slots = _derive_roster_slots_from_imported_roster(
                league_identifier=league.league_id,
                db=db,
            )

        resolved_slots = league.roster_slots or inferred_slots or dict(DEFAULT_LINEUP_SLOTS)
        league_presets.append(
            {
                "id": league.id,
                "league_id": league.league_id,
                "name": league.name,
                "year": league.year,
                "roster_slots": resolved_slots,
                "has_custom_slots": bool(league.roster_slots or inferred_slots),
                "scoring_format": _derive_scoring_format(league.scoring_settings),
            }
        )

    return league_presets


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
    league_presets = _build_league_presets(db)
    return templates.TemplateResponse(
        "draft/index.html",
        {
            "request": request,
            "strategies": strategies,
            "lineup_presets": list(LINEUP_PRESETS.values()),
            "league_presets": league_presets,
            "current_season": current_fantasy_season(),
        },
    )


@router.get("/lineup-presets")
async def get_lineup_presets(db: Session = Depends(get_db)):
    """Return built-in lineup format presets plus any league roster slots imported by the user."""
    league_presets = _build_league_presets(db)
    return {
        "presets": [
            {"key": key, **preset}
            for key, preset in LINEUP_PRESETS.items()
        ],
        "league_presets": league_presets,
    }


@router.get("/simulate")
async def simulation_page(request: Request, db: Session = Depends(get_db)):
    """Draft simulation setup page."""
    from pigskin_mastermind.api.main import templates

    strategies = [
        {"value": s.value, "label": s.value.replace("_", " ").title(), "desc": desc}
        for s, desc in DraftStrategy.descriptions().items()
    ]
    league_presets = _build_league_presets(db)
    return templates.TemplateResponse(
        "draft/simulate.html",
        {
            "request": request,
            "strategies": strategies,
            "lineup_presets": list(LINEUP_PRESETS.values()),
            "league_presets": league_presets,
            "current_season": current_fantasy_season(),
        },
    )


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


@router.post("/start")
async def start_draft(req: StartDraftRequest, db: Session = Depends(get_db)):
    """Create a new interactive mock draft and return its initial state."""
    resolved_user_pick_position = req.user_pick_position
    if req.randomize_user_pick_position or resolved_user_pick_position is None:
        resolved_user_pick_position = random.randint(1, req.num_teams)

    adp_year = req.espn_adp_year or current_fantasy_season()
    player_pool: Optional[List[dict]] = None
    if req.use_ffc_adp:
        adp_svc = ADPService(db)
        player_pool = adp_svc.get_adp_for_draft_pool(year=adp_year)
        if not player_pool:
            import_result = adp_svc.import_from_ffc(year=adp_year, scoring=req.ffc_scoring)
            if import_result.get("error"):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "No local FFC ADP data and automatic import failed. "
                        f"{import_result['error']}"
                    ),
                )

            player_pool = adp_svc.get_adp_for_draft_pool(year=adp_year)
            if not player_pool:
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "FFC ADP import completed but no players matched your local DB. "
                        "Import or sync players first, then retry."
                    ),
                )
    elif req.use_espn_adp:
        player_pool, _ = _fetch_espn_adp_with_fallback(year=adp_year)
        if player_pool is None:
            raise HTTPException(
                status_code=503,
                detail="Failed to fetch ADP data from ESPN. Check connectivity or try again.",
            )
        player_pool = _enrich_with_headshots(player_pool, db)
    elif req.player_pool:
        player_pool = req.player_pool

    # Convert position_by_round keys to int
    pbr: Optional[Dict[int, str]] = None
    if req.position_by_round:
        pbr = {int(k): v for k, v in req.position_by_round.items()}

    # Build ai_profiles from explicit profiles or aggressiveness knob
    profiles: Optional[Dict[str, Dict[str, Any]]] = None
    if req.ai_profiles:
        profiles = {k: v for k, v in req.ai_profiles.items()}
    elif req.ai_aggressiveness != 0.5:
        # Generate per-slot profiles based on overall aggressiveness
        random_result = randomize_ai_profiles(
            num_teams=req.num_teams,
            user_pick_position=resolved_user_pick_position,
            overall_aggressiveness=req.ai_aggressiveness,
        )
        profiles = {
            k: v["profile"] for k, v in random_result.items()
        }

    try:
        state = draft_engine.create_draft(
            num_teams=req.num_teams,
            num_rounds=req.num_rounds,
            user_pick_position=resolved_user_pick_position,
            ai_strategies=req.ai_strategies,
            player_pool=player_pool,
            position_by_round=pbr,
            ai_profiles=profiles,
            lineup_slots=req.lineup_slots or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Frontend uses /draft/advance for staggered AI picks
    return state


@router.post("/randomize-strategies")
async def randomize_strategies(req: RandomizeRequest):
    """Generate randomised AI strategies and profiles for all non-user slots.

    Returns a dict mapping slot string → {strategy, profile}.
    """
    user_pick_position = req.user_pick_position
    if user_pick_position is None:
        user_pick_position = random.randint(1, req.num_teams)

    return randomize_ai_profiles(
        num_teams=req.num_teams,
        user_pick_position=user_pick_position,
        overall_aggressiveness=req.overall_aggressiveness,
    )


@router.get("/adp")
async def get_draft_adp(
    year: Optional[int] = None,
    limit: int = 300,
    source: str = "ffc",
    scoring: str = "half-ppr",
    refresh: bool = False,
    db: Session = Depends(get_db),
):
    """Return ADP-ordered player rankings for the draft page.

    Supports two sources:
    - ``ffc`` (default): Locally stored Fantasy Football Calculator data.
    - ``espn``: Live fetch from ESPN public API (legacy fallback).

    Args:
        year: Fantasy football season year (default: current season).
        limit: Maximum players to return (default 300, max 500).
        source: ADP data source (``ffc`` or ``espn``).
        scoring: Scoring format slug for FFC source (``standard``, ``half-ppr``,
            ``ppr``). Ignored for ESPN source.
        refresh: Force a re-import from FFC before reading (``ffc`` source only).

    Returns:
        JSON with ``players`` list ordered by ADP, ``source`` label, and
        (for FFC) ``last_updated`` / ``total_in_db`` freshness metadata.
    """
    year = year or current_fantasy_season()
    limit = max(1, min(limit, 500))

    if source == "ffc":
        adp_svc = ADPService(db)
        import_result: Optional[dict] = None

        if refresh:
            import_result = adp_svc.import_from_ffc(year=year, scoring=scoring)
            if import_result.get("error"):
                raise HTTPException(status_code=503, detail=import_result["error"])

        all_players = adp_svc.get_adp_for_draft_pool(year=year)
        if not all_players and not refresh:
            # Bootstrap: no local data for this season yet — import once.
            import_result = adp_svc.import_from_ffc(year=year, scoring=scoring)
            if import_result.get("error"):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "No local FFC ADP data and automatic import failed. "
                        f"{import_result['error']}"
                    ),
                )
            all_players = adp_svc.get_adp_for_draft_pool(year=year)

        players = all_players[:limit]
        if not players:
            raise HTTPException(
                status_code=404,
                detail=(
                    "FFC ADP data is available but no players matched your local DB. "
                    "Import or sync players first."
                ),
            )
        metadata = adp_svc.get_adp_metadata(year=year)
        resp = {
            "source": "fantasyfootballcalculator",
            "year": year,
            "scoring": scoring,
            "count": len(players),
            "players": players,
            "last_updated": metadata["last_updated"],
            "total_in_db": metadata["count"],
        }
        if import_result:
            resp["imported"] = import_result.get("imported", 0)
            resp["created"] = import_result.get("created", 0)
        return resp

    # Legacy ESPN fallback
    players, actual_year = _fetch_espn_adp_with_fallback(year=year, limit=limit)
    if players is None:
        raise HTTPException(
            status_code=503,
            detail="Failed to fetch ADP data from ESPN. Check connectivity or try again later.",
        )
    resp: dict = {"source": "espn", "year": actual_year, "count": len(players), "players": players}
    if actual_year != year:
        resp["fallback"] = True
        resp["requested_year"] = year
    return resp


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
    """Register the user's pick. AI picks are NOT auto-advanced; use /draft/advance."""
    try:
        state = draft_engine.make_user_pick(
            req.draft_id, req.player_id, advance_ai=False
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Attach commentary for the user's pick
    if state.get("picks_log"):
        latest_pick = state["picks_log"][-1]
        items = draft_engine.generate_commentary(
            state["picks_log"], state["available_players"], latest_pick
        )
        state["commentary"] = items

    return state


class AdvancePickRequest(BaseModel):
    draft_id: str


@router.post("/advance")
async def advance_one_pick(req: AdvancePickRequest):
    """Advance exactly one AI pick and return the updated state.

    The frontend calls this in a staggered loop to create a live-draft feel.
    Returns 400 if it is the user's turn or the draft is complete.
    """
    state = draft_engine.advance_one_ai_pick(req.draft_id)
    if state is None:
        raise HTTPException(
            status_code=400,
            detail="Cannot advance: user's turn or draft complete",
        )

    # Generate commentary for the new pick
    if state.get("picks_log"):
        latest_pick = state["picks_log"][-1]
        items = draft_engine.generate_commentary(
            state["picks_log"], state["available_players"], latest_pick
        )
        state["commentary"] = items

    return state


@router.get("/grade/{draft_id}")
async def get_draft_grade(draft_id: str):
    """Return detailed draft grades and pick-by-pick analysis.

    Only available after the draft is complete.
    """
    result = draft_engine.grade_draft(draft_id)
    if result is None:
        raise HTTPException(
            status_code=400,
            detail="Draft not found or not yet complete",
        )
    return result


@router.post("/run-simulation")
async def run_simulation(req: SimulationRequest):
    """Run automated draft simulations and return aggregated results."""
    player_pool = req.player_pool if req.player_pool else None

    pbr: Optional[Dict[int, str]] = None
    if req.position_by_round:
        pbr = {int(k): v for k, v in req.position_by_round.items()}

    engine = MockDraftEngine()
    try:
        results = engine.run_simulations(
            num_teams=req.num_teams,
            num_rounds=req.num_rounds,
            strategies=req.strategies,
            num_simulations=req.num_simulations,
            player_pool=player_pool,
            position_by_round=pbr,
            lineup_slots=req.lineup_slots or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return results


@router.get("/results")
async def simulation_results_page(request: Request):
    """Simulation results page (populated via JS after /run-simulation)."""
    from pigskin_mastermind.api.main import templates

    return templates.TemplateResponse(
        "draft/results.html",
        {"request": request},
    )
