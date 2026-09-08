"""Advanced metrics: one player's trends, and the league-wide hot movers."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBPlayer
from pigskin_mastermind.services.advanced_metrics import METRICS, format_value
from pigskin_mastermind.services.metric_trends import (
    hot_movers,
    last_full_week,
    latest_season_with_metrics,
    percentile,
    player_series,
)
from pigskin_mastermind.services.season_scheduler import league_now
from pigskin_mastermind.utils.season import current_fantasy_season

router = APIRouter(tags=["metrics"])


@router.get("/metrics/hot")
async def hot_metrics_page(
    request: Request,
    year: Optional[int] = Query(None),
    week: Optional[int] = Query(None),
    position: Optional[str] = Query(None),
    rookies: bool = Query(False),
    db: Session = Depends(get_db),
):
    """Players whose opportunity has moved ahead of, or behind, their scoring."""
    from pigskin_mastermind.api.main import templates

    requested = year or current_fantasy_season(league_now().date())
    resolved = latest_season_with_metrics(db, requested)

    movers = []
    rising = []
    target_week = week
    if resolved is not None:
        target_week = week or last_full_week(db, resolved)
        # Scan wider than the table shows, so the rookie strip can surface a
        # first-year player who is real but outside the top rows.
        scanned = hot_movers(db, resolved, target_week, position=position, limit=80)
        rising = [m for m in scanned if m.rookie_rising]
        movers = [m for m in scanned if m.is_rookie] if rookies else scanned[:40]

    return templates.TemplateResponse(
        "metrics/hot.html",
        {
            "request": request,
            "year": resolved,
            "requested_year": requested,
            "week": target_week,
            "position": position,
            "rookies": rookies,
            "rising_rookies": rising,
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
    resolved = latest_season_with_metrics(db, requested)

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
