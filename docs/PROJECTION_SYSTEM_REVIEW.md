# The Player Projection System: Design vs. Reality

A review of how Pigskin Mastermind's projection system is designed to work, and how it
behaves against the live `pigskin_mastermind.db` as of 2026-08-09.

Part 1 describes the intended design, reconstructed from the code and its docstrings.
Part 2 reports what actually happens when that code runs, with the evidence for each claim.

---

# Part 1 — How it is meant to function

## 1.1 The shape of the design

The system is built as a **four-stage pipeline** feeding a **consensus blend**, with a
**tuning loop** wrapped around the model stage.

```
                    ┌── ProjectionBaselines ──┐
DB stats ──────────►│  shrunk, leakage-free   │──► historical_average_points
(game logs,         │  points-per-game anchor │
 season stats, ADP) └─────────────────────────┘
                                 │
                                 ▼
                    ProjectionCriteriaBuilder
                    (skill, touch share, momentum,
                     opponent defense, injury, age…)
                                 │
                                 ▼
                {Weekly,Yearly}ProjectionCriteria
                                 │
     AlgorithmCoefficients ──────┤   ◄── tuned per position
     (via PositionCoefficients)  │
                                 ▼
                        ProjectionService
                    (_apply_base_criteria + layer)
                                 │
                                 ▼
                          "model" projection
                                 │
                                 ▼
   ┌─────────────────────────────────────────────────┐
   │  projection_blender.blend()                     │
   │  season: model .50 / espn .30 / adp .20         │
   │  weekly: sportsbook .45 / model .35 / espn .20  │
   └─────────────────────────────────────────────────┘
                                 │
                                 ▼
                     DBPlayerProjection rows
              (source, units, components, expected_games)
                                 │
                                 ▼
        draft board · trades · lineups · player pages
```

## 1.2 Stage 1 — The baseline (`services/projection_baseline.py`)

Every projection starts from a points-per-game anchor. This module exists because
`baseline_weight` is the highest-leverage coefficient in the formula, so the quality of the
number it scales matters more than any adjustment. Its own docstring names three problems it
was written to fix:

- **Look-ahead leakage.** The old weekly baseline used a full-season average that included
  the week being projected, which flatters any backtest. `weekly_baseline()` filters to
  `week < target_week`, so a week-8 call returns the same answer whether or not weeks 8–18
  have been played.
- **No shrinkage.** `shrink()` applies the empirical-Bayes form `(n·observed + k·prior) / (n + k)`,
  so a player with two games doesn't count like a player with fifteen. `k` is `shrinkage_games`
  (default 4).
- **No prior for players without history.** Rookies anchored at 0.0. Now the prior chain is:
  the player's own prior-season rate → the positional median → and for players with no history
  at all, an **ADP-implied** value from a `ppg = a + b·ln(adp)` least-squares fit on last
  season's actuals.

Supporting choices: recency is weighted exponentially (4-game half-life) rather than linearly,
so the same game counts the same in week 5 and week 15; the positional prior is the **median**,
not the mean, because fantasy scoring has a long right tail; the ADP curve is rejected if it
slopes upward, since that means the sample is junk.

## 1.3 Stage 2 — The criteria (`services/projection_criteria_builder.py`)

~1,800 lines deriving the model's inputs from game logs and season stats, and lazily populating
missing team/defense stat rows as a side effect. It produces one of two dataclasses from
`models/projection_criteria.py`, which validate their own ranges:

**Shared base** — `player_skill_level` (0–100), `team_offense_level` (0–100),
`opponent_defense_level` (0–100), `positional_touch_percentage` (0–100),
`recent_trend_score` (−100–100), `historical_average_points` (per game),
`fantasy_points_per_touch`, `injury_risk_score` (0–100).

**Weekly adds** — `opposing_defense_vs_position_rank` (1–32), `offensive_momentum_score`,
`weather_impact_score`, `home_field` (+1/−1/0), and `is_available`.

**Yearly adds** — `age_deviation_from_optimum` (−10–10), `coaching_stability_score` (0–100),
and `expected_games` (0–17).

Two deliberate design notes are worth carrying forward:

- `is_available=False` **short-circuits the entire formula to 0.0**. A bye, IR, or OUT is a fact
  about the week, not a risk to discount, so it is kept out of `injury_risk_score`.
- `home_field=0.0` means *"no schedule information"*, not *"neutral site"*.

## 1.4 Stage 3 — The formula (`services/projection_service.py`)

`_apply_base_criteria` is an additive model over the baseline:

```
base   = historical_average_points × baseline_weight
       + (player_skill_level    − 50) × skill_multiplier
       + (team_offense_level    − 50) × offense_multiplier
       + (opponent_defense_level− 50) × defense_multiplier      [yearly only]
       +  positional_touch_percentage × touch_multiplier
       +  recent_trend_score          × trend_multiplier
       + clamp((fpts_per_touch − efficiency_baseline) × efficiency_multiplier, ±efficiency_cap)
       +  injury_risk_score           × injury_multiplier       [negative]
result = max(0, base)
```

`YearlyProjectionService` then adds an age term (asymmetric — different multipliers before and
after peak) and a coaching-stability term. `WeeklyProjectionService` adds defense rank,
momentum, weather, and home field.

Two intended subtleties:

- **The defense term is deliberately skipped for weekly.** For weekly criteria,
  `opponent_defense_level` is a linear restatement of `opposing_defense_vs_position_rank`, which
  the weekly layer already scores. Applying both double-counted one matchup and made the two
  multipliers fight each other during tuning. For yearly the field means *strength of schedule*,
  a genuinely separate signal, so it stays.
- **`calculate_projection` returns a per-game rate.** `calculate_season_projection` multiplies by
  `expected_games` to reach a season total. These are kept separate on purpose: the tuner
  backtests the per-game rate, so changing the units of `calculate_projection` would silently
  invalidate every stored tuning run.

## 1.5 Stage 4 — Coefficients and tuning

`AlgorithmCoefficients` holds every tunable multiplier; `PositionCoefficients` wraps a `default`
set plus per-position overrides for QB/RB/WR/TE/K/DEF. Two tuners with different jobs:

- **`ProjectionTunerService`** — one projection, one coefficient set, returns a per-criteria
  contribution breakdown. Backs the interactive `/projection-tuner` page.
- **`ProjectionAlgorithmTuner`** — sweeps many variations (globally or per position), scores each
  against actual game logs by MAE/RMSE, and persists runs to `~/.pigskin_mastermind/tuning_results/`.

Winners are promoted via `master_coefficients.save_master_coefficients()` to
`~/.pigskin_mastermind/master_coefficients.json`. **`get_effective_coefficients()` is the single
call production code is supposed to use** — it returns the promoted set, or the hard-coded
defaults when nothing has been promoted.

## 1.6 The consensus layer (`services/projection_blender.py`)

The model is intended to be *one opinion among several*, not the answer:

| Scope  | Weights |
|--------|---------|
| Season | model 0.50 · espn 0.30 · adp 0.20 |
| Weekly | sportsbook 0.45 · model 0.35 · espn 0.20 |

Weights are **renormalized over sources actually present**. The docstring is explicit about why:
multiplying present sources by nominal weights and summing would treat a missing source as a
zero-point vote, so a player two sources both call 300 would blend to 200 purely because a third
was silent. A source present with value `0.0` *is* a real vote — that's how a bye reaches the
blend — so only `None` counts as absent. Weekly gives the betting market the lead because it is
the sharpest short-term signal available.

## 1.7 The storage contract (`DBPlayerProjection`)

The design's most important decision, stated plainly in the model's docstring:
**`DBPlayer.projected_points` cannot hold a projection.** Two importers write that column in two
different units — `espn_sync` stores a per-game scoring-period value, `adp_service` stores the
board's season-scale `totalRating` — so *"the column ranks Philip Rivers above Josh Allen."*

`player_projections` replaces it, keyed `(player_id, year, week, source)`:

- `week = NULL` is the season scope and stores season **totals**; weekly rows store that week's points.
- A partial unique index enforces one season row per player per source (SQL treats `NULL` as
  distinct from `NULL`, so the table-level constraint never fires for season rows).
- `source` ∈ `blend | model | espn | sportsbook | adp` — each kept separate so the UI can show
  *why* two sources disagree instead of hiding it behind one number.
- `expected_games` is a real column, not a `components` key, because *"the draft pool converts
  season totals to a per-game rate and reaching into JSON for the divisor is how the mixed-unit
  bug comes back."*

---

# Part 2 — How it is actually functioning

The design above is coherent and well-reasoned. Most of it is not running.

## 2.1 Headline: the model is disconnected from the app

**`player_projections` is empty — 0 rows.**

```
tables: alembic_version, leagues, nfl_games, nfl_team_stats, player_game_logs,
        player_projections, player_season_stats, players, sportsbook_odds,
        teams, weekly_player_stats, weekly_team_stats

sqlite> select count(*) from player_projections;   →  0
```

The table was created and migrated, but nothing has ever written to it. Tracing further:

- **Only one function in the codebase writes `DBPlayerProjection` at all**:
  `adp_service.import_espn_projections()`, which writes `source="espn"`. It has never been run
  against this database.
- **Nothing writes `source="model"`, `"blend"`, or `"sportsbook"`.** There is no service, route,
  CLI command, or scheduled job that persists a model projection.
- **`projection_blender.py` has zero production callers.** The consensus weights in §1.6 are
  imported by `tests/test_projection_blender.py` and nowhere else. The blend is fully implemented,
  fully tested, and never invoked.

The house model is reachable from exactly three places, all read-only and on-demand:

| Entry point | Consumer |
|---|---|
| `GET /api/stats/players/{id}/auto-projection` | player detail page |
| `GET /api/stats/teams/{id}/weekly-projections` | team detail page (`teams/detail.html:359`) |
| `/projection-tuner` page | manual tuning UI |

The CLI never calls it. Nothing caches or stores the result.

## 2.2 What the app actually ranks players by

Every user-facing feature that orders players — the draft board, `TradeAnalyzer`,
`LineupOptimizer`, `decision_tools`, the teams pages, `draft_recap`, `draft_value` — reads
**`DBPlayer.projected_points`**, the column the design explicitly declared unfit for purpose.

Its live contents confirm the docstring's warning was accurate and is still unaddressed:

```
nonzero projected_points:  284 of 1782 players (16%)

by position (total / nonzero / max):
  WR   424 / 102 /  8.9      QB   141 /  13 / 19.6
  TE   229 /  72 /  8.8      Unknown 607 / 0 / 0.0
  RB   282 /  53 / 10.7      DEF   32 /  21 /  8.9
  K     66 /  23 /  9.1      DT     1 /   0 /  0.0
```

**Every elite player reads 0.0:**

| Player | `projected_points` |
|---|---|
| Ja'Marr Chase | 0.0 |
| Bijan Robinson | 0.0 |
| Josh Allen | 0.0 |
| Justin Jefferson | 0.0 |
| Saquon Barkley | 0.0 |
| CeeDee Lamb | 0.0 |
| Lamar Jackson | 0.0 |

**The players who *do* have values are backups, rookies and kickers** — the ones
`adp_service._create_player_from_espn()` happened to create:

```
Malik Willis   QB MIA 19.60      Philip Rivers  QB IND 13.85   ← retired
Geno Smith     QB NYJ 16.52      Max Brosmer    QB MIN 13.82
Josh Johnson   QB CIN 16.45      Chris Oladokun QB KC  13.38

top non-QB: Jacory Croskey-Merritt RB 10.7, Dylan Sampson RB 9.4,
            Eddy Pineiro K 9.06, Andy Borregales K 8.97
```

The predicted failure — *"the column ranks Philip Rivers above Josh Allen"* — is literally true
in the live database. Rivers reads 13.85; Allen reads 0.0.

## 2.3 The draft board runs on a fallback, not a projection

Running the real production path, `ADPService.get_adp_for_draft_pool(year=2026)`:

```
pool size: 1013   (1012 distinct names — no meaningful duplication)
entries with projected_points == 0:  513  (50.6%)
```

Half the draft pool has no projection at all, **including first-round picks**:

```
ADP  1.5  Jahmyr Gibbs         RB  proj=19.3
ADP  2.0  Bijan Robinson       RB  proj=19.5
ADP  4.0  Ja'Marr Chase        WR  proj=15.7
ADP 11.1  James Cook III       RB  proj= 0.0   ← round 1, no projection
```

Two things are happening here:

**(a) The nonzero values are not projections.** They come from `_pool_projection()`'s fallback,
which returns the player's **last completed season's per-game average** — a backward-looking
actual. The house model never runs for the draft.

**(b) The per-game unit systematically favors quarterbacks.** Because the fallback is per-game
and QBs out-score other positions per game, the pool's "best projected" players are almost all QBs:

```
Top of pool by projected_points:
  23.5  Patrick Mahomes    QB  (ADP 100.3)
  23.0  Drake Maye         QB  (ADP  48.5)
  21.8  Jalen Hurts        QB  (ADP  73.6)
  21.2  Justin Herbert     QB  (ADP 109.8)
  20.8  Dak Prescott       QB  (ADP  66.1)
  20.3  C. McCaffrey       RB  (ADP   5.8)   ← the actual 1.05 pick, 6th
```

`mock_draft.py` normalizes projections *within* position for the AI drafter's nudge, which
softens this — but the post-draft grade and `draft_recap`'s roster totals sum raw values across
positions, where a QB-heavy roster scores higher for free.

## 2.4 When the model does run, its adjustments make it worse

Backtest on **491 real 2025 player-weeks** (random sample, week ≥ 5 so a within-season history
exists, scored games only), using `get_effective_coefficients()` exactly as production does:

| Predictor | MAE | RMSE | Mean pred | Mean actual |
|---|---:|---:|---:|---:|
| **House model** (`WeeklyProjectionService`) | **5.16** | 6.66 | 9.18 | 7.89 |
| Naive: player's season-to-date average | **4.44** | 6.15 | 7.27 | 7.89 |
| **The model's own baseline alone** (`historical_average_points`) | **4.50** | 6.00 | 7.61 | 7.89 |
| Constant: overall mean | 5.60 | 7.17 | 7.89 | 7.89 |

Read the second and third rows together. **`ProjectionBaselines` is doing good work** — its
shrunk, leakage-free anchor (MAE 4.50) is competitive with a naive running average and clearly
beats a constant. **Everything the model layers on top of it adds ~15% error** (4.50 → 5.16) and
pushes the result below the trivial baseline it started from.

The model over-predicts at **every** position:

| Pos | n | Mean pred | Mean actual | Bias | MAE |
|---|---:|---:|---:|---:|---:|
| QB | 29 | 18.15 | 15.10 | **+3.05** | 9.38 |
| DEF | 12 | 10.20 | 8.00 | **+2.20** | 6.74 |
| K | 12 | 11.03 | 9.50 | **+1.53** | 5.05 |
| TE | 98 | 7.11 | 5.66 | **+1.46** | 4.33 |
| WR | 169 | 8.95 | 7.83 | **+1.12** | 4.51 |
| RB | 171 | 8.86 | 7.89 | **+0.97** | 5.47 |

## 2.5 Why: the adjustment terms are not mean-zero

Decomposing every yearly term across the top 60 ADP players:

| Term | Mean | Min | Max | % positive |
|---|---:|---:|---:|---:|
| `touch` | **+2.38** | +0.00 | +4.96 | **97%** |
| `skill` | **+2.29** | +0.00 | +3.78 | **97%** |
| `efficiency` | **+1.34** | +0.00 | +2.80 | **97%** |
| `offense` | +0.29 | −3.00 | +2.81 | 58% |
| `defense` (SoS) | −0.35 | −0.87 | +0.25 | 15% |
| `injury` | −0.95 | −1.68 | −0.18 | 0% |
| `trend` | **+0.00** | +0.00 | +0.00 | 0% |
| `age` | **+0.00** | −0.00 | −0.00 | 0% |
| `coaching` | **+0.00** | +0.00 | +0.00 | 0% |
| **TOTAL** | **+4.99** | | | |

Independently confirmed against the model's own output: mean lift over baseline **+5.03 ppg**,
with **57 of 60 players adjusted upward**.

Three structural causes:

1. **`touch_multiplier` is uncentered.** Every other comparable term subtracts a midpoint
   (`skill − 50`, `offense − 50`). Touch does not: it is `positional_touch_percentage × 0.05`
   over a 0–100 range, so it can only ever add. It is the single largest term in the model.
2. **`efficiency_baseline = 0.5` sits far below typical.** Nearly every real player clears it, so
   the clamped efficiency term behaves as a second additive bonus rather than a two-sided
   correction (97% positive, and it saturates at the `efficiency_cap`).
3. **`skill_multiplier` is centered at 50, which is a population midpoint, not a cohort one.**
   Draft-relevant players are by construction above it, so within the population the model
   actually serves, this term is also effectively one-directional.

The result is a formula whose adjustments **inflate** rather than **redistribute**. Since the
final step is `max(0, ...)`, there is no compensating mechanism.

## 2.6 Three tunable coefficients cannot affect anything

| Coefficient | Why it is dead |
|---|---|
| `coaching_multiplier` | `projection_criteria_builder.py:618` hardcodes `'coaching_stability_score': 50.0  # manual override only`. The term is always `(50 − 50) × c = 0`. |
| `age_post_peak_multiplier`, `age_pre_peak_multiplier` | `_get_player_age()` reads `player.stats['age']` — the ESPN JSON blob. The `players` table has a real `age` column, populated for **0 of 1782** players. So `age_dev = 0.0` universally. |
| `trend_multiplier` (yearly) | `_compute_year_over_year_trend(year=2026)` needs season rows for **both** 2025 and 2024 with ≥4 games. No top-ADP player has a 2024 row — the 607 rows for 2024 belong to a disjoint set of players from a different import. |

The age case also violates the project's own documented rule. CLAUDE.md states:
*"Profile/bio fields are real columns, not keys in `stats`. `stats` is ESPN's raw scoring-period
payload and is replaced wholesale on every sync, so anything stored there is lost."*
`_get_player_age` reads exactly the place the rule says not to.

All three are still swept by `ProjectionAlgorithmTuner.generate_variations()`, so a meaningful
fraction of every tuning run is spent varying parameters that provably cannot change the output.

## 2.7 The tuner already diagnosed §2.5 — and was never applied

Four runs exist in `~/.pigskin_mastermind/tuning_results/` (March 2026). Every full run picked
the **same** winner:

| Run | Samples | Default MAE | Best MAE | Coefficients changed |
|---|---:|---:|---:|---|
| `20260303_040821` | 59 | 9.361 | 7.769 | `offense_multiplier` 0.06→0.03, `touch_multiplier` 0.05→**0.025** |
| `20260303_042057` | 149 | 8.396 | 7.289 | identical |
| `20260303_042322` | 149 | 8.396 | 7.289 | identical |
| `20260306_030522` | 14 | 9.549 | 7.981 | (none — per-position RB run) |

The sweep's answer is to **halve the two most inflationary terms** — arrived at independently of,
and in agreement with, the decomposition in §2.5. It has never been acted on:

```
$ ls ~/.pigskin_mastermind/
tuning_results/          ← 4 runs

$ cat ~/.pigskin_mastermind/master_coefficients.json
cat: No such file or directory
```

**No `master_coefficients.json` exists**, so `get_effective_coefficients()` returns
`PositionCoefficients.from_global()` — the hard-coded defaults — on every call. The promotion
step that connects tuning to production has never been used.

Two caveats on the tuning runs themselves:

- **Sample sizes are far too small.** 59 / 149 / 149 / 14 samples, drawn from **11 players**,
  against **4,704** available 2025 game logs. A 250-variation sweep over 149 samples is fitting
  noise; the ~13% MAE "improvement" is not trustworthy at that size even though its direction
  happens to be corroborated independently.
- **The tuner's absolute MAE (8.4–9.5) is much worse than my §2.4 measurement (5.16)** because it
  includes week 1–4 samples where no within-season history exists. Both agree the model
  over-predicts.

## 2.8 The input data undermines the model regardless

Even a corrected formula would be limited by what it reads:

**2026 season rows are copies of 2025.** For many players the "2026" row is byte-identical to 2025:

```
player            2025                    2026
Bijan Robinson    17 g, 19.49 ppg    →    17 g, 19.49 ppg
Ja'Marr Chase     16 g, 15.69 ppg    →    16 g, 15.69 ppg
Puka Nacua        16 g, 19.41 ppg    →    16 g, 19.41 ppg
Jahmyr Gibbs      17 g, 19.32 ppg    →    17 g, 19.32 ppg
Josh Allen        17 g, 24.39 ppg    →     0 g,  0.00 ppg   ← inconsistent
```

This is the trap recorded in project memory as *"ESPN stats blob has no year"* — last season's
production stamped as the current season. It does not corrupt the 2026 season baseline (which
reads 2025/2024/2023), but it makes any "current season actuals" read wrong, and the
inconsistency between Chase and Allen shows two import paths disagreeing.

**Individual season rows are simply wrong.** Justin Jefferson's 2025 row reads 18 games / 159.5
points / 8.86 ppg. Eighteen games is not a valid regular season, and 8.86 ppg is roughly half his
real output. The model faithfully propagates it:

```
Justin Jefferson  WR   10.20 ppg → 173.4 season   (hist=8.68, skill=67)
```

A top-5 fantasy WR is projected as a WR4. The formula is not at fault here; the input is.

**Other data gaps:**

- **607 of 1782 players have `position = 'Unknown'`** (plus one `DT`). They are filtered out of
  the draft pool, but they are dead weight in the identity/stats tables.
- **Game logs exist only for 2025** (4,704 rows); 2026 has **6**. Weekly projections for the
  current season have essentially no in-season evidence, so `weekly_baseline()` falls back to the
  prior-year rate for every player.
- **Sportsbook odds are one stale slate.** 816 rows, all for games commencing **2025-11-09**,
  fetched 2026-02-28. The weekly blend design assigns sportsbook **45%** — the largest single
  weight in the system — and there is nothing current to weight. (The blender's renormalization
  would handle this correctly, if the blender were called at all.)

## 2.9 Summary

| Layer | Designed | Actual |
|---|---|---|
| `ProjectionBaselines` | shrunk, leakage-free anchor | **Working. Genuinely good** (MAE 4.50, beats naive on RMSE). |
| `ProjectionCriteriaBuilder` | derive all criteria from stats | Working, but `trend`/`age`/`coaching` always return neutral. |
| `ProjectionService` | adjust the baseline toward accuracy | **Runs, but degrades its own baseline** (4.50 → 5.16); over-predicts everywhere. |
| Coefficient tuning | sweep → promote → production | Sweeps ran, found the right fix, **never promoted**; defaults still live. |
| `projection_blender` | model + espn + adp/sportsbook consensus | **Never called.** Dead code. |
| `DBPlayerProjection` | single home for projections, units explicit | **Empty. 0 rows.** |
| App-wide consumption | read blended projections | Reads `DBPlayer.projected_points` — the column the design condemned. |

**The system's problems are ordered.** The formula's inflation (§2.5) is real and worth fixing,
but it is not the binding constraint: even a perfect model changes nothing user-facing, because
no user-facing surface reads the model. The gap between §1 and §2 is not primarily an algorithm
gap — it is a **wiring gap**. The design's own storage contract (`DBPlayerProjection`) and
consensus layer (`projection_blender`) were built, tested, and then never connected, leaving the
app running on a column both were written to replace.

In rough dependency order, the work that would close the gap:

1. **Populate `DBPlayerProjection`** — a job that runs the model over the player pool and writes
   `source="model"` rows, plus `import_espn_projections()` for `source="espn"`.
2. **Call `projection_blender`** and persist `source="blend"`.
3. **Repoint consumers** (draft pool, trades, lineups, recap) from `DBPlayer.projected_points` to
   the blended rows, respecting the season-total vs. per-game distinction `expected_games` exists
   to make safe.
4. **Center `touch_multiplier`** (and revisit `efficiency_baseline`) so adjustments redistribute
   instead of inflate — or simply promote the tuner's existing answer as an interim measure.
5. **Fix or retire the dead terms** — read `DBPlayer.age` instead of `stats['age']`, populate it,
   and either source coaching stability or drop the coefficient.
6. **Re-tune on a real sample** (thousands of game-weeks, not 149) once the above is true.
7. **Fix the import layer** — the duplicated 2026 season rows and Jefferson-class bad rows cap the
   accuracy of everything above.
