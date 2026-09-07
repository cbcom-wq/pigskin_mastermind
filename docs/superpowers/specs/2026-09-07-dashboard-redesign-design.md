# Dashboard redesign: league and NFL pulse

**Date:** 2026-09-07
**Status:** approved for implementation

## Problem

`/` is the app's front page and tells the user nothing they could not guess. It
renders four counters — teams, players, total points, and *the number of
distinct positions on the roster*, which is not a fact about anything — a
Quick Actions block duplicating links already in the sidebar, a position tally,
and a row of team cards.

Nothing on it is week-aware. It does not know what week it is, who the user
plays this week, whether a lineup is set, whether a starter is hurt, or that
there is an NFL game on right now. Every one of those facts is already in the
database and reachable through an existing service.

The route handler queries the ORM directly (`api/main.py::dashboard`), which is
why it has stayed shallow: each new fact means another query in `main.py`.

## Goal

A front page whose job is **"what is happening in my football world right
now"** — accurate on a live Sunday and still worth opening on a Wednesday.

Scope decisions taken during design, recorded so they are not relitigated:

- **All four user teams, equal weight.** No primary team, no focus mode.
- **No filtering.** The `test-projections` demo league appears like any other;
  hiding it would need a concept of "real league" the schema does not have.
- **Read-only.** No POST endpoints. Nothing on the dashboard can change a
  lineup, so the lineup validator is not in play here.
- **Live during game windows only.** Polling exists while NFL games are in
  progress and at no other time.

## Page structure

Five bands, top to bottom. The same five on every day of the week; only their
content changes.

1. **Pulse hero** — week, day, live badge, a one-line summary
   ("6 of 14 games in progress · 23 of your players yet to play"), and a
   countdown to the next meaningful moment (next kickoff wave, or first kickoff
   of the week).
2. **Matchup row** — one card per user team, four across, equal weight.
3. **Attention needed** (2/3 width) + **Hot movers** (1/3 width).
4. **Your players** — top 9 starters across all teams, by relevance.
5. **This week's NFL slate** — the week's games with the user's players badged.

Bands 1 and 2 are the live-polling region. Bands 3–5 are static per page load.

Deleted outright: Quick Actions, the Positions counter, Roster Breakdown.

## Architecture

```
GET /                        GET /api/dashboard/pulse
     │                                │
     └────────────────┬───────────────┘
                      ▼
    services/dashboard.py::build_view(db, now, sections=ALL_SECTIONS)
                      │
   ┌──────────┬───────┼────────────┬─────────────┐
   ▼          ▼       ▼            ▼             ▼
WeekContext LeagueCard[] AttentionItem[] PlayerCell[] SlateGame[] + Mover[]
```

A service module rather than a fatter route, for three reasons:

- The page reads from eight services. In `main.py` that is unbounded growth in
  the file that also owns app construction and router registration.
- The full page and the polling fragment **must not be able to disagree**. One
  builder, two renderings, is the only way to guarantee that.
- It is testable without HTTP. Every rule below gets a unit test against a
  fixture session.

### View model

```python
@dataclass(frozen=True)
class WeekContext:
    year: int
    week: int
    now: datetime                      # naive US-Eastern, from league_now()
    games_total: int
    games_in_progress: int
    games_final: int
    games_live: bool                   # any game inside its window
    next_kickoff: Optional[datetime]
    players_yet_to_play: int

@dataclass(frozen=True)
class LeagueCard:
    league_id: str
    league_name: str
    kind: str                          # season | espn | archive
    team_id: int
    team_name: str
    team_url: str                      # already carries ?back=/
    opponent_name: Optional[str]
    points: Optional[float]
    opponent_points: Optional[float]
    projected: Optional[float]
    opponent_projected: Optional[float]
    is_live: bool                      # points are live, not projected
    yet_to_play: int
    empty_reason: Optional[str]        # renders instead of a score
    empty_action: Optional[tuple[str, str]]   # (label, url)
    footnote: Optional[str]            # e.g. "2025 finish: 10-4, 2237.8 pts"

@dataclass(frozen=True)
class AttentionItem:
    rank: int
    kind: str                          # see ATTENTION_ORDER
    severity: str                      # critical | warning | info
    player_id: Optional[int]
    player_name: Optional[str]
    position: Optional[str]
    team_name: str
    detail: str
    url: str                           # carries ?back=/
    deadline: Optional[datetime]

@dataclass(frozen=True)
class PlayerCell:
    player_id: int
    name: str
    position: Optional[str]
    nfl_team: Optional[str]
    state: str                         # playing | concern | upcoming | final
    live_points: Optional[float]
    projected: Optional[float]
    kickoff_at: Optional[datetime]
    note: Optional[str]                # "Questionable", "on bye", "Q3"
    url: str

@dataclass(frozen=True)
class SlateGame:
    game_id: Optional[str]
    home_team: str
    away_team: str
    kickoff_at: Optional[datetime]
    home_score: Optional[int]
    away_score: Optional[int]
    state: str                         # upcoming | in_progress | final
    spread_line: Optional[float]
    total_line: Optional[float]
    your_player_count: int
    your_player_names: list[str]       # for the tooltip

@dataclass(frozen=True)
class DashboardView:
    week: WeekContext
    leagues: list[LeagueCard]
    attention: list[AttentionItem]
    players: list[PlayerCell]
    players_total: int
    slate: list[SlateGame]
    movers: list[Mover]                # reused unchanged from metric_trends
```

`Mover` is imported from `services/metric_trends.py` rather than re-wrapped. It
already carries `verdict`, `draft_label` and `rookie_rising`, which is exactly
what the strip renders.

### Sections

```python
ALL_SECTIONS = frozenset({"pulse", "attention", "players", "slate", "movers"})

def build_view(db, now, sections=ALL_SECTIONS) -> DashboardView: ...
```

`week` and `leagues` are always built — every section needs the year, the week,
and the rosters, and building them twice would be the expensive half. The
`sections` argument gates only the four independently-loaded bands, so a
fragment endpoint does not run `hot_movers()` to render the slate. Unrequested
sections come back as empty lists, never `None`, so a template cannot tell the
difference between "not requested" and "nothing to show" — the fragment
templates each render exactly one section and never inspect the others.

### Who counts as a starter

Several rules below turn on "starters". The definition is: the players in the
team's saved `DBLineupSlot` rows for `(year, week)` in a non-bench slot, and
when the team has saved no lineup for that week, the starters
`plan_lineup()` recommends.

The fallback matters because an unset lineup is exactly the case the
`no_lineup` attention item exists to report — without it, the team with the
biggest problem would contribute no players to the strip and no
`players_yet_to_play` count.

### Which year and week

`build_view` derives one `(year, week)` for the whole page from the newest
non-archive league (`max(DBLeague.year)` among `kind != 'archive'`, then that
league's `current_week or 1`). A per-card week would mean the hero's "Week 1"
could disagree with a card beside it.

An `archive` league is rendered from its own frozen record and never consulted
for the current week.

## LeagueCard: three fillers, one shape

Chosen on `DBLeague.kind`.

### `season`

Opponent and live score from `DBMatchup` filtered on
`(league.id, league.year, week)`. `home_points` / `away_points` are what
`live_scoring` writes.

Projections for both sides come from `plan_lineup(...).projected_total`.

`is_live` is true when the matchup `status` is not `scheduled`, or when any of
the team's starters is inside a game window.

### `espn`

There are no `DBMatchup` rows for an ESPN league. Opponent and score come from
`weekly_team_stats` (`opponent_name`, `points_for`, `points_against`,
`projected_points`).

**This query must join `weekly_team_stats -> teams -> leagues` and filter on
`DBLeague.year`.** `uq_team_week` is `('team_id', 'week')` with no year column,
so an unfiltered read of week 1 serves the archived 2025 row as this season's
score — a plausible number, for the right team, from the wrong year. This is
the same trap `projection_sources/espn.py` documents, and it has a regression
test below.

`is_live` is true when the week's `weekly_team_stats.result` is `U` (unplayed —
what ESPN reports mid-game) and any of the team's starters is inside a game
window. A synced row with a `W`/`L`/`T` result is final, not live.

With nothing synced, `empty_reason` is `"No {year} weeks synced"` and
`empty_action` is `("Sync from ESPN", "/settings")`.

An ESPN league's `footnote` carries **its own archived predecessor's** final
record, when one exists: the archive convention renames the old row to
`f"{league_id}-{year - 1}"`, so it is one lookup. This is why
`Pigskin Throne 2.0` reads "2025 finish: 10-4, 2237.8 pts" while
`Airframe Engine League`, which has no archived season, shows nothing. The
record belongs on the live league's card because that is the league the user
is still playing.

### `archive`

An archive league gets its own card **only when one of its teams is
`is_user_team`** — otherwise its history already appears as the footnote on its
successor, and a second card for the same league in two states is noise. Today
no archived team carries the flag, so no archive card renders; the branch exists
because `is_user_team` is set by hand and a user may set it.

When it does render: no current week, `points` and `opponent_points` are `None`,
`empty_reason` says the season is finished, and `footnote` carries the frozen
record. It is never the source of the page's `(year, week)`.

### Rosters

Every roster read is `roster_players(db, team, league)` and the result is passed
into `plan_lineup(db, team, year, week, now, league=league, players=...)`.

`plan_lineup`'s default player query reads `DBRosterSpot` only, so calling it on
an ESPN team without injecting players returns an empty `LineupPlan` — a team
with a full roster would show a projected total of 0.0 and no attention items.
This is the second regression test.

## Attention items

Built from the `LineupPlan` already constructed for each card's projection, so
it adds no queries. Ranked by a fixed order — the constant is the single
definition and the test asserts against it:

```python
ATTENTION_ORDER = (
    "no_lineup",        # critical: nothing set for this week
    "injury_excluded",  # critical: an OUT/IR starter is in the lineup
    "on_bye",           # critical: a starter's team is on bye
    "injury_haircut",   # warning: Questionable/Doubtful starter
    "bench_better",     # warning: a bench player out-projects a starter
    "lock_soon",        # info: a slot locks within LOCK_SOON_HOURS (3)
)
```

Within a kind, ties break on `(-projected_points, player_id)` — the same stable
key `plan_lineup` uses, so the ordering is reproducible.

Sources: `InjuryIndex(db, year, week)` for availability (**never**
`DBPlayer.injury_status`, which is undated and would surface year-old flags from
the archived season), `ScheduleIndex` for byes, `LockIndex` for deadlines.

`bench_better` compares against the lineup the user has actually saved
(`DBLineupSlot`), not against `plan_lineup`'s ideal — otherwise it fires on
every team that has not clicked auto-set, which is not news.

Every item's `url` points at the team page for that league kind
(`/season/{lid}/teams/{tid}` when the league is a season league, else
`/teams/{tid}`), with `?back=/`.

## Your players

Starters only, across every user team, deduplicated by `player_id` — one
`DBPlayer` row can be on an ESPN roster and a season roster at once, and showing
him twice is noise. When a player is on more than one team, `note` names the
count ("2 teams").

State and ordering:

| state | meaning | sort key |
|---|---|---|
| `playing` | kickoff passed, inside the game window | 0, `-live_points` |
| `concern` | injury verdict is not clean, or on bye | 1, `-projected` |
| `upcoming` | kickoff in the future | 2, `kickoff_at`, `-projected` |
| `final` | game finished | 3, `-live_points` |

Truncated to nine, with `players_total` rendering as `N more →` linking to
`/players?back=/`.

`players_yet_to_play` in `WeekContext` counts every starter in `upcoming`
across all teams, not just the nine shown.

## NFL slate

`DBNFLGame` rows for `(year, week)`, ordered by `kickoff_at`. `state` is derived
from the schedule, never from the score: `final` when both scores are non-null,
`in_progress` when `kickoff_at <= now < kickoff_at + GAME_WINDOW_HOURS` and the
scores are null, else `upcoming`.

`your_player_count` joins the deduplicated user-player set to each game by
`nfl_team` matching `home_team` or `away_team`, using
`utils/nfl_teams.py::normalize_team()` on both sides.

Games with no user players still render, dimmed. Hiding them would make the
slate stop being the slate.

## Liveness

`GET /api/dashboard/pulse` renders `dashboard/_pulse.html` — bands 1 and 2 only,
from the same `build_view` call the page uses.

The fragment renders its own trigger, so the polling lifecycle needs no
client-side scheduling:

```jinja
<div id="pulse" hx-get="/api/dashboard/pulse" hx-swap="outerHTML"
     {% if view.week.games_live %}hx-trigger="every 30s"{% endif %}>
```

Polling begins on the first response rendered after a kickoff and stops when the
last game leaves its window, because the replacement fragment simply omits the
attribute.

### Shared game-window predicate

`season_scheduler.py` already owns the definition of a game window
(`GAME_WINDOW_HOURS = 4`), but the predicate is inline inside `next_poll_at`.
Extract it:

```python
def in_game_window(now: datetime, kickoffs: Iterable[datetime]) -> bool:
    window = timedelta(hours=GAME_WINDOW_HOURS)
    return any(k <= now < k + window for k in kickoffs)
```

`next_poll_at` calls it, and so does `build_view`. Without this the dashboard
would carry a second, independently-drifting idea of "live".

Bands 3–5 load with `hx-trigger="load"` against their own fragment endpoints, so
a slow `hot_movers()` scan never delays the hero. Each fragment endpoint builds
only its own slice; `build_view` takes a `sections` argument naming which parts
to compute.

## Time

Every comparison uses `season_scheduler.league_now()`. `DBNFLGame.kickoff_at` is
a **naive US-Eastern wall clock**, so `datetime.utcnow()` runs 4–5 hours ahead
and would mark every Sunday 1:00 PM game as already in progress at 9:00 AM,
opening the polling window against the wrong hours and reporting players as
"playing" before kickoff.

## Navigation

The dashboard is reachable from `components/_sidebar.html`, so per the project
convention it gets **neither** a back link nor breadcrumbs.

Every outbound link carries `?back=/`, so Back from a player or team page
returns here rather than to an unrelated index. `tests/test_nav_conventions.py`
already scans template source for hand-rolled "Back" controls and must stay
green.

## Files

**New**

- `services/dashboard.py` — the view model and `build_view`.
- `api/routes/dashboard.py` — `/` (moved off `main.py`), plus
  `/api/dashboard/{pulse,attention,players,slate}`. Imported and
  `include_router`-ed in `main.py`.
- `templates/dashboard/_pulse.html`, `_attention.html`, `_players.html`,
  `_slate.html`, `_movers.html`.
- `tests/test_dashboard.py`.

**Changed**

- `templates/dashboard.html` — rewritten as the five-band shell.
- `api/main.py` — the `/` handler and its imports removed.
- `services/season_scheduler.py` — `in_game_window` extracted.

## Testing

Tests 1–4 and 7–8 build a `DashboardView` directly from a fixture session, with
no HTTP. Tests 5 and 6 are about rendering, so they go through `TestClient`.

1. **ESPN year scoping.** A team whose only `weekly_team_stats` row for week 1
   belongs to the archived 2025 league renders `empty_reason`, not that row's
   score.
2. **ESPN roster injection.** `build_view` on an ESPN team with players held via
   `DBPlayer.team_id` produces a non-zero projection and real attention items.
3. **Attention ordering.** A team constructed with one of every item kind
   returns them in `ATTENTION_ORDER`.
4. **`bench_better` compares against the saved lineup**, and does not fire on a
   team whose saved lineup is already optimal.
5. **Polling is off outside a window.** With `now` set to a Wednesday,
   `week.games_live` is False and the rendered fragment contains no
   `hx-trigger`.
6. **Page and fragment agree.** Both render the same score for the same
   `build_view` input.
7. **Player dedup.** A player on both an ESPN and a season roster appears once.
8. **`in_game_window` parity.** `next_poll_at` still returns the live cadence
   for the same inputs it did before the extraction.
9. **Archive footnote, not archive card.** A league with an archived
   predecessor whose teams are not `is_user_team` produces one card carrying the
   footnote, not two cards.
10. `tests/test_nav_conventions.py` passes unchanged.

Note the repo baseline: 12 pre-existing failures on `main`. Judge by the failure
set, not the count.

## Phasing

Three commits, each independently useful:

1. `services/dashboard.py` view model, `WeekContext` + `LeagueCard` for all
   three kinds, the route move, and the five-band shell rendering bands 1–2.
2. `AttentionItem`, `PlayerCell`, `SlateGame` and their fragments.
3. Movers band, `in_game_window` extraction, and polling.

## Deliberately out of scope

- **Win probability.** The Monte Carlo engine could produce a real one, but
  simulating every matchup on every page load is a different performance
  conversation, and a normal-approximation guess rendered as a percentage would
  be a confident wrong number. The projected margin is honest and free.
- **Any write action.** Auto-set and lineup edits stay on the team pages, which
  already route through `validate_lineup_result()`.
- **A configurable primary team.** Rejected during design in favour of equal
  weight across all four.
- **Hiding the demo league.** Would require a notion of "real league" the schema
  does not have.
