"""Score lineups from real stats, settle matchups, keep standings honest.

Runs every 60 seconds while games are live, so every operation here is
idempotent by construction: scoring a half-played week and scoring it again
must converge on the same numbers rather than accumulate.

Player matching falls back to PlayerIdentityService and back-fills ``espn_id``
on first success, so the expensive path is paid once per player per season
rather than on every poll. A player who still cannot be matched is counted and
skipped — crediting points to the wrong roster is far worse than crediting none.
"""

import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBLeague, DBLineupSlot, DBMatchup, DBPlayer, DBTeam, get_scoring_settings,
)
from pigskin_mastermind.services.espn_boxscore import (
    BoxScoreClient, parse_player_stats, parse_team_defense_stats,
)
from pigskin_mastermind.services.player_identity import PlayerIdentityService
from pigskin_mastermind.services.scoring import score_stat_line
from pigskin_mastermind.utils.nfl_teams import normalize_team

logger = logging.getLogger(__name__)

BENCH_SLOT = "BENCH"


def refresh_week(
    db: Session,
    league: DBLeague,
    week: int,
    client: Optional[BoxScoreClient] = None,
) -> Dict[str, Any]:
    """Pull box scores for *week*, score lineups, settle if complete."""
    client = client or BoxScoreClient()
    year = league.year
    settings = get_scoring_settings(league)

    events = client.week_events(year, week)
    started = [e for e in events if e.get("status") in ("in", "post")]

    player_stats: Dict[str, Dict[str, Any]] = {}
    defense_stats: Dict[str, Dict[str, Any]] = {}

    for event in started:
        summary = client.event_summary(event["event_id"])
        if not summary:
            continue
        for row in parse_player_stats(summary):
            player_stats[row["espn_id"]] = row
        for row in parse_team_defense_stats(summary):
            team = normalize_team(row.get("team"))
            if team:
                defense_stats[team] = row

    scored, unmatched = _apply_stats(
        db, league, week, player_stats, defense_stats, settings,
    )
    _recompute_matchups(db, league, week, any_started=bool(started))

    finalized = bool(events) and all(e.get("status") == "post" for e in events)
    if finalized:
        _finalize_week(db, league, week)

    db.commit()
    return {
        "scored": scored, "unmatched": unmatched,
        "finalized": finalized, "week": week,
    }


def _apply_stats(
    db: Session,
    league: DBLeague,
    week: int,
    player_stats: Dict[str, Dict[str, Any]],
    defense_stats: Dict[str, Dict[str, Any]],
    settings: Dict[str, Any],
) -> tuple:
    identity = PlayerIdentityService(db)
    team_ids = [
        t.id for t in db.query(DBTeam.id).filter(
            DBTeam.league_id == league.league_id,
        )
    ]
    rows = (
        db.query(DBLineupSlot)
        .filter(
            DBLineupSlot.team_id.in_(team_ids),
            DBLineupSlot.year == league.year,
            DBLineupSlot.week == week,
        )
        .all()
    )

    players: Dict[int, DBPlayer] = {
        p.id: p for p in db.query(DBPlayer).filter(
            DBPlayer.id.in_([r.player_id for r in rows] or [0]),
        )
    }

    scored = 0
    for row in rows:
        player = players.get(row.player_id)
        if player is None:
            continue

        if player.position == "DEF":
            team = normalize_team(player.nfl_team)
            stats = (defense_stats.get(team) or {}).get("stats") if team else None
        else:
            stats = _stats_for_player(player, player_stats, identity, db)

        if stats is None:
            continue
        row.actual_points = round(score_stat_line(stats, settings), 2)
        scored += 1

    matched_ids = {
        p.espn_id for p in players.values() if p.espn_id
    }
    unmatched = len([k for k in player_stats if k not in matched_ids])
    return scored, unmatched


def _stats_for_player(
    player: DBPlayer,
    player_stats: Dict[str, Dict[str, Any]],
    identity: PlayerIdentityService,
    db: Session,
) -> Optional[Dict[str, Any]]:
    """Stat line for one player, resolving and back-filling espn_id once."""
    if player.espn_id and player.espn_id in player_stats:
        return player_stats[player.espn_id]["stats"]

    for espn_id, row in player_stats.items():
        if player.espn_id == espn_id:
            return row["stats"]
        resolved = identity.resolve(
            espn_id=espn_id,
            name=row.get("name"),
            position=player.position,
            nfl_team=row.get("team"),
        )
        if resolved is not None and resolved.id == player.id:
            # Pay the resolution cost once per player per season, not per poll.
            if not player.espn_id:
                player.espn_id = espn_id
                db.flush()
            return row["stats"]
    return None


def _recompute_matchups(
    db: Session, league: DBLeague, week: int, any_started: bool = True,
) -> None:
    matchups = (
        db.query(DBMatchup)
        .filter_by(league_id=league.id, year=league.year, week=week)
        .all()
    )
    for matchup in matchups:
        matchup.home_points = _starter_total(db, league, matchup.home_team_id, week)
        matchup.away_points = _starter_total(db, league, matchup.away_team_id, week)
        # Polling before kickoff must leave the week alone; a matchup is only
        # in progress once one of its games has actually started.
        if any_started and matchup.status == "scheduled":
            matchup.status = "in_progress"


def _starter_total(
    db: Session, league: DBLeague, team_id: Optional[int], week: int,
) -> float:
    if team_id is None:
        return 0.0
    rows = (
        db.query(DBLineupSlot)
        .filter(
            DBLineupSlot.team_id == team_id,
            DBLineupSlot.year == league.year,
            DBLineupSlot.week == week,
            DBLineupSlot.slot != BENCH_SLOT,
        )
        .all()
    )
    return round(sum(r.actual_points or 0.0 for r in rows), 2)


def _finalize_week(db: Session, league: DBLeague, week: int) -> None:
    matchups = (
        db.query(DBMatchup)
        .filter_by(league_id=league.id, year=league.year, week=week)
        .all()
    )
    for matchup in matchups:
        matchup.status = "final"
        if matchup.home_points > matchup.away_points:
            matchup.winner_team_id = matchup.home_team_id
        elif matchup.away_points > matchup.home_points:
            matchup.winner_team_id = matchup.away_team_id
        else:
            matchup.winner_team_id = None  # a tie has no winner

    recompute_standings(db, league)

    if week == league.regular_season_weeks:
        seed_playoffs(db, league)
    elif week > league.regular_season_weeks:
        _advance_bracket(db, league, week)

    # Guarded so a second finalize of the same week does not skip a week.
    if league.current_week == week:
        league.current_week = week + 1


def recompute_standings(db: Session, league: DBLeague) -> None:
    """Rebuild every team's record from final REGULAR-SEASON matchups.

    Rebuilt rather than incremented: an increment applied twice is wrong
    forever, and this function runs on every settlement.

    Playoff games are excluded deliberately. They are not part of a team's
    record, and counting them would also re-order :func:`_seeded_teams` — which
    :func:`_advance_bracket` reads to place the bye teams into the next round.
    A quarterfinal winner could otherwise leapfrog the real 1 seed and take its
    place in the semifinal.
    """
    teams = {
        t.id: t
        for t in db.query(DBTeam).filter(DBTeam.league_id == league.league_id)
    }
    for team in teams.values():
        team.wins = team.losses = team.ties = 0
        team.total_points = 0.0

    finals = (
        db.query(DBMatchup)
        .filter_by(league_id=league.id, year=league.year, status="final")
        .filter(DBMatchup.is_playoff.isnot(True))  # NULL counts as regular season
        .all()
    )
    for matchup in finals:
        home = teams.get(matchup.home_team_id)
        away = teams.get(matchup.away_team_id)
        if home is None or away is None:
            continue
        home.total_points = round(home.total_points + matchup.home_points, 2)
        away.total_points = round(away.total_points + matchup.away_points, 2)
        if matchup.winner_team_id == home.id:
            home.wins += 1
            away.losses += 1
        elif matchup.winner_team_id == away.id:
            away.wins += 1
            home.losses += 1
        else:
            home.ties += 1
            away.ties += 1


def _seeded_teams(db: Session, league: DBLeague) -> List[DBTeam]:
    """Playoff seeds: record first, then points-for."""
    teams = db.query(DBTeam).filter(DBTeam.league_id == league.league_id).all()
    return sorted(
        teams,
        key=lambda t: (-(t.wins or 0), -(t.total_points or 0.0), t.id),
    )


def seed_playoffs(db: Session, league: DBLeague) -> int:
    """Fill the first playoff round. Returns matchups seeded."""
    rounds = (
        db.query(DBMatchup)
        .filter_by(league_id=league.id, year=league.year, is_playoff=True)
        .order_by(DBMatchup.week, DBMatchup.bracket_slot)
        .all()
    )
    if not rounds:
        return 0

    first_week = rounds[0].week
    first_round = [m for m in rounds if m.week == first_week]
    seeds = _seeded_teams(db, league)[: league.playoff_teams]
    if len(seeds) < league.playoff_teams:
        return 0

    if league.playoff_teams == 6:
        # Seeds 1-2 receive first-round byes.
        pairs = [(seeds[2], seeds[5]), (seeds[3], seeds[4])]
    elif league.playoff_teams == 4:
        pairs = [(seeds[0], seeds[3]), (seeds[1], seeds[2])]
    else:
        pairs = [(seeds[0], seeds[1])]

    seeded = 0
    for matchup, (home, away) in zip(first_round, pairs):
        matchup.home_team_id = home.id
        matchup.away_team_id = away.id
        seeded += 1
    return seeded


def _advance_bracket(db: Session, league: DBLeague, week: int) -> None:
    """Fill the next playoff round from this week's winners."""
    next_round = (
        db.query(DBMatchup)
        .filter_by(league_id=league.id, year=league.year, week=week + 1,
                   is_playoff=True)
        .order_by(DBMatchup.bracket_slot)
        .all()
    )
    if not next_round:
        return

    finished = (
        db.query(DBMatchup)
        .filter_by(league_id=league.id, year=league.year, week=week,
                   is_playoff=True)
        .order_by(DBMatchup.bracket_slot)
        .all()
    )
    winners = [m.winner_team_id for m in finished if m.winner_team_id]
    seeds = _seeded_teams(db, league)

    if len(next_round) == 2 and league.playoff_teams == 6 and len(winners) == 2:
        # The two byes enter here: 1 plays the 4/5 winner, 2 plays the 3/6 winner.
        next_round[0].home_team_id = seeds[0].id
        next_round[0].away_team_id = winners[1]
        next_round[1].home_team_id = seeds[1].id
        next_round[1].away_team_id = winners[0]
        return

    if len(next_round) == 1 and len(winners) >= 2:
        next_round[0].home_team_id = winners[0]
        next_round[0].away_team_id = winners[1]
