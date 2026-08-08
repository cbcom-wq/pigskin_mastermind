# Trustworthy player projections, end to end

**Date:** 2026-08-07
**Branch:** `feat/projection-accuracy`
**Status:** approved, ready for planning

## Problem

Pigskin Mastermind has a real projection engine — `ProjectionCriteriaBuilder` derives ~11 criteria
from stats, `ProjectionService` turns them into points, and two tuners sweep coefficients against
actuals. None of it reaches a user. Every consumer that ranks players — lineup optimizer, trade
analyzer, player list, roster pages, draft pool, power rankings — reads `DBPlayer.projected_points`.

That column is worse than sparse. It is **anti-correlated with player quality**:

| Player | `projected_points` |
|---|---|
| Josh Allen, Mahomes, Lamar, Bijan, Saquon, CeeDee Lamb | `0.0` |
| Philip Rivers (retired 2021) | `13.85` |
| Malik Willis | `19.6` |

284 of 1782 players carry a non-zero value, and the top of that list is backups and retirees.

The root cause is that **two writers put different units in one column**:

- `services/espn_sync.py:368,1355` writes `espn_player.projected_points` — ESPN's *current scoring
  period* value, i.e. per game, captured whenever the last sync ran.
- `services/adp_service.py:501` writes the ESPN board's `ratings["0"].totalRating` — a *season*
  scale value — but only via `_create_player_from_espn`, which fires only for players not already
  in the database. Every star already existed, so every star kept its `0.0`.

So improving the projection math changes nothing users see, and the column the app ranks on cannot
be repaired by weighting it differently.

## What is already built

Phases 0–2 of the original plan exist in the working tree, uncommitted:

- `services/projection_baseline.py` (417 lines) — shrinkage, leakage-free weekly baseline,
  positional priors, and an ADP→points log-linear curve.
- `DBNFLGame` plus migration `d4a1c6e9b2f7` (applied). `nfl_games` holds 2024 and 2025 complete
  with scores, and **all 272 games of 2026 scheduled and unplayed** — so opponents and byes are
  derivable for the season being drafted.
- Coefficient renames (`defense_rank_multiplier`, `efficiency_cap`), the injury sign fix, new
  `home_field_multiplier` / `shrinkage_games` / `efficiency_baseline`, and the parity guard test.

**It is red.** Commit `a6768ed` has 17 failures; the working tree has 48.

## Scope decisions

Three calls made during design, each departing from the original plan:

1. **Season-first.** It is August — pre-draft, off-season. The season path ships complete; weekly
   storage and blending are built but weekly *sportsbook* weighting waits for live odds.
2. **Season totals are canonical**, and `DBPlayer.projected_points` is retired as a ranking input.
   A mixed-unit column cannot be a safe fallback.
3. **ESPN keeps its 0.30 weight but changes source** — from the corrupted column to the board.

### Why sportsbook cannot carry 0.45 today

`sportsbook_odds` holds 816 rows covering **52 distinct players** from a **single slate on
2025-11-09**, last refreshed 2026-02-28. Markets are rec yds / receptions / rush / pass only — no K,
no DEF. It is one stale snapshot, not a feed. The weights stay in the code so refreshed odds
activate without a code change; they just have nothing to act on right now.

### Why ESPN is still a real source

`fetch_espn_adp` (`services/mock_draft.py:31`) already reads `ratings["0"].totalRating`. Live check
against the 2026 board returns genuine season totals:

```
Christian McCaffrey  RB  SF   416.6      Jahmyr Gibbs      RB  DET  366.9
Puka Nacua           WR  LAR  375.0      Ja'Marr Chase     WR  CIN  313.6
Bijan Robinson       RB  ATL  370.8      Saquon Barkley    RB  PHI  232.3
```

Those are plausible 0.5-PPR season totals. The data was always there; it just never reached a
column anything reads.

## Design

### Stage 1 — Stabilize

Make the in-flight work honest before building on it. Two independent causes:

**The 23 `NameError` failures** are one missing import. `projection_algorithm_tuner.py:164`
references `WeeklyProjectionService` with no import binding it.

**The behavior-change failures** are stale assertions. `test_builds_valid_criteria` expects
`21.875` and gets `16.845…` because shrinkage now pulls a short sample toward the positional prior.
The old number was a look-ahead artifact — the test was asserting the bug.

Re-baseline these as **property assertions**, not golden floats:

- shrinkage moves a 2-game player closer to the positional prior than a 15-game player with the
  same average
- a week-8 projection is byte-identical before and after weeks 9–18 are inserted (the leakage test)
- a future week with no game log still resolves an opponent and a `def_rank` other than the 16
  default

Keep a small number of golden values for anchoring. Asserting `16.845678306893255` re-freezes
whatever the code happens to do and teaches nothing.

**Verification bar.** Zero new failures against the 17 present at `a6768ed`, and these four files
fully green: `test_projection_tuner.py`, `test_projection_criteria_builder.py`,
`test_projection_algorithm_tuner.py`, `test_projection_service.py`. The pre-existing 17:

```
tests/integration/test_api_lineups.py::test_optimize_lineup
tests/integration/test_api_projection_tuner.py::test_projection_tuner_page_has_algorithm_section
tests/integration/test_api_projection_tuner.py::test_projection_tuner_page_links_history_to_run_detail
tests/integration/test_api_teams.py::test_get_teams_with_data
tests/integration/test_api_trades.py::test_analyze_trade
tests/test_mock_draft.py::{test_draft_home_page, test_draft_simulate_page,
                           test_start_draft_falls_back_to_previous_year, test_start_draft_with_espn_adp}
tests/test_nfl_data_service.py::TestGetPlayByPlay  (7 tests)
tests/unit/test_espn_sync.py::test_import_team_from_espn
```

The 31 to fix: `projection_algorithm_tuner` (23), `projection_criteria_builder` (4),
`test_api_projection_tuner` (3), `projection_tuner` (1).

**Out of scope at Stage 1, in scope at Stage 4.** Nine of the pre-existing 17 sit in code paths
Stage 4 rewrites — `test_mock_draft` (4), `test_api_lineups` (1), `test_api_trades` (1),
`test_api_teams` (1), and two `test_api_projection_tuner`. Deferring them past Stage 1 is correct;
declaring them permanently out of scope is not, because Stage 4 changes what those consumers read.
They get fixed as part of Stage 4, and the Stage 4 bar is that all nine pass. Only the eight
genuinely unrelated failures — `test_nfl_data_service::TestGetPlayByPlay` (7) and
`test_espn_sync::test_import_team_from_espn` (1) — stay out of scope for the whole change.

Commit Phases 0–2 as a checkpoint once green.

### Stage 2 — Storage and a real ESPN source

**`DBPlayerProjection`** (`models/database.py`, Alembic revision on head `d4a1c6e9b2f7`):

```
player_id FK, year, week (NULL = season), source,
projected_points, floor, ceiling, std_dev,
expected_games, components JSON, computed_at
UNIQUE (player_id, year, week, source)
```

`source` ∈ `blend` | `model` | `espn` | `sportsbook` | `adp`.

`expected_games` is a real column rather than a `components` key, because the draft pool needs an
exact per-game conversion and reaching into JSON for it invites the unit bug back.

**Units.** Season rows (`week IS NULL`) store season **totals**. Weekly rows store that week's
points. Per-game at season scope is `projected_points / expected_games`.

**`import_espn_projections(year)`** — new, in `services/adp_service.py` alongside the existing board
code. Reads the `fetch_espn_adp` board, resolves each entry through
`PlayerIdentityService.resolve()`, and writes `source='espn'` season rows. This is what makes ESPN's
0.30 weight mean something.

### Stage 3 — Blend and refresh

**`services/projection_blender.py`** — weighted consensus that renormalizes over whichever sources
are present, so a missing source redistributes rather than dragging the result toward zero. A
single available source returns exactly that source's value.

| Scope | Weights |
|---|---|
| Season | model 0.50, ESPN 0.30, ADP-implied 0.20 |
| Weekly | sportsbook 0.45, model 0.35, ESPN 0.20 |

Weekly resolves to model-only in practice today; the renormalization is what makes that correct
rather than a special case. Weights live in a module constant, overridable per call.

`SportsbookProjectionService.project_player()` moves from its `description ILIKE` scan
(`sportsbook_projection_service.py:172`) to `PlayerIdentityService.resolve()`.

**`services/projection_refresh.py`** — `refresh_season(year)` and `refresh_week(year, week)`. Batch
prime via `ProjectionCriteriaBuilder.ensure_players_stats()` before projecting; the criteria builder
issues dozens of queries per player, so batching is load-bearing, not an optimization.

Exposed as `pigskin projections refresh [--year] [--week]` (new CLI group) and a POST route.

### Stage 4 — Consumers

```python
get_projection(db, player_id, year, week=None) -> Optional[float]
```

Reads the `blend` row. Returns `None` when absent — **no fallback to `DBPlayer.projected_points`**.
Callers decide what absence means; ranking on a mixed-unit column is what this work exists to stop.

| Site | Scope |
|---|---|
| `api/routes/lineups.py:36`, `api/routes/trades.py:92,103` (`_db_team_to_domain`) | weekly |
| `services/adp_service.py::_pool_projection:675` | season |
| `api/routes/players.py:191,237`, `api/routes/teams.py:137` | season |
| `entertainment/__init__.py:92` (inherits via domain `Player`) | season |
| `templates/players/details.html`, `templates/teams/_weekly_lineup.html` | show blend + components |

**Risk — the draft pool scale change.** `_pool_projection`'s docstring warns that mixing per-game
and season values hands a player a ~17× advantage in the AI drafter's within-position
normalization. Moving the pool to season totals is safe only if *every* entry moves together. It is
a single call site. Guard it with a test asserting no pool entry falls under 20 points when the
board is present — a per-game leak would show up immediately as a ~16 next to a ~300.

### Stage 5 — Verification

Backtest 2025 three ways and report all three:

1. the old leaky number
2. the new model
3. a naive season-average baseline

Weekly MAE is expected to look **worse** than (1). Removing look-ahead leakage removes an unfair
advantage the old backtest had; the honest comparator is (3), not (1). Reporting only the flattering
pair would hide that.

End to end against the running app (port 8000 — not alongside Electron, two SQLite writers
deadlock):

1. `pigskin stats import-schedules --years 2025 2026`, then `pigskin projections refresh --year 2026`
2. `/players` — ranked by real projections, stars at the top
3. `/draft` — pool is season-scale and rookies are not ~0
4. `/lineups` — ordering differs from the ESPN-only baseline
5. `/projection-tuner` — defense-rank and efficiency-cap sliders move the number (they do not today)

## Non-goals

- **607 `Unknown`-position players** (all `nfl_` free agents) sit outside `FANTASY_POSITIONS` and
  will not be projected.
- **Un-mixing `DBPlayer.projected_points`.** Both writers stay as they are; the column simply stops
  being read for ranking. Fixing it properly means changing what ESPN sync stores, which is its own
  change with its own blast radius.
- **Master coefficients** stay at `~/.pigskin_mastermind/master_coefficients.json`. No file exists on
  this machine, so production runs on hard-coded defaults (PROJECT_STATUS.md #3).
- **Draft state** remains an in-memory singleton.
- **Live odds ingestion.** The weekly sportsbook weight is wired but dormant until odds are
  refreshed in-season.
