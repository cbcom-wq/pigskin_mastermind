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

### Desktop app (Electron)

A local-only launcher lives in `desktop/`. It spawns uvicorn from `.venv` on a free ephemeral port,
points a window at it, and kills the backend on quit.

```bash
cd desktop
npm install     # first time only
npm start       # launch the desktop app
npm test        # unit tests for the port picker and health poll
```

It uses the repo's `pigskin_mastermind.db` via an absolute `DATABASE_URL`. **Do not run the desktop
app and a dev `uvicorn` at the same time** — two writers on one SQLite file produce
`database is locked`.

Helper modules live in `desktop/src/`, not `desktop/lib/`: the blanket `lib/` rule in `.gitignore`
matches at any depth and would leave them untracked.

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

**Team defenses are the exception: they resolve by NFL team, not by name.** ESPN calls Atlanta's
"Falcons D/ST", FFC calls it "Atlanta Defense", and nflverse has no entry at all — so name matching
can never work, and every import used to create another row. `resolve()` takes an `nfl_team` kwarg
and checks `find_defense()` first; there is exactly one defense per team, so the team *is* the
identity. Team matching applies to `DEF` only, or two RBs on one roster would collapse together.

`merge_duplicates(dry_run=True)` folds existing duplicates together — exposed as
`pigskin players merge-identities [--dry-run|--apply] [--position DEF]`. Re-run it after a bulk
import. It calls `canonicalize_positions()` first, because a defense stored as `D/ST` matches
nothing and is dropped from the draft pool. `--position` scopes a targeted cleanup instead of
folding every duplicate at once.

When two season rows collide, real consensus ADP beats a synthetic `espn_tail` value — otherwise the
placeholder wins purely by arriving first.

Two things that will bite:

- `DBPlayer.season_stats` / `game_logs` cascade `all, delete-orphan`. Re-pointing a FK then deleting
  the old parent in the same flush cascades into the rows you just moved — flush and `expire()` first.
- **Profile/bio fields are real columns, not keys in `stats`.** `stats` is ESPN's raw
  scoring-period payload and is replaced wholesale on every sync, so anything stored there is lost.

Positions are normalized at every import boundary via `utils/positions.py::normalize_position()`
(`D/ST`/`DST` → `DEF`; non-fantasy positions return `None` and the row is skipped).
`snap_pct` is canonically **0–100** — the importer scales nflverse's 0–1 `offense_pct`.

**Teams are normalized the same way**, via `utils/nfl_teams.py::normalize_team()` — ESPN says
`WSH`, everyone else says `WAS`, and PFR-style exports say `GNB`/`KAN`. Two spellings of one
franchise make a player look like they changed teams and silently break the abbreviation joins in
`projection_criteria_builder.py`. `normalize_team()` returns `None` for `FA`/blank/unrecognized,
which means **leave the stored value alone** — never write it, or an unsigned player wipes a good
team. Applied in `adp_service.py` and `espn_sync.py`; normalizing only one of them lets the next
sync undo the other.

### A bye is not a game

ESPN reports a player every week they are *rostered*, bye weeks included, so both ESPN paths used
to store the bye as a game log with an empty stat line and 0.0 points. Both aggregation sites then
counted it (`games_played = len(logs)`), inflating games by one and deflating `fantasy_points_avg`
by ~5% for everyone — and that average is what `ProjectionBaselines` and `_compute_expected_games`
build on, so every projection inherited it.

`services/nfl_schedule.py` owns the single definition. Two rules matter:

- **Byes come from the schedule, never from the score.** A K or DEF can genuinely put up 0.0 in a
  game that happened. `ScheduleIndex.is_bye()` reports a bye only when `DBNFLGame` has rows for
  that year *and* none for that team that week — an unimported schedule makes every week look
  missing, so it must return `False` rather than guess.
- **A row is only a bye if it also has no stat line.** Game logs carry no team column, so the team
  comes from `DBPlayer.nfl_team` — the player's *current* club, which for a past season is often
  the wrong franchise after free agency. The empty-line requirement is what stops a traded
  player's real game from being deleted because his new team was on bye that week.

Applied at both aggregation sites (`nfl_data_service.compute_season_stats_from_game_logs`,
`espn_sync._aggregate_season_stats`) and at the two weekly-roster promotion paths that create the
rows. `pigskin stats purge-bye-weeks [--dry-run|--apply]` cleans rows written before this, which
matters beyond `games_played`: a stored 0.0 also drags down `_calculate_momentum` and inflates the
standard deviation behind the consistency score.

`snap_count`/`snap_pct` do **not** have this divisor problem — they come from nflverse's own
per-game snap frame, which has no row for a bye, and are never divided by `games_played`.

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

### Two projection storage paths — only one is wired up

`player_projections` (`DBPlayerProjection`, `services/projection_refresh.py`) is the canonical
store: `get_projection()` / `season_projection_map()` read a persisted `model` or `blend` season
row, one unit (season totals), one number per player-year. It is populated by
`pigskin projections refresh` and is what the **mock draft pool**
(`ADPService.get_adp_for_draft_pool()`) ranks on.

`DBPlayer.projected_points` is the legacy path. It is a single mixed-unit column that two
importers write differently, and it is still what `players.py`, `lineups.py`, `trades.py`, and
`teams.py` read — so the same player can show two different "Projected" numbers depending which
page you're on. Migrating those consumers onto `player_projections` is pending; each remaining
read site has a `NOTE:` comment pointing here. Do not add a new consumer of
`DBPlayer.projected_points` — wire it to `player_projections` instead.

`player_projections.source` also has an `llm` value, written by `pigskin agent record-projection`
(see below). It is display-only: `_READ_PRIORITY` and `DRAFT_POOL_SOURCES` in
`projection_refresh.py` never include it, so an `llm` row cannot become the `blend` row and never
reaches the mock draft pool. Being locked out of the blend is not the same as being unevaluated —
see `agent_scoring.py` below for how it's actually judged.

### Agent evidence and recorded projections

`services/agent_evidence.py::build_evidence()` assembles everything known about one player — bio,
season stats, game logs, criteria, existing projections, schedule, sportsbook props, news, data
freshness — into one JSON document, for a Claude Code agent to read instead of discovering it
endpoint by endpoint. `services/agent_projection.py::record_llm_projection()` validates what that
agent returns (required fields, a sanity band around the `model` projection, citation rules) and
upserts it as `source='llm'`. Both are exposed via `pigskin agent evidence` and
`pigskin agent record-projection`. Nothing in either module calls an LLM.

`--as-of` turns `agent evidence` into a backtest: several blocks are withheld or truncated so the
document doesn't leak data past the cutoff. `context.as_of_week` and `context.as_of_cutoff_at`
report whether the cutoff was requested and whether it actually resolved to a kickoff — read those
before trusting a `_filtered_reason` key, since an unresolvable cutoff serves rows unfiltered.

**`--year` is required on `record-projection`**, with `--week` optional and omitted for a season
projection. Pass the same values given to `agent evidence` for the same run — copied from its
`context` block, not retyped — or `record_llm_projection()`'s scope check rejects the result: it
validates the JSON's own `year`/`week` against these flags before checking anything about the
projected number itself.

`services/agent_scoring.py::score_projections()` is the justification for `llm` existing as a source
at all: it is what turns "recorded but never blended" into an actual track record. It compares stored
projections against actuals per source, then re-scores every source on the intersection of players
*all* requested sources projected. That second number is the only one worth reading — `model` carries
a row for essentially the whole draft pool (~1,000+ players) while `llm` has however many an agent has
actually run, so their raw per-source MAEs are not comparable; the head-to-head restriction is what
makes them comparable. Exposed via `pigskin agent score --year Y [--week N] [--sources model,llm]`.

The `.claude/skills/projection-evidence/` skill and the `player-analyst` agent
(`.claude/agents/player-analyst.md`) are what actually run a projection end to end: the skill teaches
an agent how to read one evidence pack, find where the deterministic model is structurally blind for
that specific player, and record a validated `llm` result without re-deriving what the model already
computed. The agent is what Claude Code dispatches to carry that out.

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

**The draft pool has two sources, and both matter.** FFC's API returns ~250 players no matter what
league size you request, but a 12-team 15-round draft is 180 picks — so an FFC-only pool empties
before the draft ends. `ADPService.import_espn_tail()` backfills from ESPN's 1000-player board,
tagged `adp_source="espn_tail"` with a synthetic ADP of `max_ffc_adp + rank`. That value is a sort
key, not a draft position; the `adp_source` label is what distinguishes it. The tail is ordered by
ESPN's projection because ~789 of its players share a placeholder ADP of 170.

- `refresh_draft_data()` refreshes ADP and team data: FFC, then the tail, then
  `canonicalize_stored_teams()`. **Order matters** — ESPN runs second, so it wins on team conflicts.
  It does **not** refresh projections — that hook was removed so this call stays offline. Run
  `pigskin projections refresh --year <year>` separately (and after `refresh_draft_data()`, since it
  depends on the ADP-derived pool membership) to populate `player_projections`.
- The tail applies team updates to *all* ~1000 ESPN players but writes ADP rows only for those FFC
  didn't rank. Skipping FFC-board players wholesale would leave the top ~250 on stale teams.
- `get_adp_for_draft_pool()` reads both sources; `get_all_adp()` (the `/adp/rankings` consensus
  view) stays FFC-only.
- `get_adp_metadata()` owns the staleness verdict (`STALE_AFTER_DAYS = 7`) so the draft page and the
  API can't disagree about what "out of date" means.

### Season leagues

A completed mock draft can become a persisted league (`DBLeague.kind='season'`)
that plays the real NFL season. Committing happens from the draft results page
via `POST /season/commit-draft` — **not** from the CLI, because `draft_engine` is
an in-process singleton and a CLI process cannot see a draft the web server
created.

Rosters are **league-scoped** (`DBRosterSpot`), not `DBPlayer.team_id`. That
column holds one team per player across the whole application, which cannot
express a 12-team league sharing player rows with an ESPN-synced one. The ESPN
path is untouched: `espn_sync` still uses `DBPlayer.team_id`.

**Two storage paths means every roster read needs
`season_league.py::roster_players(db, team, league=None)`**, which branches on
`DBLeague.kind`. Reading `DBPlayer.team_id` (or the `DBTeam.players`
relationship) directly renders a freshly drafted season team as having zero
players — that was the bug on `/leagues/{id}`, `/teams`, `/teams/{id}`,
`/lineups`, `/trades/team-players`, and the dashboard totals. It branches on
`kind` rather than falling back when one query comes up empty, so a season team
that really did drop everyone is not silently re-read through the ESPN column.
**Player-facing pages need `fantasy_teams_for(db, player_ids)` instead**, which
returns `TeamRef` rows (name, url, kind) for *every* team rostering a player.
One `DBPlayer` row is shared by all leagues, so the same person is routinely on
an ESPN roster and one or more season rosters at once, and `DBPlayer.team_id`
can only name one of them. It is batched because the search table renders 100
rows; the per-row alternative is 100 round trips for one column.

### Rolling an ESPN league into a new year

`DBLeague.year` is a single column, so a reactivated league cannot hold two
seasons at once. Three things are **not** year-scoped and are destroyed by the
next sync: `DBTeam.wins/losses/total_points`, the `DBPlayer.team_id` rosters,
and `weekly_team_stats` / `weekly_player_stats` — whose
`UniqueConstraint('team_id', 'week')` has no year column, so 2026 week 1
overwrites 2025 week 1. (`player_season_stats`, `player_game_logs` and
`player_projections` all carry `year` and are safe.)

The season is therefore **archived before the year is flipped**: the league row
is renamed to `<espn_id>-<year>`, its `kind` set to `archive`, its credentials
cleared, and its team rows re-pointed at the new `league_id`. Team records and
weekly stats hang off `teams.id` and follow those rows untouched. The roster is
copied into `DBRosterSpot` first, because `DBPlayer.team_id` is global and the
next draft re-points it.

**`kind='archive'` therefore reads rosters from `DBRosterSpot`, exactly like a
season league** — that is what `ROSTER_SPOT_KINDS` in `season_league.py` is for.
Adding a fourth kind means deciding which side of that set it belongs on. An
archived league keeps the `/leagues/{league_id}` ESPN grid (it is still an ESPN
season, just a finished one), but every action that would call ESPN is hidden
and guarded server-side: `settings.sync_league`, `leagues.import_all_players`,
and the two Sync buttons on `/teams/{id}`. Its `league_id` is not an ESPN id, so
an unguarded path fails on `int(league.league_id)` before it ever reaches ESPN.

**A season league's canonical page is `/season/{league_id}`, not
`/leagues/{league_id}`.** The latter is the ESPN team grid, and both of its
actions — Import All Players, Claim team — need ESPN credentials a drafted
league never has, so it 302s to the season home. Templates that mix both kinds
get `season_league_ids(db)` and link a season team to
`/season/{league_id}/teams/{team_id}`; the generic `/teams/{id}` page can show
that roster but cannot set its lineup.

`services/lineup_manager.py::plan_lineup()` is the only place a lineup decision
is made — the AI manager, the auto-fill fallback, the web auto-set button, and
the agent's baseline all call it. It is strictly deterministic (stable sort on
`(-projection, player_id)`, no randomness) and deliberately ignores
`DBTeam.ai_profile`: a team's draft persona shaped which players it owns, and
there is no aggressive way to start your highest projected players.

Players lock at **their own** kickoff (`services/lineup_locks.py`), not a
league-wide deadline. Every function there takes `now` as a parameter; calling
`datetime.utcnow()` inline would make locks testable only during a real game.

**`DBNFLGame.kickoff_at` is a naive US EASTERN wall clock, not UTC.** nflverse
publishes `gameday`/`gametime` as ET and `_parse_kickoff` stores them without a
tzinfo, so a Sunday early game is the literal value `13:00`. Anything compared
against that column must use
`services/season_scheduler.py::league_now()` — `datetime.utcnow()` runs 4-5
hours ahead and would fire every lineup deadline before the games it guards.
`league_now()` is the single definition; the scheduler, the evidence pack, the
proposal validator, and the team routes all take their `now` from it.

`services/season_scheduler.py` runs as an asyncio task in the FastAPI lifespan.
It polls ESPN only inside game windows, sets AI lineups when a week opens,
auto-fills any team with **no** lineup at the week's first kickoff, and settles
matchups. Disable it with `PIGSKIN_DISABLE_SCHEDULER=1` — the test suite and
every CLI command do.

**This makes the app a second writer to SQLite.** `api/database.py` enables WAL
and `busy_timeout` for that reason. The existing rule against running the
desktop app and a dev `uvicorn` together matters more now, not less.

Standings are a **regular-season** record: `live_scoring.recompute_standings()`
excludes `is_playoff` matchups. This is not cosmetic — `_seeded_teams()` sorts
on those same wins and points-for, and `_advance_bracket()` reads `seeds[0]`/
`seeds[1]` to place the bye teams, so counting playoff results would let a
quarterfinal winner leapfrog the real 1 seed into its own semifinal.

The Claude team manager (`services/season_agent.py`,
`.claude/skills/season-team-manager/`) **proposes**; AI teams apply
automatically, the user's team never does. `validate_lineup_result()` is the
real boundary: whatever an agent read in a news headline, only a legal lineup
made of that team's own players can be written. Manual lineup edits go through
the same validator.

Scoring lives in `services/scoring.py`. `DEFAULT_SCORING_SETTINGS` now includes
kicking (by field-goal distance) and team defense; `pts_allowed` is a tier step
function, not a multiplier, and is handled by the scorer rather than the
settings dict.

CLI: `pigskin season {evidence,propose-lineup,set-lineup,standings,tick}`.

### Multi-source weekly projections

`services/projection_sources/` holds one provider per source behind a single
protocol (`base.py`), registered in order by `registry.py`: `model`, `espn`,
`sportsbook`, `nflverse_xp`, `llm`, `consensus`.
`weekly_projection_refresh.refresh_week_all()` runs them all and writes into
`player_projections`, the same table the season path uses.

**Partial success is the normal outcome, not an error.** The six sources have
unrelated failure modes — one needs an API key, one scrapes HTML, one covers a
few dozen players. Each provider gets its own try/except and its own
`projection_source_runs` row; one raising must leave the other five on screen.
A provider that covered nobody is recorded `skipped`, not `ok`, because for a
scrape those mean very different things.

**The consensus is stored as `blend_multi`, never `blend`.** `_READ_PRIORITY`
in `projection_refresh.py` is `(blend, model)` and backs
`weekly_projection_map()`, which `lineup_manager.plan_lineup()` calls for every
lineup decision — AI managers, first-kickoff auto-fill, the auto-set button. No
weekly `blend` row exists, so that read falls through to `model` today. Writing
the consensus under the name `blend` would silently switch all of them onto it
as a side effect of a display feature. A regression test guards this.

**Ranks are derived at read time and always carry their denominator.**
`projection_rankings.weekly_source_table()` ranks per `(source, position)` over
every stored row for the week, never just the roster. Coverage differs by an
order of magnitude — the model ranks ~870 players, ESPN ~37 — so `WR7 of 13`
and `WR7 of 304` are different claims and `rank_of` is part of the return type
rather than something a caller can forget.

Two rules that produce wrong numbers if broken:

- **A model `0.0` is not a projection.** `max(0, base_score)` in
  `projection_service` is a clamp meaning "no signal"; `refresh_season` already
  skips it and the weekly model source must too, or ~166 players the model
  could not score get ranked below every player it scored low.
- **A refresh prunes as well as upserts.** The Refresh button re-runs the same
  week, so rows a source no longer covers would survive with a stale number and
  a stale rank. `_prune()` is restricted to the players the pass examined, so a
  `--sources` run cannot delete what it never looked at.

`nflverse_xp` computes expected points itself — the installed `nfl_data_py` has
no `import_ff_opportunity`. It prices opportunity (attempts/carries/targets) at
league-average rates, then EWMAs the last 4 games. It returns nothing at week 1:
there is no history, and the season frame is not published yet, so fetching
404s and would look like a broken source.

`consensus` is off unless `PIGSKIN_CONSENSUS_URL` is set, and everything
site-specific is one injected `fetch_rows` callable.

CLI: `pigskin projections refresh-week --year Y --week N [--sources a,b]`.
Daily refresh runs from `season_scheduler.tick()` behind a
`projection_source_runs` guard.


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
