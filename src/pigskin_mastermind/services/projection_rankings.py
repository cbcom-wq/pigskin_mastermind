"""Positional ranks for the multi-source weekly projections view.

Ranks are derived here at read time and never stored. A materialized rank goes
stale against its own projection the moment one provider re-runs, and a stale
rank is worse than no rank because it looks authoritative.

The other rule this module exists to enforce: **a rank is always reported with
its denominator.** Sportsbook props cover a few hundred players and the model
covers about a thousand, so "WR7 per the book" and "WR7 per the model" are not
the same claim. Rendering both as ``WR7`` would be the most misleading thing
this view could do, so ``rank_of`` is part of the return type rather than
something a caller may forget to ask for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
from pigskin_mastermind.services.projection_sources.base import SOURCE_BLEND_MULTI


@dataclass
class SourceCell:
    """One source's number for one player, with its rank among that source."""

    source: str
    points: float
    rank: Optional[int] = None
    rank_of: Optional[int] = None
    components: Dict = field(default_factory=dict)


@dataclass
class PlayerProjectionRow:
    """One table row: a player and every source's take on him."""

    player_id: int
    name: str
    position: Optional[str]
    nfl_team: Optional[str]
    cells: Dict[str, SourceCell] = field(default_factory=dict)

    @property
    def spread(self) -> Optional[float]:
        """Max minus min across the *forecasting* sources.

        The consensus is excluded: it is an average of the others, so including
        it can only ever pull the spread in and would understate exactly the
        disagreement this column exists to surface.
        """
        values = [
            cell.points
            for key, cell in self.cells.items()
            if key != SOURCE_BLEND_MULTI
        ]
        if len(values) < 2:
            return None
        return round(max(values) - min(values), 2)

    @property
    def consensus(self) -> Optional[float]:
        cell = self.cells.get(SOURCE_BLEND_MULTI)
        return cell.points if cell else None


def weekly_source_table(
    db: Session,
    player_ids: List[int],
    year: int,
    week: int,
) -> List[PlayerProjectionRow]:
    """Build the projections table for *player_ids*, ranked league-wide.

    Two queries regardless of roster size: one for every stored projection in
    the week (needed for the global ranks), one for the requested players'
    identities.
    """
    if not player_ids:
        return []

    # Every row for the week, not just the roster's -- a rank is only global if
    # it is computed against everyone.
    all_rows = (
        db.query(
            DBPlayerProjection.player_id,
            DBPlayerProjection.source,
            DBPlayerProjection.projected_points,
            DBPlayerProjection.components,
            DBPlayer.position,
        )
        .join(DBPlayer, DBPlayer.id == DBPlayerProjection.player_id)
        .filter(
            DBPlayerProjection.year == year,
            DBPlayerProjection.week == week,
        )
        .all()
    )

    ranks, totals = _rank_index(all_rows)

    wanted = set(player_ids)
    rows_by_player: Dict[int, PlayerProjectionRow] = {}

    players = db.query(DBPlayer).filter(DBPlayer.id.in_(player_ids)).all()
    for player in players:
        rows_by_player[player.id] = PlayerProjectionRow(
            player_id=player.id,
            name=player.name,
            position=player.position,
            nfl_team=player.nfl_team,
        )

    for player_id, source, points, components, position in all_rows:
        if player_id not in wanted:
            continue
        row = rows_by_player.get(player_id)
        if row is None:
            continue
        key = (source, position)
        row.cells[source] = SourceCell(
            source=source,
            points=round(float(points), 2),
            rank=ranks.get((player_id, source)),
            rank_of=totals.get(key),
            components=dict(components or {}),
        )

    # Sort by consensus, then by whatever the model said, so a roster with no
    # consensus yet still comes back in a sensible order rather than by id.
    return sorted(
        rows_by_player.values(),
        key=lambda r: (
            r.consensus if r.consensus is not None else -1,
            max((c.points for c in r.cells.values()), default=-1),
        ),
        reverse=True,
    )


def _rank_index(all_rows):
    """Rank every player within each ``(source, position)`` group.

    Ties share the lowest rank (two players at 14.0 are both WR5, next is WR7),
    which is the convention every ranking site uses. Ranking them 5 and 6 by id
    would invent a distinction the projections do not make.
    """
    grouped: Dict[tuple, List[tuple]] = {}
    for player_id, source, points, _components, position in all_rows:
        if position is None:
            continue
        grouped.setdefault((source, position), []).append(
            (float(points), player_id),
        )

    ranks: Dict[tuple, int] = {}
    totals: Dict[tuple, int] = {}

    for key, entries in grouped.items():
        source, _position = key
        entries.sort(key=lambda e: (-e[0], e[1]))
        totals[key] = len(entries)

        previous_points = None
        previous_rank = 0
        for index, (points, player_id) in enumerate(entries, start=1):
            if previous_points is not None and points == previous_points:
                rank = previous_rank
            else:
                rank = index
            ranks[(player_id, source)] = rank
            previous_points = points
            previous_rank = rank

    return ranks, totals
