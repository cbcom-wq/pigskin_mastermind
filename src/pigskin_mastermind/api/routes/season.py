"""Season league routes.

Committing a draft lives here rather than in the CLI because ``draft_engine`` is
an in-process singleton: a draft created by this server is invisible to any
other process.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam
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
