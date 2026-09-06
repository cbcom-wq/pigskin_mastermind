"""The house algorithm as a projection source.

Wraps ``WeeklyProjectionService`` so the model sits behind the same interface
as the five outside sources. The criteria builder is constructed once per
provider rather than per player — it issues dozens of queries each time and is
by far the slowest thing in a refresh.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.services.master_coefficients import (
    get_effective_coefficients,
)
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder,
)
from pigskin_mastermind.services.projection_service import WeeklyProjectionService
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_MODEL,
    ProjectionValue,
)

logger = logging.getLogger(__name__)

PROJECTABLE_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})


class ModelProjectionSource:
    """``source='model'`` — the tuned in-house weekly algorithm."""

    key = SOURCE_MODEL
    label = "Model"
    writes = True

    def __init__(
        self,
        builder: Optional[ProjectionCriteriaBuilder] = None,
        service: Optional[WeeklyProjectionService] = None,
    ) -> None:
        # Injectable for the same reason ProjectionRefreshService makes them
        # injectable: the real builder may reach out to ESPN, which makes it
        # unusable in a unit test.
        self._builder = builder
        self._service = service or WeeklyProjectionService(
            coefficients=get_effective_coefficients(),
        )

    def project_week(
        self,
        db: Session,
        year: int,
        week: int,
        player_ids: List[int],
    ) -> Dict[int, ProjectionValue]:
        builder = self._builder or ProjectionCriteriaBuilder(db, allow_network=False)

        players = (
            db.query(DBPlayer).filter(DBPlayer.id.in_(player_ids)).all()
            if player_ids else []
        )

        # Batch-prime: the builder issues dozens of queries per player, so this
        # is load-bearing rather than an optimization.
        builder.ensure_players_stats([p.id for p in players], year)

        out: Dict[int, ProjectionValue] = {}
        for player in players:
            if player.position not in PROJECTABLE_POSITIONS:
                continue
            try:
                criteria = builder.build_weekly_criteria(player.id, week, year)
                domain = Player(
                    player_id=player.player_id,
                    name=player.name,
                    position=player.position,
                    team=player.nfl_team or "FA",
                )
                points = self._service.calculate_projection(domain, criteria)
            except Exception:
                # One unprojectable player is not a broken source. Skipping is
                # "no coverage for him"; raising would discard the other 999.
                logger.exception(
                    "Model projection failed for player %s week %s", player.id, week,
                )
                continue

            # ``max(0, base_score)`` in projection_service is a clamp meaning
            # "no signal", not a forecast of zero -- refresh_season skips these
            # for the same reason. Storing it would rank a player the model
            # could not score below every player it actually scored low, as
            # though the model had made that call.
            if points <= 0:
                continue

            out[player.id] = ProjectionValue(
                points=round(float(points), 2),
                components={
                    "opponent_defense_level": getattr(
                        criteria, "opponent_defense_level", None,
                    ),
                    "player_skill_level": getattr(
                        criteria, "player_skill_level", None,
                    ),
                },
            )
        return out
