# The evidence pack, block by block

What `pigskin agent evidence` emits, what each block is good for, and what not to trust about it.

```bash
.venv/Scripts/python -m pigskin_mastermind.cli agent evidence \
  --player-id 123 --year 2026 [--week 5] [--as-of 5] > evidence.json
```

`--player-id` is `DBPlayer.id`, the integer primary key — **not** the prefixed string
(`espn_4431452`) that the `player.player_id` field reports back. `--year` defaults to the current
fantasy season. Omit `--week` for season scope. The command writes to the database as a side effect
(the criteria builder lazily creates missing team/defense stat rows), so it must not run while the
desktop app or a dev server holds the same SQLite file.

The document always has exactly these **16 top-level keys**, in this order:

| Key | Type | Present when |
|---|---|---|
| `player` | object | always |
| `context` | object | always |
| `season_stats` | array | always (may be `[]`) |
| `season_stats_filtered_reason` | string \| null | non-null only under `--as-of` |
| `game_logs` | array | always (may be `[]`) |
| `criteria` | object \| null | `null` under `--as-of` |
| `criteria_omitted_reason` | string \| null | non-null only under `--as-of` |
| `existing_projections` | object | always (may be `{}`) |
| `existing_projections_filtered_reason` | string \| null | non-null only under `--as-of` |
| `schedule` | array | always (may be `[]`) |
| `sportsbook` | object \| null | `null` under `--as-of`, and `null` when no props are stored |
| `sportsbook_omitted_reason` | string \| null | non-null only under `--as-of` |
| `news` | array | always (may be `[]`) |
| `news_filtered_reason` | string \| null | non-null only under `--as-of` |
| `data_freshness` | object | always |
| `evidence_hash` | string | always |

A `null` block and a `null` reason are different statements. `sportsbook: null` with
`sportsbook_omitted_reason: null` means "no props are stored for this player". `sportsbook: null`
with a non-null reason means "props exist but were withheld because you asked for a cutoff".

---

## `player`

Fifteen fields: `db_id`, `player_id` (the prefixed string), `name`, `position`, `nfl_team`, `age`,
`years_exp`, `college`, `draft_number`, `bye_week`, `injury_status`, `injured`, `espn_id`,
`gsis_id`, `pfr_id`.

**Good for:** confirming you are analysing the person you were asked about. Do this first, every
time. Three importers create player rows and duplicates have historically survived identity
resolution, so a numeric id landing on the wrong human is a real failure mode, not a hypothetical.

**Do not trust:**

- **Sparsity.** `age`, `years_exp`, `college`, `draft_number`, and `injury_status` are frequently
  `null` — they are only populated by importers that carry them. In the reference sample (player
  123, Drake Maye) all five are `null`. A `null` `age` is not cosmetic: the yearly criteria compute
  `age_deviation_from_optimum = (age - peak_age) if age else 0.0`, so a missing age produces the
  same `0.0` a perfectly-aged player produces. See "defaults are not measurements" below.
- **It is always current-state.** Every field here is read live. `--as-of` does not touch this
  block, so a backtest of week 3 sees today's `injury_status`, today's `nfl_team`, and today's
  `bye_week`.
- `nfl_team` being current-state also poisons downstream blocks for a traded player: `schedule` and
  the `--as-of` cutoff resolution both look up games by the *current* team.

## `context`

`year`, `week`, `scope` (`"season"` when `week` is null, `"weekly"` otherwise), `as_of_week`,
`as_of_cutoff_at`.

**Good for:** telling a live run from a backtest without parsing prose. Read `as_of_week` and
`as_of_cutoff_at` **before** you read any `*_reason` string:

- `as_of_week: null` — live run, nothing was withheld.
- `as_of_week: 5`, `as_of_cutoff_at: "2025-10-05T20:20:00"` — cutoff requested and resolved to that
  kickoff. Timestamp-based filtering engaged.
- `as_of_week: 5`, `as_of_cutoff_at: null` — cutoff requested but **no kickoff is on file for that
  week**. The five unconditional truncations still happened; `existing_projections` and `news` were
  served *completely unfiltered*. The document is not isolated. Say so in the rationale.

## `season_stats`

Up to the **3 most recent seasons with `year <= ` the requested year**, newest first. 24 fields per
row: `year`, `games_played`, the passing/rushing/receiving counting stats, `fantasy_points_total`,
`fantasy_points_avg`, `fantasy_points_per_touch`, `snap_pct`, `air_yards`, `yac`, `wopr`, `adp`,
`adp_source`, `adp_times_drafted`, `source`.

**Good for:** the multi-season shape of a career, and the market's opinion (`adp`) on a player
whose stats the model has not caught up with.

**Do not trust:**

- **`snap_pct` is 0–100, not 0–1.** It is also routinely `null` for ESPN-sourced rows.
- **`games_played` can exceed the games actually played.** In the reference sample the 2025 row
  reports `games_played: 18` in a 17-game season, because a bye week is stored as a 0.0-point game
  log and counted. `fantasy_points_avg` divides by that number: 413.46 / 18 = 22.97, where the
  average over the 17 real games is 24.32. That is a 5.6% understatement propagating straight into
  the baseline. Cross-check `games_played` against the `game_logs` array and the `schedule` gap.
- **A current-season row can exist and be empty.** Before week 1 the requested year's row is
  present with every counting stat at `0` and `fantasy_points_avg: 0.0`, but a real `adp`. "A row
  exists" is not "there is data".
- `source` says where the row came from (`espn`, `game_log_aggregation`, …). Rows from different
  sources are not necessarily computed the same way.
- Under `--as-of` the **requested year's row is dropped entirely** — an aggregate cannot be
  partially truncated, and its `fantasy_points_avg` is close to the answer for any single week
  inside it. Prior seasons are unaffected. This drop is unconditional; it does not depend on a
  kickoff resolving.

## `season_stats_filtered_reason`

`null` on a live run. Under `--as-of`, a fixed string explaining the target-year drop above.
`_filtered_` means the block is **present but truncated**, which is the correct word here: prior
seasons are still served.

## `game_logs`

Up to the **34 most recent rows with `year <= ` the requested year**, returned in chronological
(oldest-first) order. 17 fields: `year`, `week`, `opponent`, counting stats, `fumbles_lost`,
`fantasy_points`, `source`.

**Good for:** the single most valuable thing in the pack — week-by-week production, from which you
can see the things a season aggregate averages away: a mid-season role change, a hot streak that is
three games of one number and fourteen of another, a zero.

**Do not trust:**

- **`opponent` is frequently `null`.** In the reference sample all 18 rows have `opponent: null`, so
  no opponent-adjusted split can be computed from this block. Do not claim one.
- **A 0.0 row may be a bye, not a bad game — and `schedule` usually cannot tell you which.**
  `schedule` is scoped to the **requested year only**, while `game_logs` in a preseason pack are all
  from prior seasons. The two do not overlap, so "a log week missing from `schedule`" is a false
  test on exactly the pack where you would most want it. Use the row count instead: more than 17
  rows for one season, or a `season_stats.games_played` above 17, means at least one row is not a
  real game, and the 0.0 row is the candidate. In the reference sample, 2025 has 18 game logs and
  `games_played: 18` in a 17-game season, with week 14 at 0.0. Confirming a *prior* season's bye
  needs a second call — `agent evidence --player-id N --year <prior>` returns that season's
  schedule, where the bye is the missing week number.
- **The 34 is a row cap, not a season cap.** Two full seasons is 34–36 rows, so a two-season window
  can be silently clipped at the old end. Count the rows before saying "two seasons of data".
- Under `--as-of` only the target season is truncated (to `week < as_of_week`); prior seasons stay
  whole.

## `criteria`

`{"scope": "season" | "weekly", "fields": {...}}`. **This is the most useful block in the pack**: it
is exactly what the deterministic model sees. Everything the model's number is made of is here, so
this is where you find out whether the formula's inputs mean anything for this player.

`scope` is `"season"` (the builder internally calls it "yearly") or `"weekly"`, matching
`context.scope`.

### The eight base fields (both scopes)

| Field | Scale | Notes |
|---|---|---|
| `historical_average_points` | fantasy points **per game** | The anchor. Already shrunk toward a positional prior with k = 4 games, and toward what ADP implies when there is no usable history. |
| `player_skill_level` | 0–100 | Weighted composite: points-per-game percentile 40%, efficiency percentile 20%, consistency (inverse coefficient of variation) 20%, volume percentile 20%. Peers are same-position players with ≥ 4 games. **Not adjusted for sample size.** |
| `team_offense_level` | 0–100 | Percentile rank of team points scored per game, with a three-tier data-source fallback. **Not adjusted for sample size.** |
| `opponent_defense_level` | 0–100 | **Higher = worse defense = better for the player.** For season scope this is strength of the *upcoming* schedule. For weekly scope it is a linear restatement of `opposing_defense_vs_position_rank` and the weekly formula deliberately does **not** score it, to avoid counting the matchup twice. |
| `positional_touch_percentage` | 0–100 | Share of the same-position team pool: QB share of team pass attempts, RB share of carries+targets, WR/TE share of the combined WR+TE target pool. **Not shrunk.** |
| `recent_trend_score` | −100 to 100 | Season scope: percent change in per-game scoring between the last two seasons. Weekly scope: within-season recent form. |
| `fantasy_points_per_touch` | points per touch | Position-aware denominator. **Not shrunk.** |
| `injury_risk_score` | 0–100, **lower is better** | From injury status plus historical availability. |

### Season-scope-only fields (3 more, 11 total)

| Field | Scale | Notes |
|---|---|---|
| `age_deviation_from_optimum` | −10 to 10, 0 = peak | **`0.0` also means "age unknown".** |
| `coaching_stability_score` | 0–100 | **Hardcoded to `50.0`. Manual override only — it is never measured.** |
| `expected_games` | 0–17 | Availability. The season projection is a per-game rate multiplied by this. |

### Weekly-scope-only fields (5 more, 13 total)

| Field | Scale | Notes |
|---|---|---|
| `opposing_defense_vs_position_rank` | 1–32 | **1 = the best defense, 32 = the worst — so a *high* rank is a *good* matchup.** Defaults to 16 when the opponent or the defensive row cannot be resolved. |
| `offensive_momentum_score` | −100 to 100 | Team's recent scoring momentum over the prior 4 weeks. |
| `weather_impact_score` | −100 to 100 | **Hardcoded to `0.0`. Never measured.** |
| `home_field` | 1.0 home, −1.0 road, **0.0 = unknown** | `0.0` is "no schedule data", not "neutral site". |
| `is_available` | bool | `false` means bye / OUT / IR — a hard zero, not a risk adjustment. |

### What not to trust about `criteria`

- **A default is not a measurement.** `recent_trend_score: 0.0`, `coaching_stability_score: 50.0`,
  `age_deviation_from_optimum: 0.0`, `offensive_momentum_score: 0.0`, `weather_impact_score: 0.0`,
  `home_field: 0.0`, and `opposing_defense_vs_position_rank: 16` are all both a legitimate measured
  value *and* the value the builder emits when the underlying data is missing. The block carries no
  flag distinguishing the two. Two of them — `coaching_stability_score` and `weather_impact_score` —
  are hardcoded constants and are *always* an absence.
- **Season-scope criteria describe the previous season.** `build_yearly_criteria` computes
  `player_skill_level`, `positional_touch_percentage`, `fantasy_points_per_touch`, and
  `team_offense_level` from `year - 1` stats. A 2026 pack's criteria are a description of 2025.
- **No sample size is reported.** Nothing in `fields` says how many games backed
  `player_skill_level` or `positional_touch_percentage`. Get that from `game_logs` and
  `season_stats.games_played` yourself. Only `historical_average_points` is shrunk for small
  samples; every other field is computed from whatever games exist without any sample-size
  adjustment, so a four-game sample produces a number that reads exactly as confidently as a
  seventeen-game one.
- **The coefficients are not in the pack**, and no CLI command the analyst runs exposes them. You
  can decompose the model number into "baseline versus total adjustment"
  (`projected_points / expected_games` compared against `historical_average_points`), but you
  **cannot** attribute the gap to individual terms. Do not invent that attribution.
- Under `--as-of` this block is `null`, which removes the **defense-quality** signal entirely:
  `opposing_defense_vs_position_rank` and `opponent_defense_level` live only here. `schedule` still
  runs under a cutoff, so opponent identity, home/away, and roof remain available — what you lose is
  any measure of how good that opponent is.

## `criteria_omitted_reason`

`null` on a live run. Under `--as-of`, a fixed string explaining the withholding. `_omitted_` (not
`_filtered_`) because the block is absent entirely rather than truncated.

## `existing_projections`

An object keyed by source, each with `projected_points`, `floor`, `ceiling`, `std_dev`,
`expected_games`, `computed_at`. Only four writers exist, so the sources you can actually see are
`model` and `blend` (both from `pigskin projections refresh`), `espn` (from the ADP service's ESPN
board import), and `llm` (your own output). `sportsbook` and `adp` appear in the blender's weight
tables but nothing writes a projection row under either name. Season-scope values are **season
totals**, not per-game rates. `{}` when nothing is stored for the scope.

**Good for:** knowing what you are disagreeing with, and by how much. The `model` row is also what
the record-projection sanity band is computed against.

**Do not trust:**

- **`blend` equalling `model` is not corroboration.** At season scope `blend` is a renormalized
  weighted average of `model` (0.50) and `espn` (0.30) only — ADP carries a weight in the table but
  is deliberately excluded, because it is already folded into the model's own baseline. When no
  `espn` row exists the weights renormalize to model-only and `blend` comes out exactly equal to
  `model`. Two identical numbers here usually mean one source, not two agreeing ones.
- `llm` is **your own previous output**. It is excluded from `evidence_hash` on purpose, so
  re-running `agent evidence` after recording a projection does not change the hash. Never treat it
  as independent evidence.
- `computed_at` can be months old. Check it against `data_freshness`.
- A weekly-scope request usually returns `{}` — the refresh pipeline populates season rows.

## `existing_projections_filtered_reason`

`null` on a live run. Under `--as-of` it takes one of **two** forms, and the difference matters:

- **Filtered** — the string names the resolved cutoff timestamp. Rows with
  `computed_at >= cutoff` were withheld (a later `pigskin projections refresh` run can regenerate a
  row with the whole season on the board, which would hand a backtest hindsight).
- **Unresolved** — the string says `as_of_week=N was requested, but no kickoff is on file for that
  week`. Rows were served **unfiltered**. Any `model` number you see may postdate the week you are
  pretending to stand in.

Rows with a `null` `computed_at` are kept in both cases — there is nothing to compare.

## `schedule`

One entry per game, ordered by week: `week`, `opponent`, `home` (bool), `played` (bool), `roof`.
Season scope returns the whole season; weekly scope filters to `week >= ` the requested week.

**Good for:** remaining opponents, home/away, dome-vs-outdoors, and locating the requested year's
bye. It is also the one block that survives `--as-of` intact enough to tell you *who* the opponent
is when `criteria` has been withheld.

**Do not trust:**

- **It covers the requested year only** (`DBNFLGame.year == year`), while `game_logs` and
  `season_stats` reach back three seasons. In a preseason pack the two do not overlap at all, so
  never cross-reference a prior season's game logs against this block. Getting a prior season's
  schedule takes a separate `agent evidence --year <prior>` call.
- **The bye is a gap, not a row.** The reference sample's 2026 schedule runs
  `[1..10, 12..18]` — the missing 11 is the 2026 bye, and it matches `player.bye_week`. It says
  nothing about where the bye fell in 2025.
- **Past seasons include the postseason.** The 2025 weekly sample returns weeks 5–22; weeks 19–22
  are playoff games, not fantasy weeks.
- `played` is derived from a non-null home score. Under `--as-of`, every game at or after the cutoff
  week is forced to `played: false` regardless of the stored score.
- The lookup is by the player's **current** team, so a traded player's schedule is their new team's.

## `sportsbook`

`null`, or `{"total_projected_points": ..., "categories": [...]}` derived from stored props.

**Good for:** an independent, market-priced view when props exist.

**Do not trust:**

- **There is no year or week filter at all**, cutoff or not. The odds table keeps no as-of history,
  so a `--week 3` request run during week 8 returns whatever props are currently stored — in
  practice, week 8's. Never assume the props belong to the week you asked about.
- Props are matched by a name `ILIKE` against the prop description. A common surname can match the
  wrong player.
- `null` is deliberate — it distinguishes "no props available" from "props say zero".

## `sportsbook_omitted_reason`

`null` on a live run. Under `--as-of`, a fixed string: books price upcoming games, so a stored prop
under a cutoff describes a game that had not been played yet or a different season entirely.

## `news`

Up to **10** cached ESPN headlines, newest `published_at` first: `headline`, `description`,
`source_url`, `published_at`.

**Good for:** a cheap first look at role and injury chatter before spending a web search.

**Do not trust:**

- **This reads the cache and never fetches.** `agent evidence` stays offline and deterministic on
  purpose.
- **An empty array is ambiguous.** It means either "no news for this player" or "news has never been
  fetched for this player". `data_freshness.news_fetched_at` disambiguates: `null` there means never
  fetched. In the reference sample both are so — `news: []` with `news_fetched_at: null`, which is
  an absence of *fetching*, not an absence of news.

## `news_filtered_reason`

`null` on a live run. Under `--as-of`, the same two-outcome shape as
`existing_projections_filtered_reason`: either headlines at or after the resolved kickoff were
withheld, or the cutoff was unresolvable and headlines were served unfiltered. Rows with a `null`
`published_at` are kept either way.

## `data_freshness`

`generated_at` (wall clock, UTC), `game_logs_updated_at`, `season_stats_updated_at`,
`adp_updated_at`, `news_fetched_at`.

**This block exists to answer one question: is a web lookup worth its cost?** It is the only part of
the pack that tells you where the database is *blind* rather than what it contains. Without it you
cannot distinguish "this player has no recent news" from "nobody has synced anything since March".

Read it as a decision, not as trivia:

- Stats synced within a day or two and no games have been played since — the database is current.
  Web search buys little; skip it or keep it to one targeted query.
- Stats weeks or months stale, or `generated_at` is in-season and `game_logs_updated_at` predates
  last weekend — the database cannot know about games already played. Search.
- `news_fetched_at: null` — the `news` block's emptiness carries no information. If news matters
  here, the web is the only way to get it.
- Preseason with `adp_updated_at: null` — normal. ADP is a per-season value and the filter is
  `year == ` the requested year on purpose, so a prior season's ADP timestamp is never reported
  here as if it were fresh.

**Do not trust:** this block is always current-state and ignores `--as-of` entirely. Its
`generated_at` alone can reveal that the season you are "backtesting" is long over.

## `evidence_hash`

A 64-character lowercase SHA-256 hex digest of the document's data.

Excluded from the hash: `evidence_hash` itself, `data_freshness.generated_at`, and the `llm` entry
inside `existing_projections`. The first two change on every call; the third is the agent's own
prior output, and hashing it would make a re-run look like the data moved when nothing did.

**Same hash means the agent changed its mind. Different hash means the data moved.** That
distinction is the entire point, and it only survives if the hash you record is the one the pack
actually printed.
