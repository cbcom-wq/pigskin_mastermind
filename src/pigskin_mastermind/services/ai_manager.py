"""Run the deterministic planner for teams that manage themselves.

Deliberately thin, and deliberately ignoring ``DBTeam.ai_profile``. A team's
draft persona shaped which players it owns — that is where personality belongs.
Letting an "aggressive" profile shade a start/sit decision would make lineups
non-reproducible and buy nothing: there is no aggressive way to start your
highest projected players.
"""

import logging
from datetime import datetime
from typing import Any, Dict

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBLeague, DBLineupSlot, DBTeam
from pigskin_mastermind.services.lineup_manager import apply_plan, plan_lineup

logger = logging.getLogger(__name__)


def _has_any_lineup(db: Session, team_id: int, year: int, week: int) -> bool:
    return db.query(
        db.query(DBLineupSlot)
        .filter_by(team_id=team_id, year=year, week=week)
        .exists()
    ).scalar()


def _set_for_teams(
    db: Session, league: DBLeague, teams, week: int, now: datetime, set_by: str,
) -> Dict[str, Any]:
    handled = 0
    skipped = 0
    for team in teams:
        try:
            plan = plan_lineup(db, team, league.year, week, now, league=league)
            if not plan.decisions:
                skipped += 1
                continue
            apply_plan(db, plan, set_by=set_by)
        except Exception:
            # One bad team must not stop the rest of the league from being set.
            # The rollback is load-bearing: apply_plan commits internally, and a
            # failed commit leaves the session needing rollback -- without this,
            # the NEXT team's plan_lineup raises PendingRollbackError and the
            # loop dies uncounted, which is precisely what this block exists to
            # prevent.
            logger.exception(
                "Lineup set failed for team %s week %s", team.id, week,
            )
            db.rollback()
            skipped += 1
            continue
        handled += 1
    return {"teams": handled, "skipped": skipped, "week": week}


def set_ai_lineups(
    db: Session, league: DBLeague, week: int, now: datetime,
) -> Dict[str, Any]:
    """Set every AI team's lineup for *week*."""
    teams = (
        db.query(DBTeam)
        .filter(DBTeam.league_id == league.league_id, DBTeam.manager_type == "ai")
        .order_by(DBTeam.id)
        .all()
    )
    return _set_for_teams(db, league, teams, week, now, set_by="ai")


def autofill_missing_lineups(
    db: Session, league: DBLeague, week: int, now: datetime,
) -> Dict[str, Any]:
    """Fill lineups for teams that have set none at all.

    Only teams with zero rows for the week qualify. A partially set lineup is
    left exactly as its manager left it — this is a floor against forgetting,
    not a second opinion.
    """
    teams = [
        team
        for team in db.query(DBTeam)
        .filter(DBTeam.league_id == league.league_id)
        .order_by(DBTeam.id)
        .all()
        if not _has_any_lineup(db, team.id, league.year, week)
    ]
    return _set_for_teams(db, league, teams, week, now, set_by="auto")
