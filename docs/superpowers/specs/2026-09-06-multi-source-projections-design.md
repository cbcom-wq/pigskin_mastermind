# Multi-source weekly projections view

**Date:** 2026-09-06
**Status:** approved for implementation

## Problem

A roster page shows one projected number per player, and which number it is
depends on which button was pressed last. `/teams/{id}` has two buttons that
each overwrite the *same* "Projected" column — one runs the weekly model, one
runs sportsbook props — so the two can never be compared, and pressing the
second destroys the first. Neither works for an upcoming week: both endpoints
require a `DBWeeklyTeamStats` row, which only exists for weeks already synced.

There is also no rank anywhere. A projection of 12.4 is not actionable without
knowing whether that is WR8 or WR40 this week.

## Goal

On an ESPN-synced team page, show every rostered player's week-N projection
from six independent sources side by side, each with a global positional rank,
plus a consensus and a disagreement measure.

## Non-goals

- Season-league and archive rosters. ESPN teams only.
- Changing what `plan_lineup()` acts on. See "Blend isolation" below.
- A free-agent / all-players version of the table.
- Matchup-adjusted nflverse expected points.

## Sources

| key | origin | coverage |
|---|---|---|
| `model` | existing `WeeklyProjectionService` | draft pool + rostered (~1000) |
| `espn` | `weekly_player_stats.projected_points`, already written by `espn_sync` | rostered in synced leagues |
| `sportsbook` | existing `SportsbookProjectionService`, now persisted | players with props (~200-300) |
| `nflverse_xp` | new — recency-weighted expected points | skill positions with weekly data |
| `llm` | existing `source='llm'` rows, read-only | whatever an agent has run (sparse) |
| `consensus` | FantasyPros-style scrape, flag-gated | best effort |

Coverage differs by an order of magnitude between sources. This is surfaced,
not hidden: every rank is displayed with its denominator.

## Storage

Reuse `player_projections`. It already keys on
`(player_id, year, week, source)` and its docstring states the intent — keep
sources separate so the UI can show *why* they disagree.

### Blend isolation (load-bearing)

The consensus row is written as source **`blend_multi`**, not `blend`.

`_READ_PRIORITY = (BLEND_SOURCE, MODEL_SOURCE)` in `projection_refresh.py`
backs `weekly_projection_map()`, which `lineup_manager.plan_lineup()` calls at
line 116. Weekly `blend` rows do not exist today, so that read always falls
through to `model`. Writing a row named `blend` would therefore silently
switch the AI managers, the first-kickoff auto-fill, and the web auto-set
button onto the multi-source consensus — a lineup behavior change nobody
asked for, shipped as a side effect of a display feature.

`blend_multi` is not in `_READ_PRIORITY`, so the read path ignores it.
Adopting it for lineups later is a deliberate one-token edit plus tests.

### New table: `projection_source_runs`

One row per (source, year, week) refresh attempt.

    id, source, year, week, started_at, finished_at,
    status ('ok'|'error'|'skipped'), rows_written, error

This is the only place that knows whether a source is healthy. The header
freshness chip and the per-source tooltip both read it. Requires an Alembic
migration; `create_all()` at startup would mask a missing one on a fresh DB.

## Provider registry

New package `services/projection_sources/`:

    base.py               WeeklyProjectionProvider protocol, ProjectionValue
    registry.py           ordered providers, feature flags
    model_source.py
    espn_source.py
    sportsbook_source.py
    nflverse_xp_source.py
    llm_source.py
    consensus_source.py

Interface:

    class WeeklyProjectionProvider(Protocol):
        key: str
        label: str
        writes: bool          # llm_source is read-only
        def project_week(self, db, year, week, player_ids
                         ) -> dict[int, ProjectionValue]

`ProjectionValue` carries `points`, optional `floor`/`ceiling`, and a
`components` dict for the per-source explanation the table shows on hover.

A provider may cover a subset, or nothing. Returning `{}` is legal and is not
an error.

## Orchestrator

`services/weekly_projection_refresh.py::refresh_week_all(db, year, week,
sources=None)`.

Player universe: draft-pool players (as `_pool_players`) union every currently
rostered player, so ranks are global rather than roster-relative.

Each provider runs inside its own try/except and its own
`projection_source_runs` row. **A provider raising must not abort the others** —
with a scrape and an API-key-dependent source in the set, partial success is
the normal outcome, not an exception. After all providers, `blend_multi` is
computed from whatever landed.

## Blending

Extend `projection_blender.py` with `WEEKLY_MULTI_WEIGHTS`. The existing
`blend()` renormalizes over present sources, which is exactly the behavior
needed when four of six are missing for a given player. Starting weights:

    sportsbook   0.30
    model        0.25
    espn         0.20
    consensus    0.15
    nflverse_xp  0.10
    llm          0.00   (display only; recorded, never weighted)

`llm` is weighted zero deliberately: its coverage is a few dozen players, so a
nonzero weight would move the consensus for exactly those players and no
others, making them incomparable with the rest of the table.

## Ranking

`services/projection_rankings.py::weekly_source_table(db, player_ids, year,
week)` returns per player, per source: `points`, `rank`, `rank_of`.

Ranks are computed **at read time**, in Python, after one grouped query over
all stored rows for that `(year, week, source, position)` — never stored. A
materialized rank goes stale against its own number as soon as one provider
re-runs.

`rank_of` (the denominator) is always rendered. `WR7 of 214` from sportsbook
and `WR7 of 986` from the model are different claims; collapsing both to "WR7"
would be the most misleading thing this view could do.

## Web layer

- `GET /teams/{id}/projections?week=N` — HTMX fragment, tab on team detail.
- `GET /api/stats/teams/{id}/source-projections?week=N&year=Y` — JSON.
- `POST /api/projections/refresh-week` — manual refresh.
- `GET /api/projections/freshness` — header chip.

Table columns: player · pos · one per source (points + rank badge) ·
`blend_multi` · spread (max−min). Sortable client-side.

The two buttons at `teams/detail.html:361` and `:400` are removed; this view
replaces them.

**Roster resolution:** use the `DBWeeklyTeamStats` snapshot for that week when
it exists, else fall back to the team's current roster. Without the fallback
an upcoming week 404s, which is the defect that makes today's buttons useless
for the only week anyone needs to set a lineup for.

## Freshness and scheduling

`base.html` gets an HTMX-loaded chip in the existing top bar: "Projections
updated 3h ago", hovering shows per-source status, with a Refresh button.

Daily automatic refresh hooks into `season_scheduler.tick()` behind a last-run
guard read from `projection_source_runs`. Timing uses
`season_scheduler.league_now()` — `DBNFLGame.kickoff_at` is a naive US Eastern
wall clock, and `datetime.utcnow()` runs 4-5 hours ahead of it.

CLI: `pigskin projections refresh-week --year Y --week N [--sources a,b]`.

## nflverse expected points

Per-game expected fantasy points derived from `import_weekly_data` opportunity
columns (target share, air-yards share, WOPR, carries, red-zone touches),
scored through `get_scoring_settings()`, then a 4-week exponentially-weighted
average carried forward flat. No opponent adjustment.

Bye weeks are excluded via `ScheduleIndex.is_bye()`. An EWMA that averages in a
stored 0.0 bye drags the rate down for every player who has had one — the same
divisor trap already documented for `games_played`.

## Testing

- `projection_blender` weight renormalization with 6 sources, several absent.
- `projection_rankings`: rank and denominator per source and position, ties.
- Each provider against a stub session.
- Orchestrator: one provider raising still lands the other five, and records
  an `error` run row for the one that failed.
- `blend_multi` is NOT picked up by `weekly_projection_map()` — a regression
  test guarding the lineup isolation above.
- Freshness endpoint with zero, partial, and stale runs.

Run as `pytest tests/` (bare `pytest` fails collecting the vendored ESPN
tests). `main` has a known baseline of failing tests; judge by the failure
set, not the count.
