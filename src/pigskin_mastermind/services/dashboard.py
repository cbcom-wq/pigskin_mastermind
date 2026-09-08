"""Everything the front page knows, derived once.

The dashboard reads from eight services. Doing that in the route handler is
how the previous version stayed shallow — each new fact meant another query in
``api/main.py``, so no new facts were ever added.

The other reason this is a module and not a handler: the full page and the
live-polling fragment must render the same numbers. One builder with two
renderings is the only way to guarantee that; two query paths would drift the
first time one of them was edited.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, FrozenSet, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBLeague,
    DBMatchup,
    DBNFLGame,
    DBPlayer,
    DBTeam,
    DBWeeklyTeamStats,
)
from pigskin_mastermind.services.lineup_locks import LockIndex
from pigskin_mastermind.services.lineup_manager import LineupPlan, plan_lineup
from pigskin_mastermind.services.season_league import roster_players
from pigskin_mastermind.services.season_scheduler import (
    in_game_window,
)
from pigskin_mastermind.utils.season import current_fantasy_season

#: The four bands that load independently. ``week`` and ``leagues`` are always
#: built: every band needs the scope and the rosters, so gating them would only
#: mean building them twice.
ALL_SECTIONS: FrozenSet[str] = frozenset({"attention", "players", "slate", "movers"})


@dataclass(frozen=True)
class WeekContext:
    """What week it is, and what the NFL is doing right now."""

    year: int
    week: int
    now: datetime
    games_total: int = 0
    games_in_progress: int = 0
    games_final: int = 0
    games_live: bool = False
    next_kickoff: Optional[datetime] = None
    players_yet_to_play: int = 0


def resolve_scope(db: Session, now: datetime) -> Tuple[int, int]:
    """One ``(year, week)`` for the whole page.

    Derived from the newest league that is still being played. A per-card week
    would let the hero say "Week 1" above a card showing week 4.

    ``archive`` leagues are excluded: they are frozen past seasons, and one
    would otherwise decide the current week for every live league beside it.
    """
    league = (
        db.query(DBLeague)
        .filter(DBLeague.kind != "archive")
        .order_by(DBLeague.year.desc(), DBLeague.id.desc())
        .first()
    )
    if league is None:
        return current_fantasy_season(now.date()), 1
    return league.year, league.current_week or 1


def build_week_context(
    db: Session,
    year: int,
    week: int,
    now: datetime,
    yet_to_play: int = 0,
) -> WeekContext:
    """Game counts and the live flag for one week.

    *now* must be a naive US-Eastern wall clock — see ``league_now()``.
    ``kickoff_at`` is stored in that frame, so a UTC clock would report every
    early Sunday game as in progress from breakfast onwards.
    """
    games = (
        db.query(DBNFLGame).filter(DBNFLGame.year == year, DBNFLGame.week == week).all()
    )
    kickoffs = [g.kickoff_at for g in games if g.kickoff_at is not None]

    final = sum(
        1 for g in games if g.home_score is not None and g.away_score is not None
    )
    in_progress = sum(
        1
        for g in games
        if g.kickoff_at is not None
        and g.home_score is None
        and g.away_score is None
        and in_game_window(now, [g.kickoff_at])
    )
    upcoming = [k for k in kickoffs if k > now]

    return WeekContext(
        year=year,
        week=week,
        now=now,
        games_total=len(games),
        games_in_progress=in_progress,
        games_final=final,
        games_live=in_game_window(now, kickoffs),
        next_kickoff=min(upcoming) if upcoming else None,
        players_yet_to_play=yet_to_play,
    )


@dataclass(frozen=True)
class LeagueCard:
    """One user team's week, whatever kind of league holds it."""

    league_id: str
    league_name: str
    kind: str
    team_id: int
    team_name: str
    team_url: str
    opponent_name: Optional[str] = None
    points: Optional[float] = None
    opponent_points: Optional[float] = None
    projected: Optional[float] = None
    opponent_projected: Optional[float] = None
    is_live: bool = False
    yet_to_play: int = 0
    empty_reason: Optional[str] = None
    empty_action: Optional[Tuple[str, str]] = None
    footnote: Optional[str] = None


def team_url(team: DBTeam, league: Optional[DBLeague]) -> str:
    """Where this team's own page lives.

    A season league's canonical team page is under ``/season/``; the generic
    ``/teams/{id}`` page can show that roster but cannot set its lineup.
    ``?back=/`` so the shared back control returns to the dashboard rather than
    to an index the user never visited.
    """
    if league is not None and league.kind == "season":
        return f"/season/{league.league_id}/teams/{team.id}?back=/"
    return f"/teams/{team.id}?back=/"


def user_team_leagues(
    db: Session,
) -> List[Tuple[DBTeam, Optional[DBLeague]]]:
    """Every flagged user team, paired with its league.

    The league is fetched once per league rather than once per team, because
    ``roster_players`` and ``plan_lineup`` both want it and a per-team lookup
    would re-query the same handful of rows.
    """
    teams = db.query(DBTeam).filter(DBTeam.is_user_team.is_(True)).all()
    keys = {t.league_id for t in teams if t.league_id}
    leagues = (
        {
            lg.league_id: lg
            for lg in db.query(DBLeague).filter(DBLeague.league_id.in_(keys))
        }
        if keys
        else {}
    )
    return [(t, leagues.get(t.league_id)) for t in teams]


def _finish_line(team: DBTeam, year: int) -> str:
    """One team-season's frozen record, e.g. ``"2025 finish: 10-4, 2237.8 pts"``.

    Shared by ``archive_footnote`` (a predecessor's record shown on a live
    successor's card) and the archive league's own card (its own record,
    same team, same format) — the format string exists in exactly one place.
    """
    record = f"{team.wins or 0}-{team.losses or 0}"
    if team.ties:
        record += f"-{team.ties}"
    return f"{year} finish: {record}, {team.total_points or 0.0:.1f} pts"


def archive_footnote(db: Session, league: DBLeague, team: DBTeam) -> Optional[str]:
    """This league's own archived predecessor's final record, if there is one.

    The archive convention renames the finished row to ``<league_id>-<year>``,
    so the predecessor is one lookup away. The record belongs on the *live*
    card because that is the league still being played — a second card for the
    same league in a past state would be noise, not history.
    """
    previous = (
        db.query(DBLeague)
        .filter_by(league_id=f"{league.league_id}-{league.year - 1}")
        .first()
    )
    if previous is None:
        return None

    query = db.query(DBTeam).filter(DBTeam.league_id == previous.league_id)
    old = (
        query.filter(DBTeam.espn_team_id == team.espn_team_id).first()
        if team.espn_team_id
        else None
    )
    if old is None:
        old = query.filter(DBTeam.name == team.name).first()
    if old is None:
        return None

    return _finish_line(old, previous.year)


def _starters_in_window(
    db: Session,
    plan: LineupPlan,
    players: List[DBPlayer],
    year: int,
    week: int,
    now: datetime,
) -> bool:
    """True when at least one of this team's starters is mid-game.

    The NFL team comes from the ``players`` list the card already loaded, not
    from ``LineupDecision`` — that dataclass carries no ``nfl_team``, and
    widening it would touch the AI manager, the season agent and three
    templates to serve one card.
    """
    teams = {p.id: p.nfl_team for p in players}
    locks = LockIndex(db)
    times = [locks.kickoff(teams.get(d.player_id), year, week) for d in plan.starters()]
    return in_game_window(now, [t for t in times if t is not None])


def _espn_card(
    db: Session,
    team: DBTeam,
    league: DBLeague,
    players: List[DBPlayer],
    year: int,
    week: int,
    now: datetime,
    plan: LineupPlan,
) -> LeagueCard:
    """An ESPN or archived league's week, read from the ESPN weekly snapshot.

    The join through ``teams -> leagues`` and the filter on ``DBLeague.year``
    are the point of this function. ``weekly_team_stats`` carries no year and
    its parent's unique key is ``(team_id, week)``, so week 1 of an archived
    2025 season occupies the same slot as week 1 of 2026 — and an unguarded
    read serves it as this season's score.

    An archived league has no current week at all, so it returns before any
    of that: no ``weekly_team_stats`` read, no lineup plan consulted, just the
    team's own frozen record from the season that finished.
    """
    if league.kind == "archive":
        return LeagueCard(
            league_id=league.league_id,
            league_name=league.name,
            kind=league.kind,
            team_id=team.id,
            team_name=team.name,
            team_url=team_url(team, league),
            empty_reason=f"{league.year} season complete",
            empty_action=None,
            footnote=_finish_line(team, league.year),
            is_live=False,
        )

    base = dict(
        league_id=league.league_id,
        league_name=league.name,
        kind=league.kind,
        team_id=team.id,
        team_name=team.name,
        team_url=team_url(team, league),
        footnote=archive_footnote(db, league, team),
    )

    row = (
        db.query(DBWeeklyTeamStats)
        .join(DBTeam, DBTeam.id == DBWeeklyTeamStats.team_id)
        .join(DBLeague, DBLeague.league_id == DBTeam.league_id)
        .filter(
            DBWeeklyTeamStats.team_id == team.id,
            DBWeeklyTeamStats.week == week,
            DBLeague.year == year,
        )
        .first()
    )
    if row is None:
        return LeagueCard(
            **base,
            projected=round(plan.projected_total, 1) or None,
            empty_reason=f"No {year} weeks synced",
            empty_action=("Sync from ESPN", "/settings"),
        )

    # ESPN reports "U" for a week that has not been settled yet. A W/L/T is
    # final, however recently it was synced.
    unsettled = (row.result or "U").upper() == "U"
    return LeagueCard(
        **base,
        opponent_name=row.opponent_name,
        points=row.points_for,
        opponent_points=row.points_against,
        projected=row.projected_points or round(plan.projected_total, 1),
        is_live=unsettled and _starters_in_window(db, plan, players, year, week, now),
    )


def _season_card(
    db: Session,
    team: DBTeam,
    league: DBLeague,
    week: int,
    now: datetime,
    plan: LineupPlan,
) -> LeagueCard:
    base = dict(
        league_id=league.league_id,
        league_name=league.name,
        kind=league.kind,
        team_id=team.id,
        team_name=team.name,
        team_url=team_url(team, league),
        projected=round(plan.projected_total, 1),
    )

    matchup = (
        db.query(DBMatchup)
        .filter(
            DBMatchup.league_id == league.id,
            DBMatchup.year == league.year,
            DBMatchup.week == week,
            or_(
                DBMatchup.home_team_id == team.id,
                DBMatchup.away_team_id == team.id,
            ),
        )
        .first()
    )
    if matchup is None:
        return LeagueCard(**base, empty_reason=f"No week {week} matchup")

    at_home = matchup.home_team_id == team.id
    opponent_id = matchup.away_team_id if at_home else matchup.home_team_id
    opponent = (
        db.query(DBTeam).filter_by(id=opponent_id).first() if opponent_id else None
    )

    opponent_projected = None
    if opponent is not None:
        opponent_plan = plan_lineup(
            db,
            opponent,
            league.year,
            week,
            now,
            league=league,
            players=roster_players(db, opponent, league),
        )
        opponent_projected = round(opponent_plan.projected_total, 1)

    live = matchup.status != "scheduled"
    return LeagueCard(
        **base,
        opponent_name=opponent.name if opponent is not None else "TBD",
        points=matchup.home_points if at_home else matchup.away_points,
        opponent_points=matchup.away_points if at_home else matchup.home_points,
        opponent_projected=opponent_projected,
        is_live=live,
    )


def build_league_cards(
    db: Session,
    year: int,
    week: int,
    now: datetime,
) -> Tuple[List[LeagueCard], Dict[int, LineupPlan]]:
    """One card per user team, plus the lineup plan each card was built from.

    The plans are returned rather than rebuilt by later sections: they are the
    expensive part (a roster query, a projection map, an injury index and a
    schedule index per team) and the attention items and player cells are both
    derived from exactly these.
    """
    cards: List[LeagueCard] = []
    plans: Dict[int, LineupPlan] = {}

    for team, league in user_team_leagues(db):
        players = roster_players(db, team, league)
        plan = plan_lineup(
            db,
            team,
            year,
            week,
            now,
            league=league,
            players=players,
        )
        plans[team.id] = plan

        if league is None:
            cards.append(
                LeagueCard(
                    league_id=team.league_id or "",
                    league_name="Unknown league",
                    kind="espn",
                    team_id=team.id,
                    team_name=team.name,
                    team_url=team_url(team, None),
                    empty_reason="League row missing",
                )
            )
        elif league.kind == "season":
            cards.append(_season_card(db, team, league, week, now, plan))
        else:
            cards.append(_espn_card(db, team, league, players, year, week, now, plan))

    return cards, plans
