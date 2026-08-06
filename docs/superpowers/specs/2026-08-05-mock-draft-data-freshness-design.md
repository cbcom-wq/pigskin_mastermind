# Mock Draft Data Freshness & Pool Depth — Design

**Date:** 2026-08-05
**Status:** Approved, ready for implementation planning

## Problem

Three defects in the mock draft, all rooted in how ADP data is imported and stored.

### 1. Players who changed teams show their old team

`ADPService.import_from_ffc()` (`services/adp_service.py`) sets `DBPlayer.nfl_team` only inside
`_create_minimal_player()` — the path taken for players FFC ships that have no matching row. For
every player that *already* exists, the importer updates `bye_week` and the season's ADP fields but
never touches `nfl_team`. Existing rows therefore keep whatever the last ESPN sync wrote, which for
most of the board is the previous season's roster.

Measured against the live FFC 2026 PPR board: **32 of the top 250 players carry the wrong team.**
Examples — A.J. Brown (db `PHI`, actual `NE`), Kenneth Walker III (db `SEA`, actual `KC`),
Travis Etienne Jr. (db `JAX`, actual `NO`), Jaylen Waddle (db `MIA`, actual `DEN`).

A second, independent defect inflates that count: **abbreviation drift.** The DB holds 27 rows
spelled `WSH` (ESPN's convention, written by the vendored `espn_api` library through
`services/espn_sync.py`) and 2 spelled `WAS`. FFC, `nfl_data_py`, and mock_draft's own
`_ESPN_TEAM_MAP` all use `WAS`. `WSH` is the only conflicting abbreviation in the DB — `JAX`, `LAR`,
and `LV` are already canonical.

This has blast radius past the draft: `services/projection_criteria_builder.py` joins players to NFL
team stats by abbreviation in 47 places, so `WSH` rows are likely already failing those lookups
silently.

### 2. No visible data age, and no prompt to refresh

`ADPService.get_adp_metadata()` already returns `last_updated`, and the draft setup page renders an
inline amber "N days old" note past 7 days (`templates/draft/index.html`, `describeFreshness()`).
That note is easy to miss and there is no active prompt. A user can start a draft against
months-old data without noticing.

### 3. The player pool runs dry before the draft ends

The 2026 board in the local DB holds **200 players** (24 QB / 51 RB / 73 WR / 19 TE / 14 K / 19 DEF).
FFC's API caps out around 246 entries regardless of the `teams` parameter — 12-team and 14-team
requests return an identical 246-player list.

A 12-team, 15-round draft is 180 picks. The board is exhausted by construction, and the late rounds
have no realistic lower-end players to choose from.

ESPN's `kona_player_info` endpoint returns **1000 players** for 2026 with correct current teams.
About 789 of them share a placeholder ADP of ~170 (ESPN's "undrafted" default), so ESPN's ADP
ordering is only meaningful for roughly the top 200.

## Goals

- A refreshed draft board reflects current NFL teams.
- The user can see how old the draft data is and is actively prompted to refresh it when stale.
- Late rounds of a mock draft have a realistic supply of lower-end draftable players.

## Non-goals

- Rewriting the projection pipeline's team-abbreviation handling. This design normalizes team
  abbreviations at the ADP and ESPN-sync *write* boundaries only. Auditing the 47 read sites in
  `projection_criteria_builder.py` is a separate change.
- Replacing FFC as the consensus ADP source.
- Any new UI on the draft board itself (no "changed teams" badges).

---

## Design

### Component 1 — `utils/nfl_teams.py` (new)

A single canonical team-abbreviation vocabulary, matching the existing `utils/positions.py` idiom.

```python
NFL_TEAMS: frozenset[str]          # the canonical 32
normalize_team(value) -> Optional[str]
```

`normalize_team()` upper-cases and strips its input, maps known aliases to canonical form, returns
the value unchanged if already canonical, and returns `None` for `FA`, empty strings, `None`, and
any unrecognized abbreviation. Returning `None` rather than raising matches
`positions.normalize_position()`, whose callers treat `None` as "skip this row".

Canonical set (FFC / nflverse convention): `ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND
JAX KC LAC LAR LV MIA MIN NE NO NYG NYJ PHI PIT SEA SF TB TEN WAS`.

Alias map covers at minimum: `WSH→WAS`, `JAC→JAX`, `LA→LAR`, `STL→LAR`, `SD→LAC`, `OAK→LV`,
`ARZ→ARI`, `BLT→BAL`, `CLV→CLE`, `HST→HOU`, `GNB→GB`, `KAN→KC`, `NWE→NE`, `NOR→NO`, `SFO→SF`,
`TAM→TB`, `LVR→LV`.

### Component 2 — Team refresh on import

**Where the write happens.** `normalize_team()` is applied at two write boundaries:

1. `ADPService` — both `_create_minimal_player()` and the new update step below.
2. `services/espn_sync.py` — wherever it assigns `DBPlayer.nfl_team` from the vendored library.

Normalizing in the ADP importer alone is not sufficient: the next ESPN sync would write `WSH` back
and undo the fix. Both boundaries are required for canonicalization to hold.

**The update step.** Team refresh is a shared helper, `ADPService._apply_team_updates(entries)`,
that takes `(resolved_player, source_team)` pairs and returns the list of changes it made. A team is
written only when `normalize_team(new)` is non-`None` and differs from `normalize_team(existing)`;
this prevents a source that returns `FA` for an unsigned player from wiping a good value.

Both importers call it, and **team refresh is decoupled from ADP-row writing**:

- `import_from_ffc()` applies it to every matched player, not just newly-created ones.
- `import_espn_tail()` applies it to **all ~1000 ESPN players**, including those already on the FFC
  board. The FFC-board skip in the tail import governs only whether an `espn_tail` ADP row is
  written — it must not skip the team update, or ESPN would never correct the teams of the top ~250
  players, which is precisely the reported bug.

Authority follows from call order within `refresh_draft_data()`: FFC runs first, ESPN second, so
**ESPN's `proTeamId` wins** where both have an opinion and FFC fills in players ESPN's board does not
return. Both sources agreed on all 32 sampled changes, so this ordering matters for coverage rather
than for conflict resolution.

**One-time backfill.** The first refresh after this change also canonicalizes any pre-existing
non-canonical `nfl_team` values in the `players` table (the 27 `WSH` rows), so rows that neither
source returns are not left stranded on a stale spelling.

**Reporting.** Both importers add `team_changes: list[{name, old, new}]` to their return dicts
alongside the existing `imported` / `created` / `skipped` counts, and `refresh_draft_data()`
concatenates them (de-duplicated by player, since a player corrected by FFC and then confirmed by
ESPN must count once). The `/adp/import/ffc` and `/draft/adp?refresh=true` responses pass the merged
list through, and the refresh toast on the setup page reports `"N players changed teams"`. No
draft-board UI changes.

### Component 3 — Deeper pool via an ESPN tail

**New method:** `ADPService.import_espn_tail(year, limit=1000)`.

It calls the existing `mock_draft.fetch_espn_adp()`, applies `_apply_team_updates()` to **every**
returned player, then — for ADP rows only — skips anyone already carrying an FFC ADP for that season
and persists the remainder as `DBPlayerSeasonStats` rows tagged `adp_source="espn_tail"` (a new
value; `ADP_SOURCE_LABEL` stays `"fantasyfootballcalculator"`).

**Tail ordering.** ESPN's ADP is not usable for the tail — ~789 players are tied at the placeholder
value of 170. The tail is instead ordered by ESPN's `totalRating` projection (already parsed into
`projected_points` by `fetch_espn_adp`) descending, and each player is assigned a synthetic ADP of
`max_ffc_adp + tail_rank`, so:

- every tail player sorts strictly below every FFC-ranked player, and
- the tail's internal order reflects projected value rather than an arbitrary tie-break.

The synthetic value is a sort key, not a claim about real draft position. The distinct
`adp_source` label is what lets consumers tell the two apart.

**Identity.** Every tail player goes through `PlayerIdentityService.resolve()` before a row is
created, per the importer rule in `CLAUDE.md`. This keeps the board's `db_id` links pointing at real
profile pages and prevents a second row for players the ESPN and nfl_data_py importers already
created.

**Read path.** `get_adp_for_draft_pool()` widens its `adp_source` filter from equality against
`ADP_SOURCE_LABEL` to membership in `{ADP_SOURCE_LABEL, "espn_tail"}`, still ordered by `adp`
ascending. `get_all_adp()` keeps its FFC-only filter — the `/adp/rankings` endpoint is a consensus
ADP view, not a draft pool, and synthetic values do not belong there.

Expected result: a pool of roughly 900+ draftable players, against 200 today.

**Combined refresh.** A single `ADPService.refresh_draft_data(year, scoring)` runs
`import_from_ffc()` then `import_espn_tail()` and merges their summaries into one result dict. This
is what the refresh button and the popup both call, so freshness covers both sources with one
action.

### Component 4 — Freshness metadata and popup

**Metadata.** `get_adp_metadata()` extends to cover both sources and to return staleness directly
rather than making each caller recompute it:

```
{"year": int, "last_updated": str|None, "count": int,
 "ffc_count": int, "tail_count": int,
 "age_days": int|None, "stale": bool}
```

`stale` is `True` when `last_updated` is `None` or `age_days > 7`. Putting the threshold in the
service keeps the server and the page from disagreeing about what "stale" means; the existing
client-side `describeFreshness()` is reduced to formatting.

**Popup.** On the draft setup page (`templates/draft/index.html`), after the initial ADP load, a
modal appears when `stale` is true:

> **Draft data is 23 days old**
> Player teams and ADP may be out of date.
> [ Refresh now ]  [ Continue anyway ]

- **Refresh now** calls the combined refresh with an inline progress state, then reloads the ADP
  list and shows the result toast (including the team-change count).
- **Continue anyway** sets a `sessionStorage` flag and hides the modal until the tab is reopened.
- When `last_updated` is `None`, the copy reads "Draft data has never been imported."

The existing inline amber subtitle note is kept as the persistent, non-blocking reminder.

---

## Data flow

```
Refresh (button or popup)
  └─► ADPService.refresh_draft_data(year, scoring)
        ├─► import_from_ffc()
        │     ├─ fetch_ffc_adp()                    ~246 players
        │     ├─ PlayerIdentityService.resolve()
        │     ├─ normalize_team() → update nfl_team, collect team_changes
        │     └─ write DBPlayerSeasonStats.adp, adp_source="fantasyfootballcalculator"
        └─► import_espn_tail()
              ├─ fetch_espn_adp(limit=1000)         ~1000 players
              ├─ PlayerIdentityService.resolve()
              ├─ _apply_team_updates()  ← ALL 1000, incl. the FFC board (ESPN wins)
              ├─ skip anyone with an FFC ADP this season   ← ADP rows only
              └─ write DBPlayerSeasonStats.adp = max_ffc_adp + tail_rank,
                       adp_source="espn_tail"

Draft start
  └─► get_adp_for_draft_pool(year)   → both sources, ordered by adp asc  (~900+ players)
        └─► MockDraftEngine.create_draft(player_pool=...)
```

## Schema

No migration. Both new behaviors reuse existing columns: `DBPlayer.nfl_team` and
`DBPlayerSeasonStats.adp` / `adp_source`. The tail is distinguished by the `adp_source` *value*,
not by a new column.

## Error handling

- ESPN tail fetch failure is **non-fatal**. `refresh_draft_data()` returns the FFC result with a
  `tail_error` key; the user gets a warning toast and a board that is still correct, just shallower.
  A refresh must never leave the pool worse than it started.
- FFC fetch failure keeps today's behavior — `error` key in the result, HTTP 503 from the route.
- `normalize_team()` returning `None` means "leave the existing value alone", never "write `None`".
- Team updates and ADP writes share the existing single `commit()` at the end of the import, so a
  mid-import failure leaves no partially-updated board.

## Testing

All tests live under `tests/` and stub network fetches — no live FFC or ESPN calls.

**`utils/nfl_teams.py`**
- Canonical values pass through unchanged.
- Each alias maps to canonical (`WSH→WAS`, `JAC→JAX`, `LA→LAR`, …).
- Case and surrounding whitespace are handled.
- `FA`, `""`, `None`, and unknown strings return `None`.

**Team refresh**
- An existing player whose stubbed source team differs gets `nfl_team` updated.
- The change appears in `team_changes` with correct `old` / `new`.
- A player whose team is unchanged produces no entry.
- A source returning `FA` does **not** overwrite a real existing team.
- A pre-existing `WSH` row is canonicalized to `WAS` by the backfill.

**ESPN tail**
- Tail players receive ADP strictly greater than the maximum FFC ADP for that season.
- Tail order follows `projected_points` descending, not ESPN's tied ADP.
- A player already on the FFC board gets no `espn_tail` ADP row, **but still receives a team update**
  from the ESPN pass (the regression that would silently reintroduce the reported bug).
- A tail player matching an existing `DBPlayer` reuses that row rather than creating one
  (identity resolution).
- Tail rows are written with `adp_source="espn_tail"`.

**Read path**
- `get_adp_for_draft_pool()` returns both sources, ordered by ADP ascending, FFC players first.
- `get_all_adp()` still returns FFC rows only.

**Metadata**
- `stale` is `False` at exactly 7 days and `True` at 8 (boundary).
- `stale` is `True` and `age_days` is `None` when no data exists.
- `ffc_count` and `tail_count` are reported separately.

**Refresh orchestration**
- A failing ESPN tail still returns a successful FFC result plus `tail_error`.
- A failing FFC fetch returns the existing `error` shape.
- A player corrected by FFC and then confirmed by ESPN appears **once** in the merged
  `team_changes`.
- Where FFC and ESPN disagree on a team, the ESPN value is the one persisted.

## Risks

- **Existing DB is rewritten.** Items 1 and 2 update `nfl_team` on roughly 30–60 rows of the live
  `pigskin_mastermind.db`. Take a fresh backup before the first refresh.
- **Draft length assumptions.** Growing the pool from 200 to ~900 changes what the AI drafters see in
  late rounds. `_DEPTH_CAPS` and the late-round K/DEF logic in `mock_draft.py` were tuned against a
  board that ran out; behavior in rounds 12+ should be sanity-checked after the change.
- **ESPN endpoint stability.** `fetch_espn_adp()` targets an undocumented public endpoint. The
  non-fatal tail failure path is what keeps that from breaking drafts.
