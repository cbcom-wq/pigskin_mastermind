"""Populate ``player_advanced_metrics`` from snap counts, NGS, and game logs.

Three feeds with three different identity keys and three different coverage
levels, all landing in one table. Each importer is independent so a feed that
fails or has not been published yet costs only its own metrics.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBNFLGame, DBPlayer, DBPlayerAdvancedMetric, DBPlayerGameLog,
)
from pigskin_mastermind.utils.nfl_teams import normalize_team

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard mirrors nfl_data_service
    import nfl_data_py as nfl
except ImportError:  # pragma: no cover
    nfl = None


# ---------------------------------------------------------------------------
# Team resolution
# ---------------------------------------------------------------------------


def team_by_opponent(db: Session, year: int) -> Dict[Tuple[int, str], str]:
    """``(week, opponent) -> the team that played them`` for one season.

    Share metrics need a denominator, and a denominator needs the right team.
    Game logs carry an opponent but no team, and ``DBPlayer.nfl_team`` holds
    the player's *current* club — so computing a 2025 share from it would put
    every player who has since moved into the wrong team's total, silently.

    A team plays exactly one game a week, so the opponent identifies one game
    and the player is the other side of it.
    """
    index: Dict[Tuple[int, str], str] = {}
    for game in db.query(DBNFLGame).filter(DBNFLGame.year == year).all():
        home = normalize_team(game.home_team)
        away = normalize_team(game.away_team)
        if not home or not away:
            continue
        index[(game.week, away)] = home
        index[(game.week, home)] = away
    return index


# ---------------------------------------------------------------------------
# Importers
# ---------------------------------------------------------------------------


def import_snap_metrics(db: Session, years: List[int]) -> int:
    """Snap share, from ``import_snap_counts``. Matches on ``pfr_player_id``."""
    if nfl is None:
        raise ImportError("nfl_data_py is not installed.")

    df = nfl.import_snap_counts(years)
    if df.empty:
        return 0

    by_pfr = {
        row[1]: row[0]
        for row in db.query(DBPlayer.id, DBPlayer.pfr_id)
        .filter(DBPlayer.pfr_id.isnot(None)).all()
        if row[1]
    }

    written = 0
    for _, row in df.iterrows():
        player_id = by_pfr.get(_str(row.get("pfr_player_id")))
        if player_id is None:
            continue
        pct = _float(row.get("offense_pct"))
        if pct is None:
            continue
        # nflverse ships this 0-1; the app's canonical snap scale is 0-100.
        _upsert(
            db, player_id, _int(row.get("season")), _int(row.get("week")),
            "snap_pct", pct * 100.0, "snap_counts",
        )
        written += 1

    db.commit()
    return written


#: NGS feed -> {nflverse column: metric key}.
_NGS_COLUMNS = {
    "receiving": {
        "avg_separation": "ngs_separation",
        "avg_cushion": "ngs_cushion",
        "percent_share_of_intended_air_yards": "ngs_air_yards_share",
        "avg_intended_air_yards": "ngs_intended_air_yards",
        "catch_percentage": "ngs_catch_pct",
    },
    "rushing": {
        "rush_yards_over_expected": "ngs_ryoe",
        "efficiency": "ngs_rush_efficiency",
        "percent_attempts_gte_eight_defenders": "ngs_stacked_box_pct",
    },
    "passing": {
        "avg_time_to_throw": "ngs_time_to_throw",
        "aggressiveness": "ngs_aggressiveness",
    },
}


def import_ngs_metrics(db: Session, years: List[int]) -> int:
    """Next Gen Stats, three feeds. Matches on ``gsis_id``."""
    if nfl is None:
        raise ImportError("nfl_data_py is not installed.")

    by_gsis = _gsis_index(db)
    written = 0

    for feed, columns in _NGS_COLUMNS.items():
        try:
            df = nfl.import_ngs_data(feed, years)
        except Exception:
            # One feed being unpublished must not cost the other two.
            logger.exception("NGS %s import failed", feed)
            continue
        if df.empty:
            continue

        for _, row in df.iterrows():
            week = _int(row.get("week"))
            # week 0 is NGS's season aggregate. Season figures are derived
            # from the weekly rows instead, so there is one definition and a
            # season total can never be read as week zero.
            if not week:
                continue
            player_id = by_gsis.get(_str(row.get("player_gsis_id")))
            if player_id is None:
                continue

            year = _int(row.get("season"))
            for column, key in columns.items():
                value = _float(row.get(column))
                if value is None:
                    continue
                _upsert(db, player_id, year, week, key, value, f"ngs_{feed}")
                written += 1

    db.commit()
    return written


def compute_share_metrics(db: Session, years: List[int]) -> int:
    """Target / carry / touch share, from our own game logs.

    Derived rather than imported because the nflverse weekly feed that carries
    ``target_share`` 404s for 2025 onward, and the game logs we already hold
    have the counts needed to build it.
    """
    written = 0

    for year in years:
        opponents = team_by_opponent(db, year)
        if not opponents:
            logger.warning("No schedule for %s; cannot resolve share teams", year)
            continue

        rows = (
            db.query(
                DBPlayerGameLog.player_id, DBPlayerGameLog.week,
                DBPlayerGameLog.opponent, DBPlayerGameLog.targets,
                DBPlayerGameLog.rush_att,
            )
            .filter(DBPlayerGameLog.year == year)
            .all()
        )
        if not rows:
            continue

        # (week, team) -> totals, and the per-player rows that feed them.
        totals: Dict[Tuple[int, str], List[float]] = defaultdict(lambda: [0.0, 0.0])
        entries = []
        for player_id, week, opponent, targets, carries in rows:
            opponent = normalize_team(opponent)
            if not week or not opponent:
                continue
            team = opponents.get((week, opponent))
            if not team:
                continue
            targets = float(targets or 0)
            carries = float(carries or 0)
            totals[(week, team)][0] += targets
            totals[(week, team)][1] += carries
            entries.append((player_id, week, team, targets, carries))

        for player_id, week, team, targets, carries in entries:
            team_targets, team_carries = totals[(week, team)]
            touches = targets + carries
            team_touches = team_targets + team_carries

            for key, part, whole in (
                ("target_share", targets, team_targets),
                ("rush_share", carries, team_carries),
                ("touch_share", touches, team_touches),
            ):
                # A zero denominator is a team with no recorded volume that
                # week, not a player with a zero share.
                if whole <= 0:
                    continue
                _upsert(
                    db, player_id, year, week, key,
                    round(part / whole * 100.0, 2), "derived",
                )
                written += 1

    db.commit()
    return written


def import_production_metrics(db: Session, years: List[int]) -> int:
    """Mirror fantasy points into the metric table.

    The buy-low signal compares usage against production, and the scan reads
    one table. Copying the number here keeps that comparison a single query
    rather than a join with different semantics on each side.
    """
    written = 0
    rows = (
        db.query(
            DBPlayerGameLog.player_id, DBPlayerGameLog.year,
            DBPlayerGameLog.week, DBPlayerGameLog.fantasy_points,
        )
        .filter(DBPlayerGameLog.year.in_(years))
        .all()
    )
    for player_id, year, week, points in rows:
        if not week:
            continue
        _upsert(
            db, player_id, year, week, "fantasy_points",
            float(points or 0.0), "game_logs",
        )
        written += 1

    db.commit()
    return written


def import_all(db: Session, years: List[int]) -> Dict[str, int]:
    """Run every importer, reporting each independently.

    A feed that has not been published yet — the weekly and seasonal ones both
    404 for 2025 onward — must not stop the ones that have.
    """
    results: Dict[str, int] = {}
    for name, fn in (
        ("snaps", import_snap_metrics),
        ("ngs", import_ngs_metrics),
        ("shares", compute_share_metrics),
        ("production", import_production_metrics),
    ):
        try:
            results[name] = fn(db, years)
        except Exception as exc:
            logger.exception("Advanced metric import %s failed", name)
            db.rollback()
            results[name] = -1
            results[f"{name}_error"] = str(exc)[:200]
    return results


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _gsis_index(db: Session) -> Dict[str, int]:
    """``gsis_id -> player id``, preferring a named row over a placeholder.

    Duplicate gsis ids send metrics to a nameless stub while the real player
    gets nothing — see ``NFLDataService._gsis_index`` for the full story.
    """
    from pigskin_mastermind.services.player_identity import is_placeholder_name

    index: Dict[str, int] = {}
    best: Dict[str, tuple] = {}
    for player_id, gsis_id, name in db.query(
        DBPlayer.id, DBPlayer.gsis_id, DBPlayer.name,
    ).filter(DBPlayer.gsis_id.isnot(None)).all():
        if not gsis_id:
            continue
        rank = (1 if is_placeholder_name(name) else 0, player_id)
        if gsis_id not in best or rank < best[gsis_id]:
            best[gsis_id] = rank
            index[gsis_id] = player_id
    return index


def _upsert(
    db: Session, player_id: int, year: Optional[int], week: Optional[int],
    metric: str, value: float, source: str,
) -> None:
    if not player_id or not year or not week:
        return
    row = (
        db.query(DBPlayerAdvancedMetric)
        .filter_by(player_id=player_id, year=year, week=week, metric=metric)
        .first()
    )
    if row is None:
        row = DBPlayerAdvancedMetric(
            player_id=player_id, year=year, week=week, metric=metric,
        )
        db.add(row)
    row.value = value
    row.source = source
    row.updated_at = datetime.utcnow()


def _float(value) -> Optional[float]:
    if value is None or (isinstance(value, float) and str(value) == "nan"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> Optional[int]:
    parsed = _float(value)
    return int(parsed) if parsed is not None else None


def _str(value) -> Optional[str]:
    if value is None or (isinstance(value, float) and str(value) == "nan"):
        return None
    return str(value)
