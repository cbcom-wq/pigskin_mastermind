"""Multi-source weekly projections: the table, the refresh, the freshness chip.

The HTML fragment lives here rather than in ``teams.py`` so the view and the
two endpoints that feed it stay in one file.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import (
    DBLeague, DBPlayer, DBTeam, DBWeeklyPlayerStats, DBWeeklyTeamStats,
    get_scoring_settings,
)
from pigskin_mastermind.services.lineup_manager import plan_lineup
from pigskin_mastermind.services.projection_blender import WEEKLY_MULTI_WEIGHTS
from pigskin_mastermind.services.projection_rankings import (
    consensus_map, weekly_source_table,
)
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_BLEND_MULTI, SOURCE_CONSENSUS, SOURCE_ESPN, SOURCE_LLM,
    SOURCE_MODEL, SOURCE_NFLVERSE_XP, SOURCE_SPORTSBOOK,
)
from pigskin_mastermind.services.projection_sources.registry import source_labels
from pigskin_mastermind.services.sportsbook_projection_service import (
    MARKET_TO_SCORING,
)
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


#: Donut slice colors, slots 1-6 of the validated categorical palette.
#:
#: Keyed by source rather than by position on purpose: color identifies the
#: source, not its current rank. Unchecking one must not repaint the others,
#: or every remaining slice appears to change meaning when you toggle a box.
SOURCE_COLORS = {
    SOURCE_MODEL: "#2a78d6",        # blue
    SOURCE_ESPN: "#eb6834",         # orange
    SOURCE_SPORTSBOOK: "#1baf7a",   # aqua
    SOURCE_NFLVERSE_XP: "#eda100",  # yellow
    SOURCE_LLM: "#e87ba4",          # magenta
    SOURCE_CONSENSUS: "#008300",    # green
}

#: Marks a request as carrying the viewer's own weighting. Without it, an
#: unchecked-everything form and a plain first load are indistinguishable, and
#: the page would silently fall back to defaults the moment you cleared the
#: last checkbox — looking like the controls were ignored.
WEIGHTS_ACTIVE_FIELD = "weights_active"


def parse_source_controls(params):
    """Read the weighting form. Returns ``(weights, checked, shown)``.

    ``weights`` is what the consensus actually uses — zero for anything
    unchecked, so ``blend()`` renormalizes over the rest exactly as it does for
    a source no provider covered.

    ``shown`` is what goes back into the number inputs, and is deliberately
    **not** the same thing. Rendering an unchecked source's effective weight
    would put 0.00 in its box, and re-ticking the checkbox would then send
    ``w_espn=0`` — the source would stay silent and the checkbox would bounce
    straight back off, with no way to ever re-enable it. Keeping the last
    usable number there is what makes the checkbox reversible.

    ``checked`` comes from the checkboxes themselves rather than from
    ``weight > 0`` for the same reason. It also leaves "checked, weight 0" as a
    legal state a viewer can type — honest, and visibly their own doing.
    """
    defaults = dict(WEEKLY_MULTI_WEIGHTS)
    if not params.get(WEIGHTS_ACTIVE_FIELD):
        active = {key for key, weight in defaults.items() if weight > 0}
        return dict(defaults), active, dict(defaults)

    checked = {key for key in defaults if key in set(params.getlist("src"))}
    weights: Dict[str, float] = {}
    shown: Dict[str, float] = {}

    for key in defaults:
        try:
            value = float(params.get(f"w_{key}", defaults[key]))
        except (TypeError, ValueError):
            # A garbled number falls back to that source's tuned weight rather
            # than to zero: dropping a source the viewer explicitly checked
            # would be the more surprising failure.
            value = defaults[key]
        value = max(0.0, value)

        # A zero in the box for an unchecked source is the trap described
        # above, so fall back to the tuned default for display only.
        shown[key] = value if value > 0 else defaults[key]
        weights[key] = value if key in checked else 0.0

    return weights, checked, shown


def parse_weights(params) -> Dict[str, float]:
    """The effective per-source weights for *params*."""
    weights, _checked, _shown = parse_source_controls(params)
    return weights


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
    weights, checked, shown = parse_source_controls(request.query_params)
    player_ids = team_week_player_ids(db, team, week)
    rows = weekly_source_table(db, player_ids, year, week, weights=weights)

    labels = source_labels()
    # Every registry source gets a control row, even one no player has this
    # week -- a checkbox that vanishes when a source is empty would look like
    # the source was removed rather than uncovered.
    present = {key for row in rows for key in row.cells}
    controls = [
        {
            "key": key,
            "label": label,
            "weight": shown.get(key, 0.0),
            "checked": key in checked,
            "covered": sum(1 for row in rows if key in row.cells),
            "available": key in present,
            "color": SOURCE_COLORS.get(key, "#94a3b8"),
        }
        for key, label in labels.items()
        if key != SOURCE_BLEND_MULTI
    ]
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
            "controls": controls,
            "weights_field": WEIGHTS_ACTIVE_FIELD,
            "plan": _consensus_lineup(db, team, year, week, rows),
            "sportsbook": sportsbook_method(db, team, rows),
            "consensus_key": SOURCE_BLEND_MULTI,
            "freshness": freshness(db, year, week),
            "empty_reason": _empty_reason(player_ids, rows),
        },
    )


def sportsbook_method(db: Session, team: DBTeam, rows):
    """The market->scoring mapping, plus a worked example off this roster.

    Both are derived rather than written into the template. A hardcoded
    multiplier table silently becomes wrong the moment a league overrides its
    scoring, and a hardcoded worked example becomes a lie as soon as the lines
    move — and this panel exists precisely to be trusted about the arithmetic.
    """
    league = (
        db.query(DBLeague).filter_by(league_id=team.league_id).first()
        if team.league_id else None
    )
    scoring = get_scoring_settings(league)

    markets = [
        {"market": key, "label": label, "scoring_key": scoring_key,
         "multiplier": scoring.get(scoring_key, 0)}
        for key, (scoring_key, label) in sorted(
            MARKET_TO_SCORING.items(), key=lambda item: item[1][1],
        )
    ]

    # The player with the most priced categories, not simply the first row.
    # The table re-sorts as the viewer reweights sources, so "first row" would
    # make the worked example jump to a different player mid-session; richest
    # breakdown is both stable and the most illustrative.
    example = None
    best = 0
    for row in rows:
        cell = row.cells.get(SOURCE_SPORTSBOOK)
        categories = (cell.components or {}).get("categories") if cell else None
        if categories and len(categories) > best:
            best = len(categories)
            example = {
                "name": row.name,
                "position": row.position,
                "total": cell.points,
                "categories": categories,
                "books": max(
                    (c.get("bookmaker_count") or 0 for c in categories),
                    default=0,
                ),
                "is_qb": row.position == "QB",
            }

    return {"markets": markets, "example": example}


def _consensus_lineup(db: Session, team: DBTeam, year: int, week: int, rows):
    """The best legal lineup under the viewer's current weighting.

    Goes through ``plan_lineup`` with the roster and consensus injected rather
    than optimizing here. That function owns bye-week zeroing, injury
    exclusions and haircuts, locked-slot preservation, the stable tie-break,
    and required-then-FLEX filling — a second implementation for this panel
    would fork all of it.

    Display only: it returns a plan and writes no ``DBLineupSlot`` rows.
    """
    projections = consensus_map(rows)
    if not projections:
        return None

    players = (
        db.query(DBPlayer)
        .filter(DBPlayer.id.in_([row.player_id for row in rows]))
        .all()
    )
    league = (
        db.query(DBLeague).filter_by(league_id=team.league_id).first()
        if team.league_id else None
    )
    return plan_lineup(
        db, team, year, week, league_now(),
        league=league, players=players, projections=projections,
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
    request: Request,
    team_db_id: int,
    week: int = Query(..., ge=1, le=22),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    team = db.query(DBTeam).filter_by(id=team_db_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    year = year or _default_year()
    # Same weighting the page uses, so the JSON cannot disagree with the table
    # a caller is looking at.
    weights = parse_weights(request.query_params)
    player_ids = team_week_player_ids(db, team, week)
    rows = weekly_source_table(db, player_ids, year, week, weights=weights)

    return {
        "team_id": team.id,
        "year": year,
        "week": week,
        "labels": source_labels(),
        "weights": weights,
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
