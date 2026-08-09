# Post-Draft Team Recap

Design spec — 2026-08-09

## Problem

A finished mock draft ends in a single modal (`static/js/draft-board.js::showDraftComplete`). It
shows a letter grade, a player count, a projected total, position pills, up to five
strengths/weaknesses, and a pick-by-pick list. Everything in it comes from the in-memory draft
state, so it never touches a stat the app already has.

The database holds far more: 2025 season lines and weekly game logs for most of the draft pool,
full ADP spread (stdev, earliest/latest pick, sample size), headshots, and bye weeks. None of it
reaches the user at the moment they most want to look at their team.

**Goal:** a dedicated recap page that makes finishing a draft feel like an event and gives the user
somewhere to dig.

## Scope

In scope: a new page at `/draft/recap/{draft_id}`, a service that assembles its payload, a template,
and a small JS module for the reveal animation and on-demand simulation.

Out of scope: persisting drafts (the recap lives as long as the server process, same as the board
does today), a past-drafts index, sharing/export, and any change to how drafts are run or graded.

## Data availability

Verified against the local `pigskin_mastermind.db` on 2026-08-09. The realistic draft pool is the
FFC top 250 for 2026.

| Field | Coverage (FFC top 250) | Consequence |
|---|---|---|
| `headshot_url` | 928 / 1013 pool-wide | Cards need an initials fallback |
| `bye_week` | 247 / 250 | Bye grid is effectively complete |
| 2025 `DBPlayerSeasonStats` | 709 / 1013 pool-wide | Stat lines available for drafted players |
| 2025 `DBPlayerGameLog` | 212 / 250 | Sparkline + distribution for most; rookies and most K/DEF have none |
| `adp_stdev`, `adp_high`, `adp_low`, `adp_times_drafted` | 250 / 250 | Full ADP-spread insight available |
| `injury_status` | **0 / 1782 rows app-wide** | Injury flags dropped from the design |
| `snap_pct`, `wopr`, `air_yards` | null in 2025 | Cannot show snap share or WOPR |
| `DBPlayerProjection` | **table empty** | Floor/ceiling cannot come from stored projections |

Two consequences drive the design:

1. **Floor/ceiling is derived from 2025 weekly actuals**, not from stored projections. This is a
   single indexed query and is honest about what it is.
2. **Monte Carlo is on demand only.** `MonteCarloInputBuilder.build_for_player` plus
   `FantasySimulationEngine.simulate` measured **2.6–3.7s per player** (it builds full weekly
   criteria and lazily populates team stat rows). A 15-player roster would block a page load for
   ~45s. One player at a time behind a button is fine.

## Architecture

```
draft_engine.get_draft(id)              in-memory state; roster dicts carry db_id
        |
        v
DraftRecapService(db).build(state)
        |-- bulk query 1: DBPlayer, WHERE id IN (db_ids)
        |-- bulk query 2: DBPlayerSeasonStats, WHERE player_id IN (db_ids)
        |                 AND year IN (current, current - 1)
        |                 current-1 row -> stat line; current row -> ADP spread + adp_source
        |-- bulk query 3: DBPlayerGameLog weekly points,
        |                 WHERE player_id IN (db_ids) AND year = current - 1
        |-- pure python: lineup fill, league ranks, value analysis, bye grid
        v
    DraftRecap  -->  templates/draft/recap.html  -->  GET /draft/recap/{draft_id}
                              |
                              +-- static/js/draft-recap.js
                                    - hero reveal animation
                                    - POST /draft/recap/{draft_id}/simulate/{db_id}
```

Three queries total, all keyed on the `db_id` already present in every roster dict. Page cost is
fixed regardless of roster size.

Seasons are resolved through `utils/season.py::current_fantasy_season()` rather than hardcoded, so
the "last season" stat line rolls forward on its own. The draft pool dicts from
`ADPService.get_adp_for_draft_pool()` carry `adp_rank` and `adp_stdev` but **not** `adp_source`,
`adp_high`, `adp_low`, or `adp_times_drafted` — those come from the current-season
`DBPlayerSeasonStats` row in query 2, which is also what identifies a tail-sourced ADP.

### Why a new service module

`services/mock_draft.py` (1,265 lines) has **zero database imports** — it is a pure in-memory engine
with three test files (`test_mock_draft.py`, `test_draft_realism.py`, `test_draft_bench_slots.py`).
Adding DB enrichment to `grade_draft()` would drag a `Session` into that module and require a
database for every existing engine test. `services/draft_recap.py` keeps the boundary and is
testable against a fixture state dict without running a draft.

### Why not reuse LineupOptimizer

`services/decision_tools.py::LineupOptimizer.optimize_lineup` takes a `Team` dataclass and iterates
`lineup_rules` with a special case for `FLEX` only. The draft's `lineup_slots` can contain
`SUPERFLEX` and always contains `BENCH`; both would be treated as literal position names and match
no players, silently producing a wrong lineup for superflex drafts. The recap service implements a
local `fill_lineup()` (~25 lines) that handles `FLEX` (RB/WR/TE), `SUPERFLEX` (QB/RB/WR/TE), and
ignores `BENCH`, following the documented domain rule: required slots first, then flex slots with
the best remaining eligible player by projected points.

## Components

### `services/draft_recap.py`

`DraftRecapService(db: Session)` with one public method, `build(state: dict) -> DraftRecap`.

`DraftRecap` is a dataclass holding the sections below. Every derived number is computed here, not
in the template.

Helper functions, each independently testable:

- `fill_lineup(roster, lineup_slots) -> (starters, bench)`
- `weekly_distribution(points: list[float]) -> dict` — p10 floor, median, p90 ceiling, stdev, boom
  rate, bust rate, best and worst game
- `position_group_ranks(rosters, user_slot, lineup_slots) -> dict`
- `passed_on(picks_log, final_available, user_slot) -> list`
- `bye_grid(starters) -> dict`

**Boom/bust thresholds are position-relative.** The existing `MonteCarloResult` uses boom > 25 and
bust < 8, which are flex-player thresholds; applied to a kicker every week is a bust. Thresholds:

| Position | Boom | Bust |
|---|---|---|
| QB | > 25 | < 14 |
| RB, WR, TE | > 20 | < 8 |
| K | > 12 | < 5 |
| DEF | > 12 | < 3 |

### `api/routes/draft.py` additions

- `GET /draft/recap/{draft_id}` — renders `draft/recap.html`. Returns 404 with a friendly page when
  the draft is unknown; redirects to `/draft/board/{draft_id}` when status is not `complete`.
- `POST /draft/recap/{draft_id}/simulate/{db_id}` — runs `MonteCarloInputBuilder` +
  `FantasySimulationEngine` for one player, returns mean/median/floor/ceiling/boom/bust as JSON.
  Validates that `db_id` is actually on the user's roster in that draft.

### `templates/draft/recap.html`

Server-rendered. Sections in order: hero, roster & lineup, player cards, draft story, risk &
outlook.

### `static/js/draft-recap.js`

Handles only what needs a client: the hero reveal (grade fade-in, composite count-up, headshot
fan), headshot `onerror` fallback, and the per-player simulate button. Everything else is already in
the HTML.

### `draft-board.js` change

The existing completion modal keeps its celebratory role but loses the pick-by-pick list and
strengths/weaknesses block. It gains a primary **"See Your Team"** button linking to
`/draft/recap/{draft_id}`. Net reduction in that file.

## Page sections

### Hero

Dark, field-lit banner. Grade letter animates in; composite counts up. Headshots of the first five
or six picks fan across with team logos. Three headline stats: total projected points, rank among
the drafting teams by total projected points ("2nd of 10"), and best value pick — the user's pick
with the largest positive `pick_number - adp_rank`. One generated headline sentence assembled from
the computed facts, for example *"Three steals and the top RB corps in the league."*

Team logos come from `https://a.espncdn.com/i/teamlogos/nfl/500/{abbr}.png` with a lowercase
abbreviation map — ESPN uses `wsh` where `utils/nfl_teams.py::normalize_team` produces `WAS`.
Consistent with the app already loading Tailwind, HTMX, and headshots from CDNs.

### A. Roster & starting lineup

Optimal starters from the draft's own `lineup_slots`, then bench, with the starters' projected
weekly total. Position-group ranks against the other rosters in the draft, stated with the margin:
*"RB corps — 1st of 10, +18.4 pts over the field."*

Ranks are computed per base position (QB/RB/WR/TE/K/DEF), not per lineup slot. For each position,
every team's roster is scored as the sum of its top *N* players at that position by projected
points, where *N* is that position's required starter count in `lineup_slots`; teams are then ranked
on that sum. FLEX and SUPERFLEX slots do not get their own rank — a flex player is already counted
in their base position. "Over the field" is the margin against the mean of the other teams.

### B. Player cards

Grid of cards. Each carries headshot, team logo, team-color accent, position badge, and the round
and pick where the player was taken.

- **Where you took him vs where he goes** — pick number against `adp_high`–`adp_low` across
  `adp_times_drafted` real drafts.
- **2025 stat line, keyed to position** — QB: yards/TD/INT plus rushing; RB: attempts/yards/TD/
  receptions; WR/TE: targets/receptions/yards/TD; K/DEF: games and PPG.
- **Sparkline** of 2025 weekly fantasy points.
- **Floor / median / ceiling bar** from `weekly_distribution`, with boom and bust rates.
- **Simulate button** — runs the real Monte Carlo for that player, roughly 3 seconds.
- Links to `/players/{db_id}` and `/players/{db_id}/simulation`, both of which already exist.

### C. Draft story

- **Value timeline** — the user's picks plotted as pick number against ADP, delta shaded green for
  value and red for reach.
- **Best value and biggest reach** called out with the player and the margin.
- **Positional runs** — for each pick, how many players at that position went in the five picks
  before it.
- **Who you passed on** — at each of the user's picks, the best available player by ADP who was gone
  by their next pick. Reconstructed as `final_available_players + everyone picked after pick k`,
  which recovers the available set at any point without storing per-pick snapshots.

All of the above rests on `adp_rank`. For players sourced from `adp_source="espn_tail"` that value
is synthetic (`max_ffc_adp + rank`) — a sort key, not a real draft position — so a value verdict
built on it is meaningless. Tail-sourced players are shown on the timeline without a value verdict
and are excluded from the steal/reach counts and from the best-value and biggest-reach callouts.
In a typical 10-team, 15-round draft all 150 picks fall inside FFC's 250, so this rarely triggers;
deeper leagues and longer drafts reach into the tail.

### D. Risk & outlook

- **Bye-week grid**, weeks 5–14, showing how many starters are idle each week and flagging any week
  where a required lineup slot cannot be filled from the roster.
- **Starters floor/ceiling roll-up** — the sum of each starter's p10 and of each starter's p90,
  labeled with its coverage ("based on 7 of 9 starters"). Bench players are excluded; they do not
  score. Summing percentiles overstates the spread of the true roster distribution, so the section
  presents it as a range of outcomes, not a probability.
- **Consistency ranking** of the whole roster by 2025 weekly variance, steadiest first.

Injury flags are deliberately absent: `injury_status` is null for every player in the database, so
the badge would never render.

## Failure modes

Every case renders something rather than raising.

| Condition | Behavior |
|---|---|
| Draft id unknown (server restarted) | 404 page: "This draft is no longer available", link to `/draft` |
| Draft status is not `complete` | Redirect to `/draft/board/{draft_id}` |
| Player has no `db_id` (ESPN-ADP pool, name-matched only) | Card renders from draft-state fields; stats panel reads "no local data" |
| No 2025 game logs (rookies, most K/DEF) | Card shows "Rookie / no 2025 data"; excluded from roll-ups; coverage count adjusts |
| Missing bye week | Player omitted from the bye grid with a footnote; grid still renders |
| Headshot or logo 404s at ESPN | `onerror` swaps to a position-colored initials tile |
| Monte Carlo raises or times out | Button returns to idle with "couldn't simulate"; game-log numbers stay on screen |

## Testing

`tests/test_draft_recap.py`, driving `DraftRecapService` with a hand-built state dict and an
in-memory database. No draft run and no browser required.

- `fill_lineup` respects FLEX eligibility (RB/WR/TE only — never QB, K, or DEF)
- `fill_lineup` handles SUPERFLEX and ignores BENCH
- `fill_lineup` fills required slots before flex slots
- `position_group_ranks` matches a known ten-roster fixture
- `weekly_distribution` returns correct p10/median/p90/stdev for a known array
- boom and bust rates use position-relative thresholds
- `passed_on` returns the expected player for a known `picks_log`
- `bye_grid` counts starters only, not bench
- coverage count is correct when a player has no game logs
- players without `db_id` do not raise
- a player whose current-season `adp_source` is `espn_tail` gets no value verdict and is excluded
  from steal/reach counts
- the stat line reads the prior-season row while ADP spread reads the current-season row

Route tests:

- unknown draft id returns 404
- in-progress draft redirects to the board
- completed draft renders 200
- simulate endpoint rejects a `db_id` not on the user's roster

## Files

| File | Change |
|---|---|
| `src/pigskin_mastermind/services/draft_recap.py` | new |
| `src/pigskin_mastermind/api/routes/draft.py` | add two routes |
| `src/pigskin_mastermind/templates/draft/recap.html` | new |
| `src/pigskin_mastermind/static/js/draft-recap.js` | new |
| `src/pigskin_mastermind/static/js/draft-board.js` | trim completion modal, add link |
| `tests/test_draft_recap.py` | new |

No database migration. No new dependency.
