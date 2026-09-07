"""Sportsbook player props converted to fantasy points.

``SportsbookProjectionService`` already does the conversion — median line per
market across bookmakers, multiplied by the league's scoring weights. What was
missing is persistence: the existing ``/api/stats/teams/{id}/sportsbook-
projections`` endpoint computed this per request and threw it away, so nothing
could rank against it or score it against actuals later.

Coverage is limited to players books actually post props for — a few hundred,
concentrated in QB/RB/WR/TE. Kickers and defenses have essentially none.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBNFLGame, DBPlayer, DBSportsbookOdds
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_SPORTSBOOK,
    ProjectionValue,
)
from pigskin_mastermind.services.sportsbook_projection_service import (
    SportsbookProjectionService,
)
from pigskin_mastermind.utils.nfl_teams import normalize_team, resolve_team

logger = logging.getLogger(__name__)


class SportsbookProjectionSource:
    """``source='sportsbook'`` — consensus prop lines scored as fantasy points."""

    key = SOURCE_SPORTSBOOK
    label = "Sportsbook"
    writes = True

    def __init__(self, league_id: Optional[str] = None) -> None:
        # Which league's scoring the props are converted through. None falls
        # back to 0.5 PPR defaults inside the service.
        self.league_id = league_id

    def project_week(
        self,
        db: Session,
        year: int,
        week: int,
        player_ids: List[int],
    ) -> Dict[int, ProjectionValue]:
        if not player_ids:
            return {}

        # Props MUST be scoped to the game being projected. Without an
        # ``event_id``, ``_fetch_props`` matches ``description ILIKE '%name%'``
        # across every odds row ever stored -- no year, no week, no event. That
        # is how a hand-seeded 2025 week-10 slate ended up producing confident
        # "sportsbook" numbers for 2026 week 1. No matching event means no
        # projection, which is the correct answer rather than a wrong one.
        events = _events_for_week(db, year, week)
        if not events:
            return {}

        service = SportsbookProjectionService(db)
        out: Dict[int, ProjectionValue] = {}

        players = {
            p.id: p
            for p in db.query(DBPlayer).filter(DBPlayer.id.in_(player_ids)).all()
        }

        for player_id in player_ids:
            player = players.get(player_id)
            team = normalize_team(player.nfl_team) if player else None
            event_id = events.get(team) if team else None
            if not event_id:
                continue
            try:
                result = service.project_player_by_id(
                    player_id, event_id=event_id, league_id=self.league_id,
                )
            except Exception:
                logger.exception(
                    "Sportsbook projection failed for player %s", player_id,
                )
                continue

            points = result.get("total_projected_points") or 0.0
            # No props for this player is the overwhelmingly common case, and
            # the service reports it as 0.0. Recording that would assert the
            # book priced him at zero.
            if points <= 0:
                continue

            out[player_id] = ProjectionValue(
                points=round(float(points), 2),
                components={
                    "categories": result.get("categories", []),
                    "market_count": len(result.get("categories", [])),
                },
            )
        return out


def _events_for_week(db: Session, year: int, week: int) -> Dict[str, str]:
    """``team -> odds event_id`` for the games in *year* week *week*.

    The two tables share no key: ``DBSportsbookOdds`` carries The Odds API's
    own event id and full team names ("Kansas City Chiefs"), while
    ``DBNFLGame`` carries nflverse abbreviations ("KC"). ``normalize_team()``
    is what bridges them, and matching on the pair of teams rather than on a
    date avoids the ET-versus-UTC trap in ``kickoff_at``.
    """
    games = (
        db.query(DBNFLGame)
        .filter(DBNFLGame.year == year, DBNFLGame.week == week)
        .all()
    )
    if not games:
        return {}

    wanted = {}
    for game in games:
        home = normalize_team(game.home_team)
        away = normalize_team(game.away_team)
        if home and away:
            wanted[frozenset((home, away))] = (home, away)
    if not wanted:
        return {}

    out: Dict[str, str] = {}
    rows = (
        db.query(
            DBSportsbookOdds.event_id,
            DBSportsbookOdds.home_team,
            DBSportsbookOdds.away_team,
        )
        .distinct()
        .all()
    )
    for event_id, home_name, away_name in rows:
        home = resolve_team(home_name)
        away = resolve_team(away_name)
        if not home or not away:
            continue
        key = frozenset((home, away))
        if key not in wanted:
            continue
        out[home] = event_id
        out[away] = event_id
    return out
