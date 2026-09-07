"""Advanced metrics: one player's trends, and the league-wide hot movers."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBPlayer, DBPlayerAdvancedMetric
from pigskin_mastermind.services.advanced_metrics import METRICS, format_value
from pigskin_mastermind.services.metric_trends import (
    hot_movers, percentile, player_series,
)
from pigskin_mastermind.services.season_scheduler import league_now
from pigskin_mastermind.utils.season import current_fantasy_season

router = APIRouter(tags=["metrics"])


def _latest_season_with_metrics(db: Session, preferred: int) -> Optional[int]:
    """The newest season that actually has metrics, preferring *preferred*.

    Week 1 of a new season has no games, so the current year holds nothing to
    trend. Silently rendering an empty page would read as broken; falling back
    to the last season with data — and saying so — is the honest behaviour.
    """
    years = [
        row[0]
        for row in db.query(DBPlayerAdvancedMetric.year).distinct().all()
    ]
    if not years:
        return None
    if preferred in years:
        return preferred
    return max(years)


def _last_week(db: Session, year: int) -> int:
    """The newest week with league-wide coverage.

    Not simply ``max(week)``. A season's last stored weeks are the playoffs,
    where a handful of teams remain — so a scan anchored there finds almost
    nobody with six continuous weeks of history and the page renders empty on
    a full database. Self-calibrating on the season's own peak coverage rather
    than a hardcoded week 18, since the postseason format is not this module's
    business.
    """
    counts = (
        db.query(
            DBPlayerAdvancedMetric.week,
            func.count(func.distinct(DBPlayerAdvancedMetric.player_id)),
        )
        .filter(DBPlayerAdvancedMetric.year == year)
        .group_by(DBPlayerAdvancedMetric.week)
        .all()
    )
    if not counts:
        return 1

    peak = max(count for _week, count in counts)
    full = [week for week, count in counts if count >= peak * 0.5]
    return max(full) if full else max(week for week, _count in counts)


@router.get("/metrics/hot")
async def hot_metrics_page(
    request: Request,
    year: Optional[int] = Query(None),
    week: Optional[int] = Query(None),
    position: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Players whose opportunity has moved ahead of, or behind, their scoring."""
    from pigskin_mastermind.api.main import templates

    requested = year or current_fantasy_season(league_now().date())
    resolved = _latest_season_with_metrics(db, requested)

    movers = []
    target_week = week
    if resolved is not None:
        target_week = week or _last_week(db, resolved)
        movers = hot_movers(db, resolved, target_week, position=position)

    return templates.TemplateResponse(
        "metrics/hot.html",
        {
            "request": request,
            "year": resolved,
            "requested_year": requested,
            "week": target_week,
            "position": position,
            "movers": movers,
            # True when we are showing a season the user did not ask for.
            "fell_back": resolved is not None and resolved != requested,
        },
    )


@router.get("/players/{player_id}/advanced")
async def player_advanced_fragment(
    request: Request,
    player_id: int,
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """HTMX fragment: one player's advanced metric trends."""
    from pigskin_mastermind.api.main import templates

    player = db.query(DBPlayer).filter_by(id=player_id).first()
    if not player:
        raise HTTPException(status_code=404, detail="Player not found")

    requested = year or current_fantasy_season(league_now().date())
    resolved = _latest_season_with_metrics(db, requested)

    rows = []
    if resolved is not None:
        for series in player_series(db, player_id, resolved):
            latest_week = series.points[-1][0] if series.points else None
            rows.append({
                "series": series,
                "entry": METRICS.get(series.metric),
                "latest": format_value(series.metric, series.latest),
                "average": format_value(series.metric, series.average),
                "percentile": (
                    percentile(
                        db, series.metric, resolved, latest_week, series.latest,
                    )
                    if latest_week is not None and series.latest is not None
                    else None
                ),
            })

    return templates.TemplateResponse(
        "players/_advanced.html",
        {
            "request": request,
            "player": player,
            "year": resolved,
            "rows": rows,
            "fell_back": resolved is not None and resolved != requested,
            "requested_year": requested,
        },
    )
