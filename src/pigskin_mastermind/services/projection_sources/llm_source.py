"""Projections an agent recorded, surfaced read-only.

``pigskin agent record-projection`` already validates and stores these as
``source='llm'``. This provider does not create them — it reads what is there
so the view can show an agent's number beside the five mechanical ones.

``writes = False`` is the point: re-upserting these rows would overwrite an
agent's validated projection (and its citations in ``components``) with a copy
stripped of everything that made it worth recording.

Coverage is a few dozen players at most, against ~1000 for the model. That is
why ``llm`` carries zero weight in the consensus — see WEEKLY_MULTI_WEIGHTS.
"""

from __future__ import annotations

from typing import Dict, List

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayerProjection
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_LLM,
    ProjectionValue,
)


class LlmProjectionSource:
    """``source='llm'`` — read-through to agent-recorded projections."""

    key = SOURCE_LLM
    label = "Analyst"
    writes = False

    def project_week(
        self,
        db: Session,
        year: int,
        week: int,
        player_ids: List[int],
    ) -> Dict[int, ProjectionValue]:
        if not player_ids:
            return {}

        rows = (
            db.query(DBPlayerProjection)
            .filter(
                DBPlayerProjection.year == year,
                DBPlayerProjection.week == week,
                DBPlayerProjection.source == SOURCE_LLM,
                DBPlayerProjection.player_id.in_(player_ids),
            )
            .all()
        )

        return {
            row.player_id: ProjectionValue(
                points=round(float(row.projected_points), 2),
                floor=row.floor,
                ceiling=row.ceiling,
                components=dict(row.components or {}),
            )
            for row in rows
        }
