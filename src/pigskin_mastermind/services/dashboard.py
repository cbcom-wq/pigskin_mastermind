"""Everything the front page knows, derived once.

The dashboard reads from eight services. Doing that in the route handler is
how the previous version stayed shallow — each new fact meant another query in
``api/main.py``, so no new facts were ever added.

The other reason this is a module and not a handler: the full page and the
live-polling fragment must render the same numbers. One builder with two
renderings is the only way to guarantee that; two query paths would drift the
first time one of them was edited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import FrozenSet, Optional, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBLeague, DBNFLGame
from pigskin_mastermind.services.season_scheduler import (
    in_game_window,
)
from pigskin_mastermind.utils.season import current_fantasy_season

#: The four bands that load independently. ``week`` and ``leagues`` are always
#: built: every band needs the scope and the rosters, so gating them would only
#: mean building them twice.
ALL_SECTIONS: FrozenSet[str] = frozenset({"attention", "players", "slate", "movers"})


@dataclass(frozen=True)
class WeekContext:
    """What week it is, and what the NFL is doing right now."""

    year: int
    week: int
    now: datetime
    games_total: int = 0
    games_in_progress: int = 0
    games_final: int = 0
    games_live: bool = False
    next_kickoff: Optional[datetime] = None
    players_yet_to_play: int = 0


def resolve_scope(db: Session, now: datetime) -> Tuple[int, int]:
    """One ``(year, week)`` for the whole page.

    Derived from the newest league that is still being played. A per-card week
    would let the hero say "Week 1" above a card showing week 4.

    ``archive`` leagues are excluded: they are frozen past seasons, and one
    would otherwise decide the current week for every live league beside it.
    """
    league = (
        db.query(DBLeague)
        .filter(DBLeague.kind != "archive")
        .order_by(DBLeague.year.desc(), DBLeague.id.desc())
        .first()
    )
    if league is None:
        return current_fantasy_season(now.date()), 1
    return league.year, league.current_week or 1


def build_week_context(
    db: Session,
    year: int,
    week: int,
    now: datetime,
    yet_to_play: int = 0,
) -> WeekContext:
    """Game counts and the live flag for one week.

    *now* must be a naive US-Eastern wall clock — see ``league_now()``.
    ``kickoff_at`` is stored in that frame, so a UTC clock would report every
    early Sunday game as in progress from breakfast onwards.
    """
    games = (
        db.query(DBNFLGame).filter(DBNFLGame.year == year, DBNFLGame.week == week).all()
    )
    kickoffs = [g.kickoff_at for g in games if g.kickoff_at is not None]

    final = sum(
        1 for g in games if g.home_score is not None and g.away_score is not None
    )
    in_progress = sum(
        1
        for g in games
        if g.kickoff_at is not None
        and g.home_score is None
        and g.away_score is None
        and in_game_window(now, [g.kickoff_at])
    )
    upcoming = [k for k in kickoffs if k > now]

    return WeekContext(
        year=year,
        week=week,
        now=now,
        games_total=len(games),
        games_in_progress=in_progress,
        games_final=final,
        games_live=in_game_window(now, kickoffs),
        next_kickoff=min(upcoming) if upcoming else None,
        players_yet_to_play=yet_to_play,
    )
