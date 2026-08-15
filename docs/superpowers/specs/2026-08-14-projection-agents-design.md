# Projection Agents — Design

Date: 2026-08-14
Status: Approved, not yet implemented

Two Claude Code agents that use the Pigskin Mastermind backend: one that researches a single player
and produces a projection, and one that diagnoses and tunes the projection algorithm wholesale.
Both are invocable from a Claude Code session today and from the web UI via a thin `claude -p`
subprocess.

---

## Motivation

The deterministic projection pipeline (`ProjectionCriteriaBuilder` → `ProjectionService`) is good at
what it can see: game logs, season stats, snap share, opponent defense rank. It is blind to
everything else — a coaching change, a camp report, a depth-chart move, a player whose sample is too
small for the criteria to mean anything. It is also tuned by a human running sweeps by hand.

Two gaps, two agents:

1. **`player-analyst`** — deep-dives one player and emits its own projection, stored as a new source
   alongside `model` / `espn` / `sportsbook`, with a written rationale and citations.
2. **`projection-tuner`** — diagnoses where the model is systematically wrong, runs narrow
   hypothesis-driven coefficient sweeps with a held-out split, and proposes (never applies) a new
   master coefficient set.

## Decisions

| Question | Decision |
|---|---|
| Entry point | CLI-first. Agents + `pigskin agent` commands work standalone; a thin FastAPI route shells out to `claude -p` later. The CLI is the contract both paths share. |
| Analyst output | Its own point estimate, written to `player_projections` as `source='llm'`. |
| Data access | One evidence-pack CLI command emitting a single JSON document. Not an MCP server, not ad-hoc REST exploration. |
| Web access | `--web/--no-web`, default on for current-season runs, forced off for backtests. Non-DB claims require a URL. |
| Tuner authority | Coefficients only. Never edits Python. Promotion to `master_coefficients.json` requires an out-of-sample win **and** a human running the apply command. |
| Blend wiring | Display-only for now. `blend` and the draft pool ignore `source='llm'`. |

### Two pre-existing problems this design must fix

**`ProjectionAlgorithmTuner.run()` selects in-sample.** It evaluates up to 500 coefficient
variations against `_gather_samples(...)` and returns the lowest-MAE one — scored on the same
samples it was chosen from. Overfitting is therefore unmeasured. A human running this occasionally
gets away with it; an agent looping on it would amplify it into confident nonsense. The design adds
a train/validate split and makes the validate metric the only one the promotion gate reads.

**`generate_analysis_report()` diffs against hardcoded defaults, not the incumbent master.** Once
`~/.pigskin_mastermind/master_coefficients.json` exists, "improvement vs default" is not the number
that matters. The proposal path diffs against `get_effective_coefficients()`.

---

## Architecture

```
                 ┌─────────────────────────────────────────────┐
                 │  .claude/agents/                            │
                 │    player-analyst.md                        │
                 │    projection-tuner.md                      │
                 │  .claude/skills/                            │
                 │    projection-evidence/                     │
                 │    coefficient-tuning/                      │
                 └──────────────────┬──────────────────────────┘
                                    │ Bash
                                    ▼
                 ┌─────────────────────────────────────────────┐
                 │  pigskin agent …   (Click, cli.py)          │
                 │    evidence | record-projection | score     │
                 │    tune-report | tune-sweep | tune-propose  │
                 │  pigskin coefficients apply   (human only)  │
                 └──────────────────┬──────────────────────────┘
                                    │
                 ┌──────────────────┴──────────────────────────┐
                 │  services/agent_evidence.py                 │
                 │  services/agent_projection.py               │
                 │  services/agent_tuning.py                   │
                 │  (existing: criteria builder, tuners,       │
                 │   master_coefficients, projection_refresh)  │
                 └─────────────────────────────────────────────┘
                                    ▲
                                    │ subprocess `claude -p`
                 ┌──────────────────┴──────────────────────────┐
                 │  services/agent_runner.py + API route       │
                 └─────────────────────────────────────────────┘
```

Four layers. Only the middle two are ordinary Python and carry the test burden. The agent and skill
markdown is prompt-shaped and reviewed by eye; the invoker is thin.

### Why skills, not just agent prompts

The know-how — what each criterion means, what a defensible analysis looks like, the tuning loop,
the promotion rules — lives in `.claude/skills/`, and the agent definitions stay thin role
statements that point at them. Two reasons: the same procedure is then usable in an ordinary Claude
Code session without dispatching an agent, and the procedure versions independently of the role.

---

## Component 1: evidence pack

`services/agent_evidence.py` → `build_evidence(db, player_id, year, week=None, as_of_week=None)`
returning a plain dict. CLI: `pigskin agent evidence --player-id N --year Y [--week W] [--as-of W]`.

Blocks:

| Block | Contents |
|---|---|
| `player` | Name, position, NFL team, age, years exp, resolved IDs (`espn_id`/`gsis_id`/`pfr_id`) |
| `context` | `year`, `week`, scope (`season` \| `weekly`), opponent, opponent positional defense ranks |
| `season_stats` | Last 3 years of `DBPlayerSeasonStats` |
| `game_logs` | Recent `DBPlayerGameLog` rows |
| `criteria` | Full field dump of the `WeeklyProjectionCriteria` / `YearlyProjectionCriteria` built for this scope |
| `existing_projections` | Every `player_projections` row for the scope, by source, with `computed_at` |
| `sportsbook` | Props for the week, when present |
| `schedule` | Remaining opponents |
| `data_freshness` | Last stat sync, last ADP refresh, per-source `computed_at`, and the current date |

`data_freshness` is not decoration. It is how the agent decides whether web search is worth its cost
— the block tells it exactly where the database is blind.

`as_of_week=W` truncates game logs and derived criteria to weeks strictly before `W`. This exists so
weekly backtests are structurally sound.

**Reused, not reimplemented:** criteria come from `ProjectionCriteriaBuilder`, projections from
`projection_refresh.get_projection()`, defense ranks from the existing stats path. The evidence
builder is an assembler.

**Caution:** `ProjectionCriteriaBuilder` lazily populates missing team/defense stat rows as a side
effect. The evidence builder inherits that write behavior. It must therefore not be treated as
read-only, and its transaction must be committed and closed before any subprocess boundary.

## Component 2: recording a result

Agent result JSON:

```json
{
  "player_id": 123,
  "year": 2026,
  "week": null,
  "projected_points": 244.5,
  "floor": 188.0,
  "ceiling": 301.0,
  "expected_games": 16.2,
  "confidence": "low | medium | high",
  "rationale": "prose, a few paragraphs",
  "key_factors": [
    {"factor": "…", "direction": "+", "magnitude_pts": 8.0,
     "source": "db | web", "url": "https://…"}
  ],
  "disagreement_with_model": "why this differs from the model projection",
  "web_used": true,
  "evidence_hash": "sha256 of the evidence pack this was derived from"
}
```

`pigskin agent record-projection --result-file out.json` writes a `source='llm'` row.
`projected_points` / `floor` / `ceiling` / `expected_games` go to their columns; everything else
goes to `components`. No migration is needed — `components` is already a JSON column.

**Validation at the write boundary.** This is the hallucination tripwire, and it is Python, not a
prompt instruction:

1. `floor <= projected_points <= ceiling`.
2. `projected_points` inside a sanity band. Concretely: non-negative, and within `[0.25x, 3.0x]` of
   the existing `model` season projection for the same player/year when one exists. When no model
   row exists, fall back to a per-position absolute ceiling (season scope: QB 600, RB 500, WR 500,
   TE 400, K 250, DEF 250; weekly scope: one-tenth of those). These are deliberately loose — the
   rule catches a decimal-place or unit error, not a debatable opinion.
3. Any `key_factor` with `"source": "web"` must carry a non-empty `url`.
4. `web_used: true` is rejected when the run was launched `--no-web`.
5. `player_id` must exist; `year`/`week` must match what was requested.

Rejection is a non-zero exit with the reason on stderr, so the agent sees it and can correct rather
than silently writing garbage.

Re-running the same player/scope updates the existing row. The partial unique index
`uq_player_projection_season` enforces one season row per player per source; weekly rows are covered
by `uq_player_projection`.

`evidence_hash` ties a stored projection to the exact evidence it came from, which is what makes a
disagreement between two runs diagnosable.

## Component 3: `player-analyst` agent

`.claude/agents/player-analyst.md`

- **Tools:** `Bash`, `Read`, `Write`, `WebSearch`, `WebFetch`, `Skill`. No `Edit`. `Write` is for the
  scratch result file only — it never modifies repo files.
- **Model:** sonnet by default; overridable for a deeper single-player pass.

Procedure (in the `projection-evidence` skill):

1. Run the evidence pack for the requested scope.
2. Read `data_freshness`; decide whether a web lookup is warranted at all.
3. If web is enabled: search role / injury / depth-chart / coaching news scoped to the current
   season. Every claim that moves the number carries a URL.
4. Project. The skill's central instruction is to reason explicitly about **where the formula is
   likely wrong for this specific player** — a sample too small for the criteria to mean anything, a
   mid-season role change, a non-linear usage shift, a criterion pinned at its default because the
   underlying data is missing. That framing is what makes the output additive instead of a worse
   re-derivation of the model.
5. Write the result JSON to scratch and call `record-projection`.
6. Return a short summary: the number, the two or three factors that moved it, and the size of the
   disagreement with the model.

### Evaluating it honestly

A 2024 backtest is contaminated — the model may simply know how the season ended, and web search
would make it worse. `--as-of` truncation makes weekly backtests *structurally* sound but does not
remove training-data leakage. Backtest results are therefore treated as a smoke test, not evidence.

The real evaluation is prospective. `pigskin agent score --year Y --week W` compares stored
`source='llm'` rows against actuals alongside `model` / `espn` / `sportsbook`, accumulating a track
record over real weeks. That record — not a backtest — is what would later justify wiring `llm` into
the blend.

## Component 4: `projection-tuner` agent

`.claude/agents/projection-tuner.md`

- **Tools:** `Bash`, `Read`, `Grep`, `Glob`, `Write` (scratch only), `Skill`. No `Edit`.
- **Model:** opus.

### New commands

**`pigskin agent tune-report --year Y [--weeks …] [--positions …]`**

Residual diagnostics the current tuner does not produce. Reports **signed bias** as well as MAE and
RMSE, split by:

- position
- week bucket (early / mid / late season)
- projection decile (are we wrong at the top of the board or the bottom?)
- sample-size bucket (established vs. low-data players)

Signed bias is the point — MAE alone tells the agent it is wrong but not which direction to push a
coefficient.

**`pigskin agent tune-sweep --year Y --train-weeks 1-13 --validate-weeks 14-18 [--positions RB] [--coefficients k1,k2] [--values …]`**

A narrow, hypothesis-driven sweep rather than the 500-wide grid. Selection happens on train samples
only; the reported improvement is measured on validate samples. Writes a run file through the
existing results-dir mechanism.

Implementation: add an `evaluate_on(coeffs, samples)` seam to `ProjectionAlgorithmTuner` reusing
`_gather_samples` and `_evaluate`, then split the gathered samples by week before selection. The
existing `run()` / `run_per_position()` signatures stay as they are; the split is opt-in so nothing
already calling them changes behavior.

**`pigskin agent tune-propose --run-id X`**

Emits a proposal JSON plus a human-readable diff against the **incumbent master**
(`get_effective_coefficients()`), not against hardcoded defaults.

The promotion gate is enforced here, in Python. Starting thresholds, all overridable by flag:

1. Validate MAE must beat the incumbent by **≥ 1.0%** relative.
2. No position may regress on validate MAE by more than **2.0%** relative.
3. Every position included in the proposal must have **≥ 200 validate samples**; positions below
   that are dropped from the proposal rather than promoted on thin evidence.

Gate failure marks the proposal `rejected` with the specific reason. The agent is instructed to
report the negative result plainly rather than reframe it as a win.

**`pigskin coefficients apply --proposal X`**

The only caller of `save_master_coefficients()`, filling `source_run_id` and `source_description`
from the proposal. **Deliberately excluded from the agent's allowed commands** — a human runs this.

### The loop

`tune-report` → form a hypothesis about which coefficients are implicated → `tune-sweep` narrowly →
read the validate result → iterate or `tune-propose` → hand the proposal to the human.

## Component 5: app invocation

`services/agent_runner.py` → `run_agent(agent_name, prompt, *, timeout)`, subprocessing
`claude -p "<prompt>" --agent <name> --output-format json`. It handles the four real failure modes:
`claude` not on PATH, timeout, non-zero exit, unparseable stdout. Raw stdout is retained on failure.

Route shape: `POST /api/players/{id}/agent-projection` starts a background job and returns a job id;
`GET /api/agent-jobs/{job_id}` polls; an HTMX fragment on the player page polls that. A run takes
30–90 seconds, so a synchronous request would time out.

Two hazards, both precedented in this repo:

- The job registry is module-level in-memory state, exactly like `mock_draft.draft_engine`. Same
  constraint: **do not run `uvicorn --workers > 1`** and expect jobs to be visible.
- The agent subprocess opens its own connection to the same SQLite file. If the FastAPI handler
  holds a write transaction while waiting, the result is `database is locked` — the two-writer
  problem CLAUDE.md already flags for the desktop app. The handler must hold no session while
  waiting, and the subprocess writes through `record-projection` as one short transaction.

Throttle: refuse to launch when a completed run for the same player and scope exists within the last
**15 minutes** (overridable by a `force` flag), and cap concurrent runs at **2**.

---

## Testing

The JSON contract is the seam. Everything below it is deterministic and tested; everything above it
is prompt-shaped and reviewed by eye. **No test invokes a real LLM.**

- **Evidence builder** against a seeded in-memory DB: every block present, freshness timestamps
  correct, and `--as-of W` genuinely excludes data from week `W` onward. That last test is what
  backs the backtest-honesty claim.
- **`record-projection` validation**: table-driven cases for each of the five rejection rules, the
  happy path, and re-running the same player/scope (must update, not duplicate — exercises the
  partial unique index).
- **Held-out split**: construct samples where the in-sample winner is a validate loser, then assert
  the reported metric is the validate one. Without this test the split fix is unverified.
- **`tune-propose` gate**: table-driven pass and reject cases, including the per-position regression
  rule and the minimum-sample rule.
- **`agent_runner`** with subprocess mocked: missing binary, timeout, non-zero exit, garbage stdout,
  success.
- **Fixtures**: a canned evidence pack and a canned agent result checked into `tests/fixtures/`, so
  contract drift is caught by a failing test rather than discovered in a live run.

Run scoped, as always: `pytest tests/`.

## Delivery order

This is more than one sitting's work. The phases below are separable — each ends somewhere useful,
and each can be its own implementation plan.

1. **Evidence + record.** `agent_evidence.py`, `agent_projection.py`, `pigskin agent evidence` and
   `record-projection`, with tests. Useful on its own: you can hand the evidence pack to any Claude
   Code session by hand.
2. **`player-analyst` agent + `projection-evidence` skill.** Now the analysis runs end to end from a
   terminal. Add `pigskin agent score` here so the track record starts accumulating immediately.
3. **Tuner split + diagnostics.** `evaluate_on()`, the train/validate split, `tune-report`,
   `tune-sweep`, `tune-propose`, `coefficients apply`. This phase is valuable even without an agent
   — it fixes the in-sample selection bug for human use too.
4. **`projection-tuner` agent + `coefficient-tuning` skill.**
5. **App invocation.** `agent_runner.py`, the job route, the HTMX fragment on the player page.

Phase 3 has no dependency on phases 1–2 and could go first if the tuning bug is the more pressing
itch.

## Out of scope

Stated so it does not creep in:

- An MCP server. The CLI is the tool surface.
- Wiring `source='llm'` into `blend` or the draft pool.
- The agent editing Python — including the tuner. Formula changes stay human work.
- Agent calls during a live mock draft.
- Any multi-user, hosted, or auth story.
