# Pigskin Mastermind — Architecture

How the pieces fit together, and why. For commands and day-to-day conventions see
[CLAUDE.md](../CLAUDE.md); for current state and known issues see
[PROJECT_STATUS.md](PROJECT_STATUS.md).

---

## 1. Shape of the system

One Python package, three entry points, one shared service layer, one SQLite database.

```
┌───────────────┐   ┌──────────────┐   ┌────────────────┐
│  Web UI       │   │  REST API    │   │  CLI           │
│  Jinja2+HTMX  │   │  /api/...    │   │  `pigskin`     │
└───────┬───────┘   └──────┬───────┘   └───────┬────────┘
        │  api/routes/*.py │                   │ cli.py
        └──────────┬───────┘                   │
                   ▼                           ▼
            ┌───────────────────────────────────────┐
            │        services/  (business logic)    │
            └───────────────────┬───────────────────┘
                                ▼
            ┌───────────────────────────────────────┐
            │  models/  (dataclasses + SQLAlchemy)  │
            └───────────────────┬───────────────────┘
                                ▼
                    SQLite  ◄── Alembic migrations
```

The web UI and REST API are the *same* FastAPI application (`api/main.py`). HTML routes and JSON
routes live side by side in `api/routes/`; several modules serve both.

### External data sources

| Source | Reached via | Feeds |
|---|---|---|
| ESPN Fantasy API | `services/espn_sync.py` (vendored `espn_api` client) | rosters, weekly stats, free-agent pool, ADP |
| `nfl_data_py` | `services/nfl_data_service.py` | play-by-play, weekly/seasonal stats, snap counts, defense rankings, rosters |
| FantasyFootballCalculator | `services/adp_service.py` | ADP for the draft simulator |
| The Odds API | `services/sportsbook_service.py` | game lines and player props (`ODDS_API_KEY`) |

All four are *pull-on-demand and cache in SQLite*. Nothing hits an external API on a page render
except where a route explicitly triggers an import.

---

## 2. The dual model layer

This is the single most important structural fact about the codebase.

**Dataclass layer** (`models/player.py`, `models/team.py`) — plain in-memory objects with no
database awareness. Used by the CLI, `TeamManager`, `LineupOptimizer`, `TradeAnalyzer`, and
`ProjectionService`. `Player.stats` is an untyped dict, which is what lets scoring stay flexible.

**ORM layer** (`models/database.py`) — the persistent schema:

| Table | Grain | Notes |
|---|---|---|
| `players` | one per player | `team_id` nullable → free agents |
| `teams` | one per fantasy team | `is_user_team` gates UI visibility |
| `leagues` | one per ESPN league | holds `swid`/`espn_s2`, `scoring_settings`, `roster_slots` |
| `weekly_team_stats` | team × week | points for/against, result |
| `weekly_player_stats` | player × team-week | `slot_position` vs. `espn_slot_position` |
| `player_season_stats` | player × year | aggregated; also carries ADP and advanced metrics |
| `player_game_logs` | player × year × week | the canonical per-game record |
| `nfl_team_stats` | NFL team × year × week | offense/defense totals + positional defense ranks |
| `sportsbook_odds` | event × book × market × outcome | game lines and player props |

The two layers are **not** synchronized. Routes construct dataclasses from ORM rows when they need
the pure-logic services, and write results back explicitly. Treat conversion as a boundary you cross
deliberately, not an abstraction that hides.

### Why both exist

The dataclass layer predates the web app (the project started as a CLI package) and remains the
cleanest place for pure fantasy logic that should be unit-testable without a database. The ORM layer
arrived with the web UI. Rather than rewrite the logic services, they were kept database-free.

---

## 3. Stats ingestion pipeline

Two independent ingestion paths converge on `player_game_logs` and `player_season_stats`.

```
ESPN league  ──► ESPNSyncService ──► weekly_player_stats ──┐
                                                          ├──► player_game_logs
nfl_data_py  ──► NFLDataService  ──► weekly stats ────────┘        │
                                                                   ▼
                                                          player_season_stats
                                                       (computed from game logs)
```

- `ESPNSyncService.import_weekly_stats()` writes `weekly_player_stats`, then
  `_build_game_logs_from_weekly()` promotes those into `player_game_logs`, then
  `_aggregate_season_stats()` rolls up to `player_season_stats`.
- `NFLDataService` mirrors that with `_promote_weekly_stats_to_game_logs()` and
  `compute_season_stats_from_game_logs()`.
- Every row carries a `source` column (`'espn'`, `'nfl_data_py'`, `'combined'`) so provenance
  survives the merge.
- `import_relevant_players_for_tuner()` exists to bound the dataset: ESPN returns free agents ordered
  by ownership, so slicing per-position (`TUNER_PLAYER_LIMITS`) gives a workable pool instead of
  every player in the league.

ESPN stat IDs are translated by `ESPN_STAT_ID_TO_SCORING_KEY` in `espn_sync.py` and
`services/espn_stats_mapper.py`.

---

## 4. Projection subsystem

The most elaborate part of the codebase. Four cooperating concerns:

### 4.1 Criteria — *what do we know about this player?*

`ProjectionCriteriaBuilder` (`services/projection_criteria_builder.py`) reads game logs, season
stats, and NFL team stats and produces a `WeeklyProjectionCriteria` or `YearlyProjectionCriteria`.
It computes derived signals that are not stored anywhere: skill composite, touch share, trend,
momentum, injury risk, schedule-adjusted defense level, position efficiency, weighted historical
average.

It is also self-healing — `_ensure_team_stats()`, `_populate_defense_rankings()` and friends will
fill in missing supporting rows on demand. That makes the first call for a given year slow.

Scales are mixed and matter: most criteria are 0–100, trend/momentum/weather are −100..100, and
defense rank is 1–32.

### 4.2 Formula — *turn criteria into points*

`ProjectionService` (`services/projection_service.py`) is an ABC with `_apply_base_criteria()` shared
by `YearlyProjectionService` and `WeeklyProjectionService`. Every multiplier in that formula is a
field on `AlgorithmCoefficients`.

### 4.3 Coefficients — *tunable knobs, per position*

`AlgorithmCoefficients` holds ~16 floats. `PositionCoefficients` wraps a `default` set plus optional
per-position overrides (QB/RB/WR/TE/K/DEF), with `get_for_position()` falling back to `default`.
`from_global()` is the migration path: copy one global set to every position, then let them diverge.

Persistence is a **JSON file outside the repo and outside the database**:
`~/.pigskin_mastermind/master_coefficients.json`, managed by `services/master_coefficients.py`, with
an `_meta` block recording when and from which tuning run the values were accepted.
`get_effective_coefficients()` returns the saved set or built-in defaults.

This means projection behavior is machine-local state. Two developers can see different numbers from
identical code.

### 4.4 Tuning — *find better coefficients*

| | `ProjectionTunerService` | `ProjectionAlgorithmTuner` |
|---|---|---|
| File | `services/projection_tuner.py` | `services/projection_algorithm_tuner.py` |
| Scope | one coefficient set | sweeps many variations |
| Output | per-criteria contribution breakdown, backtest vs. actuals | ranked `TuningRunResult`, persisted to disk |
| Drives | interactive `/projection-tuner` page sliders | "run algorithm tuning" jobs, per-position |
| Promotes to master? | via the accept endpoints | via accept-from-run |

`COEFFICIENT_DEFS` in `projection_tuner.py` is the single source of truth for each coefficient's
default, slider range, description, and formula snippet — the UI and the defaults endpoint both read
it.

---

## 5. Monte Carlo layer

The deterministic projection formula gives one number. The Monte Carlo engine gives a distribution.

Design spec: `src/pigskin_mastermind/.claude/monte_carlo_model.md`. Pipeline, per
`services/monte_carlo_service.py`:

1. **Expected touches** — from `historical_average_points / fantasy_points_per_touch`, scaled by
   touch share, trend, momentum. Sampled from a normal distribution.
2. **Efficiency per touch** — base efficiency adjusted by offense-vs-defense differential and the
   positional matchup rank. Sampled with variance.
3. **Touchdowns** — Poisson with λ modified by skill and team offense.
4. **Context modifiers** — weather multiplier, injury-driven usage loss, momentum.
5. **Simulate** — default 10,000 iterations; seedable for determinism.
6. **Summarize** — `MonteCarloResult`: mean, median, floor (p10), ceiling (p90), boom probability
   (>25 pts), bust probability (<8 pts), std dev, plus percentiles and a 20-bin histogram for charting.

`MonteCarloInputBuilder` is the bridge: it converts the 0–100 / −100..100 criteria produced by
`ProjectionCriteriaBuilder` into the 0–1 normalized `FantasyPlayerInput` the engine expects. Skipping
that normalization is the easiest way to get silently wrong results — `FantasyPlayerInput.__post_init__`
will raise on out-of-range values, so trust the validation.

Served at `/api/projections/monte-carlo/player/{player_id}` and its `/compare` sibling.

---

## 6. Sportsbook path

An independent projection source that does not touch the coefficient system at all.

```
The Odds API ──► SportsbookService ──► sportsbook_odds
                                            │
                        SportsbookProjectionService
                                            │
                       implied stat lines × league scoring
                                            ▼
                                  projected fantasy points
```

`SportsbookProjectionService` groups player-prop lines by market, resolves the league's scoring
settings, and converts prop points (e.g. receiving yards line) into fantasy points. `sportsbook_seed.py`
generates plausible sample props so the feature is demoable without an API key.

---

## 7. Game visualization stack

Three services build animated field views from play-by-play, sharing the same idiom — deterministic
jitter seeded from play identity (so replays look identical), lane percentages by role, and route
paths per play type (`_pass_route`, `_receive_route`, `_rush_route`):

- `PlayerGameSimulationService` — one player's plays, with a running stat line.
- `TeamGameSimulationService` — merges every rostered player's events into one timeline and estimates
  team fantasy points.
- `NFLGameSimulationService` — a real NFL game, both teams, with headshot resolution.

`SeasonVisualizationService` is separate: it renders matplotlib charts (static PNG and animation
frames) of cumulative season points with weekly MVP highlights. See
[SEASON_ANIMATION.md](SEASON_ANIMATION.md).

---

## 8. Mock draft engine

`services/mock_draft.py` is self-contained and **stateful in process memory**.

- `draft_engine` is a module-level singleton; drafts are keyed by `draft_id` in a dict.
- Snake order is precomputed by `_build_snake_order()`.
- `AIProfile` gives each AI team weights (ADP adherence, positional need, aggressiveness);
  `randomize_ai_profiles()` generates a varied field. `DraftStrategy` enumerates named archetypes.
- Roster needs come from `lineup_slots` → `_derive_starter_needs()` → `_derive_depth_caps()`, so AI
  drafting respects the configured lineup rather than a hardcoded one.
- The player pool comes from ESPN ADP (`fetch_espn_adp`), FantasyFootballCalculator ADP
  (`ADPService`), or a caller-supplied list.
- `grade_draft()` and `generate_commentary()` produce the post-draft results page.

**Consequence:** drafts are lost on restart and cannot be served by more than one worker process.
Persisting draft state is the obvious next step if drafts need to survive.

---

## 9. Front end

Deliberately build-step-free. `templates/base.html` pulls Tailwind and HTMX from CDN, and configures
Tailwind inline (custom `field` and `pigskin` color scales). There is no `package.json`, no bundler,
no npm install.

- Full pages extend `base.html`.
- Partials prefixed with `_` are HTMX fragment responses swapped into a target element
  (`lineups/_lineup_result.html`, `trades/_trade_result.html`, `teams/_weekly_lineup.html`, …).
- Three hand-written JS modules in `static/js/` carry the interactive features: `draft-board.js`,
  `projection-tuner.js`, `simulation-field.js`.

The tradeoff: zero tooling and instant edits, at the cost of no offline rendering, no type checking,
and CDN dependency at runtime.

---

## 10. Known structural tensions

Things a future change should be aware of, not necessarily bugs:

1. **Vendored `espn_api` is git-ignored** (`lib/` rule). A clean clone cannot import
   `services/espn_sync.py`. Making it a real dependency or a real submodule would fix onboarding.
2. **`Base.metadata.create_all()` runs at import** in `api/main.py`, alongside Alembic. A fresh
   database gets its schema from `create_all`, an existing one from migrations — so a forgotten
   migration passes tests and breaks upgrades.
3. **Master coefficients live in `$HOME`**, so projection output is not reproducible across machines
   and is invisible to version control.
4. **Draft state is in-process**, blocking multi-worker deployment.
5. **Dual model layers** mean the same concept (a player's projected points) has two homes that can
   drift.
6. **`is_user_team` filtering** is applied in routes rather than at the query/model layer, so it is
   easy to add a new view that forgets it.
