"""Score stored projections against what actually happened.

Backtesting an LLM-produced projection is contaminated -- the model may
simply know how the season ended -- so the only honest evaluation is
prospective: record projections now, score them as real weeks land.

The subtlety this module exists for is **unequal coverage**. ``model`` has a
row for every player in the draft pool; ``llm`` will have a handful, chosen
by whoever ran the agent. Comparing their raw MAEs rewards a source for
projecting only the players it found easy. So every source is reported
twice: once on its own coverage, and once head-to-head on the intersection
of players every requested source projected. Only the head-to-head numbers
are comparable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayerGameLog,
    DBPlayerProjection,
    DBPlayerSeasonStats,
    DBWeeklyPlayerStats,
)


def score_projections(
    db: Session,
    year: int,
    week: Optional[int] = None,
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compare stored projections against actuals, per source.

    Args:
        db: Open session.
        year: Season year.
        week: Week to score, or ``None`` for season scope.
        sources: Restrict to these sources (default: every source present).

    Returns:
        ``{"year", "week", "scope", "per_source", "head_to_head", "no_actual"}``.
    """
    query = db.query(DBPlayerProjection).filter(DBPlayerProjection.year == year)
    if week is None:
        query = query.filter(DBPlayerProjection.week.is_(None))
    else:
        query = query.filter(DBPlayerProjection.week == week)
    if sources:
        query = query.filter(DBPlayerProjection.source.in_(sources))

    scored: Dict[str, Dict[int, Tuple[float, float]]] = {}
    no_actual = 0

    for row in query.all():
        actual = (
            _season_actual(db, row.player_id, year)
            if week is None
            else _actual_points(db, row.player_id, year, week)
        )
        if actual is None:
            # Counted per projection row, so a player missing an actual is
            # counted once for each source that projected them.
            no_actual += 1
            continue
        scored.setdefault(row.source, {})[row.player_id] = (
            row.projected_points,
            actual,
        )

    return {
        "year": year,
        "week": week,
        "scope": "season" if week is None else "weekly",
        "per_source": {
            source: _metrics(list(pairs.values())) for source, pairs in scored.items()
        },
        "head_to_head": _head_to_head(scored),
        "no_actual": no_actual,
    }


def _head_to_head(
    scored: Dict[str, Dict[int, Tuple[float, float]]],
) -> Optional[Dict[str, Any]]:
    """Re-score every source on only the players all of them projected.

    Without this restriction a source that projected three easy players
    posts a flattering MAE against a source that projected everyone, and a
    reader comparing the two numbers is misled.
    """
    if len(scored) < 2:
        return None

    shared = set.intersection(*(set(pairs) for pairs in scored.values()))
    return {
        "players": len(shared),
        "sources": {
            source: _metrics([pairs[pid] for pid in shared])
            for source, pairs in scored.items()
        },
    }


def _metrics(pairs: List[Tuple[float, float]]) -> Dict[str, float]:
    """Accuracy over ``[(projected, actual), ...]``.

    ``bias`` is the signed mean of ``projected - actual``, so **positive
    means over-projection**. MAE alone says a source is wrong; the sign says
    which way, which is the part a human can act on.
    """
    n = len(pairs)
    if n == 0:
        return {"n": 0, "mae": 0.0, "bias": 0.0, "rmse": 0.0}

    errors = [projected - actual for projected, actual in pairs]
    return {
        "n": n,
        "mae": round(sum(abs(e) for e in errors) / n, 3),
        "bias": round(sum(errors) / n, 3),
        "rmse": round((sum(e * e for e in errors) / n) ** 0.5, 3),
    }


def _actual_points(
    db: Session,
    player_id: int,
    year: int,
    week: int,
) -> Optional[float]:
    """Actual fantasy points for one week, or ``None`` if not yet played.

    Same source priority ``ProjectionTunerService._get_actual_points``
    established (``services/projection_tuner.py``): the nflverse game log
    first, then ESPN's weekly stats.
    """
    log = (
        db.query(DBPlayerGameLog)
        .filter_by(player_id=player_id, year=year, week=week)
        .first()
    )
    if log is not None and log.fantasy_points is not None:
        return round(log.fantasy_points, 2)

    weekly = (
        db.query(DBWeeklyPlayerStats)
        .filter(
            DBWeeklyPlayerStats.player_id == player_id,
            DBWeeklyPlayerStats.week == week,
        )
        .first()
    )
    if weekly and weekly.actual_points:
        return round(weekly.actual_points, 2)

    return None


def _season_actual(
    db: Session,
    player_id: int,
    year: int,
) -> Optional[float]:
    """Season total actual points.

    A stored total of zero means the season has not been played rather than
    a player who scored nothing all year, so it is treated as absent.
    """
    row = (
        db.query(DBPlayerSeasonStats).filter_by(player_id=player_id, year=year).first()
    )
    if row is None or not row.fantasy_points_total:
        return None
    return round(row.fantasy_points_total, 2)
