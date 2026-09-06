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

from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_SPORTSBOOK,
    ProjectionValue,
)
from pigskin_mastermind.services.sportsbook_projection_service import (
    SportsbookProjectionService,
)

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

        service = SportsbookProjectionService(db)
        out: Dict[int, ProjectionValue] = {}

        for player_id in player_ids:
            try:
                result = service.project_player_by_id(
                    player_id, league_id=self.league_id,
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
