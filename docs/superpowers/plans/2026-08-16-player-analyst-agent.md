# Player Analyst Agent & Projection Evidence Skill — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Phase 1 backend usable end to end — a Claude Code agent that researches one player, produces a defensible projection, and stores it; plus the scoring command that measures whether those projections are any good.

**Architecture:** One new Python module and CLI command (`pigskin agent score`), one skill holding the durable know-how, one thin agent definition pointing at it. The skill — not the agent prompt — is where the procedure lives, so the same method is usable in an ordinary Claude Code session and versions independently of the role.

**Tech Stack:** Python 3, SQLAlchemy 2.x, Click, pytest. Agent and skill are Markdown.

**Spec:** [docs/superpowers/specs/2026-08-14-projection-agents-design.md](../specs/2026-08-14-projection-agents-design.md) — read its "What Phase 1 learned that Phase 2 must act on" section before Task 2.

## Global Constraints

- **Always scope pytest to `tests/`** — bare `pytest` dies collecting the vendored `src/pigskin_mastermind/lib/espn-api/tests/` tree.
- This work happens in the **main repo**, not a worktree. Test command: `.venv/Scripts/python -m pytest tests/ -q`
- **Baseline: 17 failed, 953 passed.** Those 17 pre-date this work and are documented in `docs/PROJECT_STATUS.md`. Judge a full-suite run by diffing the failure list, never by expecting green. `tests/test_projection_tuner.py::test_custom_coefficients_change_projection` is additionally a known flake (unseeded Monte Carlo) failing roughly 1 run in 5 — if you see exactly that one extra failure, re-run once and say so.
- **Do NOT run `black` on `src/pigskin_mastermind/cli.py`** — it is not black-clean and a stray run produces ~514 lines of unrelated churn. Hand-match its single-quoted style. Black scoped to new files is fine.
- No new dependencies. No schema changes, no Alembic migration.
- The projection source string is exactly `"llm"`, lowercase.
- CLI sessions come from `_get_stats_db()` in `src/pigskin_mastermind/cli.py`, always inside `try/finally: db.close()`, with services imported lazily inside command bodies.
- **Do not run the desktop app or a dev `uvicorn` while working** — two SQLite writers on one file produce `database is locked`.

## File Structure

| File | Responsibility |
|---|---|
| `src/pigskin_mastermind/services/agent_scoring.py` (create) | Compare stored projections against actuals, per source and head-to-head. |
| `src/pigskin_mastermind/cli.py` (modify) | Add `pigskin agent score`. |
| `tests/test_agent_scoring.py` (create) | Scoring maths, coverage handling, head-to-head restriction. |
| `.claude/skills/projection-evidence/SKILL.md` (create) | The procedure, the output contract, the hard rules. |
| `.claude/skills/projection-evidence/references/evidence-pack.md` (create) | Block-by-block field reference for the evidence document. |
| `.claude/agents/player-analyst.md` (create) | Thin role definition pointing at the skill. |

---

### Task 1: `pigskin agent score`

**Files:**
- Create: `src/pigskin_mastermind/services/agent_scoring.py`
- Create: `tests/test_agent_scoring.py`
- Modify: `src/pigskin_mastermind/cli.py`

**Interfaces:**
- Produces: `score_projections(db: Session, year: int, week: Optional[int] = None, sources: Optional[List[str]] = None) -> Dict[str, Any]`
- CLI: `pigskin agent score --year Y [--week W] [--sources llm,model]`

**Why this exists.** A backtest of an LLM projection is contaminated — the model may simply know how the season ended. The only honest evaluation is prospective: record projections now, score them as real weeks land. This command is what makes the whole system measurable, and it is the evidence that would ever justify wiring `llm` into the blend.

**The design point that matters most: coverage is not comparable.** `model` has ~1013 season rows; `llm` will have a handful. Comparing their raw MAEs is meaningless — a source that only projected three easy players will look brilliant. So the output carries two things: each source scored on its own coverage, **and** a head-to-head restricted to players where every requested source has a projection *and* an actual exists. The head-to-head is the number a human should believe.

**Where actuals come from.** Reuse the priority `ProjectionTunerService._get_actual_points` already establishes (`services/projection_tuner.py:1045`): `DBPlayerGameLog.fantasy_points` first, falling back to `DBWeeklyPlayerStats.actual_points` when positive. For season scope use `DBPlayerSeasonStats.fantasy_points_total`. Do not import the tuner service for this — it drags in the whole projection stack; write the small lookup directly and reference the tuner in a comment as the precedent.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_scoring.py`. The `db` fixture idiom follows `tests/test_agent_projection.py`.

```python
"""Scoring stored projections against actuals."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerGameLog, DBPlayerProjection, DBPlayerSeasonStats,
)
from pigskin_mastermind.services.agent_scoring import score_projections


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _player(db, name, position="RB"):
    p = DBPlayer(
        player_id=f"espn_{name}", name=name, position=position, nfl_team="ATL",
    )
    db.add(p)
    db.commit()
    return p


def _proj(db, player, source, points, year=2025, week=None):
    db.add(DBPlayerProjection(
        player_id=player.id, year=year, week=week,
        source=source, projected_points=points,
    ))
    db.commit()


def _weekly_actual(db, player, points, year=2025, week=5):
    db.add(DBPlayerGameLog(
        player_id=player.id, year=year, week=week, fantasy_points=points,
    ))
    db.commit()


def _season_actual(db, player, points, year=2025):
    db.add(DBPlayerSeasonStats(
        player_id=player.id, year=year, games_played=17,
        fantasy_points_total=points,
    ))
    db.commit()


def test_mae_and_bias_for_one_source(db):
    a, b = _player(db, "A"), _player(db, "B")
    _proj(db, a, "model", 12.0, week=5)
    _proj(db, b, "model", 20.0, week=5)
    _weekly_actual(db, a, 10.0)          # over by 2
    _weekly_actual(db, b, 26.0)          # under by 6

    result = score_projections(db, 2025, week=5)
    model = result["per_source"]["model"]
    assert model["n"] == 2
    assert model["mae"] == pytest.approx(4.0)
    # Bias is signed projected - actual: positive means over-projection.
    assert model["bias"] == pytest.approx(-2.0)


def test_bias_sign_is_positive_when_over_projecting(db):
    a = _player(db, "A")
    _proj(db, a, "model", 20.0, week=5)
    _weekly_actual(db, a, 5.0)

    assert score_projections(db, 2025, week=5)["per_source"]["model"]["bias"] == (
        pytest.approx(15.0)
    )


def test_players_without_actuals_are_excluded_and_counted(db):
    a, b = _player(db, "A"), _player(db, "B")
    _proj(db, a, "model", 12.0, week=5)
    _proj(db, b, "model", 20.0, week=5)
    _weekly_actual(db, a, 10.0)          # b has no actual

    result = score_projections(db, 2025, week=5)
    assert result["per_source"]["model"]["n"] == 1
    assert result["no_actual"] == 1


def test_head_to_head_restricts_to_players_all_sources_projected(db):
    """The whole point: llm projecting 1 easy player must not beat model on 2.

    Without the restriction, a source that only projected the players it
    found easy would post a flattering MAE against a source that projected
    everyone.
    """
    a, b = _player(db, "A"), _player(db, "B")
    _proj(db, a, "model", 12.0, week=5)
    _proj(db, b, "model", 20.0, week=5)
    _proj(db, a, "llm", 10.5, week=5)    # llm only projected A
    _weekly_actual(db, a, 10.0)
    _weekly_actual(db, b, 26.0)

    result = score_projections(db, 2025, week=5, sources=["model", "llm"])

    # Own coverage: model scored on 2, llm on 1.
    assert result["per_source"]["model"]["n"] == 2
    assert result["per_source"]["llm"]["n"] == 1

    # Head to head: only player A, where both projected.
    h2h = result["head_to_head"]
    assert h2h["players"] == 1
    assert h2h["sources"]["model"]["mae"] == pytest.approx(2.0)
    assert h2h["sources"]["llm"]["mae"] == pytest.approx(0.5)


def test_head_to_head_is_absent_with_fewer_than_two_sources(db):
    a = _player(db, "A")
    _proj(db, a, "model", 12.0, week=5)
    _weekly_actual(db, a, 10.0)

    assert score_projections(db, 2025, week=5)["head_to_head"] is None


def test_season_scope_uses_season_totals(db):
    a = _player(db, "A")
    _proj(db, a, "model", 240.0)         # week=None -> season row
    _season_actual(db, a, 200.0)

    result = score_projections(db, 2025)
    assert result["scope"] == "season"
    assert result["per_source"]["model"]["n"] == 1
    assert result["per_source"]["model"]["mae"] == pytest.approx(40.0)


def test_weekly_scope_ignores_season_rows(db):
    a = _player(db, "A")
    _proj(db, a, "model", 240.0)         # season row
    _weekly_actual(db, a, 10.0)

    result = score_projections(db, 2025, week=5)
    assert result["per_source"] == {}


def test_empty_when_nothing_stored(db):
    result = score_projections(db, 2025, week=5)
    assert result["per_source"] == {}
    assert result["head_to_head"] is None
    assert result["no_actual"] == 0
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pigskin_mastermind.services.agent_scoring'`

- [ ] **Step 3: Implement `services/agent_scoring.py`**

```python
"""Score stored projections against what actually happened.

Backtesting an LLM-produced projection is contaminated -- the model may
simply know how the season ended -- so the only honest evaluation is
prospective: record projections now, score them as real weeks land.

The subtlety this module exists for is **unequal coverage**. ``model`` has a
row for every player in the draft pool; ``llm`` will have a handful, chosen
by whoever ran the agent. Comparing their raw MAEs rewards a source for
projecting only the players it found easy. So every source is reported
twice: once on its own coverage, and once head-to-head on the intersection
of players every requested source projected. Only the head-to-head numbers
are comparable.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayerGameLog,
    DBPlayerProjection,
    DBPlayerSeasonStats,
    DBWeeklyPlayerStats,
)


def score_projections(
    db: Session,
    year: int,
    week: Optional[int] = None,
    sources: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Compare stored projections against actuals, per source.

    Args:
        db: Open session.
        year: Season year.
        week: Week to score, or ``None`` for season scope.
        sources: Restrict to these sources (default: every source present).

    Returns:
        ``{"year", "week", "scope", "per_source", "head_to_head", "no_actual"}``.
    """
    query = db.query(DBPlayerProjection).filter(DBPlayerProjection.year == year)
    if week is None:
        query = query.filter(DBPlayerProjection.week.is_(None))
    else:
        query = query.filter(DBPlayerProjection.week == week)
    if sources:
        query = query.filter(DBPlayerProjection.source.in_(sources))

    scored: Dict[str, Dict[int, Tuple[float, float]]] = {}
    no_actual = 0

    for row in query.all():
        actual = (
            _season_actual(db, row.player_id, year)
            if week is None
            else _actual_points(db, row.player_id, year, week)
        )
        if actual is None:
            # Counted per projection row, so a player missing an actual is
            # counted once for each source that projected them.
            no_actual += 1
            continue
        scored.setdefault(row.source, {})[row.player_id] = (
            row.projected_points,
            actual,
        )

    return {
        "year": year,
        "week": week,
        "scope": "season" if week is None else "weekly",
        "per_source": {
            source: _metrics(list(pairs.values()))
            for source, pairs in scored.items()
        },
        "head_to_head": _head_to_head(scored),
        "no_actual": no_actual,
    }


def _head_to_head(
    scored: Dict[str, Dict[int, Tuple[float, float]]],
) -> Optional[Dict[str, Any]]:
    """Re-score every source on only the players all of them projected.

    Without this restriction a source that projected three easy players
    posts a flattering MAE against a source that projected everyone, and a
    reader comparing the two numbers is misled.
    """
    if len(scored) < 2:
        return None

    shared = set.intersection(*(set(pairs) for pairs in scored.values()))
    return {
        "players": len(shared),
        "sources": {
            source: _metrics([pairs[pid] for pid in shared])
            for source, pairs in scored.items()
        },
    }


def _metrics(pairs: List[Tuple[float, float]]) -> Dict[str, float]:
    """Accuracy over ``[(projected, actual), ...]``.

    ``bias`` is the signed mean of ``projected - actual``, so **positive
    means over-projection**. MAE alone says a source is wrong; the sign says
    which way, which is the part a human can act on.
    """
    n = len(pairs)
    if n == 0:
        return {"n": 0, "mae": 0.0, "bias": 0.0, "rmse": 0.0}

    errors = [projected - actual for projected, actual in pairs]
    return {
        "n": n,
        "mae": round(sum(abs(e) for e in errors) / n, 3),
        "bias": round(sum(errors) / n, 3),
        "rmse": round((sum(e * e for e in errors) / n) ** 0.5, 3),
    }


def _actual_points(
    db: Session, player_id: int, year: int, week: int,
) -> Optional[float]:
    """Actual fantasy points for one week, or ``None`` if not yet played.

    Same source priority ``ProjectionTunerService._get_actual_points``
    established (``services/projection_tuner.py``): the nflverse game log
    first, then ESPN's weekly stats.
    """
    log = (
        db.query(DBPlayerGameLog)
        .filter_by(player_id=player_id, year=year, week=week)
        .first()
    )
    if log is not None and log.fantasy_points is not None:
        return round(log.fantasy_points, 2)

    weekly = (
        db.query(DBWeeklyPlayerStats)
        .filter(
            DBWeeklyPlayerStats.player_id == player_id,
            DBWeeklyPlayerStats.week == week,
        )
        .first()
    )
    if weekly and weekly.actual_points:
        return round(weekly.actual_points, 2)

    return None


def _season_actual(
    db: Session, player_id: int, year: int,
) -> Optional[float]:
    """Season total actual points.

    A stored total of zero means the season has not been played rather than
    a player who scored nothing all year, so it is treated as absent.
    """
    row = (
        db.query(DBPlayerSeasonStats)
        .filter_by(player_id=player_id, year=year)
        .first()
    )
    if row is None or not row.fantasy_points_total:
        return None
    return round(row.fantasy_points_total, 2)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/Scripts/python -m pytest tests/test_agent_scoring.py -v`
Expected: 8 passed

- [ ] **Step 5: Add the CLI command**

Add to the existing `agent` group in `src/pigskin_mastermind/cli.py`, matching the style of `agent evidence` and `agent record-projection`:

```python
@agent.command('score')
@click.option('--year', type=int, default=None,
              help='Season year (defaults to the current fantasy season)')
@click.option('--week', type=int, default=None,
              help='Score weekly projections for this week. Omit for season scope.')
@click.option('--sources', default=None,
              help='Comma-separated sources to score (default: every source present)')
def agent_score(year, week, sources):
    """Score stored projections against what actually happened.

    Backtesting an LLM projection is contaminated — the model may already
    know how the season ended. The honest evaluation is prospective: record
    projections now, score them as real weeks land. This is what builds the
    track record that would ever justify trusting the ``llm`` source::

        pigskin agent score --year 2025 --week 5 --sources model,llm

    Sources are reported twice: on their own coverage, and head to head on
    the players every source projected. Only the head-to-head numbers are
    comparable — a source that projected three easy players will always
    look better on its own coverage.
    """
```

Body: resolve `year` via `current_fantasy_season()`, split `sources` on commas when given, call `score_projections`, and print a readable table. Print the head-to-head section only when present, and say plainly when it is absent and why.

- [ ] **Step 6: Verify the command against the real database**

Run:

```bash
.venv/Scripts/python -m pigskin_mastermind.cli agent score --year 2025
```

The real database has `model` and `blend` season rows for ~1013 players. Report what it prints. If no season actuals exist for 2025 yet, the command must say so clearly rather than printing an empty table or dividing by zero — fix that if it does not.

- [ ] **Step 7: Format, lint, commit**

```bash
.venv/Scripts/python -m black src/pigskin_mastermind/services/agent_scoring.py tests/test_agent_scoring.py
.venv/Scripts/python -m flake8 src/pigskin_mastermind/services/agent_scoring.py tests/test_agent_scoring.py
```

```bash
git add src/pigskin_mastermind/services/agent_scoring.py tests/test_agent_scoring.py src/pigskin_mastermind/cli.py
git commit -m "feat(agent): add 'pigskin agent score' to measure projections against actuals"
```

---

### Task 2: The `projection-evidence` skill

**Files:**
- Create: `.claude/skills/projection-evidence/SKILL.md`
- Create: `.claude/skills/projection-evidence/references/evidence-pack.md`

**Interfaces:**
- Consumes: `pigskin agent evidence`, `pigskin agent record-projection` (both shipped in Phase 1).
- Produces: the documented procedure the `player-analyst` agent follows in Task 3.

**Read first, in this order:** the spec's "What Phase 1 learned that Phase 2 must act on" section; `src/pigskin_mastermind/services/agent_evidence.py`'s `build_evidence` docstring; `src/pigskin_mastermind/services/agent_projection.py`'s validation rules; `tests/fixtures/agent_result_season.json`.

**Format.** A skill is a directory containing `SKILL.md` with YAML frontmatter:

```markdown
---
name: projection-evidence
description: Use when producing a fantasy football projection for one player in Pigskin Mastermind — gathers the evidence pack, reasons about where the deterministic model is likely wrong, and records a validated projection.
---
```

Reference material goes in `references/`, loaded on demand rather than inlined, so `SKILL.md` stays readable.

- [ ] **Step 1: Write `references/evidence-pack.md`**

A block-by-block reference for the document `pigskin agent evidence` emits. Generate a real one to work from:

```bash
.venv/Scripts/python -m pigskin_mastermind.cli agent evidence --player-id 123 --year 2025 > /tmp/sample_evidence.json
```

(Player 123 is Drake Maye, QB, NE — 36 game logs, so every block is populated.)

The document has exactly these 16 top-level keys. Document each: what it contains, what it is good for, and **what not to trust about it**.

```
player, context, season_stats, season_stats_filtered_reason, game_logs,
criteria, criteria_omitted_reason, existing_projections,
existing_projections_filtered_reason, schedule, sportsbook,
sportsbook_omitted_reason, news, news_filtered_reason, data_freshness,
evidence_hash
```

Cover in particular:

- **`criteria`** is the most useful block and needs the most explanation — it is exactly what the deterministic model sees. Document the field groups (skill, touch share, momentum, opponent defense level, expected games) and note the scale conventions: most criteria are 0–100, `opposing_defense_vs_position_rank` is 1–32 where **1 is the best defense so a high rank is a good matchup**, and `snap_pct` is 0–100 not 0–1. Get these from `src/pigskin_mastermind/models/projection_criteria.py` and the domain rules in `CLAUDE.md`.
- **`data_freshness`** is how the agent decides whether a web search is worth its cost. Spell that out — it is the block's entire purpose.
- **The five `*_reason` keys.** `_omitted_` means the block is absent; `_filtered_` means it is present but truncated. When `as_of_week` is set, these strings say what actually happened for that call, including the case where the cutoff could not be resolved and rows were served unfiltered.
- **`context.as_of_week` and `context.as_of_cutoff_at`** — read these before trusting any `_reason` string, because an unresolvable cutoff means the document is *not* isolated.

- [ ] **Step 2: Write `SKILL.md`**

Required sections:

**1. The procedure**, adapted from the spec:

1. Run the evidence pack for the requested scope.
2. Read `data_freshness` and decide whether a web lookup is warranted at all — if stats synced yesterday and it is June, it is not.
3. If web is enabled: search role, injury, depth-chart, and coaching news scoped to the current season. Every claim that moves the number carries a URL.
4. Project.
5. Write the result JSON to a scratch file and call `record-projection`.
6. Return a short summary: the number, the two or three factors that moved it, and the size of the disagreement with the model.

**2. The quality bar for step 4.** This is the section that determines whether the skill is worth anything, so give it the most care. The central instruction: reason explicitly about **where the formula is likely wrong for this specific player** — a sample too small for the criteria to mean anything, a mid-season role change, a non-linear usage shift, a criterion pinned at its default because the underlying data is missing. State plainly that an analysis which re-derives the model's own logic from the same inputs is worthless, because the model already did that and did it more consistently. Include a worked example of a good `rationale` and a bad one, and say what makes the difference.

**3. The output contract**, verbatim, matching `tests/fixtures/agent_result_season.json`:

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
  "evidence_hash": "the evidence_hash from the pack this was derived from"
}
```

**4. The validator's rules and how to recover from rejection.** `record-projection` exits non-zero with the reason on stderr. List the five rules — required fields (`player_id`, `year`, `projected_points`, `rationale`, `confidence`), `confidence` in `low|medium|high`, `floor <= projected_points <= ceiling`, the sanity band (within `[0.25×, 3.0×]` of the model projection when one exists, otherwise a per-position ceiling), and web-sourced factors requiring a `url`. Say that rejection is information: read the reason and correct, never work around the gate.

**5. Hard rules**, stated as rules and not buried in prose:

- **`evidence_hash` must be the real hash from the evidence pack you ran.** Nothing validates its format, so a fabricated one will be accepted and will silently destroy the ability to tell "the agent changed its mind" from "the data moved". The checked-in fixture uses an obvious placeholder — do not copy it.
- **Citations must be live sources.** The validator only checks that a web-sourced factor's `url` is non-empty, never that it resolves. `example.com` in the fixture is a stand-in.
- **`--as-of` is a partial, schedule-dependent cutoff, not a time machine.** Five blocks truncate unconditionally; `existing_projections` and `news` filter only when a kickoff resolves; `player` bio and `data_freshness` are always current-state. Read `context.as_of_week` and `context.as_of_cutoff_at` before trusting isolation.
- **Under a cutoff there is no matchup signal at all**, because opponent and defense ranks live inside the omitted `criteria` block. Do not assume a neutral matchup — say the projection was made without matchup information.
- **Never edit repository code.** The scratch result file is the only thing to write.

- [ ] **Step 3: Verify the skill's factual claims against the code**

Every scale, field name, threshold, and CLI flag in both files must match the implementation. Check each against `agent_evidence.py`, `agent_projection.py`, `models/projection_criteria.py`, and `cli.py`. A skill that misdescribes the contract is worse than no skill — the agent will follow it confidently.

Confirm the sample evidence document actually contains every key the reference claims, by reading `/tmp/sample_evidence.json`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/projection-evidence/
git commit -m "docs(agent): add the projection-evidence skill"
```

---

### Task 3: The `player-analyst` agent

**Files:**
- Create: `.claude/agents/player-analyst.md`

**Interfaces:**
- Consumes: the `projection-evidence` skill from Task 2.

**Read first:** `.claude/agents/user-tester.md` — the existing agent in this repo and the pattern to follow.

- [ ] **Step 1: Write the agent definition**

Frontmatter:

```markdown
---
name: player-analyst
description: Use when you want a researched fantasy football projection for one specific player in Pigskin Mastermind — gathers everything the database knows, checks current news, and records a projection with a written rationale and citations. Produces a stored `source='llm'` projection, not a code change.
tools: Bash, Read, Write, WebSearch, WebFetch, Skill
model: sonnet
---
```

Note the tool list: no `Edit`, and `Write` exists only for the scratch result file. The agent must never modify repository code.

Body — keep it thin, a role statement rather than a procedure:

- Who it is: a fantasy football analyst producing one projection for one player.
- **First instruction: invoke the `projection-evidence` skill.** The procedure lives there, not here.
- What it must return to the caller: the number, the two or three factors that moved it, the size and direction of the disagreement with the model, and its confidence — plus whether web was used.
- What it must not do: edit repo code, invent an `evidence_hash`, cite a URL it did not read, or work around a rejection from `record-projection`.
- The scope it accepts: a player ID with a year, and optionally a week (season scope when no week is given).

Resist restating the skill. Duplication here is how the two drift apart.

- [ ] **Step 2: Confirm the agent is discoverable**

The agent should appear in the available agent types. Confirm the file parses — frontmatter delimiters correct, `tools` a comma-separated list matching the names above.

- [ ] **Step 3: Commit**

```bash
git add .claude/agents/player-analyst.md
git commit -m "feat(agent): add the player-analyst agent definition"
```

---

### Task 4: End-to-end verification on real players

**Files:**
- Modify: `.claude/skills/projection-evidence/SKILL.md` and `references/evidence-pack.md` as the runs expose problems
- Modify: `.claude/agents/player-analyst.md` if the role statement proves thin

This task is where the prompt-shaped work actually gets tested. Everything before it is unverified.

**Three players, chosen to break different things:**

| Player ID | Who | Why |
|---|---|---|
| 123 | Drake Maye, QB, NE | 36 game logs — every block populated, the easy case |
| 1023 | CJ Dippre, TE, NE | 2 game logs — criteria are mostly defaults, and the analysis must *say so* rather than treating them as signal |
| 10 | Texans D/ST, DEF, HOU | Defenses resolve by team not name, have no game logs in the usual shape, and are where a projection method built around skill players falls apart |

**This writes to the real database** — `llm` rows, plus lazily-created team/defense stat rows from the criteria builder. That is expected and was agreed. Do not run the desktop app or a dev server concurrently.

- [ ] **Step 1: Dry-run the evidence pack for all three**

```bash
.venv/Scripts/python -m pigskin_mastermind.cli agent evidence --player-id 123 --year 2025
.venv/Scripts/python -m pigskin_mastermind.cli agent evidence --player-id 1023 --year 2025
.venv/Scripts/python -m pigskin_mastermind.cli agent evidence --player-id 10 --year 2025
```

Confirm each returns a well-formed document and note what is empty or defaulted, especially for 1023 and 10. If a block errors or is structurally different from what the reference file claims, fix the reference before running any agent.

- [ ] **Step 2: Run the agent on player 123**

Dispatch the `player-analyst` agent for player 123, year 2025, season scope, web enabled.

Then inspect what it actually did:

```bash
.venv/Scripts/python -c "
from pigskin_mastermind.api.database import SessionLocal
from pigskin_mastermind.models.database import DBPlayerProjection
import json
db = SessionLocal()
row = db.query(DBPlayerProjection).filter_by(player_id=123, year=2025, source='llm').first()
print(row.projected_points, row.floor, row.ceiling)
print(json.dumps(row.components, indent=2))
db.close()"
```

Judge it against the skill's own quality bar:

- Is the rationale reasoning about **where the model is wrong for this player**, or re-deriving the model's logic from the same inputs? The second is the failure mode.
- Does every `key_factor` with `"source": "web"` carry a URL that actually resolves and actually says what the factor claims? Check at least one.
- Is `evidence_hash` the real hash? Re-run `agent evidence` for the same player and compare.
- Is `disagreement_with_model` a specific mechanism, or a restatement of the two numbers?

- [ ] **Step 3: Run the agent on players 1023 and 10**

The thin-data and defense cases are where the skill will fall down.

For **1023**, the analysis must recognize that criteria pinned at defaults are not evidence, and confidence must reflect that. An analysis that treats a default `player_skill_level` of 50.0 as a real measurement is wrong, and the skill needs to say so explicitly if it does not already.

For **10**, watch for a projection method that assumes touches, targets, and snap share — none of which mean anything for a defense. If the skill has nothing to say about defenses, add a short section.

- [ ] **Step 4: Iterate the skill on what you found**

Fix the skill and the reference for each real problem the runs exposed. Re-run the affected player after each meaningful change to confirm the fix took.

Record, for the report: what each run got wrong, what changed in the skill, and what the re-run produced.

- [ ] **Step 5: Score what was produced**

```bash
.venv/Scripts/python -m pigskin_mastermind.cli agent score --year 2025 --sources model,llm
```

With three `llm` rows against ~1013 `model` rows, this is the exact situation the head-to-head exists for. Confirm the output makes the coverage difference obvious and that the head-to-head restricts to the three shared players. If a reader could mistake the per-source numbers for a fair comparison, the command's output needs work.

Note: this only produces numbers if 2025 season actuals exist. If they do not, confirm the command says so clearly and report that instead — that is a valid outcome, not a failure.

- [ ] **Step 6: Full suite and commit**

```bash
.venv/Scripts/python -m pytest tests/ -q
```

Baseline is 17 failed / 953 passed plus the 8 new scoring tests. Diff the failure list.

```bash
git add .claude/
git commit -m "docs(agent): refine the projection-evidence skill from real-player runs"
```

---

## Done when

- `pigskin agent score --year 2025 --sources model,llm` runs and its head-to-head section makes the coverage difference impossible to misread.
- The `player-analyst` agent produces a stored `llm` projection for a real player, with a rationale that identifies where the model is likely wrong rather than restating what the model already computed.
- Every web-sourced key factor carries a URL that resolves and supports the claim.
- `evidence_hash` on a stored row matches a fresh `agent evidence` run for the same player and scope.
- The thin-data player's analysis explicitly says which criteria are defaults rather than measurements, and its confidence reflects that.
- `pytest tests/` shows no new failures against the documented baseline.

## Out of scope

- The `projection-tuner` agent and the `coefficient-tuning` skill — Phase 3 and 4 in the spec's delivery order.
- Wiring `source='llm'` into `blend` or the draft pool. The scoring command exists to build the track record that decision would need; the decision itself is not this plan's.
- `services/agent_runner.py` and the FastAPI route — Phase 5.
- Any change to `agent_evidence.py` or `agent_projection.py` beyond what a genuine defect found during Task 4 requires.
