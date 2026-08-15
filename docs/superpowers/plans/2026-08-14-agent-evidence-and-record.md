# Agent Evidence Pack & Projection Recording — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic backend contract that the `player-analyst` agent will run against — one command that emits everything known about a player as JSON, and one command that validates and stores an agent-produced projection.

**Architecture:** Two new service modules and one new Click command group. `services/agent_evidence.py` assembles a JSON document from existing services (`ProjectionCriteriaBuilder`, `projection_refresh`, `SportsbookProjectionService`) — it is an assembler, not a new source of truth. `services/agent_projection.py` validates an agent result against hard rules and writes a `source='llm'` row to `player_projections`. No LLM is involved anywhere in this phase; every line is unit-testable.

**Tech Stack:** Python 3, SQLAlchemy 2.x, Click, pytest.

**Spec:** [docs/superpowers/specs/2026-08-14-projection-agents-design.md](../specs/2026-08-14-projection-agents-design.md)

## Global Constraints

- **Always scope pytest to `tests/`** — bare `pytest` dies collecting the vendored `src/pigskin_mastermind/lib/espn-api/tests/` tree.
- On Windows the venv interpreter is `.venv/Scripts/python`.
- **No new dependencies.** Everything needed is already installed.
- New DB reads only — **no Alembic migration in this phase.** `DBPlayerProjection.components` is already a `JSON` column and is where all agent metadata goes.
- The projection source string is exactly `"llm"`, lowercase.
- CLI sessions come from the existing `_get_stats_db()` helper in `src/pigskin_mastermind/cli.py:184`, always inside `try/finally: db.close()`.
- `ProjectionCriteriaBuilder` must be constructed with `allow_network=False` — the network path costs ~3.3s per player.
- Format with `black src/ tests/` and lint with `flake8 src/ tests/` before each commit.
- Positions in this codebase are always the normalized set `QB, RB, WR, TE, K, DEF` (never `D/ST`).

### Known limitation, deliberately accepted

The spec says `--as-of` truncates "game logs and derived criteria." `ProjectionCriteriaBuilder` has no as-of cutoff parameter, and adding one would mean threading a cutoff through ~1.4k lines of a module this phase does not otherwise touch. Task 6 therefore **omits the criteria block entirely when `as_of_week` is set**, with a machine-readable reason string, rather than shipping criteria that silently leak post-cutoff data into a backtest. Adding a real cutoff to the builder is future work.

## File Structure

| File | Responsibility |
|---|---|
| `src/pigskin_mastermind/services/agent_evidence.py` (create) | Assemble the evidence document. One public function plus one block-builder per section. |
| `src/pigskin_mastermind/services/agent_projection.py` (create) | Validate and persist an agent result. |
| `src/pigskin_mastermind/cli.py` (modify) | New `agent` command group with `evidence` and `record-projection`. |
| `tests/test_agent_evidence.py` (create) | Evidence assembly and truncation. |
| `tests/test_agent_projection.py` (create) | Validation rules and persistence. |
| `tests/fixtures/agent_result_season.json` (create) | Canned agent result pinning the output contract. |

---

### Task 1: Evidence skeleton — `player` and `context` blocks

**Files:**
- Create: `src/pigskin_mastermind/services/agent_evidence.py`
- Test: `tests/test_agent_evidence.py`

**Interfaces:**
- Consumes: `DBPlayer` from `pigskin_mastermind.models.database`.
- Produces: `build_evidence(db: Session, player_id: int, year: int, week: Optional[int] = None, as_of_week: Optional[int] = None) -> Dict[str, Any]`. Every later task adds a key to the dict this returns.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_evidence.py`. The `db` fixture idiom is copied from `tests/test_projection_refresh.py:14-23`, which is the established pattern in this repo.

```python
"""Assembly of the agent evidence pack."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer
from pigskin_mastermind.services.agent_evidence import build_evidence


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


@pytest.fixture
def player(db):
    p = DBPlayer(
        player_id="espn_1", name="Test Back", position="RB", nfl_team="ATL",
        espn_id="1", age=25, years_exp=3, bye_week=9,
    )
    db.add(p)
    db.commit()
    return p


def test_player_block_carries_identity_and_bio(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["player"]["db_id"] == player.id
    assert ev["player"]["name"] == "Test Back"
    assert ev["player"]["position"] == "RB"
    assert ev["player"]["nfl_team"] == "ATL"
    assert ev["player"]["espn_id"] == "1"
    assert ev["player"]["age"] == 25
    assert ev["player"]["bye_week"] == 9


def test_season_scope_when_no_week_given(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["context"]["scope"] == "season"
    assert ev["context"]["week"] is None
    assert ev["context"]["year"] == 2026


def test_weekly_scope_when_week_given(db, player):
    ev = build_evidence(db, player.id, 2026, week=5)
    assert ev["context"]["scope"] == "weekly"
    assert ev["context"]["week"] == 5


def test_unknown_player_raises(db):
    with pytest.raises(ValueError, match="not found"):
        build_evidence(db, 9999, 2026)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pigskin_mastermind.services.agent_evidence'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/pigskin_mastermind/services/agent_evidence.py`:

```python
"""Assemble everything known about one player into a single JSON document.

This module is an *assembler*, not a new source of truth. Every number in the
output already exists somewhere in the schema or is produced by an existing
service; the point is that an agent can obtain all of it in one call instead of
discovering it endpoint by endpoint.

Nothing here calls an LLM. The output of this module is the contract that the
``player-analyst`` agent reads, which is what keeps that agent testable.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer


def build_evidence(
    db: Session,
    player_id: int,
    year: int,
    week: Optional[int] = None,
    as_of_week: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the evidence document for one player.

    Args:
        db: Open session.
        player_id: ``DBPlayer.id`` (not the prefixed string ``player_id``).
        year: Season year.
        week: Target week, or ``None`` for season scope.
        as_of_week: Backtest cutoff. When set, no data from this week or later
            appears in the document.

    Returns:
        A JSON-serializable dict.

    Raises:
        ValueError: If ``player_id`` does not exist.
    """
    player = db.query(DBPlayer).filter(DBPlayer.id == player_id).first()
    if player is None:
        raise ValueError(f"Player {player_id} not found")

    return {
        "player": _player_block(player),
        "context": _context_block(year, week),
    }


def _player_block(player: DBPlayer) -> Dict[str, Any]:
    return {
        "db_id": player.id,
        "player_id": player.player_id,
        "name": player.name,
        "position": player.position,
        "nfl_team": player.nfl_team,
        "age": player.age,
        "years_exp": player.years_exp,
        "college": player.college,
        "draft_number": player.draft_number,
        "bye_week": player.bye_week,
        "injury_status": player.injury_status,
        "injured": player.injured,
        "espn_id": player.espn_id,
        "gsis_id": player.gsis_id,
        "pfr_id": player.pfr_id,
    }


def _context_block(year: int, week: Optional[int]) -> Dict[str, Any]:
    return {
        "year": year,
        "week": week,
        "scope": "season" if week is None else "weekly",
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_evidence.py tests/test_agent_evidence.py
git commit -m "feat(agent): evidence pack skeleton with player and context blocks"
```

---

### Task 2: `season_stats` and `game_logs` blocks

**Files:**
- Modify: `src/pigskin_mastermind/services/agent_evidence.py`
- Test: `tests/test_agent_evidence.py`

**Interfaces:**
- Consumes: `build_evidence` from Task 1.
- Produces: `evidence["season_stats"]` — a list of dicts ordered newest year first, at most 3 entries. `evidence["game_logs"]` — a list of dicts ordered by `(year, week)` ascending, at most 34 entries.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_agent_evidence.py`, and add the two model imports to the existing import line:

```python
from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats,
)
```

```python
@pytest.fixture
def stats(db, player):
    for year, total in [(2023, 180.0), (2024, 240.0), (2025, 300.0)]:
        db.add(DBPlayerSeasonStats(
            player_id=player.id, year=year, games_played=16,
            rush_att=200, rush_yd=900, rush_td=8, targets=50, rec=40,
            fantasy_points_total=total, fantasy_points_avg=total / 16,
            snap_pct=72.5, adp=24.0, adp_source="ffc",
        ))
    for wk in range(1, 6):
        db.add(DBPlayerGameLog(
            player_id=player.id, year=2025, week=wk, opponent="NO",
            rush_att=15, rush_yd=70, rush_td=1, targets=3, rec=2,
            fantasy_points=14.0 + wk,
        ))
    db.commit()


def test_season_stats_newest_first_and_capped_at_three(db, player, stats):
    ev = build_evidence(db, player.id, 2026)
    years = [row["year"] for row in ev["season_stats"]]
    assert years == [2025, 2024, 2023]
    assert ev["season_stats"][0]["fantasy_points_total"] == 300.0
    assert ev["season_stats"][0]["snap_pct"] == 72.5


def test_game_logs_ordered_ascending(db, player, stats):
    ev = build_evidence(db, player.id, 2026)
    assert [g["week"] for g in ev["game_logs"]] == [1, 2, 3, 4, 5]
    assert ev["game_logs"][0]["opponent"] == "NO"
    assert ev["game_logs"][4]["fantasy_points"] == 19.0


def test_blocks_are_empty_lists_when_player_has_no_data(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["season_stats"] == []
    assert ev["game_logs"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: FAIL with `KeyError: 'season_stats'`

- [ ] **Step 3: Write the implementation**

In `agent_evidence.py`, extend the imports and the returned dict, then add the two builders:

```python
from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats,
)

# Three seasons is what the criteria builder's trend and year-over-year
# calculations look back over; more would be noise in the agent's context.
_SEASON_HISTORY_YEARS = 3

# Two seasons of weeks. Enough to see a role change from last year without
# burning the agent's context on ancient games.
_GAME_LOG_LIMIT = 34
```

Add to the returned dict in `build_evidence`, after `"context"`:

```python
        "season_stats": _season_stats_block(db, player_id, year),
        "game_logs": _game_logs_block(db, player_id, year),
```

```python
def _season_stats_block(db: Session, player_id: int, year: int) -> list:
    rows = (
        db.query(DBPlayerSeasonStats)
        .filter(
            DBPlayerSeasonStats.player_id == player_id,
            DBPlayerSeasonStats.year <= year,
        )
        .order_by(DBPlayerSeasonStats.year.desc())
        .limit(_SEASON_HISTORY_YEARS)
        .all()
    )
    return [
        {
            "year": r.year,
            "games_played": r.games_played,
            "pass_att": r.pass_att, "pass_yd": r.pass_yd,
            "pass_td": r.pass_td, "pass_int": r.pass_int,
            "rush_att": r.rush_att, "rush_yd": r.rush_yd, "rush_td": r.rush_td,
            "targets": r.targets, "rec": r.rec,
            "rec_yd": r.rec_yd, "rec_td": r.rec_td,
            "fantasy_points_total": r.fantasy_points_total,
            "fantasy_points_avg": r.fantasy_points_avg,
            "fantasy_points_per_touch": r.fantasy_points_per_touch,
            # snap_pct is canonically 0-100 in this schema, not 0-1.
            "snap_pct": r.snap_pct,
            "air_yards": r.air_yards, "yac": r.yac, "wopr": r.wopr,
            "adp": r.adp, "adp_source": r.adp_source,
            "adp_times_drafted": r.adp_times_drafted,
            "source": r.source,
        }
        for r in rows
    ]


def _game_logs_block(db: Session, player_id: int, year: int) -> list:
    rows = (
        db.query(DBPlayerGameLog)
        .filter(
            DBPlayerGameLog.player_id == player_id,
            DBPlayerGameLog.year <= year,
        )
        .order_by(
            DBPlayerGameLog.year.desc(), DBPlayerGameLog.week.desc(),
        )
        .limit(_GAME_LOG_LIMIT)
        .all()
    )
    # Queried newest-first so the limit keeps recent games; the agent reads
    # them chronologically.
    rows.reverse()
    return [
        {
            "year": r.year, "week": r.week, "opponent": r.opponent,
            "pass_att": r.pass_att, "pass_yd": r.pass_yd, "pass_td": r.pass_td,
            "pass_int": r.pass_int,
            "rush_att": r.rush_att, "rush_yd": r.rush_yd, "rush_td": r.rush_td,
            "targets": r.targets, "rec": r.rec,
            "rec_yd": r.rec_yd, "rec_td": r.rec_td,
            "fumbles_lost": r.fumbles_lost,
            "fantasy_points": r.fantasy_points,
            "source": r.source,
        }
        for r in rows
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_evidence.py tests/test_agent_evidence.py
git commit -m "feat(agent): add season stats and game log blocks to evidence pack"
```

---

### Task 3: `criteria` block

**Files:**
- Modify: `src/pigskin_mastermind/services/agent_evidence.py`
- Test: `tests/test_agent_evidence.py`

**Interfaces:**
- Consumes: `ProjectionCriteriaBuilder.build_yearly_criteria(player_id, year)` and `.build_weekly_criteria(player_id, week, year)` from `services/projection_criteria_builder.py`.
- Produces: `evidence["criteria"]` — a flat dict of criteria field names to values, or `None`.

**Important:** `ProjectionCriteriaBuilder` lazily creates missing team/defense stat rows as a side effect. This block therefore *writes* to the database. `build_evidence` is not read-only, and callers must commit and close before handing control to a subprocess.

- [ ] **Step 1: Write the failing tests**

```python
def test_yearly_criteria_present_for_season_scope(db, player, stats):
    ev = build_evidence(db, player.id, 2026)
    assert ev["criteria"]["scope"] == "yearly"
    fields = ev["criteria"]["fields"]
    assert "expected_games" in fields
    assert "player_skill_level" in fields
    assert "age_deviation_from_optimum" in fields


def test_weekly_criteria_present_for_weekly_scope(db, player, stats):
    ev = build_evidence(db, player.id, 2026, week=5)
    assert ev["criteria"]["scope"] == "weekly"
    fields = ev["criteria"]["fields"]
    assert "opposing_defense_vs_position_rank" in fields
    assert "offensive_momentum_score" in fields


def test_criteria_values_are_json_serializable(db, player, stats):
    import json
    ev = build_evidence(db, player.id, 2026, week=5)
    json.dumps(ev["criteria"])  # raises TypeError if not
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: FAIL with `KeyError: 'criteria'`

- [ ] **Step 3: Write the implementation**

Add `from dataclasses import asdict` to the imports. Add to the returned dict:

```python
        "criteria": _criteria_block(db, player_id, year, week),
```

```python
def _criteria_block(
    db: Session, player_id: int, year: int, week: Optional[int],
) -> Optional[Dict[str, Any]]:
    """The exact criteria the deterministic model would use for this scope.

    This is the single most useful block in the pack: it lets the agent see
    what the formula sees, and therefore reason about where the formula is
    likely to be wrong rather than re-deriving it badly.

    Imported lazily because ``projection_criteria_builder`` is a heavy module
    and most callers of this file do not need it.
    """
    from pigskin_mastermind.services.projection_criteria_builder import (
        ProjectionCriteriaBuilder,
    )

    # allow_network=False: the per-player ESPN fetch costs ~3.3s against ~35ms
    # for a player with local data, and the agent already has web access for
    # anything the network path would add.
    builder = ProjectionCriteriaBuilder(db, allow_network=False)

    if week is None:
        criteria = builder.build_yearly_criteria(player_id, year)
        scope = "yearly"
    else:
        criteria = builder.build_weekly_criteria(player_id, week, year)
        scope = "weekly"

    return {"scope": scope, "fields": asdict(criteria)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: 10 passed

**If the builder raises on the sparse fixture database** (it derives team offense and defense levels and lazily creates the rows it needs), extend the `stats` fixture with the rows it asks for — a `DBNFLTeamStats` row for `ATL`, or `DBNFLGame` rows for the season. Do **not** wrap `_criteria_block` in `try/except` to make the test green: an exception here is a real failure mode the agent would hit on a thin database, and swallowing it would hide that until a live run.

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_evidence.py tests/test_agent_evidence.py
git commit -m "feat(agent): add model criteria block to evidence pack"
```

---

### Task 4: `existing_projections` and `data_freshness` blocks

**Files:**
- Modify: `src/pigskin_mastermind/services/agent_evidence.py`
- Test: `tests/test_agent_evidence.py`

**Interfaces:**
- Produces: `evidence["existing_projections"]` — dict keyed by source string. `evidence["data_freshness"]` — dict of ISO-8601 strings or `None`.

- [ ] **Step 1: Write the failing tests**

Add `DBPlayerProjection` to the model imports in the test file.

```python
def test_existing_projections_keyed_by_source(db, player):
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="model",
        projected_points=248.0, expected_games=16.0,
    ))
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="espn",
        projected_points=231.5,
    ))
    db.commit()

    ev = build_evidence(db, player.id, 2026)
    assert ev["existing_projections"]["model"]["projected_points"] == 248.0
    assert ev["existing_projections"]["model"]["expected_games"] == 16.0
    assert ev["existing_projections"]["espn"]["projected_points"] == 231.5
    assert ev["existing_projections"]["model"]["computed_at"] is not None


def test_season_projections_excluded_from_weekly_scope(db, player):
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="model",
        projected_points=248.0,
    ))
    db.commit()

    ev = build_evidence(db, player.id, 2026, week=5)
    assert ev["existing_projections"] == {}


def test_data_freshness_reports_stat_timestamps(db, player, stats):
    ev = build_evidence(db, player.id, 2026)
    fresh = ev["data_freshness"]
    assert fresh["game_logs_updated_at"] is not None
    assert fresh["season_stats_updated_at"] is not None
    assert fresh["adp_updated_at"] is not None
    assert fresh["generated_at"] is not None


def test_data_freshness_is_null_when_nothing_stored(db, player):
    fresh = build_evidence(db, player.id, 2026)["data_freshness"]
    assert fresh["game_logs_updated_at"] is None
    assert fresh["season_stats_updated_at"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: FAIL with `KeyError: 'existing_projections'`

- [ ] **Step 3: Write the implementation**

Extend imports:

```python
from datetime import datetime, timezone

from sqlalchemy import func

from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerGameLog, DBPlayerProjection, DBPlayerSeasonStats,
)
```

Add to the returned dict:

```python
        "existing_projections": _existing_projections_block(
            db, player_id, year, week,
        ),
        "data_freshness": _data_freshness_block(db, player_id, year),
```

```python
def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _existing_projections_block(
    db: Session, player_id: int, year: int, week: Optional[int],
) -> Dict[str, Any]:
    """Every stored projection for this scope, by source.

    Unfiltered by source on purpose: the agent should see that ``espn`` and
    ``model`` disagree, and by how much, before forming its own view.
    """
    query = db.query(DBPlayerProjection).filter(
        DBPlayerProjection.player_id == player_id,
        DBPlayerProjection.year == year,
    )
    if week is None:
        query = query.filter(DBPlayerProjection.week.is_(None))
    else:
        query = query.filter(DBPlayerProjection.week == week)

    return {
        row.source: {
            "projected_points": row.projected_points,
            "floor": row.floor,
            "ceiling": row.ceiling,
            "std_dev": row.std_dev,
            "expected_games": row.expected_games,
            "computed_at": _iso(row.computed_at),
        }
        for row in query.all()
    }


def _data_freshness_block(
    db: Session, player_id: int, year: int,
) -> Dict[str, Any]:
    """When each underlying data source was last written.

    This block is what tells the agent where the database is *blind*, and so
    whether a web lookup is worth its cost. Without it the agent has no way to
    distinguish "this player has no recent news" from "nobody has synced stats
    since March".
    """
    logs_at = (
        db.query(func.max(DBPlayerGameLog.updated_at))
        .filter(DBPlayerGameLog.player_id == player_id)
        .scalar()
    )
    season_at = (
        db.query(func.max(DBPlayerSeasonStats.updated_at))
        .filter(DBPlayerSeasonStats.player_id == player_id)
        .scalar()
    )
    adp_at = (
        db.query(func.max(DBPlayerSeasonStats.updated_at))
        .filter(
            DBPlayerSeasonStats.player_id == player_id,
            DBPlayerSeasonStats.year == year,
            DBPlayerSeasonStats.adp.isnot(None),
        )
        .scalar()
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "game_logs_updated_at": _iso(logs_at),
        "season_stats_updated_at": _iso(season_at),
        "adp_updated_at": _iso(adp_at),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: 14 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_evidence.py tests/test_agent_evidence.py
git commit -m "feat(agent): add existing projections and data freshness to evidence pack"
```

---

### Task 5: `schedule` and `sportsbook` blocks

**Files:**
- Modify: `src/pigskin_mastermind/services/agent_evidence.py`
- Test: `tests/test_agent_evidence.py`

**Interfaces:**
- Consumes: `DBNFLGame`, and `SportsbookProjectionService.project_player_by_id(player_id)` from `services/sportsbook_projection_service.py`, which returns a dict with `total_projected_points` and `categories`.
- Produces: `evidence["schedule"]` — list of dicts ordered by week. `evidence["sportsbook"]` — dict or `None`.

- [ ] **Step 1: Write the failing tests**

Add `DBNFLGame` to the model imports in the test file.

```python
@pytest.fixture
def schedule(db):
    db.add(DBNFLGame(
        year=2026, week=1, home_team="ATL", away_team="NO",
        home_score=24, away_score=17, roof="dome",
    ))
    db.add(DBNFLGame(
        year=2026, week=2, home_team="TB", away_team="ATL", roof="outdoors",
    ))
    db.commit()


def test_schedule_names_opponent_and_home_away(db, player, schedule):
    ev = build_evidence(db, player.id, 2026)
    games = ev["schedule"]
    assert games[0] == {
        "week": 1, "opponent": "NO", "home": True,
        "played": True, "roof": "dome",
    }
    assert games[1]["opponent"] == "TB"
    assert games[1]["home"] is False
    assert games[1]["played"] is False


def test_weekly_scope_shows_only_that_week_onward(db, player, schedule):
    ev = build_evidence(db, player.id, 2026, week=2)
    assert [g["week"] for g in ev["schedule"]] == [2]


def test_sportsbook_is_none_when_no_props_stored(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["sportsbook"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: FAIL with `KeyError: 'schedule'`

- [ ] **Step 3: Write the implementation**

Extend imports:

```python
from sqlalchemy import func, or_

from pigskin_mastermind.models.database import (
    DBNFLGame, DBPlayer, DBPlayerGameLog, DBPlayerProjection,
    DBPlayerSeasonStats,
)
```

Add to the returned dict:

```python
        "schedule": _schedule_block(db, player, year, week),
        "sportsbook": _sportsbook_block(db, player_id),
```

```python
def _schedule_block(
    db: Session, player: DBPlayer, year: int, week: Optional[int],
) -> list:
    """The player's team schedule, forward-looking from *week*.

    ``DBNFLGame`` is the only forward-looking table in the schema. A NULL
    ``home_score`` is how "not yet played" is represented.
    """
    team = player.nfl_team
    query = db.query(DBNFLGame).filter(
        DBNFLGame.year == year,
        or_(DBNFLGame.home_team == team, DBNFLGame.away_team == team),
    )
    if week is not None:
        query = query.filter(DBNFLGame.week >= week)

    return [
        {
            "week": g.week,
            "opponent": g.away_team if g.home_team == team else g.home_team,
            "home": g.home_team == team,
            "played": g.home_score is not None,
            "roof": g.roof,
        }
        for g in query.order_by(DBNFLGame.week).all()
    ]


def _sportsbook_block(db: Session, player_id: int) -> Optional[Dict[str, Any]]:
    """Prop-derived projection, when props for this player are stored.

    Resolved by ``DBPlayer.id`` rather than by name: the name path matches
    ``description ILIKE '%name%'``, which both over- and under-matches.

    Returns ``None`` rather than a zeroed structure so the agent can tell
    "no props available" from "props say zero".
    """
    from pigskin_mastermind.services.sportsbook_projection_service import (
        SportsbookProjectionService,
    )

    result = SportsbookProjectionService(db).project_player_by_id(player_id)
    if not result.get("categories"):
        return None
    return {
        "total_projected_points": result.get("total_projected_points"),
        "categories": result.get("categories"),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: 17 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_evidence.py tests/test_agent_evidence.py
git commit -m "feat(agent): add schedule and sportsbook blocks to evidence pack"
```

---

### Task 6: `as_of_week` truncation

**Files:**
- Modify: `src/pigskin_mastermind/services/agent_evidence.py`
- Test: `tests/test_agent_evidence.py`

**Interfaces:**
- Produces: when `as_of_week=W`, `evidence["game_logs"]` contains no row with `year == year and week >= W`; `evidence["schedule"]` entries from week `W` onward are marked `played: False` regardless of stored score; `evidence["criteria"]` is `None` and `evidence["criteria_omitted_reason"]` is a non-empty string.

This is the task that backs the backtest-honesty claim in the spec. Without these tests the `--as-of` flag is a lie.

- [ ] **Step 1: Write the failing tests**

```python
def test_as_of_excludes_the_cutoff_week_and_later(db, player, stats):
    ev = build_evidence(db, player.id, 2025, week=3, as_of_week=3)
    weeks = [g["week"] for g in ev["game_logs"] if g["year"] == 2025]
    assert weeks == [1, 2]


def test_as_of_keeps_prior_seasons_intact(db, player, stats):
    for wk in (1, 2, 3):
        db.add(DBPlayerGameLog(
            player_id=player.id, year=2024, week=wk, fantasy_points=10.0,
        ))
    db.commit()

    ev = build_evidence(db, player.id, 2025, week=2, as_of_week=2)
    prior = [g["week"] for g in ev["game_logs"] if g["year"] == 2024]
    assert prior == [1, 2, 3]


def test_as_of_hides_results_of_games_at_or_after_cutoff(db, player, schedule):
    ev = build_evidence(db, player.id, 2026, week=1, as_of_week=1)
    assert ev["schedule"][0]["week"] == 1
    assert ev["schedule"][0]["played"] is False


def test_as_of_omits_criteria_with_a_stated_reason(db, player, stats):
    ev = build_evidence(db, player.id, 2025, week=3, as_of_week=3)
    assert ev["criteria"] is None
    assert "cutoff" in ev["criteria_omitted_reason"]


def test_criteria_reason_absent_when_not_backtesting(db, player, stats):
    ev = build_evidence(db, player.id, 2026, week=3)
    assert ev["criteria"] is not None
    assert ev["criteria_omitted_reason"] is None
```

Note the `schedule` fixture builds 2026 games and the `stats` fixture builds 2025 logs, so these tests use whichever year matches their fixture.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: FAIL — logs at and after the cutoff are still present, and `KeyError: 'criteria_omitted_reason'`

- [ ] **Step 3: Write the implementation**

Add the module constant:

```python
_CRITERIA_OMITTED_UNDER_AS_OF = (
    "ProjectionCriteriaBuilder has no as-of cutoff, so its output reflects the "
    "full season. Including it here would leak post-cutoff data into a "
    "backtest, so the block is omitted instead."
)
```

Rewrite `build_evidence`'s return to thread the cutoff through:

```python
    criteria = None
    criteria_reason = None
    if as_of_week is None:
        criteria = _criteria_block(db, player_id, year, week)
    else:
        criteria_reason = _CRITERIA_OMITTED_UNDER_AS_OF

    return {
        "player": _player_block(player),
        "context": _context_block(year, week),
        "season_stats": _season_stats_block(db, player_id, year),
        "game_logs": _game_logs_block(db, player_id, year, as_of_week),
        "criteria": criteria,
        "criteria_omitted_reason": criteria_reason,
        "existing_projections": _existing_projections_block(
            db, player_id, year, week,
        ),
        "schedule": _schedule_block(db, player, year, week, as_of_week),
        "sportsbook": _sportsbook_block(db, player_id),
        "data_freshness": _data_freshness_block(db, player_id, year),
    }
```

Update `_game_logs_block` to take and apply the cutoff:

```python
def _game_logs_block(
    db: Session,
    player_id: int,
    year: int,
    as_of_week: Optional[int] = None,
) -> list:
    query = db.query(DBPlayerGameLog).filter(
        DBPlayerGameLog.player_id == player_id,
        DBPlayerGameLog.year <= year,
    )
    if as_of_week is not None:
        # Only the target season is truncated. Prior seasons are entirely in
        # the past relative to the cutoff and stay whole.
        query = query.filter(
            or_(
                DBPlayerGameLog.year < year,
                DBPlayerGameLog.week < as_of_week,
            )
        )

    rows = (
        query.order_by(
            DBPlayerGameLog.year.desc(), DBPlayerGameLog.week.desc(),
        )
        .limit(_GAME_LOG_LIMIT)
        .all()
    )
    rows.reverse()
    return [
        {
            "year": r.year, "week": r.week, "opponent": r.opponent,
            "pass_att": r.pass_att, "pass_yd": r.pass_yd, "pass_td": r.pass_td,
            "pass_int": r.pass_int,
            "rush_att": r.rush_att, "rush_yd": r.rush_yd, "rush_td": r.rush_td,
            "targets": r.targets, "rec": r.rec,
            "rec_yd": r.rec_yd, "rec_td": r.rec_td,
            "fumbles_lost": r.fumbles_lost,
            "fantasy_points": r.fantasy_points,
            "source": r.source,
        }
        for r in rows
    ]
```

Update `_schedule_block` to hide post-cutoff results:

```python
def _schedule_block(
    db: Session,
    player: DBPlayer,
    year: int,
    week: Optional[int],
    as_of_week: Optional[int] = None,
) -> list:
    team = player.nfl_team
    query = db.query(DBNFLGame).filter(
        DBNFLGame.year == year,
        or_(DBNFLGame.home_team == team, DBNFLGame.away_team == team),
    )
    if week is not None:
        query = query.filter(DBNFLGame.week >= week)

    games = []
    for g in query.order_by(DBNFLGame.week).all():
        played = g.home_score is not None
        if as_of_week is not None and g.week >= as_of_week:
            # The score exists in the database but had not happened yet at the
            # cutoff. Reporting it would hand a backtest the answer.
            played = False
        games.append({
            "week": g.week,
            "opponent": g.away_team if g.home_team == team else g.home_team,
            "home": g.home_team == team,
            "played": played,
            "roof": g.roof,
        })
    return games
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: 22 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_evidence.py tests/test_agent_evidence.py
git commit -m "feat(agent): honour --as-of cutoff across logs, schedule, and criteria"
```

---

### Task 7: `evidence_hash` and the `pigskin agent evidence` command

**Files:**
- Modify: `src/pigskin_mastermind/services/agent_evidence.py`
- Modify: `src/pigskin_mastermind/cli.py`
- Test: `tests/test_agent_evidence.py`

**Interfaces:**
- Produces: `evidence_hash(evidence: Dict[str, Any]) -> str` — a 64-char hex SHA-256, stable across repeated calls on unchanged data. `build_evidence` returns the hash at `evidence["evidence_hash"]`.
- CLI: `pigskin agent evidence --player-id N --year Y [--week W] [--as-of W]` printing the document as JSON to stdout.

`data_freshness.generated_at` is wall-clock and changes every call, so it must be excluded from the hash or the hash is worthless.

- [ ] **Step 1: Write the failing tests**

```python
def test_hash_is_stable_across_identical_calls(db, player, stats):
    from pigskin_mastermind.services.agent_evidence import evidence_hash
    first = build_evidence(db, player.id, 2026)
    second = build_evidence(db, player.id, 2026)
    assert first["evidence_hash"] == second["evidence_hash"]
    assert len(first["evidence_hash"]) == 64


def test_hash_changes_when_underlying_data_changes(db, player, stats):
    before = build_evidence(db, player.id, 2026)["evidence_hash"]
    db.add(DBPlayerGameLog(
        player_id=player.id, year=2025, week=6, fantasy_points=31.0,
    ))
    db.commit()
    after = build_evidence(db, player.id, 2026)["evidence_hash"]
    assert before != after


def test_hash_ignores_the_generated_at_timestamp(db, player, stats):
    from pigskin_mastermind.services.agent_evidence import evidence_hash
    ev = build_evidence(db, player.id, 2026)
    mutated = dict(ev)
    mutated["data_freshness"] = dict(ev["data_freshness"])
    mutated["data_freshness"]["generated_at"] = "1999-01-01T00:00:00+00:00"
    assert evidence_hash(mutated) == evidence_hash(ev)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: FAIL with `ImportError: cannot import name 'evidence_hash'`

- [ ] **Step 3: Write the implementation**

Add `import hashlib` and `import json` to `agent_evidence.py`:

```python
def evidence_hash(evidence: Dict[str, Any]) -> str:
    """Stable fingerprint of the *data* in an evidence document.

    A stored projection records the hash of the evidence it was derived from,
    which is what makes a disagreement between two runs diagnosable: same hash
    means the agent changed its mind, different hash means the data moved.

    ``generated_at`` and the hash field itself are excluded — both change on
    every call and neither is data about the player.
    """
    payload = {k: v for k, v in evidence.items() if k != "evidence_hash"}
    freshness = payload.get("data_freshness")
    if isinstance(freshness, dict):
        payload["data_freshness"] = {
            k: v for k, v in freshness.items() if k != "generated_at"
        }
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
```

At the end of `build_evidence`, replace the bare `return {...}` with:

```python
    evidence = {
        "player": _player_block(player),
        "context": _context_block(year, week),
        "season_stats": _season_stats_block(db, player_id, year),
        "game_logs": _game_logs_block(db, player_id, year, as_of_week),
        "criteria": criteria,
        "criteria_omitted_reason": criteria_reason,
        "existing_projections": _existing_projections_block(
            db, player_id, year, week,
        ),
        "schedule": _schedule_block(db, player, year, week, as_of_week),
        "sportsbook": _sportsbook_block(db, player_id),
        "data_freshness": _data_freshness_block(db, player_id, year),
    }
    evidence["evidence_hash"] = evidence_hash(evidence)
    return evidence
```

Now add the CLI group. In `src/pigskin_mastermind/cli.py`, after the existing `projections` group block:

```python
@main.group()
def agent():
    """Commands that serve the Claude Code projection agents.

    These exist so an agent can obtain everything known about a player in one
    call, and write its conclusion back through a validated path. Nothing here
    invokes an LLM.
    """
    pass


@agent.command('evidence')
@click.option('--player-id', required=True, type=int,
              help='Database player ID (DBPlayer.id, not the prefixed string)')
@click.option('--year', type=int, default=None,
              help='Season year (defaults to the current fantasy season)')
@click.option('--week', type=int, default=None,
              help='Target week. Omit for season scope.')
@click.option('--as-of', 'as_of_week', type=int, default=None,
              help='Backtest cutoff: exclude all data from this week onward')
def agent_evidence(player_id, year, week, as_of_week):
    """Print everything known about one player as a single JSON document.

    This is the input contract for the player-analyst agent::

        pigskin agent evidence --player-id 412 --year 2026 --week 5
    """
    import json as _json

    from pigskin_mastermind.services.agent_evidence import build_evidence
    from pigskin_mastermind.utils.season import current_fantasy_season

    year = year or current_fantasy_season()
    db = _get_stats_db()
    try:
        evidence = build_evidence(
            db, player_id, year, week=week, as_of_week=as_of_week,
        )
        # The criteria builder lazily creates missing team/defense stat rows as
        # a side effect, so this read path has writes to flush.
        db.commit()
        click.echo(_json.dumps(evidence, indent=2, default=str))
    finally:
        db.close()
```

- [ ] **Step 4: Run tests and exercise the command**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: 25 passed

Run: `.venv/Scripts/python -m pigskin_mastermind.cli agent evidence --help`
Expected: usage text listing `--player-id`, `--year`, `--week`, `--as-of`

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_evidence.py src/pigskin_mastermind/cli.py tests/test_agent_evidence.py
git commit -m "feat(agent): add evidence hash and 'pigskin agent evidence' command"
```

---

### Task 8: Result validation

**Files:**
- Create: `src/pigskin_mastermind/services/agent_projection.py`
- Test: `tests/test_agent_projection.py`

**Interfaces:**
- Produces: `class ResultRejected(ValueError)`; `validate_result(db: Session, result: Dict[str, Any], *, web_allowed: bool = True) -> Dict[str, Any]` returning the normalized result or raising `ResultRejected`.

These are the five rules from the spec. They live in Python, not in a prompt, because a prompt instruction is a suggestion and this is a gate.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_agent_projection.py`:

```python
"""Validation and persistence of agent-produced projections."""

import copy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerProjection,
)
from pigskin_mastermind.services.agent_projection import (
    ResultRejected, validate_result,
)


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


@pytest.fixture
def player(db):
    p = DBPlayer(
        player_id="espn_1", name="Test Back", position="RB", nfl_team="ATL",
    )
    db.add(p)
    db.commit()
    return p


@pytest.fixture
def result(player):
    return {
        "player_id": player.id,
        "year": 2026,
        "week": None,
        "projected_points": 244.5,
        "floor": 188.0,
        "ceiling": 301.0,
        "expected_games": 16.2,
        "confidence": "medium",
        "rationale": "Volume held up after the bye; line play improved.",
        "key_factors": [
            {"factor": "Target share up 4pts", "direction": "+",
             "magnitude_pts": 8.0, "source": "db"},
        ],
        "disagreement_with_model": "Model underweights the receiving role.",
        "web_used": False,
        "evidence_hash": "a" * 64,
    }


def test_valid_result_passes_through(db, result):
    assert validate_result(db, result, web_allowed=True) == result


def test_rejects_floor_above_points(db, result):
    result["floor"] = 260.0
    with pytest.raises(ResultRejected, match="floor"):
        validate_result(db, result)


def test_rejects_ceiling_below_points(db, result):
    result["ceiling"] = 200.0
    with pytest.raises(ResultRejected, match="ceiling"):
        validate_result(db, result)


def test_rejects_negative_points(db, result):
    result["projected_points"] = -5.0
    result["floor"] = -10.0
    with pytest.raises(ResultRejected, match="negative"):
        validate_result(db, result)


def test_rejects_absurd_season_total_without_a_model_row(db, result):
    result["projected_points"] = 900.0
    result["ceiling"] = 950.0
    with pytest.raises(ResultRejected, match="ceiling for RB"):
        validate_result(db, result)


def test_rejects_wild_departure_from_the_model_projection(db, player, result):
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="model",
        projected_points=240.0,
    ))
    db.commit()
    result["projected_points"] = 40.0
    result["floor"] = 20.0
    with pytest.raises(ResultRejected, match="model projection"):
        validate_result(db, result)


def test_accepts_a_large_but_defensible_departure(db, player, result):
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="model",
        projected_points=140.0,
    ))
    db.commit()
    result["projected_points"] = 280.0
    result["ceiling"] = 320.0
    assert validate_result(db, result)["projected_points"] == 280.0


def test_rejects_web_factor_without_a_url(db, result):
    result["key_factors"].append(
        {"factor": "Named starter in camp", "direction": "+",
         "magnitude_pts": 12.0, "source": "web"},
    )
    result["web_used"] = True
    with pytest.raises(ResultRejected, match="url"):
        validate_result(db, result)


def test_accepts_web_factor_with_a_url(db, result):
    result["key_factors"].append(
        {"factor": "Named starter in camp", "direction": "+",
         "magnitude_pts": 12.0, "source": "web",
         "url": "https://example.com/report"},
    )
    result["web_used"] = True
    assert validate_result(db, result, web_allowed=True)


def test_rejects_web_use_when_web_was_disallowed(db, result):
    result["web_used"] = True
    with pytest.raises(ResultRejected, match="--no-web"):
        validate_result(db, result, web_allowed=False)


def test_rejects_unknown_player(db, result):
    result["player_id"] = 9999
    with pytest.raises(ResultRejected, match="not found"):
        validate_result(db, result)


def test_rejects_missing_required_field(db, result):
    del result["rationale"]
    with pytest.raises(ResultRejected, match="rationale"):
        validate_result(db, result)


def test_weekly_scope_uses_the_weekly_ceiling(db, result):
    result["week"] = 5
    result["projected_points"] = 120.0
    # floor and ceiling must bracket the point estimate, or the interval check
    # fires first and this test would pass for the wrong reason.
    result["floor"] = 90.0
    result["ceiling"] = 130.0
    with pytest.raises(ResultRejected, match="ceiling for RB"):
        validate_result(db, result)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_projection.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pigskin_mastermind.services.agent_projection'`

- [ ] **Step 3: Write the implementation**

Create `src/pigskin_mastermind/services/agent_projection.py`:

```python
"""Validate and persist an agent-produced projection.

The rules here are the hallucination tripwire. They live in Python rather than
in the agent's prompt on purpose: a prompt instruction is a suggestion, and
this is a gate. Rejection is loud — the agent sees the reason and can correct,
which is strictly better than silently storing a bad number.

The bands are deliberately loose. They are sized to catch a unit error or a
misplaced decimal point, not to referee a debatable opinion.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection


class ResultRejected(ValueError):
    """An agent result violated a validation rule and was not stored."""


_REQUIRED_FIELDS = (
    "player_id", "year", "projected_points", "rationale", "confidence",
)

_CONFIDENCE_VALUES = ("low", "medium", "high")

# Season-scale ceilings by position. Used only when no model projection exists
# to compare against.
_SEASON_CEILINGS = {
    "QB": 600.0, "RB": 500.0, "WR": 500.0,
    "TE": 400.0, "K": 250.0, "DEF": 250.0,
}

# A week cannot plausibly be more than a tenth of a full season's ceiling.
_WEEKLY_DIVISOR = 10.0

# Multiples of the model projection outside which we assume an error rather
# than an opinion.
_MODEL_FLOOR_MULTIPLE = 0.25
_MODEL_CEILING_MULTIPLE = 3.0


def validate_result(
    db: Session,
    result: Dict[str, Any],
    *,
    web_allowed: bool = True,
) -> Dict[str, Any]:
    """Return *result* unchanged, or raise :class:`ResultRejected`."""
    for field in _REQUIRED_FIELDS:
        if field not in result or result[field] is None:
            raise ResultRejected(f"Missing required field: {field}")

    if result["confidence"] not in _CONFIDENCE_VALUES:
        raise ResultRejected(
            f"confidence must be one of {_CONFIDENCE_VALUES}, "
            f"got {result['confidence']!r}"
        )

    player = (
        db.query(DBPlayer)
        .filter(DBPlayer.id == result["player_id"])
        .first()
    )
    if player is None:
        raise ResultRejected(f"Player {result['player_id']} not found")

    points = float(result["projected_points"])
    if points < 0:
        raise ResultRejected(f"projected_points is negative: {points}")

    _check_interval(result, points)
    _check_band(db, player, result, points)
    _check_citations(result, web_allowed=web_allowed)

    return result


def _check_interval(result: Dict[str, Any], points: float) -> None:
    floor = result.get("floor")
    ceiling = result.get("ceiling")
    if floor is not None and float(floor) > points:
        raise ResultRejected(
            f"floor {floor} is above projected_points {points}"
        )
    if ceiling is not None and float(ceiling) < points:
        raise ResultRejected(
            f"ceiling {ceiling} is below projected_points {points}"
        )


def _check_band(
    db: Session,
    player: DBPlayer,
    result: Dict[str, Any],
    points: float,
) -> None:
    week = result.get("week")
    model = _model_projection(db, player.id, result["year"], week)

    if model is not None and model > 0:
        low = model * _MODEL_FLOOR_MULTIPLE
        high = model * _MODEL_CEILING_MULTIPLE
        if not low <= points <= high:
            raise ResultRejected(
                f"projected_points {points} is outside [{low:.1f}, "
                f"{high:.1f}], the sanity band around the model projection "
                f"of {model:.1f}"
            )
        return

    ceiling = _SEASON_CEILINGS.get(player.position)
    if ceiling is None:
        # An unrecognized position means positions were not normalized
        # upstream. Skipping the check is safer than rejecting a real result.
        return
    if week is not None:
        ceiling = ceiling / _WEEKLY_DIVISOR
    if points > ceiling:
        raise ResultRejected(
            f"projected_points {points} exceeds the absolute ceiling for "
            f"{player.position} at this scope ({ceiling:.1f})"
        )


def _model_projection(
    db: Session, player_id: int, year: int, week: Optional[int],
) -> Optional[float]:
    query = db.query(DBPlayerProjection).filter(
        DBPlayerProjection.player_id == player_id,
        DBPlayerProjection.year == year,
        DBPlayerProjection.source == "model",
    )
    if week is None:
        query = query.filter(DBPlayerProjection.week.is_(None))
    else:
        query = query.filter(DBPlayerProjection.week == week)
    row = query.first()
    return row.projected_points if row else None


def _check_citations(result: Dict[str, Any], *, web_allowed: bool) -> None:
    if result.get("web_used") and not web_allowed:
        raise ResultRejected(
            "Result reports web_used=true but the run was launched --no-web"
        )

    for factor in result.get("key_factors") or []:
        if factor.get("source") == "web" and not factor.get("url"):
            raise ResultRejected(
                f"key_factor {factor.get('factor')!r} is sourced from the web "
                f"but carries no url"
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_projection.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_projection.py tests/test_agent_projection.py
git commit -m "feat(agent): validate agent projection results before storage"
```

---

### Task 9: Persist a validated result

**Files:**
- Modify: `src/pigskin_mastermind/services/agent_projection.py`
- Test: `tests/test_agent_projection.py`

**Interfaces:**
- Consumes: `validate_result` from Task 8.
- Produces: `record_llm_projection(db: Session, result: Dict[str, Any], *, web_allowed: bool = True) -> DBPlayerProjection`. Validates first, then upserts a `source='llm'` row and commits.

Re-running the same player and scope must **update**, not duplicate. `uq_player_projection_season` is a partial unique index covering season rows (`week IS NULL`); `uq_player_projection` covers weekly rows.

- [ ] **Step 1: Write the failing tests**

```python
def test_writes_an_llm_row_with_columns_and_components(db, player, result):
    from pigskin_mastermind.services.agent_projection import (
        record_llm_projection,
    )
    row = record_llm_projection(db, result)

    assert row.source == "llm"
    assert row.player_id == player.id
    assert row.year == 2026
    assert row.week is None
    assert row.projected_points == 244.5
    assert row.floor == 188.0
    assert row.ceiling == 301.0
    assert row.expected_games == 16.2

    assert row.components["confidence"] == "medium"
    assert row.components["rationale"].startswith("Volume held up")
    assert row.components["key_factors"][0]["magnitude_pts"] == 8.0
    assert row.components["evidence_hash"] == "a" * 64
    assert row.components["web_used"] is False


def test_rerunning_updates_rather_than_duplicating(db, player, result):
    from pigskin_mastermind.services.agent_projection import (
        record_llm_projection,
    )
    record_llm_projection(db, result)

    second = copy.deepcopy(result)
    second["projected_points"] = 251.0
    second["rationale"] = "Revised after the depth chart moved."
    record_llm_projection(db, second)

    rows = (
        db.query(DBPlayerProjection)
        .filter_by(player_id=player.id, source="llm")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].projected_points == 251.0
    assert rows[0].components["rationale"].startswith("Revised")


def test_season_and_weekly_rows_coexist(db, player, result):
    from pigskin_mastermind.services.agent_projection import (
        record_llm_projection,
    )
    record_llm_projection(db, result)

    weekly = copy.deepcopy(result)
    weekly["week"] = 5
    weekly["projected_points"] = 15.2
    weekly["floor"] = 6.0
    weekly["ceiling"] = 27.0
    record_llm_projection(db, weekly)

    rows = (
        db.query(DBPlayerProjection)
        .filter_by(player_id=player.id, source="llm")
        .all()
    )
    assert len(rows) == 2


def test_invalid_result_writes_nothing(db, player, result):
    from pigskin_mastermind.services.agent_projection import (
        record_llm_projection,
    )
    result["floor"] = 999.0
    with pytest.raises(ResultRejected):
        record_llm_projection(db, result)

    assert db.query(DBPlayerProjection).count() == 0


def test_does_not_disturb_the_model_row(db, player, result):
    from pigskin_mastermind.services.agent_projection import (
        record_llm_projection,
    )
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="model",
        projected_points=240.0,
    ))
    db.commit()

    record_llm_projection(db, result)

    model = (
        db.query(DBPlayerProjection)
        .filter_by(player_id=player.id, source="model")
        .one()
    )
    assert model.projected_points == 240.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_projection.py -v`
Expected: FAIL with `ImportError: cannot import name 'record_llm_projection'`

- [ ] **Step 3: Write the implementation**

Append to `agent_projection.py`:

```python
LLM_SOURCE = "llm"

# Fields that live in real columns; everything else in the result goes to
# ``components``.
_COLUMN_FIELDS = (
    "player_id", "year", "week", "projected_points",
    "floor", "ceiling", "std_dev", "expected_games",
)


def record_llm_projection(
    db: Session,
    result: Dict[str, Any],
    *,
    web_allowed: bool = True,
) -> DBPlayerProjection:
    """Validate *result* and upsert it as a ``source='llm'`` row.

    Raises:
        ResultRejected: If validation fails. Nothing is written in that case.
    """
    validate_result(db, result, web_allowed=web_allowed)

    week = result.get("week")
    query = db.query(DBPlayerProjection).filter(
        DBPlayerProjection.player_id == result["player_id"],
        DBPlayerProjection.year == result["year"],
        DBPlayerProjection.source == LLM_SOURCE,
    )
    if week is None:
        query = query.filter(DBPlayerProjection.week.is_(None))
    else:
        query = query.filter(DBPlayerProjection.week == week)

    row = query.first()
    if row is None:
        row = DBPlayerProjection(
            player_id=result["player_id"],
            year=result["year"],
            week=week,
            source=LLM_SOURCE,
        )
        db.add(row)

    row.projected_points = float(result["projected_points"])
    row.floor = _opt_float(result.get("floor"))
    row.ceiling = _opt_float(result.get("ceiling"))
    row.std_dev = _opt_float(result.get("std_dev"))
    row.expected_games = _opt_float(result.get("expected_games"))

    # Everything the schema has no column for. Assigning a fresh dict rather
    # than mutating in place is what makes SQLAlchemy notice the change on a
    # JSON column.
    row.components = {
        k: v for k, v in result.items() if k not in _COLUMN_FIELDS
    }

    db.commit()
    db.refresh(row)
    return row


def _opt_float(value: Any) -> Optional[float]:
    return None if value is None else float(value)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_projection.py -v`
Expected: 18 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_projection.py tests/test_agent_projection.py
git commit -m "feat(agent): upsert validated agent projections as source='llm'"
```

---

### Task 10: `pigskin agent record-projection` and a pinned contract fixture

**Files:**
- Modify: `src/pigskin_mastermind/cli.py`
- Create: `tests/fixtures/agent_result_season.json`
- Test: `tests/test_agent_projection.py`

**Interfaces:**
- Consumes: `record_llm_projection` from Task 9.
- CLI: `pigskin agent record-projection --result-file PATH [--no-web]`. Exit 0 with a one-line confirmation on success; exit 1 with the rejection reason on stderr on failure.

The checked-in fixture is the contract the agent's `projection-evidence` skill will be written against in Phase 2. A test loads it so a change to either side breaks a test rather than a live run.

- [ ] **Step 1: Write the failing test and create the fixture**

Create `tests/fixtures/agent_result_season.json`:

```json
{
  "player_id": 1,
  "year": 2026,
  "week": null,
  "projected_points": 244.5,
  "floor": 188.0,
  "ceiling": 301.0,
  "expected_games": 16.2,
  "confidence": "medium",
  "rationale": "Target share climbed four points after the week 6 bye and held. The model's touch-share criterion is built on the full season and therefore understates the second-half role.",
  "key_factors": [
    {
      "factor": "Post-bye target share up 4 points",
      "direction": "+",
      "magnitude_pts": 8.0,
      "source": "db"
    },
    {
      "factor": "Offensive coordinator retained",
      "direction": "+",
      "magnitude_pts": 3.0,
      "source": "web",
      "url": "https://example.com/coordinator-retained"
    }
  ],
  "disagreement_with_model": "Model projects 231; the gap is almost entirely the second-half receiving role.",
  "web_used": true,
  "evidence_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}
```

Append to `tests/test_agent_projection.py`:

```python
import json
from pathlib import Path

FIXTURE = (
    Path(__file__).parent / "fixtures" / "agent_result_season.json"
)


def test_checked_in_fixture_satisfies_the_contract(db, player):
    """The canned result is what Phase 2's skill will be written against.

    If this breaks, the documented output contract and the validator have
    drifted apart — fix one of them deliberately rather than editing the
    fixture to make the test green.
    """
    from pigskin_mastermind.services.agent_projection import (
        record_llm_projection,
    )
    payload = json.loads(FIXTURE.read_text())
    payload["player_id"] = player.id

    row = record_llm_projection(db, payload, web_allowed=True)
    assert row.source == "llm"
    assert row.components["web_used"] is True
    web_factors = [
        f for f in row.components["key_factors"] if f["source"] == "web"
    ]
    assert web_factors and all(f.get("url") for f in web_factors)
```

- [ ] **Step 2: Run the test**

Run: `.venv/Scripts/python -m pytest tests/test_agent_projection.py::test_checked_in_fixture_satisfies_the_contract -v`
Expected: **PASS.** This is the one test in the plan that does not start red, and that is correct — it is a regression pin over behavior Task 9 already built, not a driver for new behavior. Its job is to fail *later*, if the documented contract and the validator ever drift apart. If it fails now, the fixture and the validator already disagree; fix whichever is wrong rather than editing the fixture until the test goes green.

- [ ] **Step 3: Add the CLI command**

In `src/pigskin_mastermind/cli.py`, after `agent_evidence`:

```python
@agent.command('record-projection')
@click.option('--result-file', required=True,
              type=click.Path(exists=True, dir_okay=False),
              help='Path to the agent result JSON')
@click.option('--web/--no-web', 'web_allowed', default=True,
              help='Whether this run was permitted to use the web. '
                   '--no-web rejects a result claiming web sources.')
def agent_record_projection(result_file, web_allowed):
    """Validate an agent result and store it as a source='llm' projection.

    Exits non-zero with the reason on stderr when a result is rejected, so the
    agent can see what was wrong and correct it::

        pigskin agent record-projection --result-file out.json
    """
    import json as _json

    from pigskin_mastermind.services.agent_projection import (
        ResultRejected, record_llm_projection,
    )

    with open(result_file, 'r', encoding='utf-8') as fh:
        payload = _json.load(fh)

    db = _get_stats_db()
    try:
        row = record_llm_projection(db, payload, web_allowed=web_allowed)
    except ResultRejected as exc:
        raise click.ClickException(f"Result rejected: {exc}")
    finally:
        db.close()

    scope = "season" if row.week is None else f"week {row.week}"
    click.echo(
        f"Stored llm projection for player {row.player_id} "
        f"({row.year} {scope}): {row.projected_points:.1f} pts"
    )
```

- [ ] **Step 4: Run the full suite and exercise the command**

Run: `.venv/Scripts/python -m pytest tests/test_agent_projection.py tests/test_agent_evidence.py -v`
Expected: 44 passed

Run: `.venv/Scripts/python -m pigskin_mastermind.cli agent record-projection --help`
Expected: usage text listing `--result-file` and `--web/--no-web`

Run: `.venv/Scripts/python -m pytest tests/`
Expected: no *new* failures. The suite has 17 known pre-existing failures documented in `docs/PROJECT_STATUS.md`; compare against that baseline rather than expecting green.

- [ ] **Step 5: Format, lint, and commit**

```bash
.venv/Scripts/python -m black src/ tests/
.venv/Scripts/python -m flake8 src/ tests/
```

```bash
git add src/pigskin_mastermind/cli.py tests/fixtures/agent_result_season.json tests/test_agent_projection.py
git commit -m "feat(agent): add 'pigskin agent record-projection' and pin the result contract"
```

---

### Task 11: `news` block

**Files:**
- Modify: `src/pigskin_mastermind/services/agent_evidence.py`
- Test: `tests/test_agent_evidence.py`

**Interfaces:**
- Consumes: `DBPlayerNews` from `pigskin_mastermind.models.database`.
- Produces: `evidence["news"]` — a list of dicts ordered newest first, at most 10 entries.

`DBPlayerNews` and `services/player_news_service.py` landed after this plan was first written. Cached ESPN headlines are exactly the kind of information the agent would otherwise pay for a web search to get, so the pack should hand them over for free.

**Two things this block must not do:**

1. **Never call `PlayerNewsService.get_player_news()`.** That method triggers a live ESPN fetch when the cache is older than `max_age_minutes`, which would turn `pigskin agent evidence` into a network call and destroy both its offline guarantee and its determinism. This block reads `DBPlayerNews` rows directly and reports how stale they are, leaving the decision to refresh with the caller.
2. **Respect `as_of_week`.** A headline published after the backtest cutoff is exactly the kind of leakage `--as-of` exists to prevent. Since news carries a timestamp rather than a week number, the cutoff is resolved to a date via the team's `DBNFLGame` kickoff for that week.

- [ ] **Step 1: Write the failing tests**

Add `DBPlayerNews` to the model imports in `tests/test_agent_evidence.py`.

```python
from datetime import datetime


@pytest.fixture
def news(db, player):
    db.add(DBPlayerNews(
        player_id=player.id, espn_headline_id="h1",
        headline="Named starter for week 1",
        description="Coach confirmed the job is his.",
        source_url="https://example.com/h1",
        published_at=datetime(2026, 8, 20, 12, 0),
        fetched_at=datetime(2026, 8, 20, 13, 0),
    ))
    db.add(DBPlayerNews(
        player_id=player.id, espn_headline_id="h2",
        headline="Limited in practice",
        published_at=datetime(2026, 9, 18, 9, 0),
        fetched_at=datetime(2026, 9, 18, 10, 0),
    ))
    db.commit()


def test_news_block_is_newest_first(db, player, news):
    ev = build_evidence(db, player.id, 2026)
    headlines = [n["headline"] for n in ev["news"]]
    assert headlines == ["Limited in practice", "Named starter for week 1"]
    assert ev["news"][1]["source_url"] == "https://example.com/h1"
    assert ev["news"][1]["description"].startswith("Coach confirmed")


def test_news_block_reports_cache_age(db, player, news):
    ev = build_evidence(db, player.id, 2026)
    assert ev["data_freshness"]["news_fetched_at"] is not None


def test_news_is_empty_list_when_none_cached(db, player):
    assert build_evidence(db, player.id, 2026)["news"] == []


def test_news_after_the_as_of_cutoff_is_excluded(db, player, news, schedule):
    # The schedule fixture puts week 2 of 2026 on the calendar; anything
    # published on or after that kickoff is post-cutoff.
    db.query(DBNFLGame).filter_by(year=2026, week=2).update(
        {"kickoff_at": datetime(2026, 9, 14, 17, 0)}
    )
    db.commit()

    ev = build_evidence(db, player.id, 2026, week=2, as_of_week=2)
    headlines = [n["headline"] for n in ev["news"]]
    assert headlines == ["Named starter for week 1"]


def test_news_unfiltered_when_cutoff_date_is_unknown(db, player, news):
    """No schedule row means no date to compare against.

    Dropping all news would be worse than keeping it: the block carries
    published_at, so the agent can judge for itself.
    """
    ev = build_evidence(db, player.id, 2026, week=2, as_of_week=2)
    assert len(ev["news"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: FAIL with `KeyError: 'news'`

- [ ] **Step 3: Write the implementation**

Add `DBPlayerNews` to the imports in `agent_evidence.py` and the constant:

```python
# Enough to see a role change or an injury designation without flooding the
# agent's context with a season of transaction wire noise.
_NEWS_LIMIT = 10
```

Add to the evidence dict built in `build_evidence`, after `"sportsbook"`:

```python
        "news": _news_block(db, player, year, as_of_week),
```

```python
def _news_block(
    db: Session,
    player: DBPlayer,
    year: int,
    as_of_week: Optional[int] = None,
) -> list:
    """Cached ESPN headlines for this player.

    Reads the cache directly and never calls
    ``PlayerNewsService.get_player_news()``, which triggers a live ESPN fetch
    when the cache is stale. This command must stay offline and deterministic:
    a network call here would make two runs of the same backtest disagree.
    Staleness is reported in ``data_freshness`` so the caller can decide
    whether to refresh out of band.
    """
    query = db.query(DBPlayerNews).filter(DBPlayerNews.player_id == player.id)

    cutoff = _cutoff_datetime(db, player, year, as_of_week)
    if cutoff is not None:
        query = query.filter(
            or_(
                DBPlayerNews.published_at.is_(None),
                DBPlayerNews.published_at < cutoff,
            )
        )

    rows = (
        query.order_by(DBPlayerNews.published_at.desc())
        .limit(_NEWS_LIMIT)
        .all()
    )
    return [
        {
            "headline": r.headline,
            "description": r.description,
            "source_url": r.source_url,
            "published_at": _iso(r.published_at),
        }
        for r in rows
    ]


def _cutoff_datetime(
    db: Session,
    player: DBPlayer,
    year: int,
    as_of_week: Optional[int],
) -> Optional[datetime]:
    """Kickoff of the player's game in *as_of_week*, or ``None``.

    News is timestamped, not week-numbered, so the week cutoff has to be
    resolved to a date. Returning ``None`` when the schedule has no kickoff
    leaves news unfiltered — the rows carry ``published_at``, so the agent can
    still judge recency itself, which beats silently dropping everything.
    """
    if as_of_week is None:
        return None

    team = player.nfl_team
    game = (
        db.query(DBNFLGame)
        .filter(
            DBNFLGame.year == year,
            DBNFLGame.week == as_of_week,
            or_(DBNFLGame.home_team == team, DBNFLGame.away_team == team),
        )
        .first()
    )
    return game.kickoff_at if game else None
```

Add the news timestamp to `_data_freshness_block`, alongside the existing lookups:

```python
    news_at = (
        db.query(func.max(DBPlayerNews.fetched_at))
        .filter(DBPlayerNews.player_id == player_id)
        .scalar()
    )
```

and add to its returned dict:

```python
        "news_fetched_at": _iso(news_at),
```

`_data_freshness_block` already takes `(db, player_id, year)`, so its signature does not change.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_agent_evidence.py -v`
Expected: 30 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/agent_evidence.py tests/test_agent_evidence.py
git commit -m "feat(agent): add cached player news to the evidence pack"
```

---

## Done when

- `pigskin agent evidence --player-id N --year Y` prints a complete JSON document for a real player in the local database.
- `pigskin agent evidence --player-id N --year Y --week W --as-of W` omits every game at or after week `W`, reports no scores for those weeks, and states why criteria are absent.
- `pigskin agent record-projection --result-file tests/fixtures/agent_result_season.json` stores a row and prints a confirmation.
- The same command run twice updates one row rather than creating two.
- A result with a web-sourced factor and no URL exits non-zero with a readable reason.
- The evidence pack carries cached ESPN headlines without ever making a network call.
- `pytest tests/` shows no new failures against the documented baseline.

## Next phase

Phase 2 from the spec: the `player-analyst` agent definition, the `projection-evidence` skill written against `tests/fixtures/agent_result_season.json`, and `pigskin agent score` so the prospective track record starts accumulating.
