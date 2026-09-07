"""ESPN's own weekly projection.

Nothing is fetched here. ``espn_sync`` already writes ESPN's per-week number
onto ``weekly_player_stats.projected_points`` when it imports a box score; this
provider only lifts it into ``player_projections`` so it can sit in the same
table as the other five sources and be ranked against them.

Coverage is therefore whatever ESPN has been synced for — rostered players in
synced leagues, not the whole league-wide player pool. That gap is real and is
reported as a rank denominator rather than hidden.
"""

from __future__ import annotations

from typing import Dict, List

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBLeague, DBTeam, DBWeeklyPlayerStats, DBWeeklyTeamStats,
)
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_ESPN,
    ProjectionValue,
)


class EspnProjectionSource:
    """``source='espn'`` — ESPN's published weekly projection."""

    key = SOURCE_ESPN
    label = "ESPN"
    writes = True

    def project_week(
        self,
        db: Session,
        year: int,
        week: int,
        player_ids: List[int],
    ) -> Dict[int, ProjectionValue]:
        if not player_ids:
            return {}

        # The season MUST come from the owning league, because
        # ``weekly_player_stats`` has no year column of its own -- its parent's
        # UniqueConstraint is ('team_id', 'week'), so 2026 week 1 and 2025
        # week 1 are the same slot. Filtering on ``week`` alone served an
        # archived 2025 league's projections as though they were this season's:
        # a plausible number, for the right player, from the wrong year.
        rows = (
            db.query(DBWeeklyPlayerStats)
            .join(
                DBWeeklyTeamStats,
                DBWeeklyTeamStats.id == DBWeeklyPlayerStats.weekly_team_stats_id,
            )
            .join(DBTeam, DBTeam.id == DBWeeklyTeamStats.team_id)
            .join(DBLeague, DBLeague.league_id == DBTeam.league_id)
            .filter(
                DBLeague.year == year,
                DBWeeklyPlayerStats.week == week,
                DBWeeklyPlayerStats.player_id.in_(player_ids),
            )
            .all()
        )

        out: Dict[int, ProjectionValue] = {}
        for row in rows:
            points = row.projected_points
            if points is None:
                continue
            # 0.0 here is ESPN's "no projection", not a forecast of zero: the
            # column defaults to 0.0 and a bench row that was never projected
            # keeps that default. Storing it would put every unprojected player
            # at the bottom of the ESPN ranking as though ESPN had ranked them.
            if points <= 0:
                continue

            # A player can appear on more than one synced team's weekly roster
            # (different leagues, same person). The values agree because they
            # come from the same ESPN feed, so first-wins is safe.
            out.setdefault(
                row.player_id,
                ProjectionValue(
                    points=round(float(points), 2),
                    components={"slot": row.slot_position},
                ),
            )
        return out
