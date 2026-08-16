"""Validate and persist an agent-produced projection.

The rules here are the hallucination tripwire. They live in Python rather than
in the agent's prompt on purpose: a prompt instruction is a suggestion, and
this is a gate. Rejection is loud — the agent sees the reason and can correct,
which is strictly better than silently storing a bad number.

The bands are deliberately loose. They are sized to catch a unit error or a
misplaced decimal point, not to referee a debatable opinion.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
from pigskin_mastermind.services.projection_refresh import MODEL_SOURCE


class ResultRejected(ValueError):
    """An agent result violated a validation rule and was not stored."""


_REQUIRED_FIELDS = (
    "player_id",
    "year",
    "projected_points",
    "rationale",
    "confidence",
)

_CONFIDENCE_VALUES = ("low", "medium", "high")

# Season-scale ceilings by position. Used only when no model projection exists
# to compare against.
_SEASON_CEILINGS = {
    "QB": 600.0,
    "RB": 500.0,
    "WR": 500.0,
    "TE": 400.0,
    "K": 250.0,
    "DEF": 250.0,
}

# A week cannot plausibly be more than a tenth of a full season's ceiling.
_WEEKLY_DIVISOR = 10.0

# Multiples of the model projection outside which we assume an error rather
# than an opinion.
_MODEL_FLOOR_MULTIPLE = 0.25
_MODEL_CEILING_MULTIPLE = 3.0


def validate_result(
    db: Session,
    result: Dict[str, Any],
    *,
    web_allowed: bool = True,
) -> Dict[str, Any]:
    """Return *result* unchanged, or raise :class:`ResultRejected`."""
    for field in _REQUIRED_FIELDS:
        if field not in result or result[field] is None:
            raise ResultRejected(f"Missing required field: {field}")

    if result["confidence"] not in _CONFIDENCE_VALUES:
        raise ResultRejected(
            f"confidence must be one of {_CONFIDENCE_VALUES}, "
            f"got {result['confidence']!r}"
        )

    player = db.query(DBPlayer).filter(DBPlayer.id == result["player_id"]).first()
    if player is None:
        raise ResultRejected(f"Player {result['player_id']} not found")

    points = float(result["projected_points"])
    if points < 0:
        raise ResultRejected(f"projected_points is negative: {points}")

    _check_interval(result, points)
    _check_band(db, player, result, points)
    _check_citations(result, web_allowed=web_allowed)

    return result


def _check_interval(result: Dict[str, Any], points: float) -> None:
    floor = result.get("floor")
    ceiling = result.get("ceiling")
    if floor is not None and float(floor) > points:
        raise ResultRejected(f"floor {floor} is above projected_points {points}")
    if ceiling is not None and float(ceiling) < points:
        raise ResultRejected(f"ceiling {ceiling} is below projected_points {points}")


def _check_band(
    db: Session,
    player: DBPlayer,
    result: Dict[str, Any],
    points: float,
) -> None:
    week = result.get("week")
    model = _model_projection(db, player.id, result["year"], week)

    if model is not None and model > 0:
        low = model * _MODEL_FLOOR_MULTIPLE
        high = model * _MODEL_CEILING_MULTIPLE
        if not low <= points <= high:
            raise ResultRejected(
                f"projected_points {points} is outside [{low:.1f}, "
                f"{high:.1f}], the sanity band around the model projection "
                f"of {model:.1f}"
            )
        return

    ceiling = _SEASON_CEILINGS.get(player.position)
    if ceiling is None:
        # An unrecognized position means positions were not normalized
        # upstream. Skipping the check is safer than rejecting a real result.
        return
    if week is not None:
        ceiling = ceiling / _WEEKLY_DIVISOR
    if points > ceiling:
        raise ResultRejected(
            f"projected_points {points} exceeds the absolute ceiling for "
            f"{player.position} at this scope ({ceiling:.1f})"
        )


def _model_projection(
    db: Session,
    player_id: int,
    year: int,
    week: Optional[int],
) -> Optional[float]:
    query = db.query(DBPlayerProjection).filter(
        DBPlayerProjection.player_id == player_id,
        DBPlayerProjection.year == year,
        DBPlayerProjection.source == MODEL_SOURCE,
    )
    if week is None:
        query = query.filter(DBPlayerProjection.week.is_(None))
    else:
        query = query.filter(DBPlayerProjection.week == week)
    row = query.first()
    return row.projected_points if row else None


def _check_citations(result: Dict[str, Any], *, web_allowed: bool) -> None:
    if result.get("web_used") and not web_allowed:
        raise ResultRejected(
            "Result reports web_used=true but the run was launched --no-web"
        )

    for factor in result.get("key_factors") or []:
        if factor.get("source") == "web" and not factor.get("url"):
            raise ResultRejected(
                f"key_factor {factor.get('factor')!r} is sourced from the web "
                f"but carries no url"
            )


LLM_SOURCE = "llm"

# Fields that live in real columns; everything else in the result goes to
# ``components``. Includes "source" even though no validated result field is
# named that today: without it here, a result that happened to carry a
# "source" key would land in ``components`` right next to the row's actual
# ``source`` column (always "llm" for this path), which reads as a
# contradiction no future debugger should have to puzzle through.
_COLUMN_FIELDS = (
    "player_id",
    "year",
    "week",
    "projected_points",
    "floor",
    "ceiling",
    "std_dev",
    "expected_games",
    "source",
)


def record_llm_projection(
    db: Session,
    result: Dict[str, Any],
    *,
    web_allowed: bool = True,
) -> DBPlayerProjection:
    """Validate *result* and upsert it as a ``source='llm'`` row.

    Raises:
        ResultRejected: If validation fails. Nothing is written in that case.
    """
    validate_result(db, result, web_allowed=web_allowed)

    week = result.get("week")
    query = db.query(DBPlayerProjection).filter(
        DBPlayerProjection.player_id == result["player_id"],
        DBPlayerProjection.year == result["year"],
        DBPlayerProjection.source == LLM_SOURCE,
    )
    if week is None:
        query = query.filter(DBPlayerProjection.week.is_(None))
    else:
        query = query.filter(DBPlayerProjection.week == week)

    row = query.first()
    if row is None:
        row = DBPlayerProjection(
            player_id=result["player_id"],
            year=result["year"],
            week=week,
            source=LLM_SOURCE,
        )
        db.add(row)

    row.projected_points = float(result["projected_points"])
    row.floor = _opt_float(result.get("floor"))
    row.ceiling = _opt_float(result.get("ceiling"))
    row.std_dev = _opt_float(result.get("std_dev"))
    row.expected_games = _opt_float(result.get("expected_games"))

    # Everything the schema has no column for. Assigning a fresh dict rather
    # than mutating in place is what makes SQLAlchemy notice the change on a
    # JSON column.
    row.components = {k: v for k, v in result.items() if k not in _COLUMN_FIELDS}

    db.commit()
    db.refresh(row)
    return row


def _opt_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)
