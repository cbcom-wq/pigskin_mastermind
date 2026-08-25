# Season League Foundation — Design

Date: 2026-08-23
Status: Approved, not yet implemented

Turn a completed mock draft into a persisted league that plays a real NFL season. Non-user teams
become deterministic AI managers that set their own lineups every week. The user's team is managed
by hand, by a one-click deterministic optimizer, or by a Claude Code agent that *proposes* a lineup
the user accepts or discards. Scores refresh in the background from live ESPN box scores.

This is **Cycle 1 of 3**. Cycles 2 (transactions: waivers, free agency, IR) and 3 (trades) attach to
the manager and evidence-pack interfaces defined here.

---

## Motivation

`MockDraftEngine` produces a full 12-team, 15-round draft with per-team strategies and AI
personalities — and then throws all of it away. The state is a module-level in-memory dict
(`mock_draft.draft_engine`) that does not survive a server restart. The user drafts a team, reads a
recap, and has nowhere to take it.

Meanwhile the app already holds everything a season needs: real schedules with kickoff times
(`DBNFLGame`), a weekly projection pipeline (`build_weekly_criteria` → `WeeklyProjectionService`),
bye-week truth (`services/nfl_schedule.py`), injury status on `DBPlayer`, and a working agent
pattern (`agent_evidence` / `agent_projection`). What is missing is the connective tissue: a league
that persists, rosters that are league-scoped, matchups, lineups, and something that advances the
clock.

## Decisions

| Question | Decision |
|---|---|
| Season clock | Real NFL season, live. Weeks score from real stats as games are played. No simulation, no fast-forward. |
| Scope of Cycle 1 | Lineups only. Rosters frozen after the draft. Waivers/FA/IR are Cycle 2; trades are Cycle 3. |
| Coexistence | Parallel. Season leagues get a league-scoped roster table; `espn_sync` and `DBPlayer.team_id` are untouched. |
| Team model | Reuse `DBLeague` and `DBTeam`, discriminated by `DBLeague.kind` and `DBTeam.manager_type`. An AI team is structurally identical to a human one. |
| AI authority | AI teams set their own lineups automatically, no approval. |
| Agent authority | The Claude agent **proposes** for the user's team. Nothing is written to the user's lineup without an explicit Apply. |
| Lineup locks | Per-player, at that player's own kickoff. Auto-fill fallback at the week's first kickoff so an unset lineup never scores zero. |
| Live stats | ESPN's unauthenticated public API, polled only inside game windows. `nfl_data_py` remains the authoritative post-game backfill. |
| Week advance | Background asyncio task in the app's lifespan. Not a button, not lazy-on-page-view. |
| Format | Weeks 1–14 regular season, 15–17 playoffs, top 6 seeded, no divisions. Stored per league, defaults not hardcoded. |
| Agent integration | CLI-first, mirroring `agent evidence` / `agent record-projection`. The web button is a thin `claude -p` subprocess. |
| Multi-human | Not built. A nullable `DBTeam.owner_user_id` is the only forward-compat hook. No users table. |

### Three pre-existing problems this design must fix

**1. `DEFAULT_SCORING_SETTINGS` cannot score a K or a DEF.** The table (`models/database.py`) has
ten keys, every one of them offense: no field goals, no extra points, no sacks, interceptions,
defensive touchdowns, safeties, or points-allowed tiers. `ESPN_STAT_ID_TO_INTERNAL` in
`espn_stats_mapper.py` maps no kicking or defensive stat ids either. This is invisible today because
nothing in the app scores a lineup from a stat line — the ESPN sync imports point totals ESPN
already computed. The default lineup starts one K and one DEF, so a season league built on the
current table would score **two of nine starters at exactly 0.0, every week, forever**. Cycle 1
extends both the settings table and the stat-id map.

**2. `Player.calculate_points()` mutates as a side effect.** It assigns `self.actual_points` while
returning the value, so it cannot be used as a pure scorer over arbitrary stat lines. The arithmetic
moves to `services/scoring.py::score_stat_line(stats, settings)`; the dataclass method delegates to
it and keeps its assignment for existing callers.

**3. `tests/integration/conftest.py` leaks a global dependency override.** It mutates
`app.dependency_overrides` at import time to point at an in-memory engine, while its `reset_db`
autouse fixture only applies inside `tests/integration/`. Any `TestClient` elsewhere inherits the
override without tables and dies with `no such table: leagues`. This is documented in
PROJECT_STATUS.md as known issue #2 and currently costs four order-dependent failures in
`test_mock_draft.py`. Cycle 1 adds integration tests that would inherit the same bug on day one, so
a root `tests/conftest.py` is in scope here rather than deferred.

---

## Architecture

```
  Mock draft (in-memory)
         │
         │  SeasonLeagueService.create_from_draft()
         ▼
  DBLeague(kind='season')  ──┬── DBTeam(manager_type='human'|'ai')
                             ├── DBRosterSpot        (league-scoped roster)
                             ├── DBMatchup           (schedule + results)
                             ├── DBLineupSlot        (per week, per team)
                             └── DBManagerRun        (agent decision record)
                                          ▲
   ┌──────────────────────────────────────┼───────────────────────────────┐
   │                                      │                               │
  ai_manager.py                    season_agent.py                 live_scoring.py
  (deterministic,                  (claude -p subprocess,          (ESPN box scores,
   auto-applied)                    proposes only)                  settles matchups)
   │                                      │                               │
   └────────► lineup_manager.plan_lineup() ◄──── baseline ────────────────┘
                       │
                       ├── lineup_locks.locked_at()      (DBNFLGame.kickoff_at)
                       ├── nfl_schedule.ScheduleIndex    (bye truth)
                       └── weekly_projection_map()       (DBPlayerProjection, week=N)

  season_scheduler.py  — asyncio task in the FastAPI lifespan; drives the three above
```

---

## Data model

### Extended tables

`DBLeague` gains:

| Column | Type | Notes |
|---|---|---|
| `kind` | String, default `'espn'` | `'espn'` or `'season'`. Discriminator; existing rows keep ESPN behavior. |
| `status` | String | `drafting` / `in_season` / `complete` |
| `current_week` | Integer | Advances on settlement |
| `regular_season_weeks` | Integer, default 14 | |
| `playoff_teams` | Integer, default 6 | |
| `playoff_start_week` | Integer, default 15 | |
| `draft_snapshot` | JSON | The finished `picks_log`, so the recap survives a restart |

`DBTeam` gains:

| Column | Type | Notes |
|---|---|---|
| `manager_type` | String, default `'human'` | `'human'` or `'ai'` |
| `ai_strategy` | String, nullable | A `DraftStrategy` value, carried from the draft |
| `ai_profile` | JSON, nullable | The `AIProfile` dict, carried from the draft |
| `draft_slot` | Integer, nullable | 1-indexed pick slot |
| `owner_user_id` | String, nullable | Forward-compat for multiple humans. Nothing reads it in Cycle 1. |

### New tables

**`DBRosterSpot`** — the league-scoped roster. `DBPlayer.team_id` is a single global FK, so a player
can be on exactly one `DBTeam` across the entire application; a 12-team league needs 180
simultaneous assignments and would collide head-on with an ESPN-synced league. This table is what
makes the two coexist.

| Column | Notes |
|---|---|
| `league_id` | FK `leagues.id`. Denormalized so the uniqueness rule below is expressible in SQL. |
| `team_id` | FK `teams.id` |
| `player_id` | FK `players.id` |
| `acquired_via` | `draft` / `waiver` / `free_agent` / `trade`. Cycle 1 only writes `draft`. |
| `acquired_at`, `dropped_at` | `dropped_at IS NULL` means currently rostered |

Partial unique index on `(league_id, player_id) WHERE dropped_at IS NULL`. Modeling drops now costs
nothing and is what lets Cycle 2 add transactions without migrating the core relationship.

**`DBMatchup`** — schedule and results, source of truth for standings.

| Column | Notes |
|---|---|
| `league_id`, `year`, `week` | |
| `bracket_slot` | 0-indexed position of this matchup within its week |
| `home_team_id`, `away_team_id` | Both nullable — playoff rows exist unseeded from league creation |
| `home_points`, `away_points`, `winner_team_id` | |
| `is_playoff`, `round_name` | |
| `status` | `scheduled` / `in_progress` / `final` |

Unique on `(league_id, year, week, bracket_slot)`. Keying on `bracket_slot` rather than
`home_team_id` is what makes the constraint work for playoff rows: those are created with null team
ids, and SQLite treats every NULL as distinct, so a team-keyed constraint would silently permit
duplicate unseeded rows in the same week.

**`DBLineupSlot`** — one row per rostered player per team-week.

| Column | Notes |
|---|---|
| `team_id`, `year`, `week`, `player_id` | Unique together |
| `slot` | `QB`/`RB`/`WR`/`TE`/`FLEX`/`K`/`DEF`/`BE` |
| `locked_at` | Kickoff of that player's game; NULL until locked |
| `set_by` | `user` / `auto` / `ai` / `agent` |
| `projected_points`, `actual_points` | Projection snapshotted at set time |

**`DBManagerRun`** — the agent decision record.

| Column | Notes |
|---|---|
| `league_id`, `team_id`, `year`, `week` | |
| `kind` | `'lineup'` in Cycle 1; Cycles 2–3 add values |
| `status` | `running` / `proposed` / `applied` / `discarded` / `failed` |
| `proposal` | JSON — slots plus per-change reasoning |
| `rationale`, `citations` | |
| `model`, `duration_ms`, `started_at`, `finished_at`, `error` | |

### Why not reuse `DBWeeklyTeamStats` / `DBWeeklyPlayerStats`

Their shape is close — `slot_position`, `projected_points`, `actual_points`, `points_for`,
`opponent_name`, `result` — and reusing them would cut new code. Two reasons not to:

1. `uq_team_week` is keyed on `(team_id, week)` with **no year**. A league that ever spans two
   seasons collides silently.
2. `espn_sync` rewrites those rows wholesale on every sync. A season league's lineup history should
   not be destroyable by an unrelated ESPN import — the same clobbering hazard CLAUDE.md already
   documents for the `stats` blob.

### Weekly projections

`DBPlayerProjection` already supports `week`-scoped rows and nothing writes them.
`projection_refresh` gains `refresh_week(year, week)`, scoped to **rostered players only** (a few
hundred, versus the 1,000+ draft pool), writing `source='model'`. A `weekly_projection_map()`
mirrors the existing `season_projection_map()`, falling back to season-total ÷ `expected_games`
when a weekly row is absent — the AI must never have to choose between "no projection" and refusing
to set a lineup.

---

## Draft → league commit

The draft setup screen gains a "Play for real" toggle with a league name and the user's team name.
The toggle records intent only; the commit happens from a button on the results page, and any
completed draft can be committed after the fact since the state is in memory either way. A mock
draft with the toggle off behaves exactly as it does today.

`SeasonLeagueService.create_from_draft(draft_id, name, user_team_name, owner)` — one transaction:

1. **Reject an incomplete draft.** `state['status'] != 'complete'` is an error. A half-drafted
   league has unfilled rosters and no honest way to schedule.
2. **Resolve every drafted player to a `DBPlayer.id`.** Pool dicts carry `db_id` when the pool came
   from `ADPService.get_adp_for_draft_pool()`, but a draft run off the live ESPN ADP feed goes
   through `_enrich_from_db()`, which sets `db_id` only on a normalized name+position match — so
   unresolved players are expected, not exceptional. Anything without a `db_id` goes through
   `PlayerIdentityService.resolve()` with `nfl_team` supplied, which is the repo's standing rule for
   every importer and the only thing that gets team defenses right. This step runs first and reports
   *every* unresolved player at once rather than failing on the first.
3. **Create the `DBLeague`** — `kind='season'`, `roster_slots` from the draft's `lineup_slots`,
   scoring settings from its scoring format, `current_week=1`, `status='in_season'`,
   `draft_snapshot=picks_log`.
4. **Create one `DBTeam` per draft slot.** The user's slot: `manager_type='human'`,
   `is_user_team=True`, chosen name. Every other slot: `manager_type='ai'`, `is_user_team=False`,
   with `ai_strategy` and `ai_profile` carried straight over — the team that drafted like a zero-RB
   gambler is still that team in October. Names come from the existing `entertainment/` generator.
5. **Create `DBRosterSpot` rows** from `state['rosters']`, `acquired_via='draft'`.
6. **Generate the schedule** — circle-method round robin over weeks 1–`regular_season_weeks`,
   repeating the rotation when there are more weeks than opponents, home/away balanced. Playoff rows
   for weeks 15–17 are created immediately with null team ids and `round_name` set.
7. **Zero the records** and return the league.

**Odd team counts are rejected at commit.** `MockDraftEngine` allows 2–20 teams, but a round robin
over an odd number leaves one team idle every week, and a fantasy league has no sensible meaning for
an idle week — it is neither a win, a loss, nor a bye. Rather than invent one, the commit fails with
a message naming the problem. Mock drafts with odd team counts keep working exactly as they do now;
only committing them to a league is refused.

**Bracket shape.** `playoff_teams` is clamped to the largest supported bracket that fits the league:
6, 4, or 2. With 6, week 15 is the quarterfinal (seeds 3v6 and 4v5, seeds 1–2 idle on a first-round
bye), week 16 the semifinal, week 17 the final. With 4, weeks 16–17 are used and week 15 is a
regular-season week. With 2, only week 17. Seeding is by record, then points-for.

Invariants asserted before commit, because a malformed league is far worse to discover in week 6:
every team has exactly `num_rounds` roster spots; no player is on two teams; every team plays
exactly once per regular-season week; no team plays itself.

`is_user_team` needs no special handling — AI teams are created `False`, so the dashboard and team
list keep showing only the user's teams.

---

## Season engine

### Scoring

`services/scoring.py::score_stat_line(stats, settings) -> float` becomes the single place a stat
line turns into points. `DEFAULT_SCORING_SETTINGS` gains:

- Kicking: `xp`, `fg_0_39`, `fg_40_49`, `fg_50_plus`, `fg_miss`
- Defense: `def_sack`, `def_int`, `def_fumble_rec`, `def_td`, `def_safety`
- `pts_allowed` as a **tier table**, not a multiplier — it needs its own step function
  (0 / 1–6 / 7–13 / 14–20 / 21–27 / 28–34 / 35+)

`ESPN_STAT_ID_TO_INTERNAL` gains the matching ESPN stat ids. All additions are additive; existing
offense scoring is unchanged and a league's own `scoring_settings` JSON still overrides through
`get_scoring_settings()`.

### Locks

`services/lineup_locks.py::locked_at(player, year, week)` resolves the player's NFL team to its
`DBNFLGame.kickoff_at`. A slot is locked when `now >= kickoff`. Every caller goes through this one
function, and **`now` is always an injected parameter** — never `datetime.utcnow()` inline — so lock
behavior is testable at a fixed clock instead of only on a Sunday.

### Deterministic lineup manager

`services/lineup_manager.py::plan_lineup(db, team, year, week, now) -> LineupPlan`

- Locked players hold their current slot and leave the eligible pool.
- Hard-excluded: on bye per `ScheduleIndex.is_bye()` (which correctly returns `False` rather than
  guessing when a year's schedule is not imported), and `injury_status` in `OUT` / `IR` /
  `SUSPENDED`.
- `QUESTIONABLE` and `DOUBTFUL` receive a projection haircut rather than a hard bench. A doubtful
  starter still deserves to beat a healthy WR4, and hard-benching every tag is how an AI ends up
  starting nobody in November.
- Required slots fill first, then FLEX from the best remaining RB/WR/TE. FLEX is never QB, K, or
  DEF.
- Ordering is a stable sort on `(projection desc, player_id asc)`. **No randomness anywhere** — the
  same roster and week always produce the same lineup.

`apply_plan(plan, set_by)` writes the `DBLineupSlot` rows. Planning and applying are separate so the
same planner can produce the agent's baseline without writing anything.

### Live scoring and settlement

`services/live_scoring.py::refresh_week(db, league, week)`:

1. Fetch ESPN box scores for that week's games.
2. Map to players by `espn_id`, translate through `map_espn_stat_ids_to_stats`.
3. Score through `score_stat_line` with the league's settings; update `DBLineupSlot.actual_points`.
4. Recompute each `DBMatchup`'s home/away totals.

Fully idempotent — re-running a half-played week updates in place. When every game in the week is
final: matchups go `final`, winners are set, `DBTeam.wins/losses/ties/total_points` are mirrored
from the matchup aggregate, and `league.current_week` advances if it was pointing at that week.
Settling the last regular-season week also seeds the playoff bracket — tiebreak on record, then
points-for.

**Two known risks here, both handled rather than assumed away.**

*Player mapping.* Matching box-score entries by `espn_id` only works for players an ESPN importer
created. Rows that came from `nfl_data_py` carry a `gsis_id` and often no `espn_id` at all, and a
drafted roster mixes both. So the mapping falls back to `PlayerIdentityService.resolve()` and
back-fills `espn_id` on first successful match, which means the fallback cost is paid once per
player per season rather than on every poll. Any box-score entry that still cannot be resolved is
logged and skipped — never guessed at, since a wrong match silently credits points to the wrong
roster.

*Endpoint stability.* ESPN's public box-score endpoint is undocumented and can change shape without
notice. The implementation pins one endpoint, records a real response as a test fixture, and treats
a parse failure as "no update this poll" rather than an exception that kills the background task.
If the public endpoint proves unusable for per-player stats, the fallback is `nfl_data_py`
post-game settlement — the league still works, it just stops ticking live during games. That
fallback is a degradation, not a redesign, because both paths land on the same `score_stat_line`.

### Background refresher

`services/season_scheduler.py`, an asyncio task started from a FastAPI `lifespan`:

- Reads `DBNFLGame.kickoff_at` for the next game. Inside a game window (kickoff → kickoff + 4h) it
  polls every 60s; otherwise it sleeps until the next kickoff. Nothing polls on a Tuesday.
- At a week's first kickoff it runs the auto-fill fallback for any team with **no `DBLineupSlot`
  rows at all** for that week, the user's included, so an unset lineup never scores zero. A
  partially set lineup is left exactly as the user left it — auto-fill is a floor against
  forgetting, not a second opinion.
- AI lineups are set when a week opens and re-set shortly before first kickoff to absorb late injury
  news.
- Window arithmetic is extracted as a pure `next_poll_at(now, games)` so it is testable without
  running the loop.
- `PIGSKIN_DISABLE_SCHEDULER=1` disables it. Tests and CLI invocations set it.

**SQLite consequence.** This makes the app a genuine second writer. `api/database.py` must enable
WAL and a `busy_timeout`, and the refresher uses its own `SessionLocal` session rather than sharing
a request's. It also sharpens the existing rule in CLAUDE.md: running the desktop app and a dev
`uvicorn` simultaneously goes from "produces `database is locked`" to "produces it reliably, on
Sunday, mid-scoring."

---

## Managers

### Deterministic AI

`services/ai_manager.py` is deliberately thin: for every `manager_type='ai'` team, `plan_lineup()`
then `apply_plan(set_by='ai')`.

It does **not** consult `ai_profile`. A team's draft persona shaped which players it owns, which is
where personality belongs; letting an "aggressive" profile shade a start/sit would make lineups
non-reproducible for no gain.

### Claude agent

`services/season_agent.py`, mirroring `agent_evidence` / `agent_projection` one-for-one.

**`build_team_evidence(db, team, week)`** emits one JSON document: league rules and scoring,
standings and the team's record, this week's opponent and their projected lineup, the roster with
weekly projection / floor / ceiling, injury status and recent news headlines, bye flags, per-player
lock status and kickoff time, and **the deterministic baseline lineup with its projected total**.

Handing over the baseline is the same principle the `projection-evidence` skill already uses: the
agent's job is to find where the model is structurally blind, not to redo arithmetic it cannot beat.

**`validate_lineup_result()`** raises `LineupRejected` unless all of:

- Scope matches the CLI's `--year` / `--week` / `--team` flags (same check, same reason, as
  `record_llm_projection`)
- Every slot filled to the league's exact `roster_slots` counts
- Every player is rostered by that team, each appearing once
- FLEX is RB, WR, or TE only
- No locked player has been moved
- No player on bye or `OUT` starts without an explicit `override_reason`
- Per-change `reasoning` is present

A rejected result sets the run `failed` with the reason and writes nothing to the lineup.

**CLI**: `pigskin season evidence --team T --week N [--year Y]` and
`pigskin season propose-lineup --result-file f.json --team T --year Y --week N`, where `T` is the
`DBTeam` primary key, matching how `agent evidence --player-id` already addresses a player. Scope
flags are required on the write command for the same reason `record-projection` requires them — the
validator checks the payload's own scope against the flags before it looks at anything else.

**Skill and agent**: `.claude/skills/season-team-manager/SKILL.md` and
`.claude/agents/team-manager.md`.

**Web**: `POST /season/{league}/teams/{team}/manage` spawns `claude -p --output-format json` as a
background subprocess — repo-root cwd, ~5 minute timeout, one live run per team enforced by a DB
check. The route returns a run id immediately; HTMX polls `/season/runs/{id}` until the run reaches
`proposed`, then renders a diff ("start Bijan over Pollard, +4.2 projected, Pollard questionable")
with Apply and Discard. Apply writes `DBLineupSlot` rows with `set_by='agent'`.

**On prompt injection**: the evidence pack embeds ESPN news headlines, which are untrusted data. The
defense that matters is not prompt wording — it is that the validator only ever writes a legal
lineup composed of that team's own players. A poisoned headline's worst case is a bad-but-legal
start/sit, never a roster escape.

---

## Surfaces

**Web** — one new `api/routes/season.py`, imported *and* `include_router`-ed in `api/main.py`:

| Route | Purpose |
|---|---|
| `GET /season` | League home: standings, current week's matchups |
| `GET /season/{league_id}/teams/{team_id}` | Roster, lineup editor, lock badges, Auto-set and Ask-Claude buttons |
| `GET /season/{league_id}/scoreboard/{week}` | Matchups with live points |
| `POST /season/{league_id}/teams/{team_id}/lineup` | Manual lineup save |
| `POST /season/{league_id}/teams/{team_id}/auto-set` | Deterministic optimizer, applied |
| `POST /season/{league_id}/teams/{team_id}/manage` | Spawn agent run |
| `GET /season/runs/{run_id}` | Proposal fragment (HTMX poll target) |
| `POST /season/runs/{run_id}/apply` and `/discard` | Resolve a proposal |

Templates under `templates/season/`, with `_proposal.html` as the HTMX fragment. Sidebar gains a
Season entry.

**CLI** — a `pigskin season` group: `evidence`, `propose-lineup`, `set-lineup`,
`refresh`, `settle`, `standings`. There is deliberately no `create-from-draft`:
`draft_engine` is an in-process singleton, so a draft created by the web server
does not exist in a CLI process. Committing is web-only.

---

## Testing

Everything below runs without a network and without invoking an LLM.

| Area | Tests |
|---|---|
| Lineup manager | Determinism (same input → same output, repeated); bye exclusion; `OUT` exclusion; questionable haircut still starts a star over a WR4; FLEX eligibility; locked players held in slot |
| Locks | Injected fixed clock, before/at/after kickoff |
| Schedule generator | Every team plays once per week; no self-matchups; home/away balance; playoff rows created unseeded |
| Commit | Roster counts; no duplicate players; incomplete draft rejected; unresolved-player path reports all failures at once |
| Scoring | Kicking distance tiers; points-allowed tier boundaries; offense unchanged from current behavior |
| Live scoring | Recorded ESPN payload fixture; idempotent re-run of a partial week; settlement advances the week exactly once |
| Scheduler | `next_poll_at()` as a pure function — in-window, out-of-window, no games remaining |
| Agent | `validate_lineup_result` rejects each illegal shape: wrong scope, missing slot, foreign player, duplicate, QB in FLEX, moved locked player, bye/OUT starter without override, missing reasoning |

**Baseline**: `pytest tests/` on `main` today is **631 passed, 17 failed**. Recorded so new breakage
is distinguishable from inherited breakage. Always scope pytest to `tests/` — bare `pytest` dies
collecting the vendored `espn-api` tree.

The root `tests/conftest.py` fix described above lands before the new integration tests, or they
inherit the order-dependent `no such table: leagues` failure from day one.

---

## Out of scope

- **Waivers, free agency, add/drop, IR** — Cycle 2. `DBRosterSpot.acquired_via` and `dropped_at`
  exist to receive it.
- **Trades** — Cycle 3. Attaches to `DBManagerRun.kind` and the same evidence pack.
- **Multiple human users** — only the nullable `owner_user_id` hook. No users table, no auth.
- **Keeper/dynasty leagues across years** — a league is one season.
- **Migrating existing consumers off `DBPlayer.projected_points`** — still pending, still out of
  scope; season leagues read `player_projections` and add no new consumer of the legacy column.
- **Simulating unplayed weeks** — the league tracks reality only.
