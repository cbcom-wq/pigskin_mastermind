# Android tablet build, and the ESPN play-by-play source it depends on

**Date:** 2026-09-09
**Status:** Design — not yet implemented

## Why this document covers two things

The ask was "prepare the app to run on an Android tablet." Investigating it
turned up a dependency that reorders the work: the feature the tablet exists to
show — live play-by-play animation — is the one feature that cannot run there
today, and cannot run *live* anywhere today.

So this is one design in two phases. Phase 1 replaces the play-by-play data
source, on the desktop, where it can be seen working. Phase 2 packages the
result for Android. Phase 1 is a prerequisite, not a preamble: shipping Phase 2
without it produces a tablet whose headline feature is blank.

## What the tablet is for

From the user, verbatim in intent: *a lightweight live view and review. Not a
workhorse. Not the projection and algorithm generator.*

Three features define it:

1. Live play-by-play animations
2. Dashboard
3. Viewing team stats

Everything else is negotiable, and the user has explicitly invited cutting.

## Evidence this design rests on

Measured during the design spike, not assumed.

**The current play-by-play source is the worst-suited component in the codebase
for this job.** `NFLDataService.get_play_by_play()` calls
`nfl_data_py.import_pbp_data`, which re-reads a full season of parquet on every
request:

```
cold 2025 w18    3.81s  plays=25
warm 2025 w17    3.49s  plays=0     <- no caching between calls
2026 w1          0.50s  plays=0     <- "Data not available for 2026"
```

It needs `pandas` + `pyarrow`. `pyarrow` has no Android wheels — it is the one
dependency in the tree that genuinely cannot be ported. And as of 2026-09-09 it
carries no 2026 data at all, so the current season cannot be animated on any
device.

It is also not live. nflverse publishes after the fact. The feature described as
"live play-by-play" is, on the current source, post-hoc review.

**ESPN's public summary API carries the same plays, live.** One JSON GET returns
~170–230 plays for a game, each with a `wallclock` timestamp. Field coverage
against everything `player_game_simulation_service` reads was complete —
`missing fields: none` across nine games in three different weeks.

**Player attribution is the only real gap, and it is tractable.** ESPN plays
carry no `participants` array, so the actor must be parsed from the play text
(`"T.Etienne left tackle to CIN 23 for 3 yards (L.Wilson)"`) and matched against
the game's own boxscore roster, which does carry ESPN athlete ids.

Spike results over nine games:

| Measure | Result |
|---|---|
| Field coverage | complete (`epa` excepted) |
| Play attribution | **98.6%** of rush/pass plays resolved to a rostered athlete |
| Stat lines exactly reproducing ESPN's own boxscore | **81.8%** |

The 81.8% is a deliberately strict proxy — the animation needs field position,
yardage, play type and actor, not boxscore-exact season totals. Residual error
traced to three identified causes, all fixable:

- **Ambiguous initials, cascading.** `c.williams` → `['Caleb Williams', 'Chris
  Williams']`. Dropping the ambiguous token voided the passer, and because
  receivers are credited relative to the passer, unrelated players (Rome Odunze,
  D'Andre Swift) also fell to zero. One ambiguous name silently voided a team's
  whole passing game.
- **QB sacks counted as rushes**, producing negative rush yards.
- **Lateral and penalty edge cases**, ±4–10 yards.

A one-line classification fix during the spike (a touchdown pass is typed
`"Passing Touchdown"`, matching neither `"reception"` nor `"pass complete"`)
moved exactness from 67.2% to 81.8%. The remaining causes are of the same
character.

## Goals

- Play-by-play animation runs on live, in-progress games.
- The animation's data source requires no native dependencies.
- The tablet build is a read-and-review client over ESPN-imported leagues.
- The desktop app keeps every capability it has today.

## Non-goals

- Pushing anything to ESPN. No write path to ESPN exists, and none is proposed.
- Two-way sync between tablet and PC. Everything on the tablet is a re-syncable
  ESPN cache; there is nothing to sync back.
- In-app season leagues on the tablet.
- Projection tuning on the tablet. It is a desktop developer utility.
- Reproducing nflverse's advanced fields (`epa` most notably) from ESPN.

---

# Phase 1 — ESPN play-by-play source

## Architecture

The three simulation services currently read raw nflverse column names
(`yardline_100`, `first_down_pass`, `total_home_score`) straight out of a dict.
That coupling is what makes the source unswappable. The fix is a normalized play
shape behind a provider protocol — deliberately mirroring
`services/projection_sources/`, which already establishes this idiom in the
codebase (a `base.py` protocol, per-source modules, an ordered `registry.py`).

```
                     ┌─ espn_source.py      (requests; live; default)
PlayByPlaySource ────┤
   (base.py)         └─ nflverse_source.py  (pandas; desktop only; fallback)
        │
        ▼
   list[Play]  ──►  player_game_simulation_service
   (normalized)     team_game_simulation_service
                    nfl_game_simulation_service
```

### New package: `services/play_by_play/`

- **`base.py`** — the `Play` dataclass and the `PlayByPlaySource` protocol.
  `Play` carries exactly the fields the animation consumes, named for the domain
  rather than for nflverse's column list: `play_id`, `description`, `play_type`,
  `role`, `yardline_100`, `yards_gained`, `quarter`, `clock`, `down`,
  `distance`, `home_score`, `away_score`, `touchdown`, `first_down`,
  `interception`, `sack`, `complete_pass`, `epa` (`Optional[float]`), and the
  resolved actor ids.

- **`espn_source.py`** — derives `Play` rows from an ESPN summary payload.
  **Reuses `espn_boxscore.BoxScoreClient`**, which already provides
  `week_events(year, week)` and `event_summary(event_id)` and already exists as
  an injectable seam so `live_scoring` can be tested without a network. No new
  HTTP client.

- **`attribution.py`** — play text → ESPN athlete id.

- **`nflverse_source.py`** — thin wrapper over the existing
  `NFLDataService.get_play_by_play`, keeping its lazy `nfl_data_py` import so
  the module is importable without pandas.

- **`registry.py`** — ESPN first, nflverse as fallback. On the tablet build the
  nflverse entry is simply absent.

### Attribution design

The naive form is general name matching. It should not be that — the candidate
set is only the ~65 players in this one game, and the boxscore hands them over
with ids and stat categories. Two rules do the work:

1. **Build an abbreviation index** from the game's boxscore: `Travis Etienne
   Jr.` → `t.etienne` (and the suffix-stripped variant, since ESPN writes both).

2. **Disambiguate by role, not by guessing.** When a token resolves to more than
   one athlete, the play's role decides: the passer on a pass play is the athlete
   in the boxscore's `passing` category. `Caleb Williams` passes; `Chris
   Williams` does not appear there. This is what turns the cascading failure into
   a resolved match.

Two constraints:

- **Attribution resolves against existing rows only — it never creates a
  `DBPlayer`.** Matching is by `DBPlayer.espn_id`. Creating players is
  `PlayerIdentityService.resolve()`'s job and a play-by-play feed is the wrong
  place to be inventing people.
- **An unresolved actor is left null, never guessed.** The animation can draw a
  play whose actor is unknown; it cannot un-draw one attributed to the wrong
  player.

### `epa` has no ESPN equivalent — show nothing, not zero

`epa` is `Optional[float]` and renders as absent when the source cannot supply
it. It must not default to `0.0`: zero is a real, meaningful EPA value, and
substituting it would state something false in the same shape as the truth. This
is the same rule the codebase already applies to a model projection of `0.0`
being "no signal" rather than a projection.

The per-play EPA readout and the running EPA total hide when the source has no
EPA, rather than displaying zeros.

### Caching, and why it earns its place

ESPN summaries are cheap, but the tablet's second stated purpose is *review*,
which implies games that are over and possibly no network. A completed game's
normalized plays are immutable, so they cache safely.

Proposal: persist normalized plays per ESPN event once the game is final, keyed
by `event_id`. In-progress games are always fetched fresh. This gives offline
review on the tablet and removes repeat fetches on the desktop.

This is the one component I would accept dropping from Phase 1 if it proves
awkward — it is additive, and the source works without it.

### Error handling

- No ESPN event for the requested `(year, week, team)` → an empty play list and
  an explicit "no play-by-play available" state. Not an exception, and not a
  silently empty animation.
- ESPN reachable but the payload shape is unrecognized → log, return empty,
  fall through to the nflverse source where it exists.
- A single unparseable play is skipped, not fatal. One malformed play must not
  blank a game.

### Testing

**Fixture-driven, no network in the suite.** Real ESPN summary payloads saved to
`tests/fixtures/espn_pbp/`, including specifically:

- the `Caleb Williams` / `Chris Williams` game, as a named regression fixture for
  ambiguous-initial cascading;
- a game with a QB sack, guarding the negative-rush-yards bug;
- a game with a touchdown pass, guarding the `"Passing Touchdown"` classification.

Tests assert: field derivation per play; attribution rate above a floor;
per-player stat lines reconstructed from parsed plays against the same payload's
boxscore totals (the self-validating check the spike used); and that `epa` is
`None` rather than `0.0`.

The `BoxScoreClient` seam means all of this runs offline.

---

# Phase 2 — Android tablet build

Lighter treatment here on purpose. Packaging carries unknowns that need their own
spike (below), and this phase should get its own spec once Phase 1 lands.

## Scope

Verified route counts, against 129 total. Exclusions are whole route modules —
no surgery inside shared files.

**Decided exclusions** (the user's stated cuts):

| Module | Routes |
|---|---|
| `season` | 10 |
| `projection_tuner` | 20 |

**99 routes kept, 30 dropped.**

**Candidate further exclusions**, following from "lightweight live view and
review, not a workhorse" — each is a live decision, not yet made:

| Module | Routes | Note |
|---|---|---|
| `draft` | 15 | cutting this is what removes `numpy` |
| `odds` | 6 | import-only; needs a paid API key the app does not have |
| `adp` | 3 | draft-support; pointless without `draft` |
| `trades` | 3 | analysis-only, writes nothing |
| `monte_carlo` | 2 | the other `numpy` consumer |

Dropping all five as well leaves **70 routes**: `dashboard` (6), `stats` (14),
`teams` (8), `settings` (9), `players` (6), `weekly_projections` (6),
`applications` (4), `games` (4), `leagues` (4), `visualizations` (4),
`projections` (2), `metrics` (2), `main` (1).

That set covers all three stated tablet features and keeps `settings` — which is
required, since it holds the ESPN credentials the tablet needs to sync itself.

Two consequences worth naming:

- **Dropping season leagues also drops the scheduler.** `season_scheduler` filters
  `DBLeague.kind == "season"`, so with no season leagues its asyncio task is dead
  weight — and with it goes the second-writer-to-SQLite hazard the desktop app
  has to manage.
- **Dropping the mock draft and Monte Carlo would remove the last native
  dependencies.** `numpy` enters only via Monte Carlo, which backs the draft
  board; `matplotlib` only via the `visualizations` router. Cut all three and the
  tablet needs no native wheels at all — pure Python, dramatically simpler
  packaging, far smaller APK, and Chaquopy's wheel repository stops mattering.
  This is the single highest-leverage scope decision in Phase 2 and is listed as
  open below.

## What the tablet still needs from the PC

- **`master_coefficients.json`** ships as a **read-only asset**. Dropping the
  tuner UI does *not* drop the dependency: `get_effective_coefficients()` is
  called by `projection_refresh.py`, `model_source.py`, and `stats.py` — all
  production paths. The tablet reads the tuned values; it never writes them. The
  asset is refreshed when the PC retunes.
- **A seed database snapshot** (currently 14.6 MB) shipped in assets and copied
  into app-private storage on first run, for nflverse-derived data the tablet
  cannot import itself (game logs, advanced metrics, schedules, injuries).

The tablet **can** keep itself current for ESPN data on its own — `espn_api` and
`espn_boxscore` are pure Python and need only network. It is not frozen between
snapshots.

## Packaging

Chaquopy plus a WebView shell, structured as the direct Android analogue of the
existing Electron launcher: pick a free port, start uvicorn on a background
thread, poll health until it answers, show a full-screen WebView. `desktop/main.js`
does exactly this in 139 lines with `src/port.js` and `src/health.js` beside it;
the same three responsibilities move into the Android shell. Reuse the design,
not the code.

Android specifics that differ from desktop:

- `DATABASE_URL` must be an absolute path into app-private storage.
- The OS kills backgrounded apps. `draft_engine` is an in-memory singleton and
  would lose in-progress state far more often than on desktop — a further
  argument for cutting the draft from this build.

## Open questions for Phase 2's own spec

1. **Go pure-Python, or carry native wheels?** Cutting `draft`, `monte_carlo` and
   `visualizations` removes `numpy` and `matplotlib` entirely. This decision
   comes first because it determines whether question 2 matters at all.
2. **Chaquopy specifics, unverified.** Current license terms, maximum supported
   Python version, and whether its wheel repository carries the needed `numpy`
   and `matplotlib` builds. Worth its own short spike before Phase 2 is planned —
   but only if question 1 lands on carrying native wheels.
3. **`visualizations` is provisionally included above**, since it is
   ESPN-derived. Note it is the matplotlib season charts, which are a different
   feature from the play-by-play field views — the field views are the ones the
   tablet is for.

## Risks

| Risk | Mitigation |
|---|---|
| ESPN's API is undocumented and can change without notice | Fixture-based tests detect shape changes; the nflverse source remains as a desktop fallback |
| Residual attribution errors show a play under the wrong player | Unresolved actors render null rather than guessed; boxscore self-validation runs in the test suite |
| `epa` absence degrades the animation's feel | Hidden rather than zeroed; the field data the animation actually needs is complete |
| Chaquopy unknowns block Phase 2 | Phase 1 delivers standalone desktop value regardless of whether Phase 2 proceeds |

## Sequencing

1. **Phase 1** — ESPN play-by-play source, on desktop. Independently valuable:
   it makes the animation live, fast, and available for the 2026 season, none of
   which is true today.
2. **Phase 2** — Android packaging, once Phase 1 is proven and the open questions
   above are answered.
