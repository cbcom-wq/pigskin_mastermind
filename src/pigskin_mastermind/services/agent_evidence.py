"""Assemble everything known about one player into a single JSON document.

This module is an *assembler*, not a new source of truth. Every number in the
output already exists somewhere in the schema or is produced by an existing
service; the point is that an agent can obtain all of it in one call instead of
discovering it endpoint by endpoint.

Nothing here calls an LLM. The output of this module is the contract that the
``player-analyst`` agent reads, which is what keeps that agent testable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBNFLGame,
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerProjection,
    DBPlayerSeasonStats,
)

# Three seasons is what the criteria builder's trend and year-over-year
# calculations look back over; more would be noise in the agent's context.
_SEASON_HISTORY_YEARS = 3

# Two seasons of weeks. Enough to see a role change from last year without
# burning the agent's context on ancient games.
_GAME_LOG_LIMIT = 34

_CRITERIA_OMITTED_UNDER_AS_OF = (
    "ProjectionCriteriaBuilder has no as-of cutoff, so its output reflects the "
    "full season. Including it here would leak post-cutoff data into a "
    "backtest, so the block is omitted instead."
)

_SEASON_STATS_OMITTED_UNDER_AS_OF = (
    "Season aggregates for the target year include the post-cutoff weeks "
    "themselves — fantasy_points_avg over a full season is close to the "
    "answer for any single week in it. An aggregate cannot be partially "
    "truncated without recomputing it from game logs, so the target season "
    "is withheld instead. Prior seasons are unaffected."
)

_SPORTSBOOK_OMITTED_UNDER_AS_OF = (
    "Sportsbook lines price upcoming games and carry no as-of history, so "
    "under a cutoff the stored props describe a game that had not been "
    "played yet — or a different season entirely. Withheld rather than "
    "served as if contemporaneous."
)


def evidence_hash(evidence: Dict[str, Any]) -> str:
    """Stable fingerprint of the *data* in an evidence document.

    A stored projection records the hash of the evidence it was derived from,
    which is what makes a disagreement between two runs diagnosable: same hash
    means the agent changed its mind, different hash means the data moved.

    ``generated_at`` and the hash field itself are excluded — both change on
    every call and neither is data about the player.
    """
    payload = {k: v for k, v in evidence.items() if k != "evidence_hash"}
    freshness = payload.get("data_freshness")
    if isinstance(freshness, dict):
        payload["data_freshness"] = {
            k: v for k, v in freshness.items() if k != "generated_at"
        }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def build_evidence(
    db: Session,
    player_id: int,
    year: int,
    week: Optional[int] = None,
    as_of_week: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the evidence document for one player.

    Not read-only: the ``criteria`` block's ``ProjectionCriteriaBuilder`` lazily
    creates missing team/defense stat rows as a side effect of computing the
    criteria it returns. Callers must commit (and close the session) before
    handing control to a subprocess, or those writes are lost and a second
    SQLite connection can deadlock against this one.

    Args:
        db: Open session.
        player_id: ``DBPlayer.id`` (not the prefixed string ``player_id``).
        year: Season year.
        week: Target week, or ``None`` for season scope.
        as_of_week: Backtest cutoff. When set: ``game_logs`` for the target
            season are truncated to weeks before it, ``schedule`` reports
            games at or after it as unplayed regardless of the stored score,
            ``criteria`` is withheld entirely (the builder it wraps has no
            cutoff of its own), the target season's row is dropped from
            ``season_stats`` (an aggregate can't be partially truncated, and
            its ``fantasy_points_avg`` is close to the answer for any single
            week inside it — prior seasons are unaffected), and
            ``sportsbook`` is withheld (books price upcoming games, so a
            stored prop under a cutoff either hasn't happened yet or belongs
            to a different season).

            None of that reaches the columns the schema keeps no historical
            snapshot for, so a backtest still sees present-day values there
            regardless of the cutoff: the ``player`` block's
            ``injury_status``, ``injured``, ``nfl_team``, ``age``,
            ``years_exp``, and ``bye_week`` are read live — a same-season
            backtest sees today's injury status, and a player traded
            mid-season shows the post-trade team, which is also why
            ``schedule`` (looked up by current ``nfl_team``) can show the
            wrong opponent for an early-season backtest run after a later
            trade. ``existing_projections`` rows carry a
            ``computed_at`` but are not filtered by it, so a projection
            computed after the cutoff can still appear; the consumer can
            compare ``computed_at`` against the cutoff itself if that
            matters. ``data_freshness`` timestamps are likewise unfiltered,
            plus its own wall-clock ``generated_at`` — enough to reveal that
            the season is over even though no stat value crosses the
            cutoff.

    Returns:
        A JSON-serializable dict.

    Raises:
        ValueError: If ``player_id`` does not exist.
    """
    player = db.query(DBPlayer).filter(DBPlayer.id == player_id).first()
    if player is None:
        raise ValueError(f"Player {player_id} not found")

    criteria = None
    criteria_reason = None
    if as_of_week is None:
        criteria = _criteria_block(db, player_id, year, week)
    else:
        criteria_reason = _CRITERIA_OMITTED_UNDER_AS_OF

    evidence = {
        "player": _player_block(player),
        "context": _context_block(year, week),
        "season_stats": _season_stats_block(db, player_id, year, as_of_week),
        "season_stats_omitted_reason": (
            _SEASON_STATS_OMITTED_UNDER_AS_OF if as_of_week is not None else None
        ),
        "game_logs": _game_logs_block(db, player_id, year, as_of_week),
        "criteria": criteria,
        "criteria_omitted_reason": criteria_reason,
        "existing_projections": _existing_projections_block(
            db,
            player_id,
            year,
            week,
        ),
        "schedule": _schedule_block(db, player, year, week, as_of_week),
        "sportsbook": _sportsbook_block(db, player_id, as_of_week),
        "sportsbook_omitted_reason": (
            _SPORTSBOOK_OMITTED_UNDER_AS_OF if as_of_week is not None else None
        ),
        "data_freshness": _data_freshness_block(db, player_id, year),
    }
    evidence["evidence_hash"] = evidence_hash(evidence)
    return evidence


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


def _season_stats_block(
    db: Session,
    player_id: int,
    year: int,
    as_of_week: Optional[int] = None,
) -> list:
    query = db.query(DBPlayerSeasonStats).filter(
        DBPlayerSeasonStats.player_id == player_id,
        DBPlayerSeasonStats.year <= year,
    )
    if as_of_week is not None:
        # The target season's aggregate covers the post-cutoff weeks; only
        # completed prior seasons are safe to serve.
        query = query.filter(DBPlayerSeasonStats.year < year)

    rows = (
        query.order_by(DBPlayerSeasonStats.year.desc())
        .limit(_SEASON_HISTORY_YEARS)
        .all()
    )
    return [
        {
            "year": r.year,
            "games_played": r.games_played,
            "pass_att": r.pass_att,
            "pass_yd": r.pass_yd,
            "pass_td": r.pass_td,
            "pass_int": r.pass_int,
            "rush_att": r.rush_att,
            "rush_yd": r.rush_yd,
            "rush_td": r.rush_td,
            "targets": r.targets,
            "rec": r.rec,
            "rec_yd": r.rec_yd,
            "rec_td": r.rec_td,
            "fantasy_points_total": r.fantasy_points_total,
            "fantasy_points_avg": r.fantasy_points_avg,
            "fantasy_points_per_touch": r.fantasy_points_per_touch,
            # snap_pct is canonically 0-100 in this schema, not 0-1.
            "snap_pct": r.snap_pct,
            "air_yards": r.air_yards,
            "yac": r.yac,
            "wopr": r.wopr,
            "adp": r.adp,
            "adp_source": r.adp_source,
            "adp_times_drafted": r.adp_times_drafted,
            "source": r.source,
        }
        for r in rows
    ]


def _criteria_block(
    db: Session,
    player_id: int,
    year: int,
    week: Optional[int],
) -> Optional[Dict[str, Any]]:
    """The exact criteria the deterministic model would use for this scope.

    This is the single most useful block in the pack: it lets the agent see
    what the formula sees, and therefore reason about where the formula is
    likely to be wrong rather than re-deriving it badly.

    Imported lazily because ``projection_criteria_builder`` is a heavy module
    and most callers of this file do not need it.

    This is what makes ``build_evidence`` a writer, not a reader: the builder
    below lazily creates missing team/defense stat rows as it computes.
    """
    from pigskin_mastermind.services.projection_criteria_builder import (
        ProjectionCriteriaBuilder,
    )

    # allow_network=False: the per-player ESPN fetch costs ~3.3s against ~35ms
    # for a player with local data, and the agent already has web access for
    # anything the network path would add.
    builder = ProjectionCriteriaBuilder(db, allow_network=False)

    if week is None:
        criteria = builder.build_yearly_criteria(player_id, year)
        scope = "yearly"
    else:
        criteria = builder.build_weekly_criteria(player_id, week, year)
        scope = "weekly"

    return {"scope": scope, "fields": asdict(criteria)}


def _game_logs_block(
    db: Session,
    player_id: int,
    year: int,
    as_of_week: Optional[int] = None,
) -> list:
    query = db.query(DBPlayerGameLog).filter(
        DBPlayerGameLog.player_id == player_id,
        DBPlayerGameLog.year <= year,
    )
    if as_of_week is not None:
        # Only the target season is truncated. Prior seasons are entirely in
        # the past relative to the cutoff and stay whole.
        query = query.filter(
            or_(
                DBPlayerGameLog.year < year,
                DBPlayerGameLog.week < as_of_week,
            )
        )

    rows = (
        query.order_by(
            DBPlayerGameLog.year.desc(),
            DBPlayerGameLog.week.desc(),
        )
        .limit(_GAME_LOG_LIMIT)
        .all()
    )
    # Queried newest-first so the limit keeps recent games; the agent reads
    # them chronologically.
    rows.reverse()
    return [
        {
            "year": r.year,
            "week": r.week,
            "opponent": r.opponent,
            "pass_att": r.pass_att,
            "pass_yd": r.pass_yd,
            "pass_td": r.pass_td,
            "pass_int": r.pass_int,
            "rush_att": r.rush_att,
            "rush_yd": r.rush_yd,
            "rush_td": r.rush_td,
            "targets": r.targets,
            "rec": r.rec,
            "rec_yd": r.rec_yd,
            "rec_td": r.rec_td,
            "fumbles_lost": r.fumbles_lost,
            "fantasy_points": r.fantasy_points,
            "source": r.source,
        }
        for r in rows
    ]


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _existing_projections_block(
    db: Session,
    player_id: int,
    year: int,
    week: Optional[int],
) -> Dict[str, Any]:
    """Every stored projection for this scope, by source.

    Unfiltered by source on purpose: the agent should see that ``espn`` and
    ``model`` disagree, and by how much, before forming its own view.
    """
    query = db.query(DBPlayerProjection).filter(
        DBPlayerProjection.player_id == player_id,
        DBPlayerProjection.year == year,
    )
    if week is None:
        query = query.filter(DBPlayerProjection.week.is_(None))
    else:
        query = query.filter(DBPlayerProjection.week == week)

    return {
        row.source: {
            "projected_points": row.projected_points,
            "floor": row.floor,
            "ceiling": row.ceiling,
            "std_dev": row.std_dev,
            "expected_games": row.expected_games,
            "computed_at": _iso(row.computed_at),
        }
        for row in query.all()
    }


def _data_freshness_block(
    db: Session,
    player_id: int,
    year: int,
) -> Dict[str, Any]:
    """When each underlying data source was last written.

    This block is what tells the agent where the database is *blind*, and so
    whether a web lookup is worth its cost. Without it the agent has no way to
    distinguish "this player has no recent news" from "nobody has synced stats
    since March".
    """
    logs_at = (
        db.query(func.max(DBPlayerGameLog.updated_at))
        .filter(DBPlayerGameLog.player_id == player_id)
        .scalar()
    )
    season_at = (
        db.query(func.max(DBPlayerSeasonStats.updated_at))
        .filter(DBPlayerSeasonStats.player_id == player_id)
        .scalar()
    )
    adp_at = (
        db.query(func.max(DBPlayerSeasonStats.updated_at))
        .filter(
            DBPlayerSeasonStats.player_id == player_id,
            # ==, not <=: ADP is a per-season value, and this block's whole
            # job is to tell the agent where the database is blind. A prior
            # season's ADP is close to worthless for the requested season, so
            # reporting its timestamp here would read as "recently written"
            # about a number that is a full season stale. When the requested
            # season has no ADP row yet (the normal preseason state), the
            # honest answer is None.
            DBPlayerSeasonStats.year == year,
            DBPlayerSeasonStats.adp.isnot(None),
        )
        .scalar()
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "game_logs_updated_at": _iso(logs_at),
        "season_stats_updated_at": _iso(season_at),
        "adp_updated_at": _iso(adp_at),
    }


def _schedule_block(
    db: Session,
    player: DBPlayer,
    year: int,
    week: Optional[int],
    as_of_week: Optional[int] = None,
) -> list:
    """The player's team schedule, forward-looking from *week*.

    ``DBNFLGame`` is the only forward-looking table in the schema. A NULL
    ``home_score`` is how "not yet played" is represented.
    """
    team = player.nfl_team
    query = db.query(DBNFLGame).filter(
        DBNFLGame.year == year,
        or_(DBNFLGame.home_team == team, DBNFLGame.away_team == team),
    )
    if week is not None:
        query = query.filter(DBNFLGame.week >= week)

    games = []
    for g in query.order_by(DBNFLGame.week).all():
        played = g.home_score is not None
        if as_of_week is not None and g.week >= as_of_week:
            # The score exists in the database but had not happened yet at the
            # cutoff. Reporting it would hand a backtest the answer.
            played = False
        games.append(
            {
                "week": g.week,
                "opponent": g.away_team if g.home_team == team else g.home_team,
                "home": g.home_team == team,
                "played": played,
                "roof": g.roof,
            }
        )
    return games


def _sportsbook_block(
    db: Session,
    player_id: int,
    as_of_week: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Prop-derived projection, when props for this player are stored.

    Resolved by ``DBPlayer.id`` rather than by name: the name path matches
    ``description ILIKE '%name%'``, which both over- and under-matches.

    Returns ``None`` rather than a zeroed structure so the agent can tell
    "no props available" from "props say zero".

    Under a cutoff there is no historical line to serve: books price
    upcoming games, so stored props either belong to a game that had not
    been played yet at the cutoff or to a different season entirely (the
    only table involved here, ``DBSportsbookOdds``, is never checked against
    ``commence_time`` elsewhere in this module). Bail before doing any query
    work.
    """
    if as_of_week is not None:
        return None

    from pigskin_mastermind.services.sportsbook_projection_service import (
        SportsbookProjectionService,
    )

    result = SportsbookProjectionService(db).project_player_by_id(player_id)
    if not result.get("categories"):
        return None
    return {
        "total_projected_points": result.get("total_projected_points"),
        "categories": result.get("categories"),
    }
