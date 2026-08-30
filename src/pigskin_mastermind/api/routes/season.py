"""Season league routes.

Committing a draft lives here rather than in the CLI because ``draft_engine`` is
an in-process singleton: a draft created by this server is invisible to any
other process.
"""

from typing import Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, Request,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import (
    DBLeague, DBManagerRun, DBMatchup, DBTeam,
)
from pigskin_mastermind.services.season_agent import (
    LineupRejected, apply_proposal, run_agent_subprocess, start_manager_run,
)
from pigskin_mastermind.services.season_league import (
    DraftCommitError, SeasonLeagueService,
)

router = APIRouter(prefix="/season", tags=["season"])


class CommitDraftRequest(BaseModel):
    draft_id: str
    name: str
    user_team_name: str
    owner: str = "Me"
    year: Optional[int] = None


@router.post("/commit-draft")
async def commit_draft(req: CommitDraftRequest, db: Session = Depends(get_db)):
    """Turn a completed draft into a persisted season league."""
    service = SeasonLeagueService(db)
    try:
        league = service.create_from_draft(
            req.draft_id,
            name=req.name,
            user_team_name=req.user_team_name,
            owner=req.owner,
            year=req.year,
        )
    except DraftCommitError as exc:
        db.rollback()
        if exc.code == "unresolved":
            # 422 rather than 400: the request was well-formed, the draft
            # simply contains players this database has never seen.
            raise HTTPException(
                status_code=422,
                detail={"message": str(exc), "unresolved": exc.unresolved},
            )
        if exc.code == "not_found":
            raise HTTPException(status_code=404, detail=str(exc))
        raise HTTPException(status_code=400, detail=str(exc))

    team_count = db.query(DBTeam).filter_by(league_id=league.league_id).count()
    return {
        "league_id": league.league_id,
        "name": league.name,
        "teams": team_count,
        "redirect_url": f"/season/{league.league_id}",
    }


# NOTE (Ruling P2): every route below is literal-prefixed (`/runs/...`) or has
# a literal first segment. They MUST stay registered ahead of the catch-all
# `GET /{league_key}` — FastAPI matches in registration order, so a
# `/{league_key}` declared earlier would swallow `/season/runs/5` as
# league_key="runs".
@router.post("/{league_key}/teams/{team_id}/manage")
async def manage_team(
    league_key: str,
    team_id: int,
    background: BackgroundTasks,
    week: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Ask Claude to propose a lineup. Returns immediately with a run id."""
    league = db.query(DBLeague).filter_by(league_id=league_key).first()
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")
    team = db.query(DBTeam).filter_by(id=team_id, league_id=league_key).first()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    target_week = week or league.current_week or 1
    try:
        run = start_manager_run(db, team, target_week, year=league.year)
    except LineupRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    background.add_task(
        run_agent_subprocess, run.id, team.id, league.year, target_week,
    )
    return {"run_id": run.id, "status": run.status, "week": target_week}


@router.get("/runs/{run_id}")
async def get_run(request: Request, run_id: int, db: Session = Depends(get_db)):
    """Poll target for the proposal. Renders an HTMX fragment."""
    from pigskin_mastermind.api.main import templates

    run = db.query(DBManagerRun).filter_by(id=run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    return templates.TemplateResponse(
        "season/_proposal.html",
        {"request": request, "run": run},
    )


@router.post("/runs/{run_id}/apply")
async def apply_run(run_id: int, db: Session = Depends(get_db)):
    """Accept a proposed lineup."""
    run = db.query(DBManagerRun).filter_by(id=run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    try:
        written = apply_proposal(db, run)
    except LineupRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"run_id": run.id, "status": run.status, "slots": written}


@router.post("/runs/{run_id}/discard")
async def discard_run(run_id: int, db: Session = Depends(get_db)):
    """Reject a proposed lineup. Nothing is written."""
    run = db.query(DBManagerRun).filter_by(id=run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "proposed":
        run.status = "discarded"
        db.commit()
    return {"run_id": run.id, "status": run.status}


def _standings(db: Session, league_key: str):
    teams = db.query(DBTeam).filter_by(league_id=league_key).all()
    return sorted(
        teams, key=lambda t: (-(t.wins or 0), -(t.total_points or 0.0), t.id),
    )


# NOTE (Ruling P2): `/{league_key}` is a CATCH-ALL. Everything with a literal
# first segment — `/commit-draft`, `/runs/...` — must already be registered
# above this point, or FastAPI will match this route first and read the literal
# as a league key. tests/integration/test_api_season_pages.py has a regression
# test for exactly that. Add new literal routes ABOVE, never below.
@router.get("/{league_key}")
async def league_home(
    request: Request, league_key: str, db: Session = Depends(get_db),
):
    """Standings and the current week's matchups."""
    from pigskin_mastermind.api.main import templates

    league = db.query(DBLeague).filter_by(league_id=league_key).first()
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")

    week = league.current_week or 1
    matchups = (
        db.query(DBMatchup)
        .filter_by(league_id=league.id, year=league.year, week=week)
        .order_by(DBMatchup.bracket_slot)
        .all()
    )
    teams = {t.id: t for t in db.query(DBTeam).filter_by(league_id=league_key)}

    return templates.TemplateResponse(
        "season/detail.html",
        {
            "request": request, "league": league, "week": week,
            "standings": _standings(db, league_key),
            "matchups": matchups, "teams": teams,
        },
    )


@router.get("/{league_key}/scoreboard/{week}")
async def scoreboard(
    request: Request, league_key: str, week: int, db: Session = Depends(get_db),
):
    """Every matchup in one week, with live points."""
    from pigskin_mastermind.api.main import templates

    league = db.query(DBLeague).filter_by(league_id=league_key).first()
    if league is None:
        raise HTTPException(status_code=404, detail="League not found")

    matchups = (
        db.query(DBMatchup)
        .filter_by(league_id=league.id, year=league.year, week=week)
        .order_by(DBMatchup.bracket_slot)
        .all()
    )
    teams = {t.id: t for t in db.query(DBTeam).filter_by(league_id=league_key)}

    return templates.TemplateResponse(
        "season/scoreboard.html",
        {
            "request": request, "league": league, "week": week,
            "matchups": matchups, "teams": teams,
        },
    )
