"""Run every weekly projection source and persist what each produced.

The design constraint that shapes this whole module: the six sources have
nothing in common in how they fail. One needs an API key, one parses someone
else's HTML, one downloads a season of nflverse data, one covers a few dozen
players because an agent had to be run for each. **Partial success is the
normal outcome**, so every provider gets its own try/except and its own
``projection_source_runs`` row, and one blowing up must leave the other five
on screen.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBNFLGame,
    DBPlayer,
    DBPlayerProjection,
    DBPlayerSeasonStats,
    DBProjectionSourceRun,
    DBRosterSpot,
)
from pigskin_mastermind.services.projection_blender import (
    WEEKLY_MULTI_WEIGHTS,
    blend,
)
# Imported rather than restated so the draft-pool definition cannot drift
# between the season refresh and this one.
from pigskin_mastermind.services.projection_refresh import DRAFT_POOL_SOURCES
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_BLEND_MULTI,
)
from pigskin_mastermind.services.projection_sources.registry import build_registry

logger = logging.getLogger(__name__)

#: How old the newest successful run may be before the header calls it stale.
#: Matches the daily refresh cadence with room for one missed pass.
STALE_AFTER_HOURS = 36


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def refresh_week_all(
    db: Session,
    year: int,
    week: int,
    *,
    sources: Optional[List[str]] = None,
    league_id: Optional[str] = None,
    player_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Refresh every registered source for *week*, then rebuild the consensus.

    Returns a per-source summary. Never raises for a provider failure -- that
    is recorded on the run row and reported in the result.
    """
    universe = player_ids if player_ids is not None else projection_universe(db, year)
    providers = build_registry(sources, league_id=league_id)

    results: Dict[str, Any] = {
        "year": year, "week": week, "players": len(universe), "sources": {},
    }

    for provider in providers:
        run = _start_run(db, provider.key, year, week)
        try:
            values = provider.project_week(db, year, week, universe)
        except Exception as exc:
            logger.exception("Projection source %s failed", provider.key)
            _finish_run(db, run, status="error", rows=0, error=str(exc)[:500])
            results["sources"][provider.key] = {"status": "error", "error": str(exc)}
            continue

        written = 0
        if provider.writes:
            for player_id, value in values.items():
                _upsert(
                    db,
                    player_id=player_id,
                    year=year,
                    week=week,
                    source=provider.key,
                    value=value,
                )
                written += 1
            # Drop rows this source wrote on an earlier pass but no longer
            # covers. Upserting alone would leave them behind forever -- a
            # player whose props were pulled, or one the model can no longer
            # score, would keep a stale number and a stale rank, and re-running
            # the week (which is what the Refresh button does) would never
            # clear it.
            _prune(
                db,
                year=year,
                week=week,
                source=provider.key,
                keep=set(values),
                scope=universe,
            )
        else:
            # Read-only providers (llm) already have their rows in the table.
            # Re-upserting would overwrite an agent's validated projection and
            # its citations with a copy stripped of both.
            written = len(values)

        # A provider that covered nobody is 'skipped', not 'ok'. The header
        # needs to distinguish "ran and found nothing" from "ran and worked",
        # because for a scrape those mean very different things.
        status = "ok" if values else "skipped"
        _finish_run(db, run, status=status, rows=written, error=None)
        results["sources"][provider.key] = {"status": status, "rows": written}

    db.commit()

    blended = rebuild_blend(db, year, week, universe)
    results["sources"][SOURCE_BLEND_MULTI] = {"status": "ok", "rows": blended}
    db.commit()
    return results


def rebuild_blend(
    db: Session, year: int, week: int, player_ids: List[int],
) -> int:
    """Recompute ``blend_multi`` for *week* from whatever sources landed."""
    if not player_ids:
        return 0

    rows = (
        db.query(DBPlayerProjection)
        .filter(
            DBPlayerProjection.year == year,
            DBPlayerProjection.week == week,
            DBPlayerProjection.player_id.in_(player_ids),
            DBPlayerProjection.source != SOURCE_BLEND_MULTI,
        )
        .all()
    )

    by_player: Dict[int, Dict[str, float]] = {}
    for row in rows:
        by_player.setdefault(row.player_id, {})[row.source] = row.projected_points

    run = _start_run(db, SOURCE_BLEND_MULTI, year, week)
    written = 0
    blended: set = set()
    for player_id, sources in by_player.items():
        result = blend(sources, WEEKLY_MULTI_WEIGHTS)
        # None means no *weighted* source had a value -- e.g. a player only the
        # zero-weight llm source covers. Writing a consensus there would invent
        # one from a single unweighted vote.
        if result is None:
            continue
        _upsert(
            db,
            player_id=player_id,
            year=year,
            week=week,
            source=SOURCE_BLEND_MULTI,
            value=_BlendValue(result),
        )
        blended.add(player_id)
        written += 1

    # A consensus outlives its inputs otherwise: a player every source has
    # stopped covering would keep the number they last agreed on.
    _prune(
        db,
        year=year,
        week=week,
        source=SOURCE_BLEND_MULTI,
        keep=blended,
        scope=player_ids,
    )

    _finish_run(db, run, status="ok", rows=written, error=None)
    return written


def projection_universe(db: Session, year: int) -> List[int]:
    """Every player a weekly rank should be computed over.

    The draft pool union everyone currently rostered. The pool alone would
    leave a mid-season waiver pickup unranked; rosters alone would make every
    rank roster-relative, which is the thing this feature exists to fix.
    """
    pool = {
        row[0]
        for row in db.query(DBPlayerSeasonStats.player_id)
        .filter(
            DBPlayerSeasonStats.year == year,
            DBPlayerSeasonStats.adp.isnot(None),
            DBPlayerSeasonStats.adp_source.in_(DRAFT_POOL_SOURCES),
        )
        .all()
    }

    # Both roster storage paths: DBPlayer.team_id is the ESPN column, and
    # DBRosterSpot is the league-scoped one used by season and archive leagues.
    espn_rostered = {
        row[0]
        for row in db.query(DBPlayer.id).filter(DBPlayer.team_id.isnot(None)).all()
    }
    spot_rostered = {
        row[0]
        for row in db.query(DBRosterSpot.player_id)
        .filter(DBRosterSpot.dropped_at.is_(None))
        .all()
    }

    return sorted(pool | espn_rostered | spot_rostered)


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def freshness(
    db: Session,
    year: int,
    week: Optional[int] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Per-source run status plus the newest successful finish overall."""
    now = now or datetime.utcnow()

    query = db.query(DBProjectionSourceRun).filter(
        DBProjectionSourceRun.year == year,
    )
    if week is not None:
        query = query.filter(DBProjectionSourceRun.week == week)

    runs = query.all()
    per_source = {
        run.source: {
            "status": run.status,
            "rows": run.rows_written or 0,
            "error": run.error,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        }
        for run in runs
    }

    finished = [r.finished_at for r in runs if r.finished_at and r.status == "ok"]
    latest = max(finished) if finished else None

    return {
        "year": year,
        "week": week,
        "latest_at": latest.isoformat() if latest else None,
        "age_seconds": (now - latest).total_seconds() if latest else None,
        "stale": latest is None or (now - latest) > timedelta(hours=STALE_AFTER_HOURS),
        "sources": per_source,
    }


def current_nfl_week(db: Session, year: int, now: datetime) -> Optional[int]:
    """The week still being played, or the next one up.

    Defined as the lowest week that has not finished kicking off. *now* must be
    the same naive US-Eastern wall clock ``DBNFLGame.kickoff_at`` is stored in —
    ``season_scheduler.league_now()``, never ``utcnow()``, which runs 4-5 hours
    ahead and would roll the week over on Sunday evening.

    Returns None when no schedule is imported: guessing a week and refreshing
    projections against it would be worse than doing nothing.
    """
    from sqlalchemy import func

    row = (
        db.query(DBNFLGame.week)
        .filter(DBNFLGame.year == year, DBNFLGame.kickoff_at.isnot(None))
        .group_by(DBNFLGame.week)
        .having(func.max(DBNFLGame.kickoff_at) >= now)
        .order_by(DBNFLGame.week.asc())
        .first()
    )
    if row is not None:
        return row[0]

    # Season is over. The last week played is the one worth showing.
    last = (
        db.query(func.max(DBNFLGame.week))
        .filter(DBNFLGame.year == year)
        .scalar()
    )
    return last


def needs_daily_refresh(
    db: Session, year: int, week: int, now: datetime,
) -> bool:
    """True when no source has finished successfully in the last day.

    *now* is a parameter rather than a call to ``utcnow()`` so the scheduler
    can be tested without waiting a day, and so the caller supplies the clock
    it already uses.
    """
    latest = (
        db.query(DBProjectionSourceRun.finished_at)
        .filter(
            DBProjectionSourceRun.year == year,
            DBProjectionSourceRun.week == week,
            DBProjectionSourceRun.status == "ok",
            DBProjectionSourceRun.finished_at.isnot(None),
        )
        .order_by(DBProjectionSourceRun.finished_at.desc())
        .first()
    )
    if latest is None or latest[0] is None:
        return True
    return (now - latest[0]) >= timedelta(days=1)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


class _BlendValue:
    """Adapts a ``BlendResult`` to the ``ProjectionValue`` shape ``_upsert`` wants."""

    def __init__(self, result) -> None:
        self.points = round(result.points, 2)
        self.floor = None
        self.ceiling = None
        self.components = {
            "sources": {k: round(v, 2) for k, v in result.sources.items()},
            "weights_used": {k: round(v, 4) for k, v in result.weights_used.items()},
        }


def _start_run(
    db: Session, source: str, year: int, week: Optional[int],
) -> DBProjectionSourceRun:
    run = (
        db.query(DBProjectionSourceRun)
        .filter_by(source=source, year=year, week=week)
        .first()
    )
    if run is None:
        run = DBProjectionSourceRun(source=source, year=year, week=week)
        db.add(run)
    run.started_at = datetime.utcnow()
    run.finished_at = None
    run.error = None
    run.status = "running"
    db.flush()
    return run


def _finish_run(
    db: Session,
    run: DBProjectionSourceRun,
    *,
    status: str,
    rows: int,
    error: Optional[str],
) -> None:
    run.status = status
    run.rows_written = rows
    run.error = error
    run.finished_at = datetime.utcnow()
    db.flush()


def _prune(
    db: Session,
    *,
    year: int,
    week: int,
    source: str,
    keep: set,
    scope: List[int],
) -> None:
    """Delete *source*'s rows for players it no longer covers.

    Restricted to ``scope`` -- the players this pass actually looked at -- so a
    partial refresh (``--sources``, or a caller passing its own player list)
    cannot delete rows for players it never examined.
    """
    if not scope:
        return

    stale = [pid for pid in scope if pid not in keep]
    if not stale:
        return

    # Chunked: SQLite caps host parameters per statement, and the universe is
    # ~1000 players.
    for start in range(0, len(stale), 500):
        chunk = stale[start:start + 500]
        (
            db.query(DBPlayerProjection)
            .filter(
                DBPlayerProjection.year == year,
                DBPlayerProjection.week == week,
                DBPlayerProjection.source == source,
                DBPlayerProjection.player_id.in_(chunk),
            )
            .delete(synchronize_session=False)
        )


def _upsert(
    db: Session,
    *,
    player_id: int,
    year: int,
    week: int,
    source: str,
    value,
) -> None:
    """Insert or update one weekly projection row.

    Read-then-write rather than an ON CONFLICT: the table's unique constraint
    does not fire for season rows (NULL week), and keeping one code path for
    both scopes is what stops that asymmetry becoming a bug.
    """
    row = (
        db.query(DBPlayerProjection)
        .filter_by(player_id=player_id, year=year, week=week, source=source)
        .first()
    )
    if row is None:
        row = DBPlayerProjection(
            player_id=player_id, year=year, week=week, source=source,
        )
        db.add(row)

    row.projected_points = value.points
    row.floor = value.floor
    row.ceiling = value.ceiling
    row.components = value.components or {}
    row.computed_at = datetime.utcnow()
