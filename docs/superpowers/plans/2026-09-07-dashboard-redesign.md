# Dashboard Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the counter-and-quick-actions dashboard with five week-aware bands — a live pulse hero, a matchup card per user team, attention items, your players, and the NFL slate.

**Architecture:** One builder (`services/dashboard.py::build_view`) produces a frozen `DashboardView` dataclass tree. The full page and every HTMX fragment render from that same builder, so they cannot disagree. The route moves out of `api/main.py` into `api/routes/dashboard.py`. Polling is self-terminating: the pulse fragment renders its own `hx-trigger` only while NFL games are inside their window.

**Tech Stack:** FastAPI, SQLAlchemy (SQLite), Jinja2 server-rendered templates, HTMX 1.9 and Tailwind via CDN (no build step), pytest.

**Spec:** [docs/superpowers/specs/2026-09-07-dashboard-redesign-design.md](../specs/2026-09-07-dashboard-redesign-design.md)

## Global Constraints

Copied verbatim from the spec and from `CLAUDE.md`. Every task's requirements implicitly include these.

- **Always run pytest scoped to `tests/`.** Bare `pytest` fails during collection — the vendored ESPN library at `src/pigskin_mastermind/lib/espn-api/` ships its own `tests/` tree that imports `espn_api` as an installed package.
- **The repo has 12 known pre-existing test failures on `main`.** Judge a run by the failure *set*, not the count. Never "fix" a failure you did not cause.
- **Every time comparison uses `season_scheduler.league_now()`, never `datetime.utcnow()`.** `DBNFLGame.kickoff_at` is a naive US-Eastern wall clock; UTC runs 4–5 hours ahead and would mark a 1:00 PM game as live at 9:00 AM.
- **Every roster read is `season_league.roster_players(db, team, league)`.** Reading `DBPlayer.team_id` renders a season team as empty; reading `DBRosterSpot` renders an ESPN team as empty.
- **`plan_lineup()` must be given `players=`** when called on an ESPN team — its default query reads `DBRosterSpot` only.
- **Availability comes from `injury_status.InjuryIndex`, never `DBPlayer.injury_status`.**
- **The dashboard is read-only.** No POST endpoints, no writes.
- **Every outbound link carries `?back=/`.** The dashboard itself gets neither a back link nor breadcrumbs — it is a sidebar page.
- **No new template may contain an `<a>` or `<button>` whose visible text matches `\bBack\b`** unless it carries `class="nav-back"`. `tests/test_nav_conventions.py` scans template source and fails otherwise.
- Windows venv interpreter is `.venv/Scripts/python`. Formatting: `black src/ tests/`, linting: `flake8 src/ tests/`.
- Commit messages end with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

## File Structure

| File | Responsibility |
|---|---|
| `src/pigskin_mastermind/services/dashboard.py` | **New.** View-model dataclasses, scope resolution, and `build_view`. The only place a dashboard fact is derived. |
| `src/pigskin_mastermind/api/routes/dashboard.py` | **New.** `GET /` plus four read-only fragment endpoints. Thin — no derivation. |
| `src/pigskin_mastermind/templates/dashboard.html` | **Rewritten.** The five-band shell. |
| `src/pigskin_mastermind/templates/dashboard/_pulse.html` | **New.** Bands 1–2, and the only template carrying `hx-trigger="every 30s"`. |
| `src/pigskin_mastermind/templates/dashboard/_attention.html` | **New.** Band 3 left. |
| `src/pigskin_mastermind/templates/dashboard/_movers.html` | **New.** Band 3 right. |
| `src/pigskin_mastermind/templates/dashboard/_players.html` | **New.** Band 4. |
| `src/pigskin_mastermind/templates/dashboard/_slate.html` | **New.** Band 5. |
| `src/pigskin_mastermind/services/season_scheduler.py` | **Modified.** `in_game_window` extracted so the page and the scheduler share one definition. |
| `src/pigskin_mastermind/services/metric_trends.py` | **Modified.** `latest_season_with_metrics` / `last_full_week` moved in from `api/routes/metrics.py` so the dashboard can reuse them. |
| `src/pigskin_mastermind/api/routes/metrics.py` | **Modified.** Private helpers deleted, imports the moved ones. |
| `src/pigskin_mastermind/api/main.py` | **Modified.** `/` handler deleted, `dashboard_router` included. |
| `tests/test_dashboard.py` | **New.** Every derivation rule. |
| `tests/test_season_scheduler.py` | **Modified** (or created if absent) — `in_game_window` parity. |

---

# Phase 1 — View model, league cards, page shell

### Task 1: Extract the shared game-window predicate

The dashboard must not carry a second, independently-drifting idea of what "a game is on" means. `season_scheduler.next_poll_at` already contains the predicate inline; lift it out and have both callers use it.

**Files:**
- Modify: `src/pigskin_mastermind/services/season_scheduler.py:61-75`
- Test: `tests/test_season_scheduler.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `season_scheduler.in_game_window(now: datetime, kickoffs: Iterable[datetime]) -> bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_season_scheduler.py` if it does not exist; otherwise append this class.

```python
"""The game-window predicate shared by the scheduler and the dashboard."""

from datetime import datetime, timedelta

from pigskin_mastermind.services.season_scheduler import (
    GAME_WINDOW_HOURS, LIVE_POLL_SECONDS, in_game_window, next_poll_at,
)

KICKOFF = datetime(2026, 9, 13, 13, 0)


class TestInGameWindow:
    def test_true_at_kickoff(self):
        assert in_game_window(KICKOFF, [KICKOFF]) is True

    def test_true_inside_the_window(self):
        assert in_game_window(KICKOFF + timedelta(hours=2), [KICKOFF]) is True

    def test_false_before_kickoff(self):
        assert in_game_window(KICKOFF - timedelta(minutes=1), [KICKOFF]) is False

    def test_false_at_the_window_edge(self):
        """The window is half-open: a game is over exactly four hours in."""
        edge = KICKOFF + timedelta(hours=GAME_WINDOW_HOURS)
        assert in_game_window(edge, [KICKOFF]) is False

    def test_false_with_no_kickoffs(self):
        assert in_game_window(KICKOFF, []) is False

    def test_any_one_game_is_enough(self):
        later = KICKOFF + timedelta(hours=7)
        assert in_game_window(later + timedelta(hours=1), [KICKOFF, later]) is True


class TestNextPollAtStillUsesIt:
    def test_live_cadence_inside_a_window(self):
        now = KICKOFF + timedelta(hours=1)
        assert next_poll_at(now, [KICKOFF]) == now + timedelta(
            seconds=LIVE_POLL_SECONDS
        )

    def test_waits_for_the_next_kickoff_outside_a_window(self):
        now = KICKOFF - timedelta(hours=2)
        assert next_poll_at(now, [KICKOFF]) == KICKOFF
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_season_scheduler.py -v`
Expected: FAIL with `ImportError: cannot import name 'in_game_window'`

- [ ] **Step 3: Write minimal implementation**

In `src/pigskin_mastermind/services/season_scheduler.py`, add the function directly above `next_poll_at` and rewrite `next_poll_at`'s first branch to call it. Add `Iterable` to the `typing` import.

```python
def in_game_window(now: datetime, kickoffs: Iterable[datetime]) -> bool:
    """True while any of *kickoffs* is inside its GAME_WINDOW_HOURS window.

    The single definition of "a game is on". The scheduler polls ESPN against
    it and the dashboard decides whether to poll itself against it; two copies
    would drift and the page would keep refreshing after the scheduler had
    stopped fetching anything new.

    Half-open: a game is live at its kickoff minute and over exactly
    GAME_WINDOW_HOURS later.
    """
    window = timedelta(hours=GAME_WINDOW_HOURS)
    return any(kickoff <= now < kickoff + window for kickoff in kickoffs)


def next_poll_at(now: datetime, kickoffs: List[datetime]) -> datetime:
    """When the loop should wake next.

    Fast inside a game window, otherwise at the next kickoff, never later than
    the idle cap.
    """
    if in_game_window(now, kickoffs):
        return now + timedelta(seconds=LIVE_POLL_SECONDS)

    upcoming = [kickoff for kickoff in kickoffs if kickoff > now]
    idle_cap = now + timedelta(seconds=IDLE_MAX_SECONDS)
    if not upcoming:
        return idle_cap
    return min(min(upcoming), idle_cap)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_season_scheduler.py tests/test_live_scoring.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/season_scheduler.py tests/test_season_scheduler.py
git commit -m "refactor(scheduler): extract in_game_window

The dashboard needs the same predicate to decide whether to poll. Two
copies would drift, and the page would keep refreshing after the
scheduler had stopped fetching anything new.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Dashboard scope and week context

`WeekContext` answers "what week is it and what is happening in the NFL right now". Everything else hangs off the `(year, week)` it resolves.

**Files:**
- Create: `src/pigskin_mastermind/services/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `season_scheduler.in_game_window` (Task 1).
- Produces:
  - `dashboard.WeekContext` (frozen dataclass)
  - `dashboard.resolve_scope(db: Session, now: datetime) -> tuple[int, int]`
  - `dashboard.build_week_context(db: Session, year: int, week: int, now: datetime, yet_to_play: int = 0) -> WeekContext`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dashboard.py`:

```python
"""The dashboard view model.

Every rule here is one the page gets wrong silently if it breaks: a score from
the wrong year, an empty roster, a lineup nobody flagged as unset.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBNFLGame, DBTeam,
)
from pigskin_mastermind.services import dashboard

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
WEEK = 1
SUNDAY_EARLY = datetime(2026, 9, 13, 13, 0)
SUNDAY_LATE = datetime(2026, 9, 13, 16, 25)
WEDNESDAY = datetime(2026, 9, 9, 10, 0)
MID_EARLY_GAME = datetime(2026, 9, 13, 14, 30)

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1, "BENCH": 6}


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def db():
    session = TestSessionLocal()
    yield session
    session.close()


def add_schedule(db, year=YEAR, week=WEEK):
    """Two early games and one late game, none played."""
    db.add(DBNFLGame(year=year, week=week, home_team="CHI", away_team="DET",
                     kickoff_at=SUNDAY_EARLY, total_line=48.5, spread_line=1.5))
    db.add(DBNFLGame(year=year, week=week, home_team="MIN", away_team="GB",
                     kickoff_at=SUNDAY_EARLY, total_line=44.5, spread_line=-2.5))
    db.add(DBNFLGame(year=year, week=week, home_team="KC", away_team="LAC",
                     kickoff_at=SUNDAY_LATE, total_line=45.0, spread_line=3.0))
    db.commit()


def add_league(db, league_id, name, kind, year=YEAR, week=WEEK):
    lg = DBLeague(league_id=league_id, name=name, year=year, kind=kind,
                  current_week=week, roster_slots=SLOTS)
    db.add(lg)
    db.commit()
    return lg


def add_team(db, league, name, is_user=True, espn_team_id=None,
             wins=0, losses=0, ties=0, points=0.0):
    t = DBTeam(team_id=f"{league.league_id}-{name}", name=name, owner="Brandon",
               league_id=league.league_id, is_user_team=is_user,
               espn_team_id=espn_team_id, wins=wins, losses=losses, ties=ties,
               total_points=points)
    db.add(t)
    db.commit()
    return t


class TestResolveScope:
    def test_uses_the_newest_non_archive_league(self, db):
        add_league(db, "old", "Old", "archive", year=2025)
        add_league(db, "cur", "Current", "season", year=YEAR, week=4)
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 4)

    def test_ignores_an_archive_league_even_when_it_is_newest(self, db):
        add_league(db, "cur", "Current", "season", year=YEAR, week=2)
        add_league(db, "arc", "Archived", "archive", year=2030)
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 2)

    def test_falls_back_to_the_calendar_season_with_no_leagues(self, db):
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 1)

    def test_missing_current_week_reads_as_week_one(self, db):
        lg = add_league(db, "cur", "Current", "season")
        lg.current_week = None
        db.commit()
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 1)


class TestWeekContext:
    def test_counts_games(self, db):
        add_schedule(db)
        ctx = dashboard.build_week_context(db, YEAR, WEEK, WEDNESDAY)
        assert ctx.games_total == 3
        assert ctx.games_in_progress == 0
        assert ctx.games_final == 0

    def test_not_live_on_a_wednesday(self, db):
        add_schedule(db)
        ctx = dashboard.build_week_context(db, YEAR, WEEK, WEDNESDAY)
        assert ctx.games_live is False
        assert ctx.next_kickoff == SUNDAY_EARLY

    def test_live_during_the_early_window(self, db):
        add_schedule(db)
        ctx = dashboard.build_week_context(db, YEAR, WEEK, MID_EARLY_GAME)
        assert ctx.games_live is True
        assert ctx.games_in_progress == 2
        assert ctx.next_kickoff == SUNDAY_LATE

    def test_a_scored_game_is_final_not_in_progress(self, db):
        add_schedule(db)
        game = db.query(DBNFLGame).filter_by(home_team="CHI").first()
        game.home_score, game.away_score = 20, 17
        db.commit()
        ctx = dashboard.build_week_context(db, YEAR, WEEK, MID_EARLY_GAME)
        assert ctx.games_final == 1
        assert ctx.games_in_progress == 1

    def test_no_schedule_is_not_live(self, db):
        ctx = dashboard.build_week_context(db, YEAR, WEEK, MID_EARLY_GAME)
        assert ctx.games_total == 0
        assert ctx.games_live is False
        assert ctx.next_kickoff is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pigskin_mastermind.services.dashboard'`

- [ ] **Step 3: Write minimal implementation**

Create `src/pigskin_mastermind/services/dashboard.py`:

```python
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

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBLeague, DBNFLGame
from pigskin_mastermind.services.season_scheduler import (
    GAME_WINDOW_HOURS, in_game_window,
)
from pigskin_mastermind.utils.season import current_fantasy_season

#: The four bands that load independently. ``week`` and ``leagues`` are always
#: built: every band needs the scope and the rosters, so gating them would only
#: mean building them twice.
ALL_SECTIONS: FrozenSet[str] = frozenset(
    {"attention", "players", "slate", "movers"}
)


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
        db.query(DBNFLGame)
        .filter(DBNFLGame.year == year, DBNFLGame.week == week)
        .all()
    )
    kickoffs = [g.kickoff_at for g in games if g.kickoff_at is not None]
    window = timedelta(hours=GAME_WINDOW_HOURS)

    final = sum(
        1 for g in games if g.home_score is not None and g.away_score is not None
    )
    in_progress = sum(
        1
        for g in games
        if g.kickoff_at is not None
        and g.home_score is None
        and g.away_score is None
        and g.kickoff_at <= now < g.kickoff_at + window
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -v`
Expected: PASS, 9 tests

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): week context and scope resolution

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: League cards — the season branch

**Files:**
- Modify: `src/pigskin_mastermind/services/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `WeekContext`, `resolve_scope` (Task 2).
- Produces:
  - `dashboard.LeagueCard` (frozen dataclass, fields exactly as in the spec)
  - `dashboard.team_url(team: DBTeam, league: Optional[DBLeague]) -> str`
  - `dashboard.user_team_leagues(db: Session) -> list[tuple[DBTeam, Optional[DBLeague]]]`
  - `dashboard.build_league_cards(db, year, week, now) -> tuple[list[LeagueCard], dict[int, LineupPlan]]` — the plans are returned because Tasks 6 and 8 need them and rebuilding costs a second full pass over every roster.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard.py`. Add these imports to the existing import block at the top of the file:

```python
from pigskin_mastermind.models.database import (
    Base, DBLeague, DBMatchup, DBNFLGame, DBPlayer, DBPlayerProjection,
    DBRosterSpot, DBTeam, DBWeeklyTeamStats,
)
```

Then append these helpers and the test class:

```python
ROSTER = [
    ("QB1", "QB", "CHI", 27.8), ("QB2", "QB", "MIN", 17.3),
    ("RB1", "RB", "DET", 18.7), ("RB2", "RB", "KC", 13.5),
    ("RB3", "RB", "LAC", 9.5),
    ("WR1", "WR", "GB", 15.3), ("WR2", "WR", "LAC", 13.9),
    ("WR3", "WR", "MIN", 10.9),
    ("TE1", "TE", "CHI", 15.6), ("K1", "K", "KC", 11.6),
    ("DEF1", "DEF", "DET", 12.1),
]


def add_roster(db, league, team, roster=ROSTER, year=YEAR, week=WEEK):
    """Give *team* a legal roster, stored the way its league kind stores it."""
    players = []
    for name, position, nfl_team, points in roster:
        p = DBPlayer(player_id=f"p_{team.id}_{name}", name=f"{team.name} {name}",
                     position=position, nfl_team=nfl_team)
        db.add(p)
        db.commit()
        if league.kind in ("season", "archive"):
            db.add(DBRosterSpot(league_id=league.id, team_id=team.id,
                                player_id=p.id, acquired_via="draft"))
        else:
            p.team_id = team.id
        db.add(DBPlayerProjection(player_id=p.id, year=year, week=week,
                                  source="model", projected_points=points))
        db.commit()
        players.append(p)
    return players


def add_matchup(db, league, home, away, week=WEEK, **kwargs):
    m = DBMatchup(league_id=league.id, year=league.year, week=week,
                  bracket_slot=0, home_team_id=home.id, away_team_id=away.id,
                  **kwargs)
    db.add(m)
    db.commit()
    return m


def card_for(cards, team):
    return next(c for c in cards if c.team_id == team.id)


class TestSeasonLeagueCard:
    @pytest.fixture
    def league(self, db):
        return add_league(db, "season-x", "Bird Turds", "season")

    def test_names_the_opponent_from_the_matchup(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        theirs = add_team(db, league, "Touchdown There", is_user=False)
        add_roster(db, league, mine)
        add_roster(db, league, theirs)
        add_matchup(db, league, mine, theirs)

        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        card = card_for(cards, mine)
        assert card.opponent_name == "Touchdown There"
        assert card.kind == "season"
        assert card.empty_reason is None

    def test_projects_both_sides_before_kickoff(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        theirs = add_team(db, league, "Touchdown There", is_user=False)
        add_roster(db, league, mine)
        add_roster(db, league, theirs)
        add_matchup(db, league, mine, theirs)

        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        card = card_for(cards, mine)
        # QB1 27.8 + RB1 18.7 + RB2 13.5 + WR1 15.3 + WR2 13.9 + TE1 15.6
        # + WR3 10.9 (FLEX, beating RB3 9.5) + K1 11.6 + DEF1 12.1 = 139.4
        assert card.is_live is False
        assert card.projected == pytest.approx(139.4, abs=0.1)
        assert card.opponent_projected == pytest.approx(139.4, abs=0.1)

    def test_uses_live_points_once_the_matchup_is_in_progress(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        theirs = add_team(db, league, "Touchdown There", is_user=False)
        add_roster(db, league, mine)
        add_roster(db, league, theirs)
        add_matchup(db, league, mine, theirs, status="in_progress",
                    home_points=61.4, away_points=44.9)

        cards, _plans = dashboard.build_league_cards(
            db, YEAR, WEEK, MID_EARLY_GAME,
        )
        card = card_for(cards, mine)
        assert card.is_live is True
        assert card.points == pytest.approx(61.4)
        assert card.opponent_points == pytest.approx(44.9)

    def test_reads_the_roster_from_roster_spots(self, db, league):
        """A season roster lives in DBRosterSpot, never DBPlayer.team_id."""
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        add_roster(db, league, mine)
        _cards, plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert plans[mine.id].projected_total > 0

    def test_no_matchup_this_week_is_an_empty_reason(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        add_roster(db, league, mine)
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        card = card_for(cards, mine)
        assert card.empty_reason == "No week 1 matchup"
        assert card.points is None

    def test_links_to_the_season_team_page_with_a_back_param(self, db, league):
        mine = add_team(db, league, "The Scoobies")
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert card_for(cards, mine).team_url == (
            f"/season/season-x/teams/{mine.id}?back=/"
        )

    def test_only_user_teams_get_cards(self, db, league):
        mine = add_team(db, league, "The Scoobies")
        add_team(db, league, "Someone Else", is_user=False)
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert [c.team_id for c in cards] == [mine.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py::TestSeasonLeagueCard -v`
Expected: FAIL with `AttributeError: module 'pigskin_mastermind.services.dashboard' has no attribute 'build_league_cards'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/pigskin_mastermind/services/dashboard.py`. Extend the imports:

```python
from sqlalchemy import or_

from pigskin_mastermind.models.database import (
    DBLeague, DBMatchup, DBNFLGame, DBTeam,
)
from pigskin_mastermind.services.lineup_manager import LineupPlan, plan_lineup
from pigskin_mastermind.services.season_league import roster_players
```

Then append:

```python
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
    leagues = {
        lg.league_id: lg
        for lg in db.query(DBLeague).filter(DBLeague.league_id.in_(keys))
    } if keys else {}
    return [(t, leagues.get(t.league_id)) for t in teams]


def _season_card(
    db: Session,
    team: DBTeam,
    league: DBLeague,
    week: int,
    now: datetime,
    plan: LineupPlan,
) -> LeagueCard:
    base = dict(
        league_id=league.league_id, league_name=league.name, kind=league.kind,
        team_id=team.id, team_name=team.name,
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
        db.query(DBTeam).filter_by(id=opponent_id).first()
        if opponent_id
        else None
    )

    opponent_projected = None
    if opponent is not None:
        opponent_plan = plan_lineup(
            db, opponent, league.year, week, now, league=league,
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
    db: Session, year: int, week: int, now: datetime,
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
            db, team, year, week, now, league=league, players=players,
        )
        plans[team.id] = plan

        if league is None:
            cards.append(LeagueCard(
                league_id=team.league_id or "", league_name="Unknown league",
                kind="espn", team_id=team.id, team_name=team.name,
                team_url=team_url(team, None),
                empty_reason="League row missing",
            ))
        elif league.kind == "season":
            cards.append(_season_card(db, team, league, week, now, plan))
        else:
            cards.append(_espn_card(db, team, league, year, week, plan))

    return cards, plans
```

`_espn_card` is written in Task 4. To keep this task's tests green on their own, add a temporary stub immediately above `build_league_cards` — Task 4 replaces its body:

```python
def _espn_card(db, team, league, year, week, plan):
    return LeagueCard(
        league_id=league.league_id, league_name=league.name, kind=league.kind,
        team_id=team.id, team_name=team.name, team_url=team_url(team, league),
        empty_reason=f"No {year} weeks synced",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -v`
Expected: PASS, 16 tests

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): season league cards

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: League cards — the ESPN branch, its year filter, and the archive footnote

The one bug that would be invisible in review: `uq_team_week` on `weekly_team_stats` is `('team_id', 'week')` with no year, so an unguarded read of week 1 can serve an archived 2025 row as this season's score. Plausible number, right team, wrong year.

**Files:**
- Modify: `src/pigskin_mastermind/services/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `LeagueCard`, `build_league_cards`, `team_url` (Task 3).
- Produces: `_espn_card` (private — replaces the Task 3 stub) and `dashboard.archive_footnote(db, league) -> Optional[str]`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard.py`:

```python
def add_week_stats(db, team, week=WEEK, **kwargs):
    row = DBWeeklyTeamStats(team_id=team.id, week=week, **kwargs)
    db.add(row)
    db.commit()
    return row


class TestEspnLeagueCard:
    @pytest.fixture
    def league(self, db):
        return add_league(db, "878627004", "Airframe Engine League", "espn")

    def test_reads_the_synced_week(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "55 burgers", espn_team_id="4")
        add_roster(db, league, mine)
        add_week_stats(db, mine, points_for=61.4, points_against=44.9,
                       projected_points=118.4, opponent_name="The Crushers",
                       result="U")

        cards, _plans = dashboard.build_league_cards(
            db, YEAR, WEEK, MID_EARLY_GAME,
        )
        card = card_for(cards, mine)
        assert card.opponent_name == "The Crushers"
        assert card.points == pytest.approx(61.4)
        assert card.opponent_points == pytest.approx(44.9)
        assert card.is_live is True
        assert card.empty_reason is None

    def test_a_settled_week_is_not_live(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "55 burgers")
        add_roster(db, league, mine)
        add_week_stats(db, mine, points_for=61.4, points_against=44.9,
                       opponent_name="The Crushers", result="W")
        cards, _plans = dashboard.build_league_cards(
            db, YEAR, WEEK, MID_EARLY_GAME,
        )
        assert card_for(cards, mine).is_live is False

    def test_an_archived_leagues_week_one_is_not_served_as_this_season(self, db):
        """The regression this branch exists for.

        weekly_team_stats has no year column and its unique key is
        (team_id, week), so 2025 week 1 and 2026 week 1 are the same slot. A
        read that does not go through the league's year serves the wrong
        season's score for the right team.
        """
        archived = add_league(db, "1977617326-2025", "Throne 2.0 (2025)",
                              "archive", year=2025)
        old_team = add_team(db, archived, "Stable of Stars",
                            wins=10, losses=4, points=2237.8)
        add_week_stats(db, old_team, points_for=151.2, points_against=98.0,
                       opponent_name="Somebody 2025", result="W")

        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        card = card_for(cards, old_team)
        assert card.points is None
        assert card.opponent_name != "Somebody 2025"

    def test_unsynced_league_says_so_and_offers_the_sync(self, db, league):
        mine = add_team(db, league, "55 burgers")
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        card = card_for(cards, mine)
        assert card.empty_reason == "No 2026 weeks synced"
        assert card.empty_action == ("Sync from ESPN", "/settings")

    def test_reads_the_roster_from_the_team_id_column(self, db, league):
        """An ESPN roster lives on DBPlayer.team_id, never DBRosterSpot.

        plan_lineup's own query reads DBRosterSpot, so without the injected
        roster this team projects 0.0 with a full roster.
        """
        add_schedule(db)
        mine = add_team(db, league, "55 burgers")
        add_roster(db, league, mine)
        _cards, plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert plans[mine.id].projected_total > 0

    def test_links_to_the_generic_team_page(self, db, league):
        mine = add_team(db, league, "55 burgers")
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert card_for(cards, mine).team_url == f"/teams/{mine.id}?back=/"


class TestArchiveFootnote:
    def test_the_predecessors_record_lands_on_the_live_card(self, db):
        archived = add_league(db, "1977617326-2025", "Throne 2.0 (2025)",
                              "archive", year=2025)
        add_team(db, archived, "Bozos Dubbed Over", is_user=False,
                 espn_team_id="7", wins=10, losses=4, points=2237.8)
        live = add_league(db, "1977617326", "Pigskin Throne 2.0", "espn")
        mine = add_team(db, live, "Bozos Dubbed Over", espn_team_id="7")

        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert card_for(cards, mine).footnote == "2025 finish: 10-4, 2237.8 pts"

    def test_no_predecessor_means_no_footnote(self, db):
        live = add_league(db, "878627004", "Airframe Engine League", "espn")
        mine = add_team(db, live, "55 burgers", espn_team_id="4")
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert card_for(cards, mine).footnote is None

    def test_a_tie_is_included_in_the_record(self, db):
        archived = add_league(db, "1977617326-2025", "Throne (2025)",
                              "archive", year=2025)
        add_team(db, archived, "Bozos Dubbed Over", is_user=False,
                 espn_team_id="7", wins=9, losses=4, ties=1, points=2100.0)
        live = add_league(db, "1977617326", "Pigskin Throne 2.0", "espn")
        mine = add_team(db, live, "Bozos Dubbed Over", espn_team_id="7")
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert card_for(cards, mine).footnote == "2025 finish: 9-4-1, 2100.0 pts"

    def test_an_archived_team_that_is_not_a_user_team_gets_no_card(self, db):
        """Its history is already the footnote on the successor's card."""
        archived = add_league(db, "1977617326-2025", "Throne (2025)",
                              "archive", year=2025)
        add_team(db, archived, "Bozos Dubbed Over", is_user=False,
                 espn_team_id="7", wins=10, losses=4, points=2237.8)
        live = add_league(db, "1977617326", "Pigskin Throne 2.0", "espn")
        mine = add_team(db, live, "Bozos Dubbed Over", espn_team_id="7")

        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert [c.team_id for c in cards] == [mine.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py::TestEspnLeagueCard tests/test_dashboard.py::TestArchiveFootnote -v`
Expected: FAIL — `test_reads_the_synced_week` asserts `opponent_name == "The Crushers"` but the Task 3 stub always returns `empty_reason`.

- [ ] **Step 3: Write minimal implementation**

Add `DBWeeklyTeamStats` to the model imports in `dashboard.py`, then replace the Task 3 `_espn_card` stub with:

```python
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

    record = f"{old.wins or 0}-{old.losses or 0}"
    if old.ties:
        record += f"-{old.ties}"
    return f"{previous.year} finish: {record}, {old.total_points or 0.0:.1f} pts"


def _espn_card(
    db: Session,
    team: DBTeam,
    league: DBLeague,
    year: int,
    week: int,
    plan: LineupPlan,
) -> LeagueCard:
    """An ESPN or archived league's week, read from the ESPN weekly snapshot.

    The join through ``teams -> leagues`` and the filter on ``DBLeague.year``
    are the point of this function. ``weekly_team_stats`` carries no year and
    its parent's unique key is ``(team_id, week)``, so week 1 of an archived
    2025 season occupies the same slot as week 1 of 2026 — and an unguarded
    read serves it as this season's score.
    """
    base = dict(
        league_id=league.league_id, league_name=league.name, kind=league.kind,
        team_id=team.id, team_name=team.name,
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
        is_live=unsettled
        and _starters_in_window(db, plan, players, year, week, now),
    )
```

`_starters_in_window` is the ESPN branch's equivalent of the season branch's
matchup `status`. Add it directly above `_espn_card`:

```python
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
    times = [
        locks.kickoff(teams.get(d.player_id), year, week)
        for d in plan.starters()
    ]
    return in_game_window(now, [t for t in times if t is not None])
```

So `_espn_card`'s full signature is
`_espn_card(db, team, league, players, year, week, now, plan)`.

Add `DBPlayer` to the model imports and
`from pigskin_mastermind.services.lineup_locks import LockIndex` to the service
imports, and update the two dispatch branches in `build_league_cards`:

```python
        elif league.kind == "season":
            cards.append(_season_card(db, team, league, week, now, plan))
        else:
            cards.append(
                _espn_card(db, team, league, players, year, week, now, plan)
            )
```

Finally, exclude a non-user archived team from producing a card — `user_team_leagues` already does this, since it filters on `is_user_team`. No further change; the test documents the behaviour.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -v`
Expected: PASS, 26 tests

- [ ] **Step 5: Format, lint, commit**

```bash
.venv/Scripts/python -m black src/pigskin_mastermind/services/dashboard.py tests/test_dashboard.py
.venv/Scripts/python -m flake8 src/pigskin_mastermind/services/dashboard.py tests/test_dashboard.py
git add src/pigskin_mastermind/services/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): ESPN league cards, scoped to the league's year

weekly_team_stats has no year column and its parent's unique key is
(team_id, week), so an unguarded read of week 1 serves an archived 2025
row as this season's score. The card joins through teams -> leagues and
filters on DBLeague.year.

An archived season's final record renders as a footnote on its successor's
card rather than as a card of its own.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: `build_view`, the route move, and the page shell

**Files:**
- Modify: `src/pigskin_mastermind/services/dashboard.py`
- Create: `src/pigskin_mastermind/api/routes/dashboard.py`
- Create: `src/pigskin_mastermind/templates/dashboard/_pulse.html`
- Modify: `src/pigskin_mastermind/templates/dashboard.html` (full rewrite)
- Modify: `src/pigskin_mastermind/api/main.py:147-186`
- Test: `tests/test_dashboard.py`, `tests/integration/test_api_dashboard.py`

**Interfaces:**
- Consumes: `build_week_context`, `build_league_cards` (Tasks 2–4).
- Produces:
  - `dashboard.DashboardView` (frozen dataclass; `attention`, `players`, `slate`, `movers` default to empty lists and are filled by Phases 2–3)
  - `dashboard.build_view(db, now, sections=ALL_SECTIONS) -> DashboardView`
  - Routes `GET /` and `GET /api/dashboard/pulse`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard.py`:

```python
class TestBuildView:
    def test_assembles_week_and_leagues(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        theirs = add_team(db, league, "Touchdown There", is_user=False)
        add_roster(db, league, mine)
        add_roster(db, league, theirs)
        add_matchup(db, league, mine, theirs)

        view = dashboard.build_view(db, WEDNESDAY)
        assert view.week.year == YEAR
        assert view.week.week == WEEK
        assert [c.team_name for c in view.leagues] == ["The Scoobies"]

    def test_unrequested_sections_are_empty_lists_not_none(self, db):
        add_schedule(db)
        view = dashboard.build_view(db, WEDNESDAY, sections=frozenset())
        assert view.attention == []
        assert view.players == []
        assert view.slate == []
        assert view.movers == []

    def test_week_and_leagues_are_built_even_with_no_sections(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        add_team(db, league, "The Scoobies")
        view = dashboard.build_view(db, WEDNESDAY, sections=frozenset())
        assert view.week.games_total == 3
        assert len(view.leagues) == 1
```

Create `tests/integration/test_api_dashboard.py`:

```python
"""The dashboard page and its live-polling fragment."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import (
    Base, DBLeague, DBNFLGame, DBTeam,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def seeded():
    db = TestSessionLocal()
    lg = DBLeague(league_id="season-x", name="Bird Turds", year=2026,
                  kind="season", current_week=1)
    db.add(lg)
    db.add(DBTeam(team_id="t1", name="The Scoobies", owner="Brandon",
                  league_id="season-x", is_user_team=True))
    db.add(DBNFLGame(year=2026, week=1, home_team="CHI", away_team="DET"))
    db.commit()
    db.close()


class TestDashboardPage:
    def test_renders(self, client, seeded):
        response = client.get("/")
        assert response.status_code == 200
        assert "The Scoobies" in response.text

    def test_no_quick_actions_block(self, client, seeded):
        assert "Quick Actions" not in client.get("/").text

    def test_pulse_fragment_renders_the_same_team(self, client, seeded):
        page = client.get("/").text
        fragment = client.get("/api/dashboard/pulse").text
        assert "The Scoobies" in page
        assert "The Scoobies" in fragment

    def test_no_polling_when_nothing_is_live(self, client, seeded):
        """A schedule with no kickoff times can never be inside a window."""
        assert "hx-trigger=\"every 30s\"" not in client.get("/api/dashboard/pulse").text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py::TestBuildView tests/integration/test_api_dashboard.py -v`
Expected: FAIL — `build_view` undefined; `/api/dashboard/pulse` returns 404.

- [ ] **Step 3: Write the implementation**

**3a.** Append to `src/pigskin_mastermind/services/dashboard.py`:

```python
@dataclass(frozen=True)
class DashboardView:
    """Everything the front page renders, from one pass over the database."""

    week: WeekContext
    leagues: List[LeagueCard] = field(default_factory=list)
    attention: List["AttentionItem"] = field(default_factory=list)
    players: List["PlayerCell"] = field(default_factory=list)
    players_total: int = 0
    slate: List["SlateGame"] = field(default_factory=list)
    movers: List[object] = field(default_factory=list)


def build_view(
    db: Session,
    now: datetime,
    sections: FrozenSet[str] = ALL_SECTIONS,
) -> DashboardView:
    """The whole page, or the slice of it a fragment endpoint asked for.

    ``week`` and ``leagues`` are always built. Every section needs the scope
    and the rosters, so gating them would only mean building them twice.

    Unrequested sections come back as empty lists rather than ``None``, so a
    template cannot accidentally distinguish "not asked for" from "nothing to
    show" — each fragment renders exactly one section and never inspects the
    others.
    """
    year, week = resolve_scope(db, now)
    cards, _plans = build_league_cards(db, year, week, now)
    return DashboardView(
        week=build_week_context(db, year, week, now),
        leagues=cards,
    )
```

Phases 2 and 3 replace the body's tail; the `sections` parameter is unused until then and that is deliberate — the signature is fixed now so the route module never has to change again.

**3b.** Create `src/pigskin_mastermind/api/routes/dashboard.py`:

```python
"""The front page and its fragments.

Every endpoint here is read-only and every one of them renders from
``services/dashboard.py::build_view``. Nothing derives a fact locally: the page
and the polling fragment showing different scores for the same matchup is the
specific failure this arrangement exists to prevent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.services.dashboard import build_view
from pigskin_mastermind.services.season_scheduler import league_now

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    """The five-band front page.

    Only bands 1-2 are rendered server-side; the rest load themselves so a slow
    metrics scan cannot delay the scores.
    """
    from pigskin_mastermind.api.main import templates

    view = build_view(db, league_now(), sections=frozenset())
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "view": view},
    )


@router.get("/api/dashboard/pulse")
async def dashboard_pulse(request: Request, db: Session = Depends(get_db)):
    """Bands 1-2 alone, for the live refresh."""
    from pigskin_mastermind.api.main import templates

    view = build_view(db, league_now(), sections=frozenset())
    return templates.TemplateResponse(
        "dashboard/_pulse.html", {"request": request, "view": view},
    )
```

**3c.** Create `src/pigskin_mastermind/templates/dashboard/_pulse.html`:

```jinja
{# Bands 1-2. The only template that polls.

   The hx-trigger is rendered by the server and only while a game is inside
   its window, so polling starts at the first kickoff and stops on its own
   when the last game goes final -- the replacement fragment simply omits the
   attribute. No client-side timer to leave running on a Wednesday. #}
<div id="pulse"
     hx-get="/api/dashboard/pulse"
     hx-swap="outerHTML"
     {% if view.week.games_live %}hx-trigger="every 30s"{% endif %}>

  <div class="field-pattern rounded-2xl p-5 md:p-6 mb-5 shadow-lg flex flex-wrap items-center justify-between gap-4">
    <div>
      <h2 class="text-2xl md:text-3xl font-bold text-white mb-1 flex items-center gap-3">
        Week {{ view.week.week }}
        {% if view.week.games_live %}
        <span class="text-[10px] bg-red-600 text-white px-2 py-0.5 rounded font-extrabold tracking-wide">LIVE</span>
        {% endif %}
      </h2>
      <p class="text-field-200 text-sm">
        {% if view.week.games_live %}
          {{ view.week.games_in_progress }} of {{ view.week.games_total }} games in progress
        {% elif view.week.games_total %}
          {{ view.week.games_final }} of {{ view.week.games_total }} games final
        {% else %}
          No {{ view.week.year }} schedule imported
        {% endif %}
        {% if view.week.players_yet_to_play %}
        &middot; {{ view.week.players_yet_to_play }} of your players yet to play
        {% endif %}
      </p>
    </div>
    {% if view.week.next_kickoff %}
    <div class="text-right text-white">
      <span class="block text-2xl font-extrabold tracking-tight">
        {{ view.week.next_kickoff | kickoff }}
      </span>
      <span class="text-[10px] uppercase tracking-widest text-field-200">next kickoff</span>
    </div>
    {% endif %}
  </div>

  <div class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 mb-5">
    {% for card in view.leagues %}
    <a href="{{ card.team_url }}"
       class="block bg-white rounded-xl shadow-sm border p-4 card-hover
              {% if card.is_live %}border-field-300 ring-2 ring-field-100{% else %}border-slate-200{% endif %}">
      <p class="text-[10px] font-extrabold uppercase tracking-widest text-slate-400 truncate">
        {{ card.league_name }} &middot; {{ card.kind }}
      </p>

      {% if card.empty_reason %}
        <p class="text-sm font-bold text-slate-700 truncate mt-1">{{ card.team_name }}</p>
        <p class="text-xs text-slate-500 mt-3">{{ card.empty_reason }}</p>
        {% if card.empty_action %}
        <p class="text-xs text-pigskin-600 font-semibold mt-1">{{ card.empty_action[0] }} &rarr;</p>
        {% endif %}
      {% else %}
        {# Bound once, above every use: `A if C else B or D` parses as
           `A if C else (B or D)` in Jinja, so an inline version renders None
           for a live card whose points have not been written yet. #}
        {% set mine = (card.points if card.is_live else card.projected) or 0.0 %}
        {% set theirs = (card.opponent_points if card.is_live else card.opponent_projected) or 0.0 %}
        {% set total = (mine + theirs) or 1.0 %}
        <div class="flex items-baseline justify-between gap-2 mt-1">
          <span class="text-sm font-bold text-slate-800 truncate">{{ card.team_name }}</span>
          <span class="text-xl font-extrabold text-field-700">{{ "%.1f"|format(mine) }}</span>
        </div>
        <div class="h-1.5 rounded bg-slate-200 my-2 overflow-hidden flex">
          <span class="block bg-field-600" style="width: {{ (100 * mine / total)|round(1) }}%"></span>
          <span class="block bg-slate-300" style="width: {{ (100 * theirs / total)|round(1) }}%"></span>
        </div>
        <div class="flex items-baseline justify-between gap-2">
          <span class="text-sm font-semibold text-slate-500 truncate">{{ card.opponent_name }}</span>
          <span class="text-base font-bold text-slate-400">{{ "%.1f"|format(theirs) }}</span>
        </div>
        <p class="text-[11px] text-slate-500 mt-2">
          {% if card.is_live %}{{ card.yet_to_play }} yet to play{% else %}Projected{% endif %}
        </p>
      {% endif %}

      {% if card.footnote %}
      <p class="text-[11px] text-slate-400 mt-2">{{ card.footnote }}</p>
      {% endif %}
    </a>
    {% endfor %}
  </div>
</div>
```

**3d.** Replace `src/pigskin_mastermind/templates/dashboard.html` entirely:

```jinja
{% extends "base.html" %}

{% block page_title %}Dashboard{% endblock %}

{% block content %}
{# The dashboard is reachable from the sidebar, so per the project's nav
   convention it gets neither a back link nor breadcrumbs: there is no honest
   single answer to "where did you come from", and the sidebar is the way out.
   Every link OUT of here carries ?back=/ instead. #}

{% include "dashboard/_pulse.html" %}

<div class="grid grid-cols-1 lg:grid-cols-3 gap-5 mb-5">
  <div class="lg:col-span-2" hx-get="/api/dashboard/attention" hx-trigger="load" hx-swap="innerHTML">
    <div class="bg-white rounded-xl border border-slate-200 p-6 text-sm text-slate-400">Loading&hellip;</div>
  </div>
  <div hx-get="/api/dashboard/movers" hx-trigger="load" hx-swap="innerHTML">
    <div class="bg-white rounded-xl border border-slate-200 p-6 text-sm text-slate-400">Loading&hellip;</div>
  </div>
</div>

<div class="mb-5" hx-get="/api/dashboard/players" hx-trigger="load" hx-swap="innerHTML">
  <div class="bg-white rounded-xl border border-slate-200 p-6 text-sm text-slate-400">Loading&hellip;</div>
</div>

<div hx-get="/api/dashboard/slate" hx-trigger="load" hx-swap="innerHTML">
  <div class="bg-white rounded-xl border border-slate-200 p-6 text-sm text-slate-400">Loading&hellip;</div>
</div>
{% endblock %}
```

The four `hx-get` targets 404 until Phases 2–3 land. HTMX leaves the placeholder in place on a 404, so the page degrades to "Loading…" rather than breaking — acceptable for one commit, and Task 7 is the next one.

**3e.** Register a `kickoff` Jinja filter in `src/pigskin_mastermind/api/main.py`, beside the existing `templates.env.filters["timeago"] = _timeago` at line 95.

**This is not cosmetic.** `strftime('%-I')` — the usual way to get an
unpadded hour — is a glibc extension and raises `ValueError` on Windows,
which is the platform this app runs on. One filter, used by all five
dashboard templates, keeps that mistake out of every one of them.

```python
def _kickoff(value):
    """A kickoff as "Sun 1:00 PM".

    Not ``strftime('%a %-I:%M %p')``: ``%-I`` is a glibc extension and raises
    ValueError on Windows. Stripping the pad by hand is portable.
    """
    if value is None:
        return ""
    return value.strftime("%a %I:%M %p").replace(" 0", " ", 1)


templates.env.filters["kickoff"] = _kickoff
```

Then in `src/pigskin_mastermind/api/main.py`: delete the whole `async def dashboard(...)` handler (and its `templates.TemplateResponse` block), add `from pigskin_mastermind.api.routes.dashboard import router as dashboard_router` beside the other router imports, and add `app.include_router(dashboard_router)` to the `include_router` block. Remove `func` and `DBTeam`/`DBPlayer` imports from `main.py` if nothing else there uses them — `flake8` will say.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py tests/integration/test_api_dashboard.py tests/test_nav_conventions.py -v`
Expected: PASS

- [ ] **Step 5: Verify in the browser**

Start the dev server and look at it — the user's own app usually holds port 8000, so use a distinct port.

```bash
uvicorn pigskin_mastermind.api.main:app --reload --port 8010
```

Confirm: four league cards render, the ESPN ones say "No 2026 weeks synced", no Quick Actions block, and (on a Wednesday) the pulse `<div>` has no `hx-trigger`.

- [ ] **Step 6: Format, lint, commit**

```bash
.venv/Scripts/python -m black src/pigskin_mastermind tests/
.venv/Scripts/python -m flake8 src/pigskin_mastermind tests/
git add src/pigskin_mastermind tests/
git commit -m "feat(dashboard): pulse hero and matchup row

Moves GET / out of main.py into its own router. The page and the polling
fragment both render from one build_view call, so they cannot show
different scores for the same matchup.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Phase 2 — Attention, players, slate

### Task 6: Starters, and the attention items

**Files:**
- Modify: `src/pigskin_mastermind/services/dashboard.py`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `build_league_cards` (returns the plans), `LineupPlan`.
- Produces:
  - `dashboard.ATTENTION_ORDER: tuple[str, ...]`
  - `dashboard.LOCK_SOON_HOURS: int = 3`
  - `dashboard.AttentionItem` (frozen dataclass)
  - `dashboard.saved_starters(db, team, year, week) -> dict[int, str]`
  - `dashboard.effective_starters(db, team, plan, year, week) -> dict[int, str]`
  - `dashboard.build_attention(db, cards, plans, rosters, year, week, now) -> list[AttentionItem]`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard.py`. Add `DBLineupSlot` and `DBPlayerInjury` to the model imports at the top of the file.

```python
def set_lineup(db, team, assignments, year=YEAR, week=WEEK):
    """assignments: {player: slot}"""
    for player, slot in assignments.items():
        db.add(DBLineupSlot(team_id=team.id, year=year, week=week,
                            player_id=player.id, slot=slot))
    db.commit()


def bench_all_but(db, team, players, starters, year=YEAR, week=WEEK):
    """Save a lineup: *starters* is {player: slot}, everyone else benched."""
    assignments = dict(starters)
    for p in players:
        assignments.setdefault(p, "BENCH")
    set_lineup(db, team, assignments, year, week)


def add_injury(db, player, status, year=YEAR, week=WEEK):
    db.add(DBPlayerInjury(player_id=player.id, year=year, week=week,
                          report_status=status))
    db.commit()


def optimal_lineup(db, team, league, year=YEAR, week=WEEK, now=WEDNESDAY):
    """The saved lineup plan_lineup would choose, so nothing is 'unset'."""
    from pigskin_mastermind.services.lineup_manager import plan_lineup
    from pigskin_mastermind.services.season_league import roster_players

    plan = plan_lineup(db, team, year, week, now, league=league,
                       players=roster_players(db, team, league))
    for decision in plan.decisions:
        db.add(DBLineupSlot(team_id=team.id, year=year, week=week,
                            player_id=decision.player_id, slot=decision.slot))
    db.commit()


def kinds(items):
    return [i.kind for i in items]


class TestAttentionItems:
    @pytest.fixture
    def setup(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        players = add_roster(db, league, mine)
        return league, mine, {p.name.split()[-1]: p for p in players}

    def _build(self, db, now=WEDNESDAY):
        cards, plans = dashboard.build_league_cards(db, YEAR, WEEK, now)
        rosters = {
            t.id: dashboard.roster_players(db, t, lg)
            for t, lg in dashboard.user_team_leagues(db)
        }
        return dashboard.build_attention(
            db, cards, plans, rosters, YEAR, WEEK, now,
        )

    def test_no_saved_lineup_is_the_top_item(self, db, setup):
        items = self._build(db)
        assert items[0].kind == "no_lineup"
        assert items[0].severity == "critical"
        assert items[0].team_name == "The Scoobies"

    def test_an_optimal_saved_lineup_raises_nothing(self, db, setup):
        league, mine, _p = setup
        optimal_lineup(db, mine, league)
        assert self._build(db) == []

    def test_an_out_starter_is_critical(self, db, setup):
        league, mine, p = setup
        optimal_lineup(db, mine, league)
        add_injury(db, p["QB1"], "Out")
        items = self._build(db)
        assert "injury_excluded" in kinds(items)
        item = next(i for i in items if i.kind == "injury_excluded")
        assert item.player_name == "The Scoobies QB1"
        assert item.severity == "critical"

    def test_a_questionable_starter_is_a_warning(self, db, setup):
        league, mine, p = setup
        optimal_lineup(db, mine, league)
        add_injury(db, p["QB1"], "Questionable")
        items = self._build(db)
        assert "injury_haircut" in kinds(items)
        assert next(
            i for i in items if i.kind == "injury_haircut"
        ).severity == "warning"

    def test_a_bye_week_starter_is_critical(self, db, setup):
        league, mine, p = setup
        optimal_lineup(db, mine, league)
        # No week-1 game for ARI, so a player on ARI is on bye.
        p["WR1"].nfl_team = "ARI"
        db.commit()
        assert "on_bye" in kinds(self._build(db))

    def test_bench_better_fires_only_against_the_saved_lineup(self, db, setup):
        """A saved lineup that starts RB3 over RB1 is the user's own choice
        gone wrong -- that is news. plan_lineup's own ideal is not."""
        league, mine, p = setup
        starters = {
            p["QB1"]: "QB", p["RB3"]: "RB", p["RB2"]: "RB",
            p["WR1"]: "WR", p["WR2"]: "WR", p["TE1"]: "TE",
            p["WR3"]: "FLEX", p["K1"]: "K", p["DEF1"]: "DEF",
        }
        bench_all_but(db, mine, list(p.values()), starters)
        items = self._build(db)
        assert "bench_better" in kinds(items)
        item = next(i for i in items if i.kind == "bench_better")
        assert "RB1" in item.detail

    def test_ordering_follows_attention_order(self, db, setup):
        league, mine, p = setup
        starters = {
            p["QB1"]: "QB", p["RB3"]: "RB", p["RB2"]: "RB",
            p["WR1"]: "WR", p["WR2"]: "WR", p["TE1"]: "TE",
            p["WR3"]: "FLEX", p["K1"]: "K", p["DEF1"]: "DEF",
        }
        bench_all_but(db, mine, list(p.values()), starters)
        add_injury(db, p["QB1"], "Out")
        add_injury(db, p["WR2"], "Questionable")

        seen = kinds(self._build(db))
        positions = [dashboard.ATTENTION_ORDER.index(k) for k in seen]
        assert positions == sorted(positions)

    def test_a_lock_within_three_hours_is_informational(self, db, setup):
        league, mine, _p = setup
        optimal_lineup(db, mine, league)
        items = self._build(db, now=SUNDAY_EARLY - dashboard.timedelta(hours=1))
        assert "lock_soon" in kinds(items)
        assert next(
            i for i in items if i.kind == "lock_soon"
        ).severity == "info"

    def test_every_item_links_back_to_the_dashboard(self, db, setup):
        for item in self._build(db):
            assert item.url.endswith("?back=/")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py::TestAttentionItems -v`
Expected: FAIL with `AttributeError: ... has no attribute 'build_attention'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/pigskin_mastermind/services/dashboard.py`. Extend imports with:

```python
from pigskin_mastermind.models.database import DBLineupSlot
from pigskin_mastermind.services.injury_status import InjuryIndex
from pigskin_mastermind.services.mock_draft import BENCH_SLOT
from pigskin_mastermind.services.nfl_schedule import ScheduleIndex
```

`roster_players` is already imported and is re-exported implicitly by that
import — the tests above call `dashboard.roster_players`, which works because
the name is bound in this module.

```python
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
    db: Session, team: DBTeam, year: int, week: int,
) -> Dict[int, str]:
    """The non-bench slots this team has actually saved for *week*."""
    return {
        row.player_id: row.slot
        for row in db.query(DBLineupSlot).filter_by(
            team_id=team.id, year=year, week=week,
        )
        if row.slot != BENCH_SLOT
    }


def effective_starters(
    db: Session, team: DBTeam, plan: LineupPlan, year: int, week: int,
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
            items.append(AttentionItem(
                rank=ATTENTION_ORDER.index("no_lineup"),
                kind="no_lineup", severity=_SEVERITY["no_lineup"],
                team_name=card.team_name,
                detail=f"Nothing set for week {week}",
                url=card.team_url,
            ))

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

            items.append(AttentionItem(
                rank=ATTENTION_ORDER.index(kind), kind=kind,
                severity=_SEVERITY[kind], team_name=card.team_name,
                detail=detail, url=card.team_url,
                player_id=player_id, player_name=player.name,
                position=player.position, deadline=kickoff,
            ))

        items.extend(
            _bench_better(card, plan, players, saved, projections)
        )

        if saved:
            soonest = [
                locks.kickoff(players[pid].nfl_team, year, week)
                for pid in starting
                if pid in players
            ]
            soonest = [k for k in soonest if k is not None and k > now]
            if soonest and min(soonest) - now <= timedelta(
                hours=LOCK_SOON_HOURS
            ):
                items.append(AttentionItem(
                    rank=ATTENTION_ORDER.index("lock_soon"),
                    kind="lock_soon", severity=_SEVERITY["lock_soon"],
                    team_name=card.team_name,
                    detail="First lineup lock is close",
                    url=card.team_url, deadline=min(soonest),
                ))

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

    from pigskin_mastermind.services.mock_draft import FLEX_ELIGIBLE

    bench = [
        pid for pid in players
        if pid not in saved and projections.get(pid, 0.0) > 0
    ]
    out: List[AttentionItem] = []

    for starter_id, slot in saved.items():
        starter = players.get(starter_id)
        if starter is None:
            continue
        eligible = [
            pid for pid in bench
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
        out.append(AttentionItem(
            rank=ATTENTION_ORDER.index("bench_better"),
            kind="bench_better", severity=_SEVERITY["bench_better"],
            team_name=card.team_name,
            detail=(
                f"{players[best].name} {projections[best]:.1f} on your bench "
                f"beats {starter.name} {projections.get(starter_id, 0.0):.1f} "
                f"in {slot}"
            ),
            url=card.team_url, player_id=best,
            player_name=players[best].name, position=players[best].position,
        ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python -m black src/pigskin_mastermind/services/dashboard.py tests/test_dashboard.py
.venv/Scripts/python -m flake8 src/pigskin_mastermind/services/dashboard.py tests/test_dashboard.py
git add src/pigskin_mastermind/services/dashboard.py tests/test_dashboard.py
git commit -m "feat(dashboard): attention items

Ranked worst-first from the LineupPlan each card was already built from.
bench_better compares against the SAVED lineup, not plan_lineup's ideal --
against the ideal it fires for every team that has not clicked auto-set,
which is the optimizer restating itself rather than news.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The attention fragment

**Files:**
- Create: `src/pigskin_mastermind/templates/dashboard/_attention.html`
- Modify: `src/pigskin_mastermind/api/routes/dashboard.py`
- Modify: `src/pigskin_mastermind/services/dashboard.py` (wire `sections` into `build_view`)
- Test: `tests/integration/test_api_dashboard.py`

**Interfaces:**
- Consumes: `build_attention`, `AttentionItem` (Task 6).
- Produces: `GET /api/dashboard/attention`; `build_view(..., sections={"attention"})` populates `view.attention`.

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_api_dashboard.py`:

```python
class TestAttentionFragment:
    def test_renders_an_item_for_a_team_with_no_lineup(self, client, seeded):
        response = client.get("/api/dashboard/attention")
        assert response.status_code == 200
        assert "Attention needed" in response.text
        assert "Nothing set for week 1" in response.text

    def test_says_nothing_needs_attention_when_clean(self, client):
        """No user teams at all: the panel must render, not 500."""
        response = client.get("/api/dashboard/attention")
        assert response.status_code == 200
        assert "Nothing needs your attention" in response.text

    def test_no_control_says_back(self, client, seeded):
        """The dashboard is a sidebar page; only nav.back_link may say Back."""
        assert "Back" not in client.get("/api/dashboard/attention").text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/integration/test_api_dashboard.py::TestAttentionFragment -v`
Expected: FAIL with 404

- [ ] **Step 3: Write the implementation**

**3a.** Replace the tail of `build_view` in `services/dashboard.py`:

```python
    year, week = resolve_scope(db, now)
    cards, plans = build_league_cards(db, year, week, now)
    rosters = {
        team.id: roster_players(db, team, league)
        for team, league in user_team_leagues(db)
    }

    attention: List[AttentionItem] = []
    if "attention" in sections:
        attention = build_attention(
            db, cards, plans, rosters, year, week, now,
        )

    return DashboardView(
        week=build_week_context(db, year, week, now),
        leagues=cards,
        attention=attention,
    )
```

**3b.** Add to `api/routes/dashboard.py`:

```python
@router.get("/api/dashboard/attention")
async def dashboard_attention(request: Request, db: Session = Depends(get_db)):
    """Band 3 left. Loaded separately so it cannot delay the scores."""
    from pigskin_mastermind.api.main import templates

    view = build_view(db, league_now(), sections=frozenset({"attention"}))
    return templates.TemplateResponse(
        "dashboard/_attention.html", {"request": request, "view": view},
    )
```

**3c.** Create `src/pigskin_mastermind/templates/dashboard/_attention.html`:

```jinja
{# Band 3 left. Read-only: every row is a link to the page that can fix it.
   No control here may say "Back" -- see tests/test_nav_conventions.py. #}
<div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
  <div class="flex items-center justify-between mb-3">
    <h3 class="text-base font-bold text-slate-800">Attention needed</h3>
    {% if view.attention %}
    <span class="text-xs font-bold text-red-600">{{ view.attention|length }} open</span>
    {% endif %}
  </div>

  {% if not view.attention %}
  <p class="text-sm text-slate-400 py-6 text-center">Nothing needs your attention.</p>
  {% else %}
  <div class="divide-y divide-slate-100">
    {% for item in view.attention %}
    <a href="{{ item.url }}" class="flex items-center gap-3 py-2.5 hover:bg-slate-50 -mx-2 px-2 rounded">
      <span class="w-1.5 h-1.5 rounded-full flex-none
        {% if item.severity == 'critical' %}bg-red-600
        {% elif item.severity == 'warning' %}bg-pigskin-500
        {% else %}bg-slate-400{% endif %}"></span>
      {% if item.position %}
      <span class="text-[9px] font-extrabold px-1.5 py-0.5 rounded badge-{{ item.position|lower }}">{{ item.position }}</span>
      {% endif %}
      <span class="text-sm font-semibold text-slate-700 truncate">
        {{ item.player_name or item.team_name }}
      </span>
      <span class="text-xs text-slate-500 truncate flex-1">{{ item.detail }}</span>
      {% if item.deadline %}
      <span class="text-xs text-slate-400 flex-none">{{ item.deadline | kickoff }}</span>
      {% endif %}
    </a>
    {% endfor %}
  </div>
  {% endif %}
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/integration/test_api_dashboard.py tests/test_nav_conventions.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind tests/
git commit -m "feat(dashboard): attention panel fragment

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Your players

**Files:**
- Modify: `src/pigskin_mastermind/services/dashboard.py`
- Create: `src/pigskin_mastermind/templates/dashboard/_players.html`
- Modify: `src/pigskin_mastermind/api/routes/dashboard.py`
- Test: `tests/test_dashboard.py`, `tests/integration/test_api_dashboard.py`

**Interfaces:**
- Consumes: `effective_starters` (Task 6), `LockIndex`, `InjuryIndex`, `ScheduleIndex`.
- Produces:
  - `dashboard.PLAYER_STRIP_LIMIT: int = 9`
  - `dashboard.PlayerCell` (frozen dataclass)
  - `dashboard.build_players(db, cards, plans, rosters, year, week, now) -> tuple[list[PlayerCell], int]`
  - `GET /api/dashboard/players`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard.py`:

```python
class TestPlayerCells:
    def _build(self, db, now):
        cards, plans = dashboard.build_league_cards(db, YEAR, WEEK, now)
        rosters = {
            t.id: dashboard.roster_players(db, t, lg)
            for t, lg in dashboard.user_team_leagues(db)
        }
        return dashboard.build_players(
            db, cards, plans, rosters, YEAR, WEEK, now,
        )

    @pytest.fixture
    def setup(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        players = add_roster(db, league, mine)
        return league, mine, {p.name.split()[-1]: p for p in players}

    def test_one_team_fills_the_strip_exactly(self, db, setup):
        """A legal starting lineup is nine slots, which is the strip."""
        cells, total = self._build(db, WEDNESDAY)
        assert len(cells) == dashboard.PLAYER_STRIP_LIMIT
        assert total == 9

    def test_caps_the_strip_and_reports_the_true_total(self, db, setup):
        """Two teams is eighteen starters; the strip still shows nine and
        says so, which is what the "N more" link is built from."""
        league, _mine, _players = setup
        second = add_team(db, league, "Second Squad")
        add_roster(db, league, second)

        cells, total = self._build(db, WEDNESDAY)
        assert len(cells) == dashboard.PLAYER_STRIP_LIMIT
        assert total == 18

    def test_upcoming_players_sort_by_kickoff_then_projection(self, db, setup):
        cells, _total = self._build(db, WEDNESDAY)
        assert {c.state for c in cells} == {"upcoming"}
        early = [c for c in cells if c.kickoff_at == SUNDAY_EARLY]
        assert early[0].projected >= early[-1].projected

    def test_a_player_mid_game_sorts_first_and_reads_as_playing(self, db, setup):
        cells, _total = self._build(db, MID_EARLY_GAME)
        assert cells[0].state == "playing"

    def test_a_finished_game_reads_as_final(self, db, setup):
        game = db.query(DBNFLGame).filter_by(home_team="CHI").first()
        game.home_score, game.away_score = 20, 17
        db.commit()
        cells, _total = self._build(db, MID_EARLY_GAME)
        assert "final" in {c.state for c in cells}

    def test_an_injured_starter_is_a_concern(self, db, setup):
        _league, _mine, p = setup
        add_injury(db, p["QB1"], "Questionable")
        cells, _total = self._build(db, WEDNESDAY)
        cell = next(c for c in cells if c.player_id == p["QB1"].id)
        assert cell.state == "concern"
        assert "questionable" in cell.note.lower()

    def test_a_player_on_two_teams_appears_once(self, db):
        add_schedule(db)
        espn = add_league(db, "espn-x", "Airframe", "espn")
        season = add_league(db, "season-x", "Bird Turds", "season")
        espn_team = add_team(db, espn, "55 burgers")
        season_team = add_team(db, season, "The Scoobies")
        shared = add_roster(db, espn, espn_team)
        # Put the same DBPlayer rows on the season roster too.
        for p in shared:
            db.add(DBRosterSpot(league_id=season.id, team_id=season_team.id,
                                player_id=p.id, acquired_via="draft"))
        db.commit()

        cells, _total = self._build(db, WEDNESDAY)
        ids = [c.player_id for c in cells]
        assert len(ids) == len(set(ids))

    def test_every_cell_links_back_to_the_dashboard(self, db, setup):
        cells, _total = self._build(db, WEDNESDAY)
        assert all(c.url.endswith("?back=/") for c in cells)
```

And to `tests/integration/test_api_dashboard.py`:

```python
class TestPlayersFragment:
    def test_renders(self, client, seeded):
        response = client.get("/api/dashboard/players")
        assert response.status_code == 200
        assert "Your players" in response.text

    def test_empty_roster_does_not_500(self, client):
        assert client.get("/api/dashboard/players").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py::TestPlayerCells -v`
Expected: FAIL with `AttributeError: ... has no attribute 'build_players'`

- [ ] **Step 3: Write the implementation**

**3a.** Append to `services/dashboard.py`:

```python
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
    """The user's starters across every team, deduplicated and ranked.

    League boundaries are deliberately dissolved here: one ``DBPlayer`` row is
    routinely on an ESPN roster and a season roster at once, and showing him
    twice is noise. Returns the capped strip and the true total, so the
    template can say how many it is not showing.
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
                player_id=player_id, name=player.name,
                url=f"/players/{player_id}?back=/",
                state=state, position=player.position,
                nfl_team=player.nfl_team,
                projected=round(projections.get(player_id, 0.0), 1),
                kickoff_at=kickoff, note=note,
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
    return cells[:PLAYER_STRIP_LIMIT], len(cells)
```

Add `from pigskin_mastermind.utils.nfl_teams import normalize_team` to the imports.

**3b.** Wire it into `build_view`, immediately after the `attention` block:

```python
    players: List[PlayerCell] = []
    players_total = 0
    if "players" in sections:
        players, players_total = build_players(
            db, cards, plans, rosters, year, week, now,
        )
```

and pass `players=players, players_total=players_total` to the `DashboardView(...)` call.

Also feed the count back into the hero — replace the `week=` argument with:

```python
        week=build_week_context(
            db, year, week, now,
            yet_to_play=sum(1 for c in players if c.state == "upcoming"),
        ),
```

**3c.** Add the endpoint to `api/routes/dashboard.py`:

```python
@router.get("/api/dashboard/players")
async def dashboard_players(request: Request, db: Session = Depends(get_db)):
    """Band 4: the user's starters across every team."""
    from pigskin_mastermind.api.main import templates

    view = build_view(db, league_now(), sections=frozenset({"players"}))
    return templates.TemplateResponse(
        "dashboard/_players.html", {"request": request, "view": view},
    )
```

**3d.** Create `src/pigskin_mastermind/templates/dashboard/_players.html`:

```jinja
{# Band 4. One row, nine cells, ranked by what matters right now. #}
<div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
  <div class="flex items-center justify-between mb-3">
    <h3 class="text-base font-bold text-slate-800">Your players &middot; week {{ view.week.week }}</h3>
    {% if view.players_total > view.players|length %}
    <a href="/players?back=/" class="text-xs text-pigskin-600 font-semibold">
      {{ view.players_total - view.players|length }} more &rarr;
    </a>
    {% endif %}
  </div>

  {% if not view.players %}
  <p class="text-sm text-slate-400 py-6 text-center">No starters yet — draft or sync a team.</p>
  {% else %}
  <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-9 gap-2">
    {% for cell in view.players %}
    <a href="{{ cell.url }}"
       class="rounded-lg border p-2 card-hover
         {% if cell.state == 'playing' %}border-field-300 bg-field-50
         {% elif cell.state == 'concern' %}border-red-200 bg-red-50
         {% elif cell.state == 'final' %}border-slate-200 opacity-60
         {% else %}border-slate-200{% endif %}">
      <span class="text-[9px] font-extrabold px-1.5 py-0.5 rounded badge-{{ (cell.position or 'flex')|lower }}">
        {{ cell.position or '--' }}
      </span>
      <span class="block text-xs font-bold text-slate-800 truncate mt-1">{{ cell.name }}</span>
      <span class="block text-[10px] text-slate-500 truncate">
        {% if cell.note %}{{ cell.note }}{% elif cell.kickoff_at %}{{ cell.kickoff_at | kickoff }}{% endif %}
        {% if cell.projected %}&middot; {{ "%.1f"|format(cell.projected) }}{% endif %}
      </span>
    </a>
    {% endfor %}
  </div>
  {% endif %}
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py tests/integration/test_api_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python -m black src/pigskin_mastermind tests/
.venv/Scripts/python -m flake8 src/pigskin_mastermind tests/
git add src/pigskin_mastermind tests/
git commit -m "feat(dashboard): your players strip

Deduplicated across teams -- one DBPlayer row is routinely on an ESPN
roster and a season roster at once.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The NFL slate

**Files:**
- Modify: `src/pigskin_mastermind/services/dashboard.py`
- Create: `src/pigskin_mastermind/templates/dashboard/_slate.html`
- Modify: `src/pigskin_mastermind/api/routes/dashboard.py`
- Test: `tests/test_dashboard.py`, `tests/integration/test_api_dashboard.py`

**Interfaces:**
- Consumes: `build_players` (for the deduplicated player set), `normalize_team`.
- Produces:
  - `dashboard.SlateGame` (frozen dataclass)
  - `dashboard.build_slate(db, rosters, year, week, now) -> list[SlateGame]`
  - `GET /api/dashboard/slate`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard.py`:

```python
class TestSlate:
    @pytest.fixture
    def setup(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        add_roster(db, league, mine)
        return league, mine

    def _build(self, db, now):
        rosters = {
            t.id: dashboard.roster_players(db, t, lg)
            for t, lg in dashboard.user_team_leagues(db)
        }
        return dashboard.build_slate(db, rosters, YEAR, WEEK, now)

    def test_orders_by_kickoff(self, db, setup):
        slate = self._build(db, WEDNESDAY)
        assert [g.kickoff_at for g in slate] == sorted(
            g.kickoff_at for g in slate
        )

    def test_badges_the_users_players(self, db, setup):
        """Counts the whole roster, not just starters — you care that four of
        your players are in one game whichever of them you started."""
        slate = self._build(db, WEDNESDAY)
        chi_det = next(g for g in slate if g.home_team == "CHI")
        # CHI: QB1, TE1.  DET: RB1, DEF1.
        assert chi_det.your_player_count == 4
        assert "The Scoobies QB1" in chi_det.your_player_names

    def test_a_game_with_none_of_your_players_still_renders(self, db, setup):
        db.add(DBNFLGame(year=YEAR, week=WEEK, home_team="NYJ",
                         away_team="BUF", kickoff_at=SUNDAY_EARLY))
        db.commit()
        slate = self._build(db, WEDNESDAY)
        nyj = next(g for g in slate if g.home_team == "NYJ")
        assert nyj.your_player_count == 0

    def test_state_is_derived_from_the_schedule_not_the_score(self, db, setup):
        """A 0-0 game that has kicked off is in progress, not upcoming."""
        slate = self._build(db, MID_EARLY_GAME)
        early = next(g for g in slate if g.home_team == "CHI")
        late = next(g for g in slate if g.home_team == "KC")
        assert early.state == "in_progress"
        assert late.state == "upcoming"

    def test_a_scored_game_is_final(self, db, setup):
        game = db.query(DBNFLGame).filter_by(home_team="CHI").first()
        game.home_score, game.away_score = 20, 17
        db.commit()
        slate = self._build(db, MID_EARLY_GAME)
        assert next(g for g in slate if g.home_team == "CHI").state == "final"

    def test_carries_the_market_lines(self, db, setup):
        slate = self._build(db, WEDNESDAY)
        chi = next(g for g in slate if g.home_team == "CHI")
        assert chi.total_line == pytest.approx(48.5)
        assert chi.spread_line == pytest.approx(1.5)
```

And to `tests/integration/test_api_dashboard.py`:

```python
class TestSlateFragment:
    def test_renders(self, client, seeded):
        response = client.get("/api/dashboard/slate")
        assert response.status_code == 200
        assert "CHI" in response.text

    def test_no_schedule_does_not_500(self, client):
        assert client.get("/api/dashboard/slate").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py::TestSlate -v`
Expected: FAIL with `AttributeError: ... has no attribute 'build_slate'`

- [ ] **Step 3: Write the implementation**

**3a.** Append to `services/dashboard.py`:

```python
@dataclass(frozen=True)
class SlateGame:
    home_team: str
    away_team: str
    state: str
    game_id: Optional[str] = None
    kickoff_at: Optional[datetime] = None
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    spread_line: Optional[float] = None
    total_line: Optional[float] = None
    your_player_count: int = 0
    your_player_names: List[str] = field(default_factory=list)


def build_slate(
    db: Session,
    rosters: Dict[int, List[DBPlayer]],
    year: int,
    week: int,
    now: datetime,
) -> List[SlateGame]:
    """The week's NFL games, with the user's players badged onto each.

    ``state`` comes from the schedule and the clock, never from the score. A
    real game can sit at 0-0 well into the first quarter, and calling that
    "upcoming" would contradict the live badge in the hero.

    Games with none of the user's players still appear, dimmed. Hiding them
    would stop this being the slate.
    """
    window = timedelta(hours=GAME_WINDOW_HOURS)

    owned: Dict[int, DBPlayer] = {}
    for players in rosters.values():
        for player in players:
            owned.setdefault(player.id, player)

    by_team: Dict[str, List[str]] = {}
    for player in owned.values():
        canonical = normalize_team(player.nfl_team)
        if canonical:
            by_team.setdefault(canonical, []).append(player.name)

    games = (
        db.query(DBNFLGame)
        .filter(DBNFLGame.year == year, DBNFLGame.week == week)
        .all()
    )

    slate: List[SlateGame] = []
    for game in games:
        if game.home_score is not None and game.away_score is not None:
            state = "final"
        elif (
            game.kickoff_at is not None
            and game.kickoff_at <= now < game.kickoff_at + window
        ):
            state = "in_progress"
        else:
            state = "upcoming"

        names = sorted(
            by_team.get(normalize_team(game.home_team) or "", [])
            + by_team.get(normalize_team(game.away_team) or "", [])
        )
        slate.append(SlateGame(
            game_id=game.game_id, home_team=game.home_team,
            away_team=game.away_team, kickoff_at=game.kickoff_at,
            home_score=game.home_score, away_score=game.away_score,
            spread_line=game.spread_line, total_line=game.total_line,
            state=state, your_player_count=len(names),
            your_player_names=names,
        ))

    slate.sort(key=lambda g: (g.kickoff_at or datetime.max, g.home_team))
    return slate
```

**3b.** Wire into `build_view` after the players block:

```python
    slate: List[SlateGame] = []
    if "slate" in sections:
        slate = build_slate(db, rosters, year, week, now)
```

and pass `slate=slate` to `DashboardView(...)`.

**3c.** Add the endpoint to `api/routes/dashboard.py`:

```python
@router.get("/api/dashboard/slate")
async def dashboard_slate(request: Request, db: Session = Depends(get_db)):
    """Band 5: the week's NFL games."""
    from pigskin_mastermind.api.main import templates

    view = build_view(db, league_now(), sections=frozenset({"slate"}))
    return templates.TemplateResponse(
        "dashboard/_slate.html", {"request": request, "view": view},
    )
```

**3d.** Create `src/pigskin_mastermind/templates/dashboard/_slate.html`:

```jinja
{# Band 5. Wide content scrolls inside its own container so the page body
   never scrolls horizontally. #}
<div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
  <div class="flex items-center justify-between mb-3">
    <h3 class="text-base font-bold text-slate-800">This week's NFL slate</h3>
    <a href="/games?back=/" class="text-xs text-pigskin-600 font-semibold">All scores &rarr;</a>
  </div>

  {% if not view.slate %}
  <p class="text-sm text-slate-400 py-6 text-center">No {{ view.week.year }} schedule imported.</p>
  {% else %}
  <div class="overflow-x-auto">
    <div class="flex gap-2 min-w-max">
      {% for game in view.slate %}
      <div class="w-40 flex-none rounded-lg border p-2.5
        {% if game.state == 'in_progress' %}border-field-300 bg-field-50
        {% elif game.your_player_count == 0 %}border-slate-200 opacity-50
        {% else %}border-slate-200{% endif %}">
        <p class="text-xs font-bold text-slate-800">{{ game.away_team }} @ {{ game.home_team }}</p>
        <p class="text-[10px] text-slate-500 mt-0.5">
          {% if game.state == 'final' %}
            Final {{ game.away_score }}&ndash;{{ game.home_score }}
          {% elif game.state == 'in_progress' %}
            In progress
          {% elif game.kickoff_at %}
            {{ game.kickoff_at | kickoff }}
          {% else %}
            Time TBD
          {% endif %}
          {% if game.total_line %}&middot; o{{ "%.1f"|format(game.total_line) }}{% endif %}
        </p>
        {% if game.your_player_count %}
        <span class="inline-block mt-1.5 text-[9px] font-extrabold bg-field-100 text-field-800 rounded px-1.5 py-0.5"
              title="{{ game.your_player_names|join(', ') }}">
          {{ game.your_player_count }} yours
        </span>
        {% endif %}
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_dashboard.py tests/integration/test_api_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python -m black src/pigskin_mastermind tests/
.venv/Scripts/python -m flake8 src/pigskin_mastermind tests/
git add src/pigskin_mastermind tests/
git commit -m "feat(dashboard): NFL slate band

State comes from the schedule and the clock, never the score -- a real
game can sit 0-0 into the first quarter.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Phase 3 — Hot movers and integration

### Task 10: Share the metrics-week helpers, then render the movers band

`api/routes/metrics.py` already solves "which season and week actually have usable metrics" in two private helpers. The dashboard needs the same answers, and a second copy would drift the first time either is tuned.

**Files:**
- Modify: `src/pigskin_mastermind/services/metric_trends.py`
- Modify: `src/pigskin_mastermind/api/routes/metrics.py:23-64`
- Modify: `src/pigskin_mastermind/services/dashboard.py`
- Create: `src/pigskin_mastermind/templates/dashboard/_movers.html`
- Modify: `src/pigskin_mastermind/api/routes/dashboard.py`
- Test: `tests/test_advanced_metrics.py`, `tests/test_dashboard.py`, `tests/integration/test_api_dashboard.py`

**Interfaces:**
- Consumes: `metric_trends.hot_movers`, `metric_trends.Mover`.
- Produces:
  - `metric_trends.latest_season_with_metrics(db, preferred) -> Optional[int]`
  - `metric_trends.last_full_week(db, year) -> int`
  - `dashboard.MOVER_LIMIT: int = 6`
  - `dashboard.build_movers(db, now, limit=MOVER_LIMIT) -> list[Mover]`
  - `GET /api/dashboard/movers`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_advanced_metrics.py`:

```python
class TestSharedWeekHelpers:
    def test_latest_season_prefers_the_requested_year(self, db):
        from pigskin_mastermind.services.metric_trends import (
            latest_season_with_metrics,
        )
        assert latest_season_with_metrics(db, 2025) == 2025

    def test_latest_season_falls_back_to_the_newest_stored(self, db):
        from pigskin_mastermind.services.metric_trends import (
            latest_season_with_metrics,
        )
        assert latest_season_with_metrics(db, 2030) == 2025

    def test_latest_season_is_none_with_no_metrics_at_all(self, db):
        from pigskin_mastermind.models.database import DBPlayerAdvancedMetric
        from pigskin_mastermind.services.metric_trends import (
            latest_season_with_metrics,
        )
        db.query(DBPlayerAdvancedMetric).delete()
        db.commit()
        assert latest_season_with_metrics(db, 2025) is None
```

Adapt the fixture names to whatever `tests/test_advanced_metrics.py` already uses — read the file first and reuse its `db` fixture and its metric-seeding helper rather than adding a second one.

Append to `tests/test_dashboard.py`:

```python
class TestMovers:
    def test_no_metrics_means_an_empty_band_not_an_error(self, db):
        assert dashboard.build_movers(db, WEDNESDAY) == []
```

And to `tests/integration/test_api_dashboard.py`:

```python
class TestMoversFragment:
    def test_renders_with_no_metrics(self, client, seeded):
        response = client.get("/api/dashboard/movers")
        assert response.status_code == 200
        assert "Hot movers" in response.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_advanced_metrics.py::TestSharedWeekHelpers tests/test_dashboard.py::TestMovers -v`
Expected: FAIL with `ImportError: cannot import name 'latest_season_with_metrics'`

- [ ] **Step 3: Write the implementation**

**3a.** Move both helpers into `services/metric_trends.py`, verbatim apart from the name and the docstring's first line. Add `from sqlalchemy import func` to its imports if absent.

```python
def latest_season_with_metrics(db: Session, preferred: int) -> Optional[int]:
    """The newest season that actually has metrics, preferring *preferred*.

    Week 1 of a new season has no games, so the current year holds nothing to
    trend. Silently rendering an empty page would read as broken; falling back
    to the last season with data — and saying so — is the honest behaviour.
    """
    years = [
        row[0]
        for row in db.query(DBPlayerAdvancedMetric.year).distinct().all()
    ]
    if not years:
        return None
    if preferred in years:
        return preferred
    return max(years)


def last_full_week(db: Session, year: int) -> int:
    """The newest week with league-wide coverage.

    Not simply ``max(week)``. A season's last stored weeks are the playoffs,
    where a handful of teams remain — so a scan anchored there finds almost
    nobody with six continuous weeks of history and the page renders empty on
    a full database. Self-calibrating on the season's own peak coverage rather
    than a hardcoded week 18, since the postseason format is not this module's
    business.
    """
    counts = (
        db.query(
            DBPlayerAdvancedMetric.week,
            func.count(func.distinct(DBPlayerAdvancedMetric.player_id)),
        )
        .filter(DBPlayerAdvancedMetric.year == year)
        .group_by(DBPlayerAdvancedMetric.week)
        .all()
    )
    if not counts:
        return 1

    peak = max(count for _week, count in counts)
    full = [week for week, count in counts if count >= peak * 0.5]
    return max(full) if full else max(week for week, _count in counts)
```

**3b.** In `api/routes/metrics.py`, delete `_latest_season_with_metrics` and `_last_week`, import the moved names, and rename every call site:

```python
from pigskin_mastermind.services.metric_trends import (
    hot_movers, last_full_week, latest_season_with_metrics, percentile,
    player_series,
)
```

Search the file for `_latest_season_with_metrics(` and `_last_week(` and replace with the public names. Remove the now-unused `func` import if `flake8` flags it.

**3c.** Append to `services/dashboard.py`:

```python
#: A strip beside the attention panel, not a page. /metrics/hot is the page.
MOVER_LIMIT = 6


def build_movers(db: Session, now: datetime, limit: int = MOVER_LIMIT):
    """The strongest buy signals, from whichever season actually has metrics.

    Week 1 of a new season has no trend to compute, so this deliberately reads
    the last season with coverage rather than rendering an empty band and
    looking broken.
    """
    from pigskin_mastermind.services.metric_trends import (
        hot_movers, last_full_week, latest_season_with_metrics,
    )

    year = latest_season_with_metrics(db, current_fantasy_season(now.date()))
    if year is None:
        return []
    return hot_movers(db, year, last_full_week(db, year), limit=limit)
```

Wire it into `build_view` after the slate block:

```python
    movers: List[object] = []
    if "movers" in sections:
        movers = build_movers(db, now)
```

and pass `movers=movers` to `DashboardView(...)`.

**3d.** Add the endpoint to `api/routes/dashboard.py`:

```python
@router.get("/api/dashboard/movers")
async def dashboard_movers(request: Request, db: Session = Depends(get_db)):
    """Band 3 right: usage running ahead of production."""
    from pigskin_mastermind.api.main import templates

    view = build_view(db, league_now(), sections=frozenset({"movers"}))
    return templates.TemplateResponse(
        "dashboard/_movers.html", {"request": request, "view": view},
    )
```

**3e.** Create `src/pigskin_mastermind/templates/dashboard/_movers.html`:

```jinja
{# Band 3 right. A strip, not the page -- /metrics/hot is the page. #}
<div class="bg-white rounded-xl shadow-sm border border-slate-200 p-5">
  <div class="flex items-center justify-between mb-3">
    <h3 class="text-base font-bold text-slate-800">Hot movers</h3>
    <a href="/metrics/hot?back=/" class="text-xs text-pigskin-600 font-semibold">All &rarr;</a>
  </div>

  {% if not view.movers %}
  <p class="text-sm text-slate-400 py-6 text-center">No metrics imported yet.</p>
  {% else %}
  <div class="divide-y divide-slate-100">
    {% for mover in view.movers %}
    <a href="/players/{{ mover.player_id }}?back=/"
       class="flex items-center gap-2 py-2 hover:bg-slate-50 -mx-2 px-2 rounded">
      <span class="text-[9px] font-extrabold px-1.5 py-0.5 rounded badge-{{ (mover.position or 'flex')|lower }}">
        {{ mover.position or '--' }}
      </span>
      <span class="text-sm font-semibold text-slate-700 truncate flex-1">{{ mover.name }}</span>
      {% if mover.rookie_rising %}
      <span class="text-[9px] font-extrabold text-purple-700">R&middot;{{ mover.draft_label }}</span>
      {% endif %}
      <span class="text-xs font-bold {% if mover.verdict == 'buy' %}text-field-700{% else %}text-slate-400{% endif %}">
        {{ mover.verdict }} {{ "%+.1f"|format(mover.divergence) }}
      </span>
    </a>
    {% endfor %}
  </div>
  {% endif %}
</div>
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_advanced_metrics.py tests/test_dashboard.py tests/integration/test_api_dashboard.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
.venv/Scripts/python -m black src/pigskin_mastermind tests/
.venv/Scripts/python -m flake8 src/pigskin_mastermind tests/
git add src/pigskin_mastermind tests/
git commit -m "feat(dashboard): hot movers band

Moves the season/week resolution helpers out of the metrics route into
metric_trends so the dashboard and /metrics/hot cannot disagree about
which week has usable coverage.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Live polling, page/fragment agreement, and the full-suite check

**Files:**
- Test: `tests/integration/test_api_dashboard.py`
- Modify: none expected — this task verifies. Fix whatever it catches.

**Interfaces:**
- Consumes: everything above.
- Produces: no new interfaces.

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_api_dashboard.py`:

```python
from datetime import datetime, timedelta

from pigskin_mastermind.models.database import DBMatchup


@pytest.fixture
def live_week():
    """A kickoff two hours ago, so the week is inside a game window."""
    db = TestSessionLocal()
    now = datetime.now()
    lg = DBLeague(league_id="season-x", name="Bird Turds", year=now.year,
                  kind="season", current_week=1)
    db.add(lg)
    db.commit()
    mine = DBTeam(team_id="t1", name="The Scoobies", owner="Brandon",
                  league_id="season-x", is_user_team=True)
    theirs = DBTeam(team_id="t2", name="Touchdown There", owner="AI",
                    league_id="season-x", is_user_team=False)
    db.add_all([mine, theirs])
    db.commit()
    db.add(DBNFLGame(year=now.year, week=1, home_team="CHI", away_team="DET",
                     kickoff_at=now - timedelta(hours=2)))
    db.add(DBMatchup(league_id=lg.id, year=now.year, week=1, bracket_slot=0,
                     home_team_id=mine.id, away_team_id=theirs.id,
                     status="in_progress", home_points=61.4, away_points=44.9))
    db.commit()
    db.close()


class TestLivePolling:
    def test_polls_while_a_game_is_in_its_window(self, client, live_week):
        assert 'hx-trigger="every 30s"' in client.get(
            "/api/dashboard/pulse"
        ).text

    def test_the_live_badge_appears(self, client, live_week):
        assert "LIVE" in client.get("/api/dashboard/pulse").text


class TestPageAndFragmentAgree:
    def test_same_score_in_both(self, client, live_week):
        page = client.get("/").text
        fragment = client.get("/api/dashboard/pulse").text
        assert "61.4" in page
        assert "61.4" in fragment
        assert "44.9" in page
        assert "44.9" in fragment
```

> **Note on `league_now()`:** the routes call it directly, so this fixture builds its kickoff from `datetime.now()` — naive local time — rather than a fixed date. On a machine outside US-Eastern the two clocks differ; if that makes this test flaky, the fix is to import `league_now` and build the fixture from `league_now()`, not to widen the window.

- [ ] **Step 2: Run tests to verify they fail (or pass, and say which)**

Run: `.venv/Scripts/python -m pytest tests/integration/test_api_dashboard.py -v`
Expected: These may already pass — Tasks 5–10 implemented the behaviour. If they pass first time, that is the confirmation, not a reason to skip the task. If any fail, fix the implementation, not the test.

- [ ] **Step 3: Run the whole suite and compare against the baseline**

```bash
.venv/Scripts/python -m pytest tests/ -q
```

Expected: the 12 known pre-existing failures and nothing else. Write the failure set down and diff it against a `git stash`-ed run on the merge base if anything looks unfamiliar. **Do not fix a failure you did not cause.**

- [ ] **Step 4: Verify in the browser**

```bash
uvicorn pigskin_mastermind.api.main:app --reload --port 8010
```

Check all five bands render, the ESPN cards read "No 2026 weeks synced", the attention panel lists the unset lineups, the players strip shows nine cells, the slate scrolls horizontally without the page body scrolling, and no console errors. Take a screenshot for the commit.

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test(dashboard): live polling and page/fragment agreement

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 6: Update the architecture docs**

Add a short section to `CLAUDE.md` under "Web layer conventions", and a line to `docs/PROJECT_STATUS.md`. Keep it to the two facts a future reader cannot infer from the code:

```markdown
### The dashboard is one builder, two renderings

`services/dashboard.py::build_view` is the only place a dashboard fact is
derived. `GET /` and the four `/api/dashboard/*` fragments all call it — the
page and the live pulse fragment showing different scores for one matchup is
the specific failure that arrangement prevents.

Polling is **self-terminating**: `_pulse.html` renders its own
`hx-trigger="every 30s"` only while `week.games_live`, so it starts at the
first kickoff and stops when the last game leaves its window, because the
replacement fragment omits the attribute. `games_live` comes from
`season_scheduler.in_game_window()` — the same predicate the scheduler polls
ESPN on, so the page cannot keep refreshing after the scheduler has stopped
fetching anything new.

The dashboard is **read-only**. Every row links to the page that can act; no
endpoint here writes.
```

```bash
git add CLAUDE.md docs/PROJECT_STATUS.md
git commit -m "docs: record the dashboard's builder and polling contract

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Self-review notes

Checked against the spec on 2026-09-07:

- **Every spec section has a task.** Page structure → Tasks 5, 7, 8, 9, 10. View model → Tasks 2, 3, 6, 8, 9. Three LeagueCard fillers → Tasks 3 (season), 4 (espn + archive footnote). Derivation rules → Task 6. Liveness → Tasks 1, 5, 11. Time → global constraint, exercised in Tasks 2 and 11. Navigation → Tasks 5, 7, 8, 9, 10 templates plus the `?back=/` assertions in Tasks 3, 6, 8. Files → the File Structure table. Testing (spec items 1–10) → Tasks 4, 4, 6, 6, 5, 11, 8, 1, 4, and the nav suite in Task 7.
- **`sections` is threaded from Task 5 forward.** Task 5 fixes the signature with an unused parameter deliberately, so `api/routes/dashboard.py` is written once and never edited again as bands land.
- **Name consistency:** `build_week_context`, `build_league_cards`, `build_attention`, `build_players`, `build_slate`, `build_movers`, `build_view` — one prefix throughout. `effective_starters` / `saved_starters` are used identically in Tasks 6 and 8. `latest_season_with_metrics` / `last_full_week` are the post-move names everywhere they appear.
- **Defects found and fixed during this review:** `strftime('%-I')` in four templates (a glibc extension that raises `ValueError` on Windows — replaced with a registered `kickoff` filter, added to Task 5); a wrong expected projection in Task 3 (139.4, not 138.4 — arithmetic shown in the test); a wrong badge count in Task 9 (CHI@DET holds four of the fixture roster, not three); `saved_starters` called with an `int` instead of a `DBTeam` in Task 6; and a Jinja precedence bug in `_pulse.html`, where `A if C else B or D` parses as `A if C else (B or D)` and would render `None` for a live card whose points had not been written yet.
- **Task 8's strip cap is now actually exercised.** The original test used a single nine-starter team, so `len(cells) == 9` and `total == 9` proved nothing about truncation; it now adds a second team and asserts 9 of 18.
