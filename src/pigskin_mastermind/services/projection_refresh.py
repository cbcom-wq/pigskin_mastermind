"""Persist projections so consumers read one number with one unit.

The model has always been able to produce a projection; nothing stored it, so
every ranking in the app fell back to ``DBPlayer.projected_points`` — a column
two importers write in two different units. This service is the write path that
makes ``player_projections`` the single source of truth.

Season rows (``week=None``) store season TOTALS.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayer,
    DBPlayerProjection,
    DBPlayerSeasonStats,
)
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.services.master_coefficients import (
    get_effective_coefficients,
)
from pigskin_mastermind.services.player_identity import ESPN_TAIL_ADP_SOURCE
from pigskin_mastermind.services.projection_blender import SEASON_WEIGHTS, blend
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder,
)
from pigskin_mastermind.services.projection_service import YearlyProjectionService

logger = logging.getLogger(__name__)

#: Positions the projection model accepts. ``Player`` raises on anything else.
PROJECTABLE_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})

#: ADP sources that define membership of the draft pool. Deliberately not
#: imported from ``ADPService.DRAFT_POOL_SOURCES``: adp_service imports
#: ``season_projection_map`` from this module, so importing back would create a
#: cycle. ``ESPN_TAIL_ADP_SOURCE`` comes from player_identity, which imports
#: only models and utils and is safe from either side.
DRAFT_POOL_SOURCES = ("fantasyfootballcalculator", ESPN_TAIL_ADP_SOURCE)

MODEL_SOURCE = "model"
ESPN_SOURCE = "espn"
BLEND_SOURCE = "blend"


class ProjectionRefreshService:
    """Runs the projection model over the draft pool and persists the results.

    ``builder`` and ``service`` are injectable so tests can supply stubs; the
    real criteria builder issues dozens of queries per player and may reach out
    to ESPN, which makes it unusable in a unit test.
    """

    def __init__(
        self,
        db: Session,
        builder: Optional[ProjectionCriteriaBuilder] = None,
        service: Optional[YearlyProjectionService] = None,
    ) -> None:
        self.db = db
        # Offline: a full-pool refresh with per-player ESPN fetches takes ~27
        # minutes against ~35 seconds without them (Task 6 measurement).
        self.builder = builder or ProjectionCriteriaBuilder(db, allow_network=False)
        self.service = service or YearlyProjectionService(
            coefficients=get_effective_coefficients(),
        )

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def refresh_season(
        self, year: int, limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Project every draft-pool player for *year* and persist the rows."""
        players = self._pool_players(year, limit)

        # Batch-prime stats: the criteria builder issues dozens of queries per
        # player, so this is load-bearing rather than an optimization.
        self.builder.ensure_players_stats([p.id for p in players], year - 1)

        counts = {"model": 0, "espn": 0, "blend": 0, "skipped": 0, "year": year}
        now = datetime.utcnow()

        for player in players:
            if player.position not in PROJECTABLE_POSITIONS:
                counts["skipped"] += 1
                continue
            try:
                criteria = self.builder.build_yearly_criteria(player.id, year)
                domain = Player(
                    player_id=player.player_id,
                    name=player.name,
                    position=player.position,
                    team=player.nfl_team or "FA",
                )
                total = self.service.calculate_season_projection(domain, criteria)
            except Exception:
                logger.exception("Projection failed for player %s", player.id)
                counts["skipped"] += 1
                continue

            if total <= 0:
                # ``max(0, base_score)`` in projection_service is a clamp
                # meaning "no signal", not a forecast of zero. Writing that as
                # a persisted 0.0 would assert a projection the model never
                # made, and would suppress adp_service's fallback to last
                # season's total for a player the model simply couldn't score.
                counts["skipped"] += 1
                continue

            self._upsert(
                player_id=player.id,
                year=year,
                source=MODEL_SOURCE,
                points=total,
                expected_games=criteria.expected_games,
                components={
                    "historical_average_points": criteria.historical_average_points,
                    "player_skill_level": criteria.player_skill_level,
                    "positional_touch_percentage": criteria.positional_touch_percentage,
                    "team_offense_level": criteria.team_offense_level,
                },
                now=now,
            )
            counts["model"] += 1

            espn_row = (
                self.db.query(DBPlayerProjection)
                .filter_by(
                    player_id=player.id, year=year, week=None, source=ESPN_SOURCE,
                )
                .first()
            )
            if espn_row is not None:
                counts["espn"] += 1

            # Only model and espn. ADP is already folded into the model's
            # baseline by ProjectionBaselines.season_baseline(), so blending it
            # again would double-count the market for exactly the players whose
            # projection is most market-derived.
            blended = blend(
                {
                    MODEL_SOURCE: total,
                    ESPN_SOURCE: espn_row.projected_points if espn_row else None,
                },
                SEASON_WEIGHTS,
            )
            if blended is not None:
                self._upsert(
                    player_id=player.id,
                    year=year,
                    source=BLEND_SOURCE,
                    points=blended.points,
                    expected_games=criteria.expected_games,
                    components={
                        "sources": blended.sources,
                        "weights_used": blended.weights_used,
                    },
                    now=now,
                )
                counts["blend"] += 1

        self.db.commit()
        return counts

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _pool_players(self, year: int, limit: Optional[int]) -> List[DBPlayer]:
        """Players holding a draft-pool ADP row for *year*."""
        query = (
            self.db.query(DBPlayer)
            .join(DBPlayerSeasonStats, DBPlayerSeasonStats.player_id == DBPlayer.id)
            .filter(
                DBPlayerSeasonStats.year == year,
                DBPlayerSeasonStats.adp.isnot(None),
                DBPlayerSeasonStats.adp_source.in_(DRAFT_POOL_SOURCES),
            )
            .order_by(DBPlayerSeasonStats.adp.asc())
        )
        if limit:
            query = query.limit(limit)
        return query.all()

    def _upsert(
        self,
        *,
        player_id: int,
        year: int,
        source: str,
        points: float,
        expected_games: Optional[float],
        components: Optional[Dict[str, Any]],
        now: datetime,
    ) -> None:
        """Insert or update one season row.

        Keyed on ``week=None``. The table-level unique constraint never fires
        for season rows because SQL treats NULL as distinct from NULL — the
        partial index ``uq_player_projection_season`` is what enforces this, so
        the read-then-write here must stay.
        """
        row = (
            self.db.query(DBPlayerProjection)
            .filter_by(player_id=player_id, year=year, week=None, source=source)
            .first()
        )
        if row is None:
            row = DBPlayerProjection(
                player_id=player_id, year=year, week=None, source=source,
            )
            self.db.add(row)

        row.projected_points = points
        row.expected_games = expected_games
        row.components = components or {}
        row.computed_at = now


# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

#: Preference order. The blend is the consensus; the model alone is the
#: fallback when no blend row was written.
_READ_PRIORITY = (BLEND_SOURCE, MODEL_SOURCE)


def get_projection(
    db: Session,
    player_id: int,
    year: int,
    week: Optional[int] = None,
) -> Optional[float]:
    """Persisted projection for one player, or ``None`` when absent.

    Deliberately returns ``None`` rather than falling back to
    ``DBPlayer.projected_points``: that column mixes per-game and season units,
    and ranking on it is what this module exists to stop. Callers decide what
    absence means.
    """
    rows = (
        db.query(DBPlayerProjection)
        .filter(
            DBPlayerProjection.player_id == player_id,
            DBPlayerProjection.year == year,
            DBPlayerProjection.week.is_(week) if week is None
            else DBPlayerProjection.week == week,
            DBPlayerProjection.source.in_(_READ_PRIORITY),
        )
        .all()
    )
    by_source = {r.source: r.projected_points for r in rows}
    for source in _READ_PRIORITY:
        if source in by_source:
            return by_source[source]
    return None


def season_projection_map(
    db: Session,
    player_ids: List[int],
    year: int,
) -> Dict[int, float]:
    """Batch form of :func:`get_projection` for season scope.

    The draft pool resolves ~1000 players per page load; one query per player
    is what this avoids.
    """
    if not player_ids:
        return {}

    rows = (
        db.query(DBPlayerProjection)
        .filter(
            DBPlayerProjection.player_id.in_(player_ids),
            DBPlayerProjection.year == year,
            DBPlayerProjection.week.is_(None),
            DBPlayerProjection.source.in_(_READ_PRIORITY),
        )
        .all()
    )

    best: Dict[int, tuple] = {}
    for row in rows:
        rank = _READ_PRIORITY.index(row.source)
        current = best.get(row.player_id)
        if current is None or rank < current[0]:
            best[row.player_id] = (rank, row.projected_points)

    return {pid: points for pid, (_rank, points) in best.items()}
