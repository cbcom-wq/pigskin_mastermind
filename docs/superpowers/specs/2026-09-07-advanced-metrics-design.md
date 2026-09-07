# Advanced metrics, trends, and a hot-movers page

**Date:** 2026-09-07
**Status:** approved for implementation

## Problem

The app stores raw counting stats and nothing about *opportunity*. There is no
way to see that a receiver's snap share has climbed for three weeks while his
scoring has not, which is the earliest reliable signal that a player is about to
be worth more than he currently looks.

The schema has `snap_count`, `snap_pct`, `air_yards`, `yac` and `wopr` columns
on `player_season_stats`, but every one of them is **zero** for 2025 — the
importer that fills them depends on `nfl_data_py.import_seasonal_data()`, which
now 404s. They are also season-level, so they could not show a trend anyway.

## Goal

1. Populate advanced usage and efficiency metrics per player per week.
2. Show them, with trends, on the player page.
3. Rank players league-wide whose **opportunity has moved ahead of (or behind)
   their production** — the buy-low / sell-high signal.

## Data availability (measured 2026-09-07)

`nfl_data_py` is at 0.3.3, the newest published version, so this is not a stale
install — nflverse renamed these releases and this library no longer resolves
them. The maintained successor is `nflreadpy`.

| Feed | 2022-24 | 2025 | 2026 |
|---|---|---|---|
| `import_weekly_data` (target share, WOPR, RACR, EPA) | OK | **404** | **404** |
| `import_seasonal_data` | OK | **404** | **404** |
| `import_pbp_data` (EPA, success rate) | OK | 0 rows | 0 rows |
| `import_snap_counts` | OK | OK (26,612) | OK |
| `import_ngs_data` (rec/rush/pass) | OK | OK (1402/648/605) | OK |

So target share cannot come from nflverse for the current season. It is derived
from our own game logs instead, which also removes a dependency on a broken
feed.

**Consequence for an existing source:** `nflverse_xp` ("Opportunity") calls
`import_weekly_data` for the current year and therefore cannot work this season.
It currently appears to pass only because it short-circuits at week 1. Out of
scope here, but it will surface as an `error` run row from week 2.

## Storage

```
player_advanced_metrics
    id, player_id, year, week, metric, value, source, updated_at
    UniqueConstraint(player_id, year, week, metric)
    Index(year, week, metric)     -- the league-wide hot scan
    Index(player_id, metric)      -- one player's trend
```

Long rather than wide. Four feeds today and `nflreadpy` likely later, with
deliberately uneven coverage — snaps cover every player, NGS covers a few
hundred qualifying ones. A long table absorbs a new metric as rows rather than
a migration, sparse coverage is natural instead of a wall of NULLs, and the hot
engine gets **one implementation for every metric** rather than a hand-kept list
of column names that rots the first time someone adds one.

The cost is untyped values and a pivot when rendering a player's table. Both are
contained.

**NGS `week=0` rows are season aggregates and are skipped.** Season figures are
computed from the stored weekly rows so there is a single definition, and so a
season row can never be mistaken for week zero of anything.

## Metric registry

In code (`services/advanced_metrics.py`), not the database. Each entry carries
key, label, unit, `higher_is_better`, applicable positions, source feed, and
whether it is **usage** or **production**. The trend and hot engines read only
the registry, so adding a metric is one entry plus an importer and touches no
analysis code.

| metric | kind | positions | source |
|---|---|---|---|
| `snap_pct` | usage | all | snap counts |
| `target_share` | usage | WR TE RB | derived |
| `rush_share` | usage | RB QB | derived |
| `touch_share` | usage | RB WR TE | derived |
| `ngs_separation` | usage | WR TE | NGS receiving |
| `ngs_cushion` | usage | WR TE | NGS receiving |
| `ngs_air_yards_share` | usage | WR TE | NGS receiving |
| `ngs_intended_air_yards` | usage | WR TE | NGS receiving |
| `ngs_catch_pct` | production | WR TE | NGS receiving |
| `ngs_ryoe` | production | RB | NGS rushing |
| `ngs_rush_efficiency` | production | RB | NGS rushing |
| `ngs_stacked_box_pct` | usage | RB | NGS rushing |
| `ngs_time_to_throw` | usage | QB | NGS passing |
| `ngs_aggressiveness` | usage | QB | NGS passing |
| `fantasy_points` | production | all | game logs |

`snap_pct` is stored **0-100**, matching the existing canonical scale, so the
importer scales nflverse's 0-1 `offense_pct`.

## Importers

`services/advanced_metrics_import.py`.

- **Snaps** match on `pfr_player_id` -> `DBPlayer.pfr_id` and carry their own
  `team` column.
- **NGS** matches on `gsis_id`, three feeds, week 0 dropped.
- **Share metrics** are computed from `DBPlayerGameLog`, summing targets and
  carries by team-week.

**The team for a share metric comes from the schedule, never from
`DBPlayer.nfl_team`.** Game logs carry an opponent but no team, and the player
column holds his *current* club — so a 2025 backfill computed from it would
misattribute every player who has since changed teams, and a share is only
meaningful against the right denominator. A team plays exactly one game a week,
so the opponent identifies one `DBNFLGame` row and the player is the other side
of it. Verified: 2025 week 5, `KC` appears in exactly one game (`KC @ JAX`).

All importers resolve ids through `NFLDataService._gsis_index()`-style
preference (real name over placeholder), for the reason documented in
`player_identity.py`.

## Trend and hot engine

`services/metric_trends.py`.

- `player_trend(db, player_id, year, metric)` -> the weekly series plus the
  count of weeks it actually covers.
- `hot_movers(db, year, week, window=3, baseline=3)` -> the league scan.

For each player and metric: the mean over the last `window` weeks against the
mean of the `baseline` weeks before it. Each delta is **z-scored against that
metric's own league distribution for the season**, because a 4-point snap-share
move and a 0.3-yard separation move are otherwise incomparable. `higher_is_better`
flips the sign so "improving" always means the same thing.

**Divergence = mean usage z-delta − production z-delta.** Positive means
opportunity has moved ahead of scoring (buy); negative the reverse. Ranked by
magnitude.

A player needs at least `window` recent and 1 baseline week to appear at all;
otherwise a single game masquerades as a trend.

## Views

- **`/players/{id}`** gains an Advanced section: per metric, a sparkline, the
  current value against the season average, and a league percentile.
- **`/metrics/hot`** — ranked movers with both deltas, a Buy/Sell verdict, and
  filters for position and metric.

Every trend renders **how many weeks it actually covers**. A metric's history
depends on which feed produced it and those differ by years, so a uniform
"last 3 weeks" label would be a claim the data does not support.

**Week 1 of 2026 has no games, so the hot page is empty for the current
season.** It is backfilled from 2025 and states plainly which season it is
reading, rather than rendering blank and looking broken.

## Plumbing

`pigskin stats import-advanced --years Y[,Y]`, plus a daily scheduler hook
beside the injury import. Failures there must not abort the projection refresh
that follows, matching the existing pattern.

## Testing

- Team-from-schedule resolution, including a player whose current team differs
  from the one he played for.
- Share denominators: a player's target share against the right team-week total.
- `snap_pct` stored 0-100, not 0-1.
- NGS week-0 rows are excluded.
- z-scoring makes two differently-scaled metrics comparable.
- `higher_is_better` inverts correctly (a falling `ngs_time_to_throw` is an
  improvement).
- A player with too little history does not appear in `hot_movers`.
- Divergence sign: usage up with production flat ranks as a buy.

Run as `pytest tests/`; the repo has a standing 12-failure baseline unrelated to
this work.
