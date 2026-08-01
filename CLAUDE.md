# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Orientation

Start with [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) for current state and known issues,
and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the layers fit together.

Pigskin Mastermind is a fantasy football research/management app with three front doors over one
shared service layer: a **FastAPI web UI** (server-rendered Jinja2 + HTMX), a **REST API** in the
same app, and a **Click CLI** (`pigskin`). Data lives in SQLite via SQLAlchemy, migrated with
Alembic.

## Commands

Run from the repo root. On Windows the venv interpreter is `.venv/Scripts/python`.

```bash
# Install
pip install -r requirements-dev.txt   # includes requirements.txt
pip install -e .

# Database
alembic upgrade head

# Run the web app  (http://127.0.0.1:8000, Swagger at /docs)
uvicorn pigskin_mastermind.api.main:app --reload
```

### Testing

```bash
# ALWAYS scope pytest to tests/ — see gotcha below
pytest tests/

# Single file / single test
pytest tests/test_player.py
pytest tests/test_player.py::test_player_creation -v

# Coverage
pytest tests/ --cov=pigskin_mastermind --cov-report=html
```

**Gotcha:** bare `pytest` fails during collection. The vendored ESPN library at
`src/pigskin_mastermind/lib/espn-api/` ships its own `tests/` tree that imports `espn_api` as an
installed package (it is not installed). Always pass `tests/`.

There is no `pytest.ini` / `pyproject.toml` — pytest runs on defaults.

### Code quality

```bash
black src/ tests/
flake8 src/ tests/
```

## Environment

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL | `sqlite:///./pigskin_mastermind.db` |
| `ODDS_API_KEY` | The Odds API key for sportsbook lines/props | unset — odds import fails |

ESPN private-league access needs `swid` + `espn_s2` browser cookies. They are stored per league on
the `leagues` table (`DBLeague.swid`, `DBLeague.espn_s2`), entered via **Settings → ESPN**.

Tuned projection coefficients are **not** in the database — they live at
`~/.pigskin_mastermind/master_coefficients.json` (`services/master_coefficients.py`).

## Architecture essentials

### The vendored ESPN client is untracked

`src/pigskin_mastermind/lib/espn-api/` holds the `espn_api` package and is **git-ignored**
(`.gitignore` has a blanket `lib/` rule). It is not a submodule — there is no `.gitmodules`. A
fresh clone will not have it, and importing `services/espn_sync.py` then fails with
`ModuleNotFoundError: No module named 'espn_api'`. `espn_sync.py` inserts that directory into
`sys.path` at import time. To recover: `pip install espn_api`, or restore the source tree at that
path.

### Two parallel model layers, deliberately not synchronized

- **Dataclasses** — `models/player.py`, `models/team.py`. In-memory; used by the CLI,
  `TeamManager`, `LineupOptimizer`, `TradeAnalyzer`, `ProjectionService`. `Player` validates
  position against `['QB','RB','WR','TE','K','DEF']`; `Player.stats` is a free-form dict.
- **SQLAlchemy ORM** — `models/database.py`: `DBPlayer`, `DBTeam`, `DBLeague`,
  `DBWeeklyTeamStats`, `DBWeeklyPlayerStats`, `DBPlayerSeasonStats`, `DBPlayerGameLog`,
  `DBNFLTeamStats`, `DBSportsbookOdds`. Everything web-facing uses these.

Routes convert between them at the boundary. A change on one side does not propagate to the other.

### Scoring is 0.5 PPR, resolved per league

`DEFAULT_SCORING_SETTINGS` in `models/database.py` is **half PPR** (`rec: 0.5`), and it is the
fallback for both `get_scoring_settings(league)` and the dataclass `Player.calculate_points()`.
Always resolve through `get_scoring_settings()` rather than hardcoding, so a league's JSON
`scoring_settings` overrides apply.

### Player identity is resolved, never assumed

Three importers create `DBPlayer` rows — ESPN (`espn_<id>`), nfl_data_py (`nfl_<gsis_id>`), and
FantasyFootballCalculator (`ffc_<id>`). They are the *same people*, so `DBPlayer` carries
`espn_id` / `gsis_id` / `pfr_id` and **every importer must go through
`services/player_identity.py::PlayerIdentityService.resolve()` before creating a row.** It tries the
ID columns, then the legacy prefixed `player_id`, then nflverse's cross-ID table
(`nfl_data_py.import_ids()`), then normalized name + position.

`merge_duplicates(dry_run=True)` folds existing duplicates together — exposed as
`pigskin players merge-identities [--dry-run|--apply]`. Re-run it after a bulk import.

Two things that will bite:

- `DBPlayer.season_stats` / `game_logs` cascade `all, delete-orphan`. Re-pointing a FK then deleting
  the old parent in the same flush cascades into the rows you just moved — flush and `expire()` first.
- **Profile/bio fields are real columns, not keys in `stats`.** `stats` is ESPN's raw
  scoring-period payload and is replaced wholesale on every sync, so anything stored there is lost.

Positions are normalized at every import boundary via `utils/positions.py::normalize_position()`
(`D/ST`/`DST` → `DEF`; non-fantasy positions return `None` and the row is skipped).
`snap_pct` is canonically **0–100** — the importer scales nflverse's 0–1 `offense_pct`.

### Projection pipeline

```
DB stats ──► ProjectionCriteriaBuilder ──► {Weekly,Yearly}ProjectionCriteria
                                                  │
       AlgorithmCoefficients ─────────────────────┤
       (per-position via PositionCoefficients)    ▼
                                          ProjectionService ──► projected points
```

- `models/projection_criteria.py` — criteria dataclasses (base → weekly / yearly).
- `models/algorithm_coefficients.py` — every tunable multiplier. `PositionCoefficients` wraps a
  `default` set plus per-position overrides for QB/RB/WR/TE/K/DEF, falling back to `default`.
  `from_dict` accepts both the legacy flat `{key: float}` shape and the nested position-keyed shape.
- `services/projection_criteria_builder.py` (~1.4k lines) — derives skill, touch share, momentum,
  opponent defense level, etc. from game logs and season stats. It lazily populates missing
  team/defense stat rows as a side effect.
- `services/projection_service.py` — the formula itself (`_apply_base_criteria` plus weekly/yearly
  layers). Passing `coefficients=None` keeps the hard-coded defaults.

**Two tuners, different jobs — don't confuse them:**

- `services/projection_tuner.py` → `ProjectionTunerService`: runs a *single* projection with a
  given coefficient set and returns a per-criteria contribution breakdown; also backtests weekly
  and yearly against actuals. Backs the interactive `/projection-tuner` page.
- `services/projection_algorithm_tuner.py` → `ProjectionAlgorithmTuner`: sweeps *many* coefficient
  variations (globally or per position), scores each against actuals, and persists run results to
  disk. Winners are promoted through `master_coefficients.save_master_coefficients()`.

`get_effective_coefficients()` is the single call production code should use for the active set.

### Monte Carlo simulation

`src/pigskin_mastermind/.claude/monte_carlo_model.md` is the original design spec for this engine —
read it before changing the math.

`FantasyPlayerInput` (`models/monte_carlo.py`; all inputs normalized 0–1 except the 1–32 defense
rank) → `FantasySimulationEngine.simulate()` (`services/monte_carlo_service.py`) →
`MonteCarloResult` (mean / median / floor p10 / ceiling p90 / boom >25 / bust <8, plus the raw
array). `MonteCarloInputBuilder` adapts DB-derived `WeeklyProjectionCriteria` into
`FantasyPlayerInput` — that is where 0–100 and −100..100 criteria get normalized. Seed the engine
for deterministic output.

### Game / play visualizations

Three sibling services build animated field views from `nfl_data_py` play-by-play, sharing the same
route-path and deterministic-jitter idiom: `player_game_simulation_service.py` (one player),
`team_game_simulation_service.py` (merges a fantasy roster's events), and
`nfl_game_simulation_service.py` (a real NFL game). Front end is `static/js/simulation-field.js`
plus `static/css/simulation.css`.

### Mock draft engine

`services/mock_draft.py` exposes a **module-level singleton** `draft_engine` holding in-memory draft
state keyed by `draft_id`. It does not survive a server restart and is not shared across processes —
do not run uvicorn with `--workers > 1` and expect drafts to work. Snake order, the `DraftStrategy`
enum, `AIProfile` weights, ADP-driven pools (ESPN, or FantasyFootballCalculator via `ADPService`),
draft grading, and commentary all live in this module.

### Web layer conventions

- `api/main.py` mounts `/static`, configures Jinja2, calls `Base.metadata.create_all()` at import,
  then includes every router. A new router must be both imported *and* `include_router`-ed there.
- Route modules mix HTML and JSON: HTML pages sit at the resource prefix (`/teams`, `/draft`), JSON
  under `/api/...`. `stats.py`, `players.py`, and `projection_tuner.py` each serve both.
- `get_db()` in `api/database.py` is the session dependency.
- Templates load **Tailwind and HTMX from CDN** in `templates/base.html` — no build step, no
  `package.json`. Templates prefixed `_` (e.g. `lineups/_lineup_result.html`) are HTMX fragment
  responses.
- The dashboard and team list filter on `DBTeam.is_user_team == True`. A team created without that
  flag will not appear in the UI.

## Domain rules

- **FLEX eligibility**: RB, WR, TE only — never QB, K, or DEF.
- **Default lineup**: `{'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1, 'K': 1, 'DEF': 1}`.
- **Lineup optimization**: fill required slots first, then FLEX with the best remaining eligible
  player by projected points.
- **Trade fairness**: `fairness_score` is the absolute difference in net projected points; under
  5.0 is "fair". Recommendations are Accept / Reject / Consider.
- **Positional defense ranks**: 1 = best defense, 32 = worst — a *high* rank means a good matchup.

## Adding things

- **New API route module**: create it in `api/routes/`, then import and `include_router` in
  `api/main.py`.
- **New DB column**: edit `models/database.py`, then `alembic revision --autogenerate -m "..."` and
  `alembic upgrade head`. Because `create_all()` runs at startup, a missing migration can pass
  locally on a fresh DB and still break an existing one.
- **New tunable coefficient**: add the field to `AlgorithmCoefficients`, consume it in
  `projection_service.py`, add an entry to `COEFFICIENT_DEFS` in `projection_tuner.py` (single
  source of truth for defaults, slider ranges, and UI docs), and add it to the sweep in
  `projection_algorithm_tuner.generate_variations()`.
- **New importer**: subclass `FantasyServiceImporter` in `services/importer.py`, implement
  `authenticate()` / `import_team()` / `get_player_data()`, and register it in
  `ImporterFactory.create_importer()`. The ESPN/Yahoo classes there are stubs — real ESPN work goes
  through `services/espn_sync.py`.
