"""Multi-source weekly projections: the table, the refresh, the freshness chip.

The HTML fragment lives here rather than in ``teams.py`` so the view and the
two endpoints that feed it stay in one file.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import (
    DBLeague, DBTeam, DBWeeklyPlayerStats, DBWeeklyTeamStats,
)
from pigskin_mastermind.services.projection_rankings import weekly_source_table
from pigskin_mastermind.services.projection_sources.base import SOURCE_BLEND_MULTI
from pigskin_mastermind.services.projection_sources.registry import source_labels
from pigskin_mastermind.services.season_league import roster_players
from pigskin_mastermind.services.season_scheduler import league_now
from pigskin_mastermind.services.weekly_projection_refresh import (
    freshness, refresh_week_all,
)
from pigskin_mastermind.utils.season import current_fantasy_season

router = APIRouter(tags=["projections"])


def _default_year() -> int:
    """The season these projections belong to.

    ``current_fantasy_season`` rather than a calendar year: January and
    February still belong to the previous season, and dated off
    ``league_now()`` because every date in this app is anchored to the naive
    US-Eastern clock ``DBNFLGame.kickoff_at`` is stored in.
    """
    return current_fantasy_season(league_now().date())


def team_week_player_ids(db: Session, team: DBTeam, week: int) -> List[int]:
    """The roster to project for *team* in *week*.

    Prefers the week's ``DBWeeklyTeamStats`` snapshot, which is who was
    actually rostered then. Falls back to the team's current roster when no
    snapshot exists — which is the normal case for an *upcoming* week, and the
    reason the two endpoints this view replaces were unusable for the only week
    anyone needs to set a lineup for.
    """
    weekly_team = (
        db.query(DBWeeklyTeamStats)
        .filter_by(team_id=team.id, week=week)
        .first()
    )
    if weekly_team is not None:
        ids = [
            row[0]
            for row in db.query(DBWeeklyPlayerStats.player_id)
            .filter(DBWeeklyPlayerStats.weekly_team_stats_id == weekly_team.id)
            .all()
        ]
        if ids:
            return ids

    league = (
        db.query(DBLeague).filter_by(league_id=team.league_id).first()
        if team.league_id else None
    )
    return [p.id for p in roster_players(db, team, league)]


def _serialize(rows) -> List[dict]:
    return [
        {
            "player_id": row.player_id,
            "name": row.name,
            "position": row.position,
            "nfl_team": row.nfl_team,
            "consensus": row.consensus,
            "spread": row.spread,
            "sources": {
                key: {
                    "points": cell.points,
                    "rank": cell.rank,
                    "rank_of": cell.rank_of,
                }
                for key, cell in row.cells.items()
            },
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------


@router.get("/teams/{team_db_id}/projections")
async def team_projections_fragment(
    request: Request,
    team_db_id: int,
    week: int = Query(..., ge=1, le=22),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """HTMX fragment: the multi-source table for one team's week."""
    from pigskin_mastermind.api.main import templates

    team = db.query(DBTeam).filter_by(id=team_db_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    year = year or _default_year()
    player_ids = team_week_player_ids(db, team, week)
    rows = weekly_source_table(db, player_ids, year, week)

    labels = source_labels()
    # Column order is the registry's order, with the consensus last. Only
    # columns some player actually has are rendered: six mostly-empty columns
    # would be worse than four honest ones.
    present = {key for row in rows for key in row.cells}
    columns = [
        (key, label)
        for key, label in labels.items()
        if key in present and key != SOURCE_BLEND_MULTI
    ]

    return templates.TemplateResponse(
        "teams/_projections.html",
        {
            "request": request,
            "team": team,
            "week": week,
            "year": year,
            "rows": rows,
            "columns": columns,
            "consensus_key": SOURCE_BLEND_MULTI,
            "freshness": freshness(db, year, week),
            "empty_reason": _empty_reason(player_ids, rows),
        },
    )


def _empty_reason(player_ids: List[int], rows) -> Optional[str]:
    """Why the table is blank, when it is.

    "No roster" and "roster but no projections yet" need different fixes, and a
    blank table that does not say which is a support question.
    """
    if not player_ids:
        return "This team has no roster for that week."
    if not any(row.cells for row in rows):
        return (
            "No projections stored for this week yet. Refresh to build them."
        )
    return None


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------


@router.get("/api/projections/teams/{team_db_id}")
async def team_projections_json(
    team_db_id: int,
    week: int = Query(..., ge=1, le=22),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    team = db.query(DBTeam).filter_by(id=team_db_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    year = year or _default_year()
    player_ids = team_week_player_ids(db, team, week)
    rows = weekly_source_table(db, player_ids, year, week)

    return {
        "team_id": team.id,
        "year": year,
        "week": week,
        "labels": source_labels(),
        "players": _serialize(rows),
    }


@router.get("/api/projections/freshness-chip")
async def projections_freshness_chip(
    request: Request,
    db: Session = Depends(get_db),
):
    """The top-bar freshness indicator, across every week of the current year."""
    from pigskin_mastermind.api.main import templates

    state = freshness(db, _default_year())
    return templates.TemplateResponse(
        "components/_freshness_chip.html",
        {
            "request": request,
            "freshness": state,
            "age_label": _age_label(state.get("age_seconds")),
        },
    )


def _age_label(age_seconds: Optional[float]) -> str:
    """A coarse "how long ago", because the exact minute never matters here."""
    if age_seconds is None:
        return "never"
    minutes = int(age_seconds // 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


@router.get("/api/projections/freshness")
async def projections_freshness(
    week: Optional[int] = Query(None, ge=1, le=22),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    return freshness(db, year or _default_year(), week)


@router.post("/api/projections/refresh-week")
async def projections_refresh_week(
    week: int = Query(..., ge=1, le=22),
    year: Optional[int] = Query(None),
    sources: Optional[str] = Query(
        None, description="Comma-separated source keys; omit for all.",
    ),
    db: Session = Depends(get_db),
):
    """Run every source for *week* and rebuild the consensus.

    Synchronous on purpose: this is a user pressing Refresh and waiting for the
    table to change. The scheduler's daily pass is the unattended path.
    """
    keys = [s.strip() for s in sources.split(",")] if sources else None
    return refresh_week_all(db, year or _default_year(), week, sources=keys)
