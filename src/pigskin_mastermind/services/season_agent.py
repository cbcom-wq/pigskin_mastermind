"""The Claude team-manager agent: evidence in, validated proposal out.

Mirrors services/agent_evidence.py and services/agent_projection.py. Nothing in
this module calls an LLM -- it assembles a document and validates whatever comes
back, which is what makes both halves testable without one.

The pack embeds ESPN news headlines. Those are untrusted third-party text and
are carried as data. The protection that matters is not phrasing in a prompt:
``validate_lineup_result`` will only ever accept a legal lineup made of this
team's own players, so the worst a poisoned headline can do is argue for a
bad-but-legal start/sit.
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import SessionLocal

from pigskin_mastermind.models.database import (
    DBLeague, DBLineupSlot, DBManagerRun, DBMatchup, DBPlayer, DBPlayerNews,
    DBPlayerProjection, DBRosterSpot, DBTeam, get_scoring_settings,
)
from pigskin_mastermind.services.lineup_locks import LockIndex
from pigskin_mastermind.services.lineup_manager import FLEX_SLOT, plan_lineup
from pigskin_mastermind.services.mock_draft import BENCH_SLOT, FLEX_ELIGIBLE
from pigskin_mastermind.services.nfl_schedule import ScheduleIndex
from pigskin_mastermind.services.season_scheduler import league_now

logger = logging.getLogger(__name__)

NEWS_PER_PLAYER = 3

#: Generous -- a real analysis reads news and reasons over a full roster -- but
#: bounded, so a wedged subprocess does not hold a run open forever.
AGENT_TIMEOUT_SECONDS = 300


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def build_team_evidence(
    db: Session,
    team: DBTeam,
    week: int,
    year: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Everything known about one team's lineup decision, as one document.

    *now* defaults to :func:`league_now`, not ``utcnow``: it is compared against
    ``DBNFLGame.kickoff_at`` through LockIndex, and that column is a naive US
    Eastern wall clock. A UTC default would report a Sunday-morning roster as
    already locked. See Ruling T14-1.
    """
    league = db.query(DBLeague).filter_by(league_id=team.league_id).first()
    now = now or league_now()
    year = year or (league.year if league else now.year)

    locks = LockIndex(db)
    schedule = ScheduleIndex(db)

    players = (
        db.query(DBPlayer)
        .join(DBRosterSpot, DBRosterSpot.player_id == DBPlayer.id)
        .filter(
            DBRosterSpot.team_id == team.id,
            DBRosterSpot.dropped_at.is_(None),
        )
        .all()
    )

    projections = {
        row.player_id: row
        for row in db.query(DBPlayerProjection).filter(
            DBPlayerProjection.player_id.in_([p.id for p in players] or [0]),
            DBPlayerProjection.year == year,
            DBPlayerProjection.week == week,
        )
    }
    current_slots = {
        row.player_id: row.slot
        for row in db.query(DBLineupSlot).filter_by(
            team_id=team.id, year=year, week=week,
        )
    }

    roster: List[Dict[str, Any]] = []
    for player in players:
        projection = projections.get(player.id)
        news = (
            db.query(DBPlayerNews)
            .filter_by(player_id=player.id)
            .order_by(DBPlayerNews.published_at.desc())
            .limit(NEWS_PER_PLAYER)
            .all()
        )
        roster.append({
            "player_id": player.id,
            "name": player.name,
            "position": player.position,
            "nfl_team": player.nfl_team,
            "projected_points": projection.projected_points if projection else None,
            "floor": projection.floor if projection else None,
            "ceiling": projection.ceiling if projection else None,
            "injury_status": player.injury_status,
            "on_bye": schedule.is_bye(player.nfl_team, year, week),
            "locked": locks.is_locked(player.nfl_team, year, week, now),
            "kickoff_at": _iso(locks.kickoff(player.nfl_team, year, week)),
            "current_slot": current_slots.get(player.id),
            # Untrusted third-party text. Data, not instructions.
            "news": [
                {"headline": n.headline, "published_at": _iso(n.published_at),
                 "source_url": n.source_url}
                for n in news
            ],
        })

    roster.sort(
        key=lambda r: (-(r["projected_points"] or 0.0), r["player_id"]),
    )

    baseline = plan_lineup(db, team, year, week, now, league=league)
    matchup = _matchup_block(db, league, team, week, year, now)

    return {
        "context": {
            "league_id": league.id if league else None,
            "league_key": team.league_id,
            "team_id": team.id,
            "team_name": team.name,
            "year": year,
            "week": week,
            "generated_at": _iso(league_now()),
            "as_of": _iso(now),
        },
        "league": {
            "name": league.name if league else None,
            "roster_slots": (league.roster_slots if league else None) or {},
            "scoring_settings": get_scoring_settings(league),
            "current_week": league.current_week if league else week,
            "regular_season_weeks": league.regular_season_weeks if league else None,
            "playoff_teams": league.playoff_teams if league else None,
        },
        "team": {
            "id": team.id,
            "name": team.name,
            "record": {
                "wins": team.wins or 0,
                "losses": team.losses or 0,
                "ties": team.ties or 0,
            },
            "points_for": team.total_points or 0.0,
        },
        "matchup": matchup,
        "roster": roster,
        "baseline": {
            "projected_total": baseline.projected_total,
            "slots": [
                {"player_id": d.player_id, "name": d.name, "slot": d.slot,
                 "projected_points": d.projected_points, "reason": d.reason}
                for d in baseline.decisions
            ],
        },
        "standings": _standings_block(db, team),
    }


def _matchup_block(
    db: Session,
    league: Optional[DBLeague],
    team: DBTeam,
    week: int,
    year: int,
    now: datetime,
) -> Dict[str, Any]:
    if league is None:
        return {"opponent_name": None}

    matchup = (
        db.query(DBMatchup)
        .filter(
            DBMatchup.league_id == league.id,
            DBMatchup.year == year,
            DBMatchup.week == week,
            (DBMatchup.home_team_id == team.id)
            | (DBMatchup.away_team_id == team.id),
        )
        .first()
    )
    if matchup is None:
        return {"opponent_name": None}

    opponent_id = (
        matchup.away_team_id if matchup.home_team_id == team.id
        else matchup.home_team_id
    )
    opponent = db.query(DBTeam).filter_by(id=opponent_id).first()
    if opponent is None:
        return {"opponent_name": None}

    opponent_plan = plan_lineup(db, opponent, year, week, now, league=league)
    return {
        "opponent_id": opponent.id,
        "opponent_name": opponent.name,
        "opponent_record": {
            "wins": opponent.wins or 0,
            "losses": opponent.losses or 0,
            "ties": opponent.ties or 0,
        },
        "opponent_projected_total": opponent_plan.projected_total,
        "is_playoff": bool(matchup.is_playoff),
        "round_name": matchup.round_name,
    }


def _standings_block(db: Session, team: DBTeam) -> List[Dict[str, Any]]:
    teams = (
        db.query(DBTeam)
        .filter(DBTeam.league_id == team.league_id)
        .all()
    )
    ordered = sorted(
        teams, key=lambda t: (-(t.wins or 0), -(t.total_points or 0.0), t.id),
    )
    return [
        {
            "rank": i + 1, "team_id": t.id, "name": t.name,
            "wins": t.wins or 0, "losses": t.losses or 0, "ties": t.ties or 0,
            "points_for": t.total_points or 0.0,
        }
        for i, t in enumerate(ordered)
    ]


class LineupRejected(ValueError):
    """A proposed lineup is not legal, so nothing is written."""


def validate_lineup_result(
    db: Session,
    result: Dict[str, Any],
    team: DBTeam,
    year: int,
    week: int,
    now: datetime,
) -> Dict[str, Any]:
    """Check a proposal against the league's rules and this team's roster.

    Scope is checked before anything else, exactly as record_llm_projection
    does: a result for the wrong week is not a bad lineup, it is a different
    question entirely.

    This function is the security boundary. Whatever an agent was told by a
    news headline, only a legal lineup made of this team's own players can be
    written.
    """
    if result.get("year") != year:
        raise LineupRejected(
            f"result year {result.get('year')} does not match --year {year}",
        )
    if result.get("week") != week:
        raise LineupRejected(
            f"result week {result.get('week')} does not match --week {week}",
        )
    if result.get("team_id") != team.id:
        raise LineupRejected(
            f"result team {result.get('team_id')} does not match team {team.id}",
        )

    slots = result.get("slots")
    if not isinstance(slots, list) or not slots:
        raise LineupRejected("result has no slots")

    league = db.query(DBLeague).filter_by(league_id=team.league_id).first()
    roster_slots = (league.roster_slots if league else None) or {}

    rostered = {
        row.player_id
        for row in db.query(DBRosterSpot).filter(
            DBRosterSpot.team_id == team.id,
            DBRosterSpot.dropped_at.is_(None),
        )
    }
    positions = {
        p.id: p
        for p in db.query(DBPlayer).filter(DBPlayer.id.in_(rostered or {0}))
    }

    seen: set = set()
    counts: Dict[str, int] = {}
    for entry in slots:
        player_id = entry.get("player_id")
        slot = entry.get("slot")
        if player_id not in rostered:
            raise LineupRejected(f"player {player_id} is not on this team")
        if player_id in seen:
            raise LineupRejected(f"player {player_id} appears twice")
        seen.add(player_id)
        counts[slot] = counts.get(slot, 0) + 1

        if slot == FLEX_SLOT and positions[player_id].position not in FLEX_ELIGIBLE:
            raise LineupRejected(
                f"{positions[player_id].name} is a "
                f"{positions[player_id].position} and cannot fill FLEX",
            )

    # Slot counts before completeness: a lineup missing its QB should say so by
    # name. The completeness check would otherwise swallow every shape error
    # into one generic message, since a dropped starter is also a missing
    # player. See Ruling T16-1.
    for slot, required in roster_slots.items():
        if slot == BENCH_SLOT:
            continue
        if counts.get(slot, 0) != required:
            raise LineupRejected(
                f"slot {slot} has {counts.get(slot, 0)} players, expected {required}",
            )

    if seen != rostered:
        raise LineupRejected(
            "result must place every rostered player, including the bench",
        )

    locks = LockIndex(db)
    current = {
        row.player_id: row.slot
        for row in db.query(DBLineupSlot).filter_by(
            team_id=team.id, year=year, week=week,
        )
    }
    for entry in slots:
        player = positions[entry["player_id"]]
        if not locks.is_locked(player.nfl_team, year, week, now):
            continue
        existing_slot = current.get(player.id)
        if existing_slot is not None and existing_slot != entry["slot"]:
            raise LineupRejected(
                f"{player.name} is locked and cannot move from "
                f"{existing_slot} to {entry['slot']}",
            )

    for change in result.get("changes") or []:
        if not (change.get("reasoning") or "").strip():
            raise LineupRejected(
                f"change for player {change.get('player_id')} has no reasoning",
            )

    return result


def record_proposal(
    db: Session,
    result: Dict[str, Any],
    team: DBTeam,
    year: int,
    week: int,
    run_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> DBManagerRun:
    """Validate a result and store it as a proposal. Writes no lineup rows."""
    now = now or league_now()
    league = db.query(DBLeague).filter_by(league_id=team.league_id).first()

    run = (
        db.query(DBManagerRun).filter_by(id=run_id).first() if run_id else None
    )
    if run is None:
        run = DBManagerRun(
            league_id=league.id if league else None,
            team_id=team.id, year=year, week=week, kind="lineup",
        )
        db.add(run)

    try:
        validate_lineup_result(db, result, team, year, week, now)
    except LineupRejected as exc:
        run.status = "failed"
        run.error = str(exc)
        run.finished_at = datetime.utcnow()
        db.commit()
        raise

    run.status = "proposed"
    run.proposal = result
    run.rationale = result.get("rationale")
    run.citations = result.get("citations")
    run.error = None
    run.finished_at = datetime.utcnow()
    db.commit()
    return run


def apply_proposal(db: Session, run: DBManagerRun) -> int:
    """Write a proposed lineup. Returns rows written."""
    if run.status != "proposed":
        raise LineupRejected(f"run {run.id} is {run.status}, not proposed")

    team = db.query(DBTeam).filter_by(id=run.team_id).one()
    locks = LockIndex(db)
    now = league_now()

    existing = {
        row.player_id: row
        for row in db.query(DBLineupSlot).filter_by(
            team_id=team.id, year=run.year, week=run.week,
        )
    }
    projections = {
        row.player_id: row.projected_points
        for row in db.query(DBPlayerProjection).filter(
            DBPlayerProjection.year == run.year,
            DBPlayerProjection.week == run.week,
        )
    }

    written = 0
    for entry in run.proposal.get("slots", []):
        player = db.query(DBPlayer).filter_by(id=entry["player_id"]).one()
        row = existing.get(player.id)
        if row is None:
            row = DBLineupSlot(
                team_id=team.id, year=run.year, week=run.week, player_id=player.id,
            )
            db.add(row)
        row.slot = entry["slot"]
        row.set_by = "agent"
        row.projected_points = projections.get(player.id, 0.0) or 0.0
        row.locked_at = (
            locks.kickoff(player.nfl_team, run.year, run.week)
            if locks.is_locked(player.nfl_team, run.year, run.week, now)
            else None
        )
        written += 1

    run.status = "applied"
    db.commit()
    return written


def start_manager_run(
    db: Session, team: DBTeam, week: int, year: Optional[int] = None,
) -> DBManagerRun:
    """Create a ``running`` manager run, refusing a concurrent duplicate."""
    league = db.query(DBLeague).filter_by(league_id=team.league_id).first()
    year = year or (league.year if league else league_now().year)

    existing = (
        db.query(DBManagerRun)
        .filter_by(team_id=team.id, year=year, week=week, status="running")
        .first()
    )
    if existing is not None:
        raise LineupRejected(
            f"a manager run is already running for this team "
            f"(run {existing.id})",
        )

    run = DBManagerRun(
        league_id=league.id if league else None,
        team_id=team.id, year=year, week=week,
        kind="lineup", status="running", started_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    return run


def _agent_prompt(team_id: int, year: int, week: int, run_id: int) -> str:
    return (
        f"Use the season-team-manager skill to set the lineup for team "
        f"{team_id}, year {year}, week {week}. "
        f"Record your result with: pigskin season propose-lineup "
        f"--result-file <file> --team {team_id} --year {year} --week {week} "
        f"--run-id {run_id}"
    )


def run_agent_subprocess(
    run_id: int,
    team_id: int,
    year: int,
    week: int,
    timeout: int = AGENT_TIMEOUT_SECONDS,
) -> None:
    """Launch ``claude -p`` and record whether it produced a proposal.

    Runs in a background task with its own session -- the request's session is
    long gone by the time this finishes.

    A clean exit is not success. The agent records its result through the
    propose-lineup CLI, which is what moves the run to ``proposed``; if the run
    is still ``running`` afterwards, the agent produced nothing usable.
    """
    repo_root = Path(__file__).resolve().parents[3]
    started = datetime.utcnow()
    error: Optional[str] = None

    try:
        completed = subprocess.run(
            [
                "claude", "-p", _agent_prompt(team_id, year, week, run_id),
                "--output-format", "json",
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            error = (completed.stderr or completed.stdout or "").strip()[:2000]
    except subprocess.TimeoutExpired:
        error = f"agent timed out after {timeout}s"
    except FileNotFoundError:
        error = (
            "the `claude` CLI was not found on PATH; the team manager needs "
            "Claude Code installed and signed in"
        )
    except Exception as exc:
        logger.exception("Manager agent subprocess failed")
        error = str(exc)[:2000]

    db = SessionLocal()
    try:
        run = db.query(DBManagerRun).filter_by(id=run_id).first()
        if run is None:
            return
        run.duration_ms = int(
            (datetime.utcnow() - started).total_seconds() * 1000,
        )
        if run.status == "running":
            # propose-lineup never fired, or fired and was rejected.
            run.status = "failed"
            run.error = error or "agent exited cleanly but recorded no proposal"
            run.finished_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
