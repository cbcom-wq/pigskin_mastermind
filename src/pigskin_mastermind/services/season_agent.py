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

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBLeague, DBLineupSlot, DBMatchup, DBPlayer, DBPlayerNews,
    DBPlayerProjection, DBRosterSpot, DBTeam, get_scoring_settings,
)
from pigskin_mastermind.services.lineup_locks import LockIndex
from pigskin_mastermind.services.lineup_manager import plan_lineup
from pigskin_mastermind.services.nfl_schedule import ScheduleIndex
from pigskin_mastermind.services.season_scheduler import league_now

NEWS_PER_PLAYER = 3


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
