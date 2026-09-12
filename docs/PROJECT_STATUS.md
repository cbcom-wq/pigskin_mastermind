# Project Status

Snapshot for anyone (human or agent) picking this repo up cold.
Last verified: **2026-07-26**, on `main` @ `69a9cee`.

Companion docs: [CLAUDE.md](../CLAUDE.md) for commands and conventions,
[ARCHITECTURE.md](ARCHITECTURE.md) for how the system is put together.

---

## What this is

A fantasy football research and management application. It syncs a real ESPN league, pulls
league-wide NFL data, projects player performance (deterministic formula *and* Monte Carlo
distribution), simulates mock drafts, analyzes trades and lineups, prices players off sportsbook
props, and animates games play-by-play.

~14k lines of Python across `src/pigskin_mastermind/`, plus Jinja2 templates and three hand-written
JS modules. Single-user, local-first: SQLite on disk, no auth, no deployment story.

## Stack

| Layer | Choice |
|---|---|
| Web | FastAPI + Uvicorn, Jinja2 server rendering |
| Interactivity | HTMX + Tailwind, both from CDN — **no build step, no `package.json`** |
| Data | SQLAlchemy 2.x + Alembic over SQLite |
| Numerics | numpy, scipy, pandas, matplotlib |
| External | vendored `espn_api`, `nfl_data_py`, FantasyFootballCalculator, The Odds API |
| CLI | Click (`pigskin`) |
| Tests | pytest (+ pytest-cov, httpx) |

## Getting running in five minutes

```bash
pip install -r requirements-dev.txt
pip install -e .
alembic upgrade head
uvicorn pigskin_mastermind.api.main:app --reload
```

Then open http://127.0.0.1:8000 (Swagger at `/docs`).

Two things you will hit immediately on a fresh clone:

1. **`ModuleNotFoundError: No module named 'espn_api'`** the moment anything imports
   `services/espn_sync.py`. The client is vendored at `src/pigskin_mastermind/lib/espn-api/`, which
   `.gitignore` excludes via a blanket `lib/` rule — and it is *not* a submodule. Fix with
   `pip install espn_api` or by restoring the tree at that path.
2. **An empty app.** Nothing is seeded. The dashboard, team list, and most features filter on
   `DBTeam.is_user_team == True`, so until you add a league under **Settings → ESPN** (needs `swid`
   and `espn_s2` cookies) and claim a team, the UI looks broken rather than empty.

Optional: set `ODDS_API_KEY` for live sportsbook data, or call the odds `/seed` endpoint
(`services/sportsbook_seed.py`) to generate plausible sample props without a key.

## Feature status

| Area | State |
|---|---|
| Team / player / league CRUD, dashboard | Working. Dashboard is five bands — pulse hero, matchup cards, attention alongside movers, players, slate — built once by `services/dashboard.py::build_view` and rendered by both `GET /` and its `/api/dashboard/*` HTMX fragments, with self-terminating live polling while a game is in its window |
| Lineup optimizer, trade analyzer | Working |
| ESPN sync — rosters, weekly stats, multi-season, free agents | Working; the largest and most-changed module |
| `nfl_data_py` import — pbp, weekly, seasonal, snaps, defense ranks | Working |
| Deterministic projections (weekly + yearly) | Working |
| Projection tuner UI + algorithm sweep + master coefficients | Working |
| Monte Carlo simulation | Working |
| Mock draft simulator | Working; in-progress state in-memory, but can commit to a persisted season league |
| Sportsbook odds and prop-derived projections | Working with an API key; seedable without |
| Game / player / team play animations | Working |
| Season animation charts | Working |
| Desktop app (Electron launcher) | Working, local-only; requires the repo and `.venv` in place |
| Entertainment (names, power rankings, awards, trash talk) | Working; CLI + library only, no UI |
| Yahoo import | **Stub.** `YahooImporter` in `services/importer.py` is unimplemented |
| `ESPNImporter` in `services/importer.py` | **Stub.** Real ESPN work goes through `espn_sync.py` |
| Persistence for draft state | Working for committed leagues (`DBLeague.draft_snapshot`); in-progress drafts are still in-memory |
| Season leagues (rosters, matchups, lineups, AI managers, live scoring) | Working |
| Claude team manager agent | Working; proposes lineups, a human applies or discards |
| Auth / multi-user | Not implemented (`DBTeam.owner_user_id` exists as a hook) |

## Test suite

Verified by running it, not assumed.

```
pytest tests/            →  1241 passed, 13 failed
```

**Never run bare `pytest`** — it tries to collect the vendored `espn-api/tests/` tree and dies with
12 collection errors before running anything.

The 13 failures split into two groups. The order-dependent group is **fixed** — see below.

### 1. Order-dependent — FIXED

`tests/test_mock_draft.py` used to lose 4 tests when the full suite ran:
`tests/integration/conftest.py` mutated the **global** `app.dependency_overrides` at import time,
so `TestClient` tests elsewhere inherited an in-memory engine that never got tables →
`sqlite3.OperationalError: no such table: leagues`.

Resolved by scoping the override to a self-undoing autouse fixture inside
`tests/integration/conftest.py`, which sets it per test and pops it afterwards. This removed 5
failures, not the 4 predicted — an 18th pre-existing failure was hiding behind the same mechanism.

### 2. Stale tests, code moved on (8)

- `tests/test_nfl_data_service.py::TestGetPlayByPlay` (7 tests) — index results as
  `plays[0]['play_id']`, but `NFLDataService.get_play_by_play()` now returns
  `{'plays': …, 'game_summary': …, 'player_stats': …}`. The tests need updating to the dict shape,
  not the code.
- `tests/unit/test_espn_sync.py::test_import_team_from_espn` — `TypeError: 'Mock' object is not
  subscriptable`; the mock no longer matches how `import_team()` accesses the ESPN league object.

### 3. Assertion drift in integration tests (5)

- `test_api_teams.py::test_get_teams_with_data` — seeds a team without `is_user_team=True`, so the
  filtered team list correctly omits it.
- `test_api_lineups.py::test_optimize_lineup`, `test_api_trades.py::test_analyze_trade` — 404s,
  route shapes have moved.
- `test_api_projection_tuner.py` (2) — assert on literal strings that are no longer in the rendered
  template.

`test_mock_draft.py::test_get_adp_endpoint_ffc_auto_import_success` used to be a sixth, genuine
failure here; routing FFC name matching through `PlayerIdentityService` fixed it.

None of these indicate broken production behavior that was verified here — but none have been
investigated beyond the diagnosis above either. Treat group 1 as the highest-value fix: it makes the
suite's result depend on invocation order, which will keep wasting time.

## Known structural issues

Ranked by how likely they are to bite:

1. **Vendored `espn_api` is git-ignored.** Onboarding is broken for anyone who clones. Make it a
   pinned dependency in `requirements.txt` or a real submodule.
2. **Test isolation leak** (above).
3. **Master coefficients live in `~/.pigskin_mastermind/master_coefficients.json`** — outside the
   repo and the database. Projection output is not reproducible across machines and is invisible to
   version control and code review.
4. **`Base.metadata.create_all()` runs at import in `api/main.py`** alongside Alembic. A fresh DB is
   built by `create_all`, an existing one by migrations, so a forgotten migration passes tests and
   breaks upgrades.
5. **Draft state is a module-level in-memory singleton** (`mock_draft.draft_engine`). Drafts vanish on
   restart and break under `uvicorn --workers > 1`.
6. **Dual model layers** (dataclass vs. ORM) are unsynchronized by design; the same concept can drift
   between them.
7. **`is_user_team` filtering is applied ad hoc in routes**, so a new view can easily forget it and
   leak imported non-user teams into the UI.
8. **`test_import_players.py` sits in the repo root**, outside `tests/`, and is a manual script rather
   than a test — it hits the real database and real ESPN. It is collected by name pattern if you point
   pytest at the root.
9. **Deprecation noise**: ~3,000 warnings per run, mostly `datetime.utcnow()` (removed in a future
   Python) and Starlette's `TemplateResponse(name, {...})` argument-order change.

## Where things live

```
src/pigskin_mastermind/
├── api/
│   ├── main.py            # app assembly — every router registered here
│   ├── database.py        # engine, SessionLocal, get_db()
│   └── routes/            # 15 modules; HTML pages and /api JSON side by side
├── models/
│   ├── player.py team.py  # dataclass layer (DB-free logic)
│   ├── database.py        # SQLAlchemy schema + DEFAULT_SCORING_SETTINGS (0.5 PPR)
│   ├── projection_criteria.py
│   ├── algorithm_coefficients.py
│   └── monte_carlo.py
├── services/              # all business logic — see ARCHITECTURE.md §4–8
├── entertainment/         # names, power rankings, awards, trash talk
├── templates/             # Jinja2; `_`-prefixed files are HTMX fragments
├── static/                # draft-board.js, projection-tuner.js, simulation-field.js
├── lib/espn-api/          # vendored, git-ignored
└── cli.py                 # Click groups: team, lineup, import-cmd, entertainment, stats, odds

alembic/versions/          # 15 migrations; single head c9f4a2b7d3e5
docs/                      # this file, ARCHITECTURE.md, TUTORIAL.md, feature docs, plans/
```

## Suggested first moves

If you are here to make the project healthier rather than add a feature:

1. Pin `espn_api` as a dependency so a clone works.
2. Add a root `tests/conftest.py` and remove the global override leak.
3. Update the 8 stale tests to current return shapes.
4. Decide whether master coefficients belong in the database (reproducible, reviewable) or stay
   machine-local.

If you are here to add a feature, read [ARCHITECTURE.md](ARCHITECTURE.md) §2 (dual models) and the
section for whichever subsystem you are touching — the projection and draft subsystems in particular
have conventions that are not obvious from the file names.
