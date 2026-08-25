"""When a lineup slot stops being editable.

A player locks at his own game's kickoff, not at a league-wide deadline — which
is what lets someone still swap a Monday-night player on Sunday evening.

Every function takes ``now`` explicitly. Reading the system clock inside would
make lock behavior testable only during an actual NFL game window.
"""

from datetime import datetime
from typing import Dict, Optional, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBNFLGame
from pigskin_mastermind.utils.nfl_teams import normalize_team


class LockIndex:
    """Caches ``(team, week) -> kickoff`` for one year.

    The lineup planner asks once per rostered player, so an uncached lookup
    would be a dozen-plus round trips to answer one lineup.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._by_year_week: Dict[Tuple[int, int], Dict[str, datetime]] = {}

    def _load(self, year: int, week: int) -> Dict[str, datetime]:
        key = (year, week)
        if key not in self._by_year_week:
            kickoffs: Dict[str, datetime] = {}
            rows = self.db.query(
                DBNFLGame.home_team, DBNFLGame.away_team, DBNFLGame.kickoff_at,
            ).filter(DBNFLGame.year == year, DBNFLGame.week == week)
            for home, away, kickoff_at in rows:
                if kickoff_at is None:
                    continue
                for team in (home, away):
                    canonical = normalize_team(team)
                    if canonical:
                        kickoffs[canonical] = kickoff_at
            self._by_year_week[key] = kickoffs
        return self._by_year_week[key]

    def kickoff(
        self, team: Optional[str], year: int, week: int,
    ) -> Optional[datetime]:
        """Kickoff for *team* in *week*, or None for a bye or unknown team."""
        canonical = normalize_team(team)
        if not canonical:
            return None
        return self._load(year, week).get(canonical)

    def is_locked(
        self, team: Optional[str], year: int, week: int, now: datetime,
    ) -> bool:
        """True once *team*'s game has kicked off.

        A team with no scheduled kickoff — on bye, unknown, or a schedule
        imported without times — never locks. Guessing would freeze a lineup
        the user is entitled to change.
        """
        kickoff_at = self.kickoff(team, year, week)
        if kickoff_at is None:
            return False
        return now >= kickoff_at


def first_kickoff(db: Session, year: int, week: int) -> Optional[datetime]:
    """Earliest kickoff of *week*, or None when the week is not scheduled.

    This is the auto-fill deadline: a team with no lineup at all gets one
    filled here so it never scores zero through inattention.
    """
    row = (
        db.query(DBNFLGame.kickoff_at)
        .filter(
            DBNFLGame.year == year,
            DBNFLGame.week == week,
            DBNFLGame.kickoff_at.isnot(None),
        )
        .order_by(DBNFLGame.kickoff_at.asc())
        .first()
    )
    return row[0] if row else None
