"""Assemble everything known about one player into a single JSON document.

This module is an *assembler*, not a new source of truth. Every number in the
output already exists somewhere in the schema or is produced by an existing
service; the point is that an agent can obtain all of it in one call instead of
discovering it endpoint by endpoint.

Nothing here calls an LLM. The output of this module is the contract that the
``player-analyst`` agent reads, which is what keeps that agent testable.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer


def build_evidence(
    db: Session,
    player_id: int,
    year: int,
    week: Optional[int] = None,
    as_of_week: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the evidence document for one player.

    Args:
        db: Open session.
        player_id: ``DBPlayer.id`` (not the prefixed string ``player_id``).
        year: Season year.
        week: Target week, or ``None`` for season scope.
        as_of_week: Backtest cutoff. When set, no data from this week or later
            appears in the document.

    Returns:
        A JSON-serializable dict.

    Raises:
        ValueError: If ``player_id`` does not exist.
    """
    player = db.query(DBPlayer).filter(DBPlayer.id == player_id).first()
    if player is None:
        raise ValueError(f"Player {player_id} not found")

    return {
        "player": _player_block(player),
        "context": _context_block(year, week),
    }


def _player_block(player: DBPlayer) -> Dict[str, Any]:
    return {
        "db_id": player.id,
        "player_id": player.player_id,
        "name": player.name,
        "position": player.position,
        "nfl_team": player.nfl_team,
        "age": player.age,
        "years_exp": player.years_exp,
        "college": player.college,
        "draft_number": player.draft_number,
        "bye_week": player.bye_week,
        "injury_status": player.injury_status,
        "injured": player.injured,
        "espn_id": player.espn_id,
        "gsis_id": player.gsis_id,
        "pfr_id": player.pfr_id,
    }


def _context_block(year: int, week: Optional[int]) -> Dict[str, Any]:
    return {
        "year": year,
        "week": week,
        "scope": "season" if week is None else "weekly",
    }
