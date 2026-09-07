"""Advance season leagues in the background.

A "normal league" ticks during games without anyone pressing a button, so this
runs as an asyncio task in the app's lifespan. It polls only inside game
windows -- reading DBNFLGame.kickoff_at rather than a fixed interval -- so
nothing hammers ESPN on a Tuesday.

The cadence maths lives in ``next_poll_at``, a pure function, so the schedule
can be tested without running the loop or waiting on a real clock.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBLeague, DBNFLGame
from pigskin_mastermind.services.ai_manager import (
    autofill_missing_lineups,
    set_ai_lineups,
)
from pigskin_mastermind.services.lineup_locks import first_kickoff
from pigskin_mastermind.services.live_scoring import refresh_week

logger = logging.getLogger(__name__)

#: Cadence while a game is in progress.
LIVE_POLL_SECONDS = 60
#: Longest the loop ever sleeps, so a new league or an edited schedule is
#: noticed within the hour rather than days later.
IDLE_MAX_SECONDS = 3600
#: How long after kickoff a game is assumed to still be running.
GAME_WINDOW_HOURS = 4

DISABLE_ENV = "PIGSKIN_DISABLE_SCHEDULER"

#: The frame ``DBNFLGame.kickoff_at`` is stored in. nflverse publishes
#: gameday/gametime as US Eastern wall clock and ``_parse_kickoff`` keeps them
#: naive, so a Sunday early game is the literal value 13:00. Every deadline in
#: this module compares against that column, so the loop's own clock has to sit
#: in the same frame -- see :func:`league_now`.
LEAGUE_TZ = ZoneInfo("America/New_York")


def league_now() -> datetime:
    """Current time as a naive US-Eastern wall clock.

    Deliberately not ``datetime.utcnow()``. Kickoffs are stored naive-Eastern,
    so a UTC clock reads a 1pm ET kickoff as already started at 9am ET: lineups
    would auto-fill four hours before the deadline they are meant to enforce,
    and the live-polling window would open and close against the wrong hours.
    Converting here rather than at each call site keeps one definition of
    "now" for the whole loop.
    """
    return datetime.now(LEAGUE_TZ).replace(tzinfo=None)


def in_game_window(now: datetime, kickoffs: Iterable[datetime]) -> bool:
    """True while any of *kickoffs* is inside its GAME_WINDOW_HOURS window.

    The single definition of "a game is on". The scheduler polls ESPN against
    it and the dashboard decides whether to poll itself against it; two copies
    would drift and the page would keep refreshing after the scheduler had
    stopped fetching anything new.

    Half-open: a game is live at its kickoff minute and over exactly
    GAME_WINDOW_HOURS later.
    """
    window = timedelta(hours=GAME_WINDOW_HOURS)
    return any(kickoff <= now < kickoff + window for kickoff in kickoffs)


def next_poll_at(now: datetime, kickoffs: List[datetime]) -> datetime:
    """When the loop should wake next.

    Fast inside a game window, otherwise at the next kickoff, never later than
    the idle cap.
    """
    if in_game_window(now, kickoffs):
        return now + timedelta(seconds=LIVE_POLL_SECONDS)

    upcoming = [kickoff for kickoff in kickoffs if kickoff > now]
    idle_cap = now + timedelta(seconds=IDLE_MAX_SECONDS)
    if not upcoming:
        return idle_cap
    return min(min(upcoming), idle_cap)


def _kickoffs_around(db: Session, now: datetime) -> List[datetime]:
    """Kickoffs near *now* -- enough to decide the next poll, not the season."""
    window_start = now - timedelta(hours=GAME_WINDOW_HOURS)
    rows = (
        db.query(DBNFLGame.kickoff_at)
        .filter(
            DBNFLGame.kickoff_at.isnot(None),
            DBNFLGame.kickoff_at >= window_start,
        )
        .order_by(DBNFLGame.kickoff_at.asc())
        .limit(64)
        .all()
    )
    return [row[0] for row in rows]


def tick(db: Session, now: datetime, client=None) -> Dict[str, Any]:
    """One synchronous pass over every in-season league.

    Separate from the loop so the CLI can run exactly one pass, and so tests
    never touch asyncio. *now* must be a naive US-Eastern datetime, the frame
    ``kickoff_at`` uses -- callers inside this module get it from
    :func:`league_now`.
    """
    leagues = (
        db.query(DBLeague)
        .filter(DBLeague.kind == "season", DBLeague.status == "in_season")
        .all()
    )

    summary = {
        "leagues": 0,
        "ai_lineups": 0,
        "autofilled": 0,
        "scored": 0,
        "errors": 0,
        "projections": 0,
    }

    # Projections are global, not league-scoped, so this runs whether or not
    # any season league exists — the multi-source view is on ESPN teams.
    try:
        summary["projections"] = _refresh_projections_daily(db, now)
    except Exception:
        logger.exception("Daily projection refresh failed")
        db.rollback()
        summary["errors"] += 1

    for league in leagues:
        summary["leagues"] += 1
        week = league.current_week or 1
        try:
            summary["ai_lineups"] += set_ai_lineups(db, league, week, now)["teams"]

            kickoff = first_kickoff(db, league.year, week)
            if kickoff is not None and now >= kickoff:
                # Only at first kickoff: before that the manager still has time.
                summary["autofilled"] += autofill_missing_lineups(
                    db,
                    league,
                    week,
                    now,
                )["teams"]
                summary["scored"] += refresh_week(
                    db,
                    league,
                    week,
                    client=client,
                )["scored"]
        except Exception:
            # One league's failure must not stop the rest from advancing.
            logger.exception("Scheduler tick failed for league %s", league.league_id)
            db.rollback()
            summary["errors"] += 1

    return summary


def _refresh_projections_daily(db: Session, now: datetime) -> int:
    """Run the multi-source projection refresh at most once a day.

    Guarded on ``projection_source_runs`` rather than on a timer, so a restart
    does not re-run a refresh that already succeeded an hour ago, and a machine
    that was asleep at the scheduled hour still catches up on the next tick.
    """
    from pigskin_mastermind.services.weekly_projection_refresh import (
        current_nfl_week,
        needs_daily_refresh,
        refresh_week_all,
    )

    week = current_nfl_week(db, now.year, now)
    if week is None:
        # No schedule imported. Refreshing against a guessed week would store
        # projections under the wrong one.
        return 0
    if not needs_daily_refresh(db, now.year, week, now):
        return 0

    # Availability first, and never fatally. Injury reports firm up across the
    # week — practice participation from Wednesday, the official designation
    # about Friday — so this has to re-run daily rather than once when the week
    # opens. A failure here must not cost the projections that follow it.
    try:
        from pigskin_mastermind.services.nfl_data_service import NFLDataService

        service = NFLDataService(db)
        service.import_injuries([now.year])
        service.import_depth_charts([now.year])
        # Rookie season and draft position. Rarely changes, but a waiver
        # pickup mid-season arrives unstamped and would otherwise never be
        # recognised as a rookie.
        service.import_player_bio()
    except Exception:
        logger.exception("Injury/depth refresh failed; continuing to projections")
        db.rollback()

    # Advanced metrics feed the trend and hot-movers views rather than any
    # projection, so this runs after availability and, like it, never fatally.
    try:
        from pigskin_mastermind.services.advanced_metrics_import import import_all

        import_all(db, [now.year])
    except Exception:
        logger.exception("Advanced metric refresh failed; continuing")
        db.rollback()

    result = refresh_week_all(db, now.year, week)
    return sum(entry.get("rows", 0) for entry in result["sources"].values())


async def run_scheduler(stop_event: Optional[asyncio.Event] = None) -> None:
    """Poll forever, sleeping between passes according to ``next_poll_at``."""
    if os.getenv(DISABLE_ENV):
        logger.info("Season scheduler disabled by %s", DISABLE_ENV)
        return

    from pigskin_mastermind.api.database import SessionLocal

    stop_event = stop_event or asyncio.Event()
    logger.info("Season scheduler started")

    while not stop_event.is_set():
        now = league_now()
        # A dedicated session per pass: sharing a request's would outlive it.
        db = SessionLocal()
        try:
            await asyncio.to_thread(tick, db, now)
            kickoffs = _kickoffs_around(db, now)
        except Exception:
            logger.exception("Season scheduler pass failed")
            kickoffs = []
        finally:
            db.close()

        delay = max(
            1.0,
            (next_poll_at(now, kickoffs) - league_now()).total_seconds(),
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            continue

    logger.info("Season scheduler stopped")
