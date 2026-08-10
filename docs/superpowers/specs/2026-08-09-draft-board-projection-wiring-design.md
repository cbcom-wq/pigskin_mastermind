# Wiring the projection model into the draft board

**Date:** 2026-08-09
**Branch:** `main` (branch before implementing)
**Status:** approved, ready for planning
**Parent spec:** [`2026-08-07-projection-accuracy-design.md`](2026-08-07-projection-accuracy-design.md)

## Relationship to the parent spec

This is **not a new design.** The 2026-08-07 spec already covers this work across five stages. That
design remains authoritative for units, weights, and non-goals; this document narrows it to the
slice that makes the draft board read real projections, and records what has changed on disk since
it was written.

| Parent stage | State on 2026-08-09 |
|---|---|
| 1 — Stabilize | **Done.** Suite is at exactly the 17 documented pre-existing failures (843 pass). |
| 2 — Storage + ESPN source | **Done.** `DBPlayerProjection` and `import_espn_projections()` exist. |
| 3 — Blend and refresh | **Half done.** `projection_blender.py` exists and is tested; `projection_refresh.py` does **not** exist. |
| 4 — Consumers | **Not started.** Every consumer still reads `DBPlayer.projected_points`. |
| 5 — Verification | Not started. |

**This spec delivers: the rest of Stage 3, plus the draft-board slice of Stage 4.** The weekly
consumers (lineups, trades, team pages) stay for a later slice.

## Problem

An independent review on 2026-08-09 ([`docs/PROJECTION_SYSTEM_REVIEW.md`](../../PROJECTION_SYSTEM_REVIEW.md))
measured the live database and reached the parent spec's conclusions from the other direction:

- `player_projections` holds **0 rows**. The table exists; nothing writes it.
- `projection_blender.blend()` has **zero production callers** — imported only by its own test.
- `get_adp_for_draft_pool(year=2026)` returns 1013 players, **50.6% with `projected_points == 0`**,
  including James Cook III at ADP 11.1.
- The non-zero half are not projections. They come from `_pool_projection`'s fallback: last
  season's *per-game* average. Because that unit favors quarterbacks, the pool's top five by
  "projection" are all QBs, with McCaffrey (ADP 5.8) sixth.

So the draft board ranks on a mixture of a retired-quarterback column and a backward-looking
per-game average, and the model that exists to replace both never runs.

## Design

### 1. `services/projection_refresh.py` (new)

```python
class ProjectionRefreshService:
    def __init__(self, db: Session): ...
    def refresh_season(self, year: int, limit: int | None = None) -> dict: ...
```

Returns `{"model": n, "espn": n, "blend": n, "skipped": n, "year": year}`.

For every player holding a draft-pool ADP row for `year`:

1. `ProjectionCriteriaBuilder.build_yearly_criteria(player_id, year)`
2. `YearlyProjectionService(get_effective_coefficients()).calculate_season_projection()`
3. Upsert `source="model"`, `week=None`, `projected_points=<season total>`, `expected_games`
   in its own column, criteria snapshot in `components`
4. Read existing `source="espn"` rows
5. `blend({"model": …, "espn": …}, SEASON_WEIGHTS)` → upsert `source="blend"`

**ADP is deliberately not passed as a third blend source.** The parent spec lists season weights as
model 0.50 / ESPN 0.30 / ADP 0.20, but `ProjectionBaselines.season_baseline()` already folds
ADP-implied points into the model's own baseline for players without history. Feeding ADP again at
the blend would double-count the market for exactly the players whose projection is *most* market-
derived. Renormalization means omitting it yields model 0.625 / ESPN 0.375, which is the intended
relative weighting of the two independent opinions. **This is a departure from the parent spec and
is the one design decision here that it does not already sanction.**

Batch-prime with `ensure_players_stats()` before the loop — the criteria builder issues dozens of
queries per player, so batching is load-bearing, not an optimization. Measured cost: **117 ms per
player, ~119 s for a 1013-player pool.**

Upserts key on `(player_id, year, week=NULL, source)` and must respect the partial unique index
`uq_player_projection_season`, since SQL treats `NULL` as distinct from `NULL` and the table-level
constraint never fires for season rows.

### 2. `get_projection()` read helper

Per the parent spec, in `services/projection_refresh.py`:

```python
def get_projection(db, player_id, year, week=None) -> Optional[float]
```

Reads the `blend` row, falls back to `model`, returns `None` when neither exists. **No fallback to
`DBPlayer.projected_points`** — ranking on a mixed-unit column is what this work exists to stop.

### 3. `adp_service.get_adp_for_draft_pool` rewiring

`_pool_projection` currently runs one query per player inside a list comprehension. Replace it with
a single preloaded `{player_id: points}` map built once per call, resolved in order:

**`blend` row → `model` row → last season's `fantasy_points_total` → 0.0**

Two changes inside that chain:

- **`DBPlayer.projected_points` leaves the draft path entirely.** It is currently consulted *first*;
  it will not be consulted at all.
- **The fallback moves from `fantasy_points_avg` to `fantasy_points_total`**, so every value on this
  path is a season total. The existing `games_played >= 4` filter still screens out the ESPN-sourced
  rows that record a season total against `games_played=1`.

The fallback stays rather than returning 0: without it, a database where the refresh has not yet run
would produce an all-zero pool, which is worse than today's behavior.

### 4. Trigger

- **`pigskin projections refresh [--year] [--limit]`** — new CLI group in `cli.py`.
- **A call at the end of `refresh_draft_data()`**, after `canonicalize_stored_teams()`. Order is
  load-bearing: projections read the ADP rows that FFC and the ESPN tail write, so they must run
  last. Counts are merged into the returned dict so the ~2-minute cost is not silent.

## Units

Season rows store season **totals** (parent spec, Stage 2). A top RB reads ~330, not ~19.5.

The unit switch was checked against every draft consumer:

| Consumer | Effect |
|---|---|
| `mock_draft._proj_score` | None — normalizes within position, unit-invariant. |
| `mock_draft.grade_draft` | None — the letter grade is computed in ADP rank-space (value 50% / balance 30% / tier 20%), and reads no projection. |
| `mock_draft` strategy summary, `draft_recap` position + starter totals | Values change scale. Cross-position sums of per-game rates were not a meaningful quantity; sums of season totals are. |
| `draft_value.stamp_pool_ranks` | None — projections are only an ordinal fallback sort when no ADP exists. |

**Risk — a partial unit migration.** The parent spec's warning applies: mixing per-game and season
values hands a player a ~17× advantage in the within-position normalization. Mitigated by a guard
test asserting no pool entry falls between 0 and 20 points when the board is present — a per-game
leak surfaces immediately as a ~16 beside a ~300.

## Testing

New `tests/test_projection_refresh_service.py`, using the in-memory fixtures already in
`tests/test_projection_refresh.py`:

- a model row is written at season scale with `expected_games` populated
- blend equals model exactly when no ESPN row exists (renormalization, not a special case)
- blend sits between the two when an ESPN row exists
- re-running `refresh_season` twice updates rather than duplicating (partial-index idempotency)
- `get_projection` prefers blend over model and returns `None` when neither exists

Extending `tests/test_mock_draft.py`:

- pool prefers blend > model > season-total fallback
- **unit guard**: no pool entry falls in `(0, 20)` when projections are present
- `DBPlayer.projected_points` set to a per-game value is *not* read by the pool

## Verification

1. `pytest tests/` — no new failures against the documented 17.
2. `pytest tests/test_mock_draft.py` alone — stays 90/90.
3. `pigskin projections refresh --year 2026`, then confirm `player_projections` is non-empty with
   all three sources.
4. Re-run the pool: zero-rate well under 50.6%, stars at the top, McCaffrey above Mahomes.
5. `/draft` in the running app on port 8000 (**not** alongside Electron — two SQLite writers
   deadlock).

## Non-goals

Inherited from the parent spec: un-mixing `DBPlayer.projected_points` (both writers stay; the column
simply stops being read for ranking), the 607 `Unknown`-position players, master coefficients, the
in-memory draft singleton, and live odds ingestion.

Specific to this slice:

- **Weekly consumers.** `api/routes/lineups.py`, `trades.py`, and `teams.py` keep reading
  `DBPlayer.projected_points`. Their three integration failures
  (`test_optimize_lineup`, `test_analyze_trade`, `test_get_teams_with_data`) reproduce in isolation
  and belong to the weekly slice of Stage 4.
- **The model's over-prediction.** The review measured the adjustment layer making projections worse
  than their own baseline (MAE 5.16 vs 4.50), driven by an uncentered `touch_multiplier`. Fixing the
  formula is a separate change; wiring a flawed model in is still strictly better than ranking on
  retired quarterbacks, and wiring it in is what makes the flaw visible to tuning.
- **The 4 `test_mock_draft` full-run failures.** They pass 90/90 in isolation and fail only in the
  full suite — pre-existing cross-test pollution, most likely the module-level `draft_engine`
  singleton. Not caused by and not fixed by this change.
