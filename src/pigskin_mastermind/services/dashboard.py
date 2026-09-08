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

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, FrozenSet, List, Optional, Tuple

from sqlalchemy import or_
from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBLeague,
    DBLineupSlot,
    DBMatchup,
    DBNFLGame,
    DBPlayer,
    DBTeam,
    DBWeeklyTeamStats,
)
from pigskin_mastermind.services.injury_status import InjuryIndex
from pigskin_mastermind.services.lineup_locks import LockIndex
from pigskin_mastermind.services.lineup_manager import LineupPlan, plan_lineup
from pigskin_mastermind.services.mock_draft import BENCH_SLOT, FLEX_ELIGIBLE
from pigskin_mastermind.services.nfl_schedule import ScheduleIndex
from pigskin_mastermind.services.season_league import roster_players
from pigskin_mastermind.services.season_scheduler import (
    GAME_WINDOW_HOURS,
    in_game_window,
)
from pigskin_mastermind.utils.nfl_teams import normalize_team
from pigskin_mastermind.utils.season import current_fantasy_season

#: The four bands that load independently. ``week`` and ``leagues`` are always
#: built: every band needs the scope, but only some of them need the rosters.
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
) -> Tuple[List[LeagueCard], Dict[int, LineupPlan], Dict[int, List[DBPlayer]]]:
    """One card per user team, the lineup plan, and the roster each was built
    from.

    The plans and rosters are returned rather than rebuilt by later sections:
    they are the expensive part (a roster query, a projection map, an injury
    index and a schedule index per team), and ``build_view`` used to re-run
    ``roster_players()`` a second time, once here and once again to assemble
    its own ``rosters`` dict, on every request. Handing back what this loop
    already computed removes that duplicate query entirely, along with the
    gate it used to force on which sections were allowed to pay for it.
    """
    cards: List[LeagueCard] = []
    plans: Dict[int, LineupPlan] = {}
    rosters: Dict[int, List[DBPlayer]] = {}

    for team, league in user_team_leagues(db):
        players = roster_players(db, team, league)
        rosters[team.id] = players
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

    return cards, plans, rosters


@dataclass(frozen=True)
class DashboardView:
    """Everything the front page renders, from one pass over the database."""

    week: WeekContext
    leagues: List[LeagueCard] = field(default_factory=list)
    # Forward references to dataclasses Phases 2-3 add. `from __future__ import
    # annotations` (top of file) defers evaluation so the class body executes
    # fine without them existing yet; flake8 still checks names inside a
    # quoted annotation, so each needs its own noqa until its Phase lands.
    attention: List["AttentionItem"] = field(default_factory=list)  # noqa: F821
    players: List["PlayerCell"] = field(default_factory=list)  # noqa: F821
    players_total: int = 0
    slate: List["SlateGame"] = field(default_factory=list)  # noqa: F821
    movers: List[object] = field(default_factory=list)


def build_view(
    db: Session,
    now: datetime,
    sections: FrozenSet[str] = ALL_SECTIONS,
) -> DashboardView:
    """The whole page, or the slice of it a fragment endpoint asked for.

    ``week`` and ``leagues`` are always built: every section needs the scope.
    ``build_league_cards`` hands back the rosters it already queried, so
    there is no separate roster pass left to gate behind which sections were
    requested.

    The hero's ``players_yet_to_play`` count must be right on ``/`` and
    ``/api/dashboard/pulse``, which request no sections at all -- so the full
    (uncapped) player list is always built, regardless of ``sections``. It is
    cheap: the rosters and lineup plans it reads are already in hand.
    ``DashboardView.players`` is still only populated -- and only sliced to
    ``PLAYER_STRIP_LIMIT`` -- when ``"players"`` was actually requested.

    Unrequested sections come back as empty lists rather than ``None``, so a
    template cannot accidentally distinguish "not asked for" from "nothing to
    show" -- each fragment renders exactly one section and never inspects the
    others.
    """
    year, week = resolve_scope(db, now)
    cards, plans, rosters = build_league_cards(db, year, week, now)

    attention: List[AttentionItem] = []
    if "attention" in sections:
        attention = build_attention(
            db,
            cards,
            plans,
            rosters,
            year,
            week,
            now,
        )

    all_players, players_total_all = build_players(
        db,
        cards,
        plans,
        rosters,
        year,
        week,
        now,
    )
    players = all_players[:PLAYER_STRIP_LIMIT] if "players" in sections else []
    players_total = players_total_all if "players" in sections else 0

    return DashboardView(
        week=build_week_context(
            db,
            year,
            week,
            now,
            yet_to_play=sum(1 for c in all_players if c.state == "upcoming"),
        ),
        leagues=cards,
        attention=attention,
        players=players,
        players_total=players_total,
    )


#: Ranked worst-first. The constant is the definition; the ordering test
#: asserts against it rather than against a hand-written expected list.
ATTENTION_ORDER: Tuple[str, ...] = (
    "no_lineup",
    "injury_excluded",
    "on_bye",
    "injury_haircut",
    "bench_better",
    "lock_soon",
)

_SEVERITY = {
    "no_lineup": "critical",
    "injury_excluded": "critical",
    "on_bye": "critical",
    "injury_haircut": "warning",
    "bench_better": "warning",
    "lock_soon": "info",
}

#: How close a lock has to be before it is worth saying so.
LOCK_SOON_HOURS = 3


@dataclass(frozen=True)
class AttentionItem:
    rank: int
    kind: str
    severity: str
    team_name: str
    detail: str
    url: str
    player_id: Optional[int] = None
    player_name: Optional[str] = None
    position: Optional[str] = None
    deadline: Optional[datetime] = None


def saved_starters(
    db: Session,
    team: DBTeam,
    year: int,
    week: int,
) -> Dict[int, str]:
    """The non-bench slots this team has actually saved for *week*."""
    return {
        row.player_id: row.slot
        for row in db.query(DBLineupSlot).filter_by(
            team_id=team.id,
            year=year,
            week=week,
        )
        if row.slot != BENCH_SLOT
    }


def effective_starters(
    db: Session,
    team: DBTeam,
    plan: LineupPlan,
    year: int,
    week: int,
) -> Dict[int, str]:
    """Who is starting, saved lineup first, planner's recommendation second.

    The fallback matters: a team with no saved lineup is exactly the team the
    ``no_lineup`` item is about, and without it that team would contribute no
    players to the strip and nothing to ``players_yet_to_play`` — the worst
    team on the page would look like the quietest.
    """
    saved = saved_starters(db, team, year, week)
    if saved:
        return saved
    return {d.player_id: d.slot for d in plan.starters()}


def build_attention(
    db: Session,
    cards: List[LeagueCard],
    plans: Dict[int, LineupPlan],
    rosters: Dict[int, List[DBPlayer]],
    year: int,
    week: int,
    now: datetime,
) -> List[AttentionItem]:
    """Everything wrong with the user's teams this week, worst first.

    Derived entirely from the ``LineupPlan`` each card was already built from,
    so it adds no roster queries — only the three shared indexes, each loaded
    once for the whole page rather than once per team.
    """
    injuries = InjuryIndex(db, year, week)
    schedule = ScheduleIndex(db)
    locks = LockIndex(db)
    items: List[AttentionItem] = []

    for card in cards:
        plan = plans.get(card.team_id)
        players = {p.id: p for p in rosters.get(card.team_id, [])}
        if plan is None or not players:
            continue

        # One indexed lookup, and it keeps this function's signature free of a
        # second parallel dict of teams.
        team = db.query(DBTeam).filter_by(id=card.team_id).first()
        if team is None:
            continue
        saved = saved_starters(db, team, year, week)

        if not saved:
            items.append(
                AttentionItem(
                    rank=ATTENTION_ORDER.index("no_lineup"),
                    kind="no_lineup",
                    severity=_SEVERITY["no_lineup"],
                    team_name=card.team_name,
                    detail=f"Nothing set for week {week}",
                    url=card.team_url,
                )
            )

        starting = effective_starters(db, team, plan, year, week)
        projections = {d.player_id: d.projected_points for d in plan.decisions}

        for player_id, slot in sorted(
            starting.items(),
            key=lambda kv: (-projections.get(kv[0], 0.0), kv[0]),
        ):
            player = players.get(player_id)
            if player is None:
                continue

            verdict = injuries.verdict(player_id)
            on_bye = schedule.is_bye(player.nfl_team, year, week)
            kickoff = locks.kickoff(player.nfl_team, year, week)

            if on_bye:
                kind = "on_bye"
                detail = f"{player.nfl_team} is on bye in week {week}"
            elif verdict.excluded:
                kind = "injury_excluded"
                detail = f"{verdict.reason} — still in your {slot}"
            elif verdict.multiplier != 1.0:
                kind = "injury_haircut"
                detail = f"{verdict.reason} — in your {slot}"
            else:
                continue

            items.append(
                AttentionItem(
                    rank=ATTENTION_ORDER.index(kind),
                    kind=kind,
                    severity=_SEVERITY[kind],
                    team_name=card.team_name,
                    detail=detail,
                    url=card.team_url,
                    player_id=player_id,
                    player_name=player.name,
                    position=player.position,
                    deadline=kickoff,
                )
            )

        items.extend(_bench_better(card, plan, players, saved, projections))

        if saved:
            soonest = [
                locks.kickoff(players[pid].nfl_team, year, week)
                for pid in starting
                if pid in players
            ]
            soonest = [k for k in soonest if k is not None and k > now]
            if soonest and min(soonest) - now <= timedelta(hours=LOCK_SOON_HOURS):
                items.append(
                    AttentionItem(
                        rank=ATTENTION_ORDER.index("lock_soon"),
                        kind="lock_soon",
                        severity=_SEVERITY["lock_soon"],
                        team_name=card.team_name,
                        detail="First lineup lock is close",
                        url=card.team_url,
                        deadline=min(soonest),
                    )
                )

    items.sort(key=lambda i: (i.rank, i.team_name, i.player_name or ""))
    return items


def _bench_better(
    card: LeagueCard,
    plan: LineupPlan,
    players: Dict[int, DBPlayer],
    saved: Dict[int, str],
    projections: Dict[int, float],
) -> List[AttentionItem]:
    """A benched player out-projecting a saved starter at the same slot.

    Compared against the *saved* lineup, never against ``plan_lineup``'s ideal.
    Against the ideal this fires for every team that has not clicked auto-set,
    which is not news — it is just the optimizer restating itself.
    """
    if not saved:
        return []

    bench = [
        pid for pid in players if pid not in saved and projections.get(pid, 0.0) > 0
    ]
    out: List[AttentionItem] = []

    for starter_id, slot in saved.items():
        starter = players.get(starter_id)
        if starter is None:
            continue
        eligible = [
            pid
            for pid in bench
            if (
                players[pid].position == slot
                or (slot == "FLEX" and players[pid].position in FLEX_ELIGIBLE)
            )
        ]
        if not eligible:
            continue
        best = max(eligible, key=lambda pid: (projections.get(pid, 0.0), -pid))
        gain = projections.get(best, 0.0) - projections.get(starter_id, 0.0)
        if gain <= 0:
            continue
        out.append(
            AttentionItem(
                rank=ATTENTION_ORDER.index("bench_better"),
                kind="bench_better",
                severity=_SEVERITY["bench_better"],
                team_name=card.team_name,
                detail=(
                    f"{players[best].name} {projections[best]:.1f} on your bench "
                    f"beats {starter.name} {projections.get(starter_id, 0.0):.1f} "
                    f"in {slot}"
                ),
                url=card.team_url,
                player_id=best,
                player_name=players[best].name,
                position=players[best].position,
            )
        )
    return out


#: The strip is one row. Nine is a full starting lineup, so it reads as a
#: lineup rather than an arbitrary truncation.
PLAYER_STRIP_LIMIT = 9

_STATE_RANK = {"playing": 0, "concern": 1, "upcoming": 2, "final": 3}


@dataclass(frozen=True)
class PlayerCell:
    player_id: int
    name: str
    url: str
    state: str
    position: Optional[str] = None
    nfl_team: Optional[str] = None
    live_points: Optional[float] = None
    projected: Optional[float] = None
    kickoff_at: Optional[datetime] = None
    note: Optional[str] = None


def build_players(
    db: Session,
    cards: List[LeagueCard],
    plans: Dict[int, LineupPlan],
    rosters: Dict[int, List[DBPlayer]],
    year: int,
    week: int,
    now: datetime,
) -> Tuple[List[PlayerCell], int]:
    """The user's starters across every team, deduplicated, ranked, and
    uncapped.

    League boundaries are deliberately dissolved here: one ``DBPlayer`` row is
    routinely on an ESPN roster and a season roster at once, and showing him
    twice is noise. Returns every cell -- the caller decides how much of it
    to show: ``build_view`` slices ``[:PLAYER_STRIP_LIMIT]`` for the strip
    while counting ``state == "upcoming"`` over this full list for the hero's
    ``players_yet_to_play``, which must not be capped to what the strip
    displays.
    """
    injuries = InjuryIndex(db, year, week)
    schedule = ScheduleIndex(db)
    locks = LockIndex(db)
    window = timedelta(hours=GAME_WINDOW_HOURS)

    finals = {
        (g.home_team, g.away_team)
        for g in db.query(DBNFLGame).filter(
            DBNFLGame.year == year,
            DBNFLGame.week == week,
            DBNFLGame.home_score.isnot(None),
        )
    }
    played_teams = {t for pair in finals for t in pair}

    seen: Dict[int, PlayerCell] = {}
    for card in cards:
        plan = plans.get(card.team_id)
        players = {p.id: p for p in rosters.get(card.team_id, [])}
        if plan is None or not players:
            continue
        team = db.query(DBTeam).filter_by(id=card.team_id).first()
        if team is None:
            continue

        projections = {d.player_id: d.projected_points for d in plan.decisions}
        for player_id in effective_starters(db, team, plan, year, week):
            if player_id in seen:
                continue
            player = players.get(player_id)
            if player is None:
                continue

            verdict = injuries.verdict(player_id)
            on_bye = schedule.is_bye(player.nfl_team, year, week)
            kickoff = locks.kickoff(player.nfl_team, year, week)
            normalized = normalize_team(player.nfl_team)

            if on_bye:
                state, note = "concern", "on bye"
            elif verdict.status:
                state, note = "concern", verdict.status.title()
            elif normalized in played_teams:
                state, note = "final", "final"
            elif kickoff is not None and kickoff <= now < kickoff + window:
                state, note = "playing", "in progress"
            elif kickoff is not None and kickoff > now:
                state, note = "upcoming", None
            else:
                state, note = "upcoming", "no kickoff time"

            seen[player_id] = PlayerCell(
                player_id=player_id,
                name=player.name,
                url=f"/players/{player_id}?back=/",
                state=state,
                position=player.position,
                nfl_team=player.nfl_team,
                projected=round(projections.get(player_id, 0.0), 1),
                kickoff_at=kickoff,
                note=note,
            )

    cells = sorted(
        seen.values(),
        key=lambda c: (
            _STATE_RANK[c.state],
            c.kickoff_at or datetime.max,
            -(c.projected or 0.0),
            c.player_id,
        ),
    )
    return cells, len(cells)
