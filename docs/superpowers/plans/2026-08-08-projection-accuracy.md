# Trustworthy Player Projections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist one trustworthy projection per player per scope (season and week), blended from the house model, ESPN, and sportsbook props, and read it from every consumer that ranks players.

**Architecture:** A new `DBPlayerProjection` table stores one row per (player, year, week, source). A blender computes a weighted consensus into a `blend` row, renormalizing over whichever sources exist. A batch refresh service populates it. Consumers read `get_projection()` instead of the mixed-unit `DBPlayer.projected_points` column.

**Tech Stack:** Python 3.12, SQLAlchemy 2.x, Alembic, FastAPI, Click, pytest. SQLite.

**Spec:** [`docs/superpowers/specs/2026-08-07-projection-accuracy-design.md`](../specs/2026-08-07-projection-accuracy-design.md)

## Global Constraints

- Always scope pytest to `tests/` — bare `pytest` dies collecting the vendored `espn-api` tree.
- Windows venv interpreter is `.venv/Scripts/python`.
- Season rows (`week IS NULL`) store season **totals**. Weekly rows store that week's points. Per-game at season scope is `projected_points / expected_games`.
- `get_projection()` never falls back to `DBPlayer.projected_points` — it returns `None`.
- Positions normalize through `utils/positions.py::normalize_position()`; teams through `utils/nfl_teams.py::normalize_team()` (returns `None` meaning *leave stored value alone*).
- Every importer resolves identity through `services/player_identity.py::PlayerIdentityService.resolve()` before creating a row.
- Scoring is 0.5 PPR resolved via `get_scoring_settings()`, never hardcoded.
- Alembic head at plan start is `d4a1c6e9b2f7`.
- Baseline failure count at commit `a6768ed` is **17**. Never let the suite exceed that minus what the current stage has fixed.
- Run `black src/ tests/` and `flake8 src/ tests/` before each commit.

---

## Baseline: the 17 pre-existing failures

Eight stay broken for the whole plan (unrelated to projections):

```
tests/test_nfl_data_service.py::TestGetPlayByPlay  (7)
tests/unit/test_espn_sync.py::test_import_team_from_espn  (1)
```

Nine are fixed in Stage 4, because Stage 4 rewrites what they exercise:

```
tests/test_mock_draft.py::{test_draft_home_page, test_draft_simulate_page,
                           test_start_draft_falls_back_to_previous_year,
                           test_start_draft_with_espn_adp}
tests/integration/test_api_lineups.py::test_optimize_lineup
tests/integration/test_api_trades.py::test_analyze_trade
tests/integration/test_api_teams.py::test_get_teams_with_data
tests/integration/test_api_projection_tuner.py::{test_projection_tuner_page_has_algorithm_section,
                                                 test_projection_tuner_page_links_history_to_run_detail}
```

---

## File Structure

**Create:**
- `src/pigskin_mastermind/services/projection_blender.py` — weighted consensus over sources. Pure functions, no DB.
- `src/pigskin_mastermind/services/projection_refresh.py` — batch compute + upsert + the `get_projection` read path.
- `alembic/versions/e7b2d9f4a1c3_add_player_projections.py` — the new table.
- `tests/test_projection_blender.py`, `tests/test_projection_refresh.py`, `tests/test_projection_baseline.py`

**Modify:**
- `src/pigskin_mastermind/models/database.py` — add `DBPlayerProjection`.
- `src/pigskin_mastermind/services/adp_service.py` — add `import_espn_projections`; rewrite `_pool_projection`.
- `src/pigskin_mastermind/services/sportsbook_projection_service.py` — identity-based player match.
- `src/pigskin_mastermind/services/projection_algorithm_tuner.py` — missing import.
- `src/pigskin_mastermind/api/routes/{lineups,trades,players,teams,stats,projections}.py` — read the lookup.
- `src/pigskin_mastermind/cli.py` — new `projections` group.
- `tests/test_projection_criteria_builder.py` — re-baseline to property assertions.

---

## Task 1: Restore the missing `WeeklyProjectionService` import

`projection_algorithm_tuner.py:164` calls `WeeklyProjectionService(...)` but nothing imports it. 23 tests fail with `NameError`. This is the whole fix.

**Files:**
- Modify: `src/pigskin_mastermind/services/projection_algorithm_tuner.py:34-39`
- Test: `tests/test_projection_algorithm_tuner.py` (existing, currently failing)

**Interfaces:**
- Consumes: `WeeklyProjectionService` from `pigskin_mastermind.services.projection_service`
- Produces: nothing new — restores existing behavior

- [ ] **Step 1: Confirm the failure and its shape**

```bash
.venv/Scripts/python -m pytest tests/test_projection_algorithm_tuner.py -q 2>&1 | tail -5
```

Expected: `23 failed`, each `NameError: name 'WeeklyProjectionService' is not defined`.

- [ ] **Step 2: Add the import**

In `projection_algorithm_tuner.py`, directly after the existing
`from pigskin_mastermind.services.projection_criteria_builder import (ProjectionCriteriaBuilder,)` block, add:

```python
from pigskin_mastermind.services.projection_service import (
    WeeklyProjectionService,
)
```

- [ ] **Step 3: Verify those 23 pass**

```bash
.venv/Scripts/python -m pytest tests/test_projection_algorithm_tuner.py -q 2>&1 | tail -5
```

Expected: `23 passed` (or all-pass with no `NameError`).

- [ ] **Step 4: Confirm no collateral damage**

```bash
.venv/Scripts/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: failure count drops from 48 to 25.

- [ ] **Step 5: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add src/pigskin_mastermind/services/projection_algorithm_tuner.py
git commit -m "fix(tuner): import WeeklyProjectionService in the algorithm tuner"
```

---

## Task 2: Re-baseline the criteria-builder tests as property assertions

Four tests assert the old leaky math. `test_builds_valid_criteria` expects `21.875` — a look-ahead number that included the projected week. Replace exact-value assertions with properties that state *why* the number is what it is.

**Files:**
- Modify: `tests/test_projection_criteria_builder.py`
- Test: same file

**Interfaces:**
- Consumes: `ProjectionBaselines`, `shrink`, `FALLBACK_POSITION_PPG` from `pigskin_mastermind.services.projection_baseline`
- Produces: nothing — test-only

- [ ] **Step 1: See exactly what the four failures assert now**

```bash
.venv/Scripts/python -m pytest tests/test_projection_criteria_builder.py -q 2>&1 | grep -E "^(FAILED|E  )" | head -30
```

Record each expected-vs-actual pair before changing anything. The four are
`TestBuildWeeklyCriteria::test_builds_valid_criteria`,
`TestOpponentDefenseLevel::test_yearly_criteria_uses_schedule_defense`,
`TestScheduleDefenseLevel::test_schedule_avg_with_mixed_opponents`,
`TestMomentumComposite::test_momentum_blends_points_and_yards`.

- [ ] **Step 2: Replace the exact-value assertion in `test_builds_valid_criteria`**

The point of the test is that a weekly build produces coherent criteria, not that it produces one float. Assert the shape and the bounds, and pin the leakage property separately in Step 4.

```python
def test_builds_valid_criteria(self, db, sample_data):
    builder = ProjectionCriteriaBuilder(db)
    criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)

    assert isinstance(criteria, WeeklyProjectionCriteria)
    # Shrinkage pulls a short sample toward the positional prior, so the
    # anchor sits between the raw average and that prior rather than on
    # either. The old assertion of 21.875 was the raw full-season average
    # *including week 10* — the look-ahead this module exists to remove.
    assert 0 < criteria.historical_average_points < 21.875
    assert 0 <= criteria.player_skill_level <= 100
    assert 0 <= criteria.opponent_defense_level <= 100
    assert 1 <= criteria.opposing_defense_vs_position_rank <= 32
```

- [ ] **Step 3: Run it**

```bash
.venv/Scripts/python -m pytest tests/test_projection_criteria_builder.py::TestBuildWeeklyCriteria -v
```

Expected: PASS.

- [ ] **Step 4: Add the leakage regression test**

This is the test that would have caught the original defect. Add a new class at the end of `tests/test_projection_criteria_builder.py`:

```python
class TestNoLookAheadLeakage:
    """A projection must not move when the future changes."""

    def test_week_8_projection_ignores_later_weeks(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        before = builder.build_weekly_criteria(sample_data.id, week=8, year=2024)

        # Insert weeks 9-18 with wildly different production.
        for wk in range(9, 19):
            db.add(DBPlayerGameLog(
                player_id=sample_data.id, year=2024, week=wk,
                fantasy_points=99.0, receptions=20, receiving_yards=400,
            ))
        db.commit()

        after = ProjectionCriteriaBuilder(db).build_weekly_criteria(
            sample_data.id, week=8, year=2024,
        )
        assert after.historical_average_points == before.historical_average_points
        assert after.player_skill_level == before.player_skill_level
```

- [ ] **Step 5: Run the leakage test**

```bash
.venv/Scripts/python -m pytest tests/test_projection_criteria_builder.py::TestNoLookAheadLeakage -v
```

Expected: PASS. If it FAILS, the leakage fix in `projection_baseline.py` is incomplete — fix the source, not the test.

- [ ] **Step 6: Fix the remaining three failures**

For each of `test_yearly_criteria_uses_schedule_defense`,
`test_schedule_avg_with_mixed_opponents`, `test_momentum_blends_points_and_yards`:
read the assertion, decide whether the new value is *correct by design* (shrinkage / defense de-duplication changed it) or a real regression. If correct, update the expected value and add a one-line comment naming which change moved it. If a regression, fix the source.

- [ ] **Step 7: Whole file green**

```bash
.venv/Scripts/python -m pytest tests/test_projection_criteria_builder.py -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add tests/test_projection_criteria_builder.py
git commit -m "test(projections): assert leakage-free properties, not leaky values"
```

---

## Task 3: Add the shrinkage test and close out Stage 1

The remaining four failures are `test_projection_tuner.py` (1) and `test_api_projection_tuner.py` (3). Fix them, add the shrinkage property test the spec calls for, then checkpoint Phases 0–2.

**Files:**
- Modify: `tests/test_projection_tuner.py`, `tests/integration/test_api_projection_tuner.py`
- Create: `tests/test_projection_baseline.py`

**Interfaces:**
- Consumes: `shrink`, `ProjectionBaselines`, `FALLBACK_POSITION_PPG`, `DEFAULT_SHRINKAGE_GAMES` from `pigskin_mastermind.services.projection_baseline`
- Produces: nothing — test-only

- [ ] **Step 1: Diagnose the four**

```bash
.venv/Scripts/python -m pytest tests/test_projection_tuner.py tests/integration/test_api_projection_tuner.py -q 2>&1 | grep -E "^(FAILED|E  )" | head -20
```

`test_weekly_breakdown_has_steps` almost certainly asserts a step label that Task 0's rename changed ("Opponent Defense Adjustment" is now "Opponent Defense (folded into matchup step)" weekly). The three API tests assert page copy naming the old slider keys.

- [ ] **Step 2: Update the assertions to the new labels and keys**

Update each to the current names: `defense_rank_multiplier` (was `def_rank_multiplier`), `efficiency_cap` (was `efficiency_clamp`), plus the new `efficiency_baseline`, `home_field_multiplier`, `shrinkage_games` entries. Do not weaken assertions to `assert True` or drop them.

- [ ] **Step 3: Run those two files**

```bash
.venv/Scripts/python -m pytest tests/test_projection_tuner.py tests/integration/test_api_projection_tuner.py -q
```

Expected: `test_projection_tuner.py` fully green. `test_api_projection_tuner.py` has 2 known-baseline failures remaining (`..._has_algorithm_section`, `..._links_history_to_run_detail`) — those are Stage 4's.

- [ ] **Step 4: Write the shrinkage property test**

Create `tests/test_projection_baseline.py`:

```python
"""Properties of the shrunk, leakage-free baseline."""

from pigskin_mastermind.services.projection_baseline import (
    shrink, FALLBACK_POSITION_PPG, DEFAULT_SHRINKAGE_GAMES,
)


def test_small_sample_lands_nearer_the_prior():
    """A 2-game player is mostly prior; a 15-game player is mostly themselves.

    This is the whole point of shrinkage: without it a backup with two good
    games outranks a proven starter, which is exactly how the draft pool
    used to misprice replacement-level players.
    """
    prior, observed, k = 8.0, 20.0, DEFAULT_SHRINKAGE_GAMES

    two_games = shrink(observed, 2, prior, k)
    fifteen_games = shrink(observed, 15, prior, k)

    assert abs(two_games - prior) < abs(two_games - observed)
    assert abs(fifteen_games - observed) < abs(fifteen_games - prior)
    assert two_games < fifteen_games < observed


def test_zero_games_returns_the_prior_exactly():
    assert shrink(99.0, 0, 8.0, DEFAULT_SHRINKAGE_GAMES) == 8.0


def test_zero_k_disables_shrinkage():
    assert shrink(20.0, 2, 8.0, 0) == 20.0


def test_every_fantasy_position_has_a_fallback_prior():
    """A missing position prior silently becomes 0.0 and zeroes the player."""
    assert set(FALLBACK_POSITION_PPG) == {'QB', 'RB', 'WR', 'TE', 'K', 'DEF'}
    assert all(v > 0 for v in FALLBACK_POSITION_PPG.values())
```

- [ ] **Step 5: Run it**

```bash
.venv/Scripts/python -m pytest tests/test_projection_baseline.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Confirm the Stage 1 bar**

```bash
.venv/Scripts/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: **17 failed** — back to the `a6768ed` baseline exactly, with all four projection suites green.

- [ ] **Step 7: Commit the Stage 1 checkpoint**

```bash
black src/ tests/ && flake8 src/ tests/
git add -A src/ tests/ alembic/
git commit -m "$(cat <<'EOF'
fix(projections): leakage-free shrunk baselines, real schedules, honest coefficients

Phases 0-2 of the projection accuracy work, now green. Renames the two
coefficients whose tuner keys never matched AlgorithmCoefficients fields
(def_rank_multiplier, efficiency_clamp), so those sliders stop being
no-ops; from_dict silently dropped them. Fixes the injury sign flip
between the tuner waterfall and the production formula.

Adds DBNFLGame so an upcoming week can name its opponent -- game logs
only exist for games already played, so every future matchup used to
collapse to the neutral rank-16 default.

Adds projection_baseline.py: exponentially-weighted prior-weeks-only
means with empirical-Bayes shrinkage toward a positional prior, and an
ADP-implied curve so players with no history stop projecting at zero.
EOF
)"
```

---

## Task 4: `DBPlayerProjection` table and migration

**Files:**
- Modify: `src/pigskin_mastermind/models/database.py` (after `DBNFLGame`)
- Create: `alembic/versions/e7b2d9f4a1c3_add_player_projections.py`
- Test: `tests/test_projection_refresh.py`

**Interfaces:**
- Produces: `DBPlayerProjection` with columns `id, player_id, year, week, source, projected_points, floor, ceiling, std_dev, expected_games, components, computed_at`; unique on `(player_id, year, week, source)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_projection_refresh.py`:

```python
"""Storage and read-path for persisted projections."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerProjection,
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
    p = DBPlayer(player_id="espn_1", name="Test Back", position="RB", nfl_team="ATL")
    db.add(p)
    db.commit()
    return p


def test_season_and_weekly_rows_coexist(db, player):
    """week=NULL is the season row; it must not collide with week 1."""
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="blend",
        projected_points=280.0, expected_games=16.0,
    ))
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=1, source="blend",
        projected_points=17.5, expected_games=1.0,
    ))
    db.commit()
    assert db.query(DBPlayerProjection).count() == 2


def test_sources_coexist_for_one_scope(db, player):
    for src, pts in [("model", 300.0), ("espn", 260.0), ("adp", 275.0)]:
        db.add(DBPlayerProjection(
            player_id=player.id, year=2026, week=None,
            source=src, projected_points=pts, expected_games=16.0,
        ))
    db.commit()
    assert db.query(DBPlayerProjection).count() == 3
```

- [ ] **Step 2: Run it to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_projection_refresh.py -q
```

Expected: FAIL — `ImportError: cannot import name 'DBPlayerProjection'`.

- [ ] **Step 3: Add the model**

In `models/database.py`, after the `DBNFLGame` class:

```python
class DBPlayerProjection(Base):
    """One projection per player per scope per source.

    ``DBPlayer.projected_points`` cannot hold this. Two importers write it in
    two different units -- espn_sync stores a per-game scoring-period value,
    adp_service stores the board's season-scale totalRating -- so the column
    ranks Philip Rivers above Josh Allen. This table keeps each source
    separate and units explicit, which also lets the UI show *why* two
    sources disagree instead of hiding it behind one number.
    """
    __tablename__ = "player_projections"
    __table_args__ = (
        UniqueConstraint(
            'player_id', 'year', 'week', 'source', name='uq_player_projection',
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)

    # NULL means the season scope. Season rows store season TOTALS; weekly
    # rows store that week's points.
    week = Column(Integer, nullable=True)

    # blend | model | espn | sportsbook | adp
    source = Column(String, nullable=False)

    projected_points = Column(Float, nullable=False, default=0.0)
    floor = Column(Float, nullable=True)
    ceiling = Column(Float, nullable=True)
    std_dev = Column(Float, nullable=True)

    # A real column, not a components key: the draft pool converts season
    # totals to a per-game rate and reaching into JSON for the divisor is how
    # the mixed-unit bug comes back.
    expected_games = Column(Float, nullable=True)

    components = Column(JSON, default=dict)
    computed_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
```

- [ ] **Step 4: Run the test**

```bash
.venv/Scripts/python -m pytest tests/test_projection_refresh.py -q
```

Expected: 2 passed.

- [ ] **Step 5: Generate and inspect the migration**

```bash
.venv/Scripts/python -m alembic revision --autogenerate -m "add player projections"
```

Rename the generated file to `e7b2d9f4a1c3_add_player_projections.py`, set `revision = 'e7b2d9f4a1c3'` and `down_revision = 'd4a1c6e9b2f7'`. Read the generated `upgrade()` and delete any unrelated drift autogenerate invented — `create_all()` runs at app import, so autogenerate frequently proposes spurious diffs.

- [ ] **Step 6: Apply and verify**

```bash
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -c "import sqlite3;print(sqlite3.connect('pigskin_mastermind.db').execute(\"select name from sqlite_master where type='table' and name='player_projections'\").fetchall())"
```

Expected: `[('player_projections',)]`.

- [ ] **Step 7: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add src/pigskin_mastermind/models/database.py alembic/versions/e7b2d9f4a1c3_add_player_projections.py tests/test_projection_refresh.py
git commit -m "feat(projections): add player_projections table, one row per source and scope"
```

---

## Task 5: Import ESPN season projections from the board

`fetch_espn_adp` already reads `ratings["0"].totalRating` — real 2026 season totals (CMC 416.6, Puka 375.0). Nothing persists them anywhere readable. This writes them as `source='espn'` season rows.

**Files:**
- Modify: `src/pigskin_mastermind/services/adp_service.py`
- Test: `tests/test_adp_service.py` (append; create if absent)

**Interfaces:**
- Consumes: `fetch_espn_adp(year, limit) -> Optional[List[Dict]]` from `services/mock_draft.py`; entries carry `id, name, position, nfl_team, projected_points, adp_rank`. `PlayerIdentityService.resolve(*, espn_id=, name=, position=, nfl_team=) -> Optional[DBPlayer]`.
- Produces: `ADPService.import_espn_projections(year: Optional[int] = None, limit: int = 1000) -> Dict[str, Any]` returning `{"imported": int, "skipped": int, "total": int, "year": int, "error": Optional[str]}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_adp_service.py`:

```python
def test_import_espn_projections_writes_season_rows(db, monkeypatch):
    """Board totals land as source='espn' season rows, matched by identity."""
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
    from pigskin_mastermind.services import adp_service as adp_mod

    existing = DBPlayer(
        player_id="espn_4242", espn_id="4242",
        name="Bijan Robinson", position="RB", nfl_team="ATL",
    )
    db.add(existing)
    db.commit()

    monkeypatch.setattr(adp_mod, "fetch_espn_adp", lambda year, limit: [
        {"id": "espn_4242", "name": "Bijan Robinson", "position": "RB",
         "nfl_team": "ATL", "projected_points": 370.8, "adp_rank": 2.6},
    ])

    result = adp_mod.ADPService(db).import_espn_projections(year=2026)

    assert result["imported"] == 1
    row = db.query(DBPlayerProjection).filter_by(
        player_id=existing.id, year=2026, week=None, source="espn",
    ).one()
    assert row.projected_points == 370.8


def test_import_espn_projections_skips_zero_totals(db, monkeypatch):
    """A 0.0 totalRating is 'ESPN has no opinion', not 'worth zero points'."""
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
    from pigskin_mastermind.services import adp_service as adp_mod

    db.add(DBPlayer(player_id="espn_9", espn_id="9", name="Deep Bench",
                    position="WR", nfl_team="NYJ"))
    db.commit()
    monkeypatch.setattr(adp_mod, "fetch_espn_adp", lambda year, limit: [
        {"id": "espn_9", "name": "Deep Bench", "position": "WR",
         "nfl_team": "NYJ", "projected_points": 0.0, "adp_rank": 300.0},
    ])

    result = adp_mod.ADPService(db).import_espn_projections(year=2026)

    assert result["imported"] == 0
    assert db.query(DBPlayerProjection).count() == 0


def test_import_espn_projections_is_idempotent(db, monkeypatch):
    """Re-running updates in place rather than violating the unique index."""
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
    from pigskin_mastermind.services import adp_service as adp_mod

    db.add(DBPlayer(player_id="espn_4242", espn_id="4242",
                    name="Bijan Robinson", position="RB", nfl_team="ATL"))
    db.commit()
    board = [{"id": "espn_4242", "name": "Bijan Robinson", "position": "RB",
              "nfl_team": "ATL", "projected_points": 370.8, "adp_rank": 2.6}]
    monkeypatch.setattr(adp_mod, "fetch_espn_adp", lambda year, limit: board)

    svc = adp_mod.ADPService(db)
    svc.import_espn_projections(year=2026)
    board[0]["projected_points"] = 355.0
    svc.import_espn_projections(year=2026)

    rows = db.query(DBPlayerProjection).filter_by(source="espn").all()
    assert len(rows) == 1
    assert rows[0].projected_points == 355.0
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_adp_service.py -k espn_projections -q
```

Expected: FAIL — `AttributeError: 'ADPService' object has no attribute 'import_espn_projections'`.

- [ ] **Step 3: Implement**

Add to `ADPService` in `adp_service.py`. Import `DBPlayerProjection` at the top of the module.

```python
    ESPN_PROJECTION_SOURCE = "espn"

    def import_espn_projections(
        self,
        year: Optional[int] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """Persist ESPN's season point projections from the public board.

        ESPN's ``ratings["0"].totalRating`` is a genuine season total -- the
        2026 board returns 416.6 for McCaffrey and 375.0 for Nacua. The app
        never used it: ``_create_player_from_espn`` writes it to
        ``DBPlayer.projected_points`` but only for players it *creates*, so
        every established star kept the per-game value espn_sync wrote, and
        most kept 0.0.

        Writes season-scope rows (``week=None``) at season scale.
        """
        year = year or current_fantasy_season()
        board = fetch_espn_adp(year=year, limit=limit)
        if not board:
            return {
                "imported": 0, "skipped": 0, "total": 0, "year": year,
                "error": "Failed to fetch player board from ESPN",
            }

        now = datetime.utcnow()
        imported = 0
        skipped = 0

        for entry in board:
            points = float(entry.get("projected_points") or 0.0)
            if points <= 0:
                # ESPN reports 0.0 for players it has no projection for.
                # Storing that would rank them as genuinely worthless.
                skipped += 1
                continue

            position = normalize_position(entry.get("position"))
            if not position:
                skipped += 1
                continue

            player = self.identity.resolve(
                espn_id=_strip_espn_prefix(entry.get("id")),
                name=entry.get("name"),
                position=position,
                nfl_team=normalize_team(entry.get("nfl_team")),
            )
            if player is None:
                skipped += 1
                continue

            row = (
                self.db.query(DBPlayerProjection)
                .filter_by(
                    player_id=player.id, year=year, week=None,
                    source=self.ESPN_PROJECTION_SOURCE,
                )
                .first()
            )
            if row is None:
                row = DBPlayerProjection(
                    player_id=player.id, year=year, week=None,
                    source=self.ESPN_PROJECTION_SOURCE,
                )
                self.db.add(row)

            row.projected_points = points
            row.computed_at = now
            row.components = {"espn_total_rating": points,
                              "espn_adp": entry.get("adp_rank")}
            imported += 1

        self.db.commit()
        return {
            "imported": imported, "skipped": skipped,
            "total": len(board), "year": year, "error": None,
        }
```

- [ ] **Step 4: Run the tests**

```bash
.venv/Scripts/python -m pytest tests/test_adp_service.py -k espn_projections -q
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add src/pigskin_mastermind/services/adp_service.py tests/test_adp_service.py
git commit -m "feat(projections): persist ESPN board season totals as a projection source"
```

---

## Task 6: The blender

Pure functions over a dict of source → value. No DB, no I/O — this is the piece that must be obviously correct.

**Files:**
- Create: `src/pigskin_mastermind/services/projection_blender.py`
- Test: `tests/test_projection_blender.py`

**Interfaces:**
- Produces:
  - `SEASON_WEIGHTS: Dict[str, float]` = `{"model": 0.50, "espn": 0.30, "adp": 0.20}`
  - `WEEKLY_WEIGHTS: Dict[str, float]` = `{"sportsbook": 0.45, "model": 0.35, "espn": 0.20}`
  - `BlendResult` dataclass: `points: float`, `weights_used: Dict[str, float]`, `sources: Dict[str, float]`
  - `blend(sources: Dict[str, Optional[float]], weights: Dict[str, float]) -> Optional[BlendResult]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_projection_blender.py`:

```python
"""Weighted consensus across projection sources."""

import pytest

from pigskin_mastermind.services.projection_blender import (
    blend, SEASON_WEIGHTS, WEEKLY_WEIGHTS,
)


def test_all_sources_present_uses_nominal_weights():
    r = blend({"model": 300.0, "espn": 200.0, "adp": 100.0}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(0.5 * 300 + 0.3 * 200 + 0.2 * 100)


def test_missing_source_renormalizes_rather_than_dragging_to_zero():
    """Dropping ESPN must not pull the blend down by 30%.

    A naive implementation multiplies by 0.5 and 0.2 and returns 200 for a
    player both remaining sources call ~300 -- the failure mode this test
    exists to prevent.
    """
    r = blend({"model": 300.0, "adp": 300.0}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(300.0)
    assert sum(r.weights_used.values()) == pytest.approx(1.0)
    assert set(r.weights_used) == {"model", "adp"}


def test_single_source_returns_that_source_exactly():
    r = blend({"model": 271.4}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(271.4)
    assert r.weights_used == {"model": 1.0}


def test_none_values_are_absent_not_zero():
    r = blend({"model": 300.0, "espn": None, "adp": None}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(300.0)


def test_no_sources_returns_none():
    assert blend({}, SEASON_WEIGHTS) is None
    assert blend({"model": None}, SEASON_WEIGHTS) is None


def test_unknown_source_is_ignored():
    """A source with no weight cannot silently contribute."""
    r = blend({"model": 300.0, "vibes": 999.0}, SEASON_WEIGHTS)
    assert r.points == pytest.approx(300.0)
    assert "vibes" not in r.weights_used


def test_weekly_weights_favour_the_market():
    assert WEEKLY_WEIGHTS["sportsbook"] > WEEKLY_WEIGHTS["model"]
    assert sum(WEEKLY_WEIGHTS.values()) == pytest.approx(1.0)
    assert sum(SEASON_WEIGHTS.values()) == pytest.approx(1.0)


def test_weekly_with_only_model_returns_model():
    """Today's real weekly case: no live odds, ESPN weekly unusable."""
    r = blend({"model": 17.5}, WEEKLY_WEIGHTS)
    assert r.points == pytest.approx(17.5)
    assert r.weights_used == {"model": 1.0}


def test_zero_is_a_real_value_not_a_missing_one():
    """A bye-week 0.0 must count, or the gate is silently discarded."""
    r = blend({"model": 0.0, "sportsbook": 10.0}, WEEKLY_WEIGHTS)
    assert r.points == pytest.approx(
        (0.35 * 0.0 + 0.45 * 10.0) / (0.35 + 0.45)
    )
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_projection_blender.py -q
```

Expected: FAIL — `ModuleNotFoundError: No module named '...projection_blender'`.

- [ ] **Step 3: Implement**

Create `src/pigskin_mastermind/services/projection_blender.py`:

```python
"""Weighted consensus across projection sources.

Kept free of database and network access on purpose: the arithmetic that
decides every ranking in the app should be readable in one screen and
testable without fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

#: Season scope. No season-long prop market exists, so the house model leads
#: and ESPN plus the ADP-implied curve act as independent second opinions.
SEASON_WEIGHTS: Dict[str, float] = {
    "model": 0.50,
    "espn": 0.30,
    "adp": 0.20,
}

#: Weekly scope. A betting market is the sharpest short-term signal there is,
#: so props lead when they exist. They usually do not yet -- the odds table
#: currently holds one stale slate -- and renormalization is what makes that
#: degrade to model-only cleanly instead of as a special case.
WEEKLY_WEIGHTS: Dict[str, float] = {
    "sportsbook": 0.45,
    "model": 0.35,
    "espn": 0.20,
}


@dataclass
class BlendResult:
    """A consensus value plus the arithmetic that produced it.

    ``sources`` and ``weights_used`` are kept so the UI can show why two
    sources disagree rather than presenting one unexplained number.
    """

    points: float
    weights_used: Dict[str, float] = field(default_factory=dict)
    sources: Dict[str, float] = field(default_factory=dict)


def blend(
    sources: Dict[str, Optional[float]],
    weights: Dict[str, float],
) -> Optional[BlendResult]:
    """Combine *sources* using *weights*, renormalized over what is present.

    Renormalizing is the whole point. Multiplying a present source by its
    nominal weight and summing would treat a missing source as a zero-point
    vote, so a player two sources both call 300 would blend to 200 purely
    because a third source was silent.

    A source present with value ``0.0`` is a real vote -- that is how a bye
    week or an OUT designation reaches the blend -- so only ``None`` and
    unweighted keys are treated as absent.

    Returns ``None`` when no weighted source has a value.
    """
    present = {
        name: float(value)
        for name, value in sources.items()
        if value is not None and name in weights
    }
    if not present:
        return None

    total_weight = sum(weights[name] for name in present)
    if total_weight <= 0:
        return None

    normalized = {
        name: weights[name] / total_weight for name in present
    }
    points = sum(normalized[name] * value for name, value in present.items())

    return BlendResult(
        points=points,
        weights_used=normalized,
        sources=present,
    )
```

- [ ] **Step 4: Run the tests**

```bash
.venv/Scripts/python -m pytest tests/test_projection_blender.py -v
```

Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add src/pigskin_mastermind/services/projection_blender.py tests/test_projection_blender.py
git commit -m "feat(projections): add source blender that renormalizes over present sources"
```

---

## Task 7: Resolve sportsbook props by identity, not name LIKE

`_fetch_props` matches on `DBSportsbookOdds.description.ilike(f"%{name}%")`. A substring scan matches "Josh Allen" against "Josh Allen Jr." and misses "J. Allen" entirely.

**Files:**
- Modify: `src/pigskin_mastermind/services/sportsbook_projection_service.py:159-180`
- Test: `tests/test_sportsbook_projection_service.py` (append; create if absent)

**Interfaces:**
- Consumes: `PlayerIdentityService.resolve()`
- Produces: `SportsbookProjectionService.project_player_by_id(player_id: int, *, event_id=None, league_id=None, bookmaker=None) -> Dict[str, Any]` — same return shape as `project_player`, keyed off a `DBPlayer.id`.

- [ ] **Step 1: Write the failing test**

```python
def test_project_player_by_id_resolves_through_identity(db):
    """A DB player id, not a name substring, selects the props."""
    from pigskin_mastermind.models.database import DBPlayer
    from pigskin_mastermind.services.sportsbook_projection_service import (
        SportsbookProjectionService,
    )

    p = DBPlayer(player_id="espn_1", name="Josh Allen", position="QB", nfl_team="BUF")
    db.add(p)
    db.commit()

    result = SportsbookProjectionService(db).project_player_by_id(p.id)

    assert result["player_name"] == "Josh Allen"
    assert "total_projected_points" in result


def test_project_player_by_id_unknown_player_returns_empty(db):
    from pigskin_mastermind.services.sportsbook_projection_service import (
        SportsbookProjectionService,
    )
    result = SportsbookProjectionService(db).project_player_by_id(999999)
    assert result["total_projected_points"] == 0.0
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_sportsbook_projection_service.py -k by_id -q
```

Expected: FAIL — no attribute `project_player_by_id`.

- [ ] **Step 3: Implement**

Add to `SportsbookProjectionService`:

```python
    def project_player_by_id(
        self,
        player_id: int,
        *,
        event_id: Optional[str] = None,
        league_id: Optional[str] = None,
        bookmaker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Project by ``DBPlayer.id`` rather than by name substring.

        The name path matches ``description ILIKE '%name%'``, which both
        over-matches (a suffix like "Jr." pulls in the wrong player) and
        under-matches (books abbreviate). Resolving the player first means
        the same identity rules every importer uses apply here too.
        """
        player = self.db.query(DBPlayer).filter(DBPlayer.id == player_id).first()
        if player is None:
            return {
                "player_name": None,
                "total_projected_points": 0.0,
                "categories": [],
                "scoring_settings": self._resolve_scoring(league_id),
            }

        return self.project_player(
            player.name,
            event_id=event_id,
            league_id=league_id,
            bookmaker=bookmaker,
        )
```

Import `DBPlayer` at the top of the module if not already imported.

- [ ] **Step 4: Run**

```bash
.venv/Scripts/python -m pytest tests/test_sportsbook_projection_service.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add src/pigskin_mastermind/services/sportsbook_projection_service.py tests/test_sportsbook_projection_service.py
git commit -m "feat(odds): add id-based sportsbook projection lookup"
```

---

## Task 8: Refresh service, `get_projection`, CLI, and route

**Files:**
- Create: `src/pigskin_mastermind/services/projection_refresh.py`
- Modify: `src/pigskin_mastermind/cli.py`, `src/pigskin_mastermind/api/routes/stats.py`
- Test: `tests/test_projection_refresh.py` (append to Task 4's file)

**Interfaces:**
- Consumes: `ProjectionCriteriaBuilder(db).ensure_players_stats(player_ids: List[int], year: int)`, `.build_yearly_criteria(player_id, year, overrides=None)`, `.build_weekly_criteria(player_id, week, year, opponent_team=None)`; `YearlyProjectionService.calculate_season_projection(player, criteria)`; `WeeklyProjectionService.calculate_projection(player, criteria)`; `blend()`, `SEASON_WEIGHTS`, `WEEKLY_WEIGHTS`; `ADPService.import_espn_projections`.
- Produces:
  - `ProjectionRefreshService(db).refresh_season(year: int, player_ids: Optional[List[int]] = None) -> Dict[str, int]`
  - `ProjectionRefreshService(db).refresh_week(year: int, week: int, player_ids: Optional[List[int]] = None) -> Dict[str, int]`
  - `get_projection(db, player_id: int, year: int, week: Optional[int] = None) -> Optional[float]`
  - `get_projections_bulk(db, player_ids: List[int], year: int, week: Optional[int] = None) -> Dict[int, float]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_projection_refresh.py`:

```python
def test_get_projection_reads_the_blend_row(db, player):
    from pigskin_mastermind.services.projection_refresh import get_projection

    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="blend",
        projected_points=281.4, expected_games=16.0,
    ))
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="model",
        projected_points=999.0, expected_games=16.0,
    ))
    db.commit()

    assert get_projection(db, player.id, 2026) == 281.4


def test_get_projection_returns_none_when_absent(db, player):
    """No fallback to DBPlayer.projected_points -- it is mixed-unit."""
    from pigskin_mastermind.services.projection_refresh import get_projection

    player.projected_points = 19.6
    db.commit()

    assert get_projection(db, player.id, 2026) is None


def test_get_projection_separates_scopes(db, player):
    from pigskin_mastermind.services.projection_refresh import get_projection

    db.add(DBPlayerProjection(player_id=player.id, year=2026, week=None,
                              source="blend", projected_points=280.0))
    db.add(DBPlayerProjection(player_id=player.id, year=2026, week=3,
                              source="blend", projected_points=16.2))
    db.commit()

    assert get_projection(db, player.id, 2026) == 280.0
    assert get_projection(db, player.id, 2026, week=3) == 16.2


def test_get_projections_bulk_omits_missing(db, player):
    from pigskin_mastermind.services.projection_refresh import get_projections_bulk

    db.add(DBPlayerProjection(player_id=player.id, year=2026, week=None,
                              source="blend", projected_points=280.0))
    db.commit()

    out = get_projections_bulk(db, [player.id, 999999], 2026)
    assert out == {player.id: 280.0}
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_projection_refresh.py -q
```

Expected: FAIL — module `projection_refresh` not found.

- [ ] **Step 3: Implement the read path**

Create `src/pigskin_mastermind/services/projection_refresh.py`:

```python
"""Compute, persist, and read blended player projections.

The read path deliberately does *not* fall back to
``DBPlayer.projected_points``. Two importers write that column in two
different units, and its non-zero values land on backups and retirees while
every star reads 0.0 -- falling back to it would reintroduce exactly the
ranking this module exists to replace. Absence returns ``None`` and the
caller decides what to do about it.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerProjection, DBPlayerSeasonStats,
)
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.services.projection_blender import (
    blend, SEASON_WEIGHTS, WEEKLY_WEIGHTS,
)
from pigskin_mastermind.utils.positions import FANTASY_POSITIONS

logger = logging.getLogger(__name__)

BLEND_SOURCE = "blend"


def get_projection(
    db: Session,
    player_id: int,
    year: int,
    week: Optional[int] = None,
) -> Optional[float]:
    """Blended projection for one player, or ``None`` if not computed.

    Season scope (``week=None``) is a season total; weekly scope is that
    week's points.
    """
    row = (
        db.query(DBPlayerProjection.projected_points)
        .filter(
            DBPlayerProjection.player_id == player_id,
            DBPlayerProjection.year == year,
            DBPlayerProjection.week.is_(None) if week is None
            else DBPlayerProjection.week == week,
            DBPlayerProjection.source == BLEND_SOURCE,
        )
        .first()
    )
    return row[0] if row else None


def get_projections_bulk(
    db: Session,
    player_ids: List[int],
    year: int,
    week: Optional[int] = None,
) -> Dict[int, float]:
    """Blended projections for many players in one query.

    Ranking a page of players one ``get_projection`` call at a time is a
    query per row; every list view should use this instead.
    """
    if not player_ids:
        return {}

    rows = (
        db.query(
            DBPlayerProjection.player_id,
            DBPlayerProjection.projected_points,
        )
        .filter(
            DBPlayerProjection.player_id.in_(player_ids),
            DBPlayerProjection.year == year,
            DBPlayerProjection.week.is_(None) if week is None
            else DBPlayerProjection.week == week,
            DBPlayerProjection.source == BLEND_SOURCE,
        )
        .all()
    )
    return {pid: pts for pid, pts in rows}
```

- [ ] **Step 4: Run the read-path tests**

```bash
.venv/Scripts/python -m pytest tests/test_projection_refresh.py -q
```

Expected: all pass.

- [ ] **Step 5: Add the refresh service to the same module**

```python
class ProjectionRefreshService:
    """Batch-computes model projections and upserts blended rows.

    Batching is load-bearing, not an optimization: ``ProjectionCriteriaBuilder``
    issues dozens of queries per player, so priming stats for the whole cohort
    up front is the difference between seconds and many minutes.
    """

    def __init__(self, db: Session):
        self.db = db

    def refresh_season(
        self,
        year: int,
        player_ids: Optional[List[int]] = None,
    ) -> Dict[str, int]:
        """Compute model + blended season totals for every fantasy player."""
        from pigskin_mastermind.services.projection_criteria_builder import (
            ProjectionCriteriaBuilder,
        )
        from pigskin_mastermind.services.projection_service import (
            YearlyProjectionService,
        )
        from pigskin_mastermind.services.master_coefficients import (
            get_effective_coefficients,
        )

        players = self._target_players(player_ids)
        if not players:
            return {"players": 0, "model": 0, "blended": 0, "skipped": 0}

        builder = ProjectionCriteriaBuilder(self.db)
        builder.ensure_players_stats([p.id for p in players], year)

        coeffs = get_effective_coefficients()
        adp_by_player = self._adp_by_player(year)
        modelled = 0
        blended = 0
        skipped = 0

        for db_player in players:
            try:
                criteria = builder.build_yearly_criteria(db_player.id, year)
                service = YearlyProjectionService(
                    coefficients=coeffs.get_for_position(db_player.position),
                )
                domain = Player(
                    player_id=db_player.player_id,
                    name=db_player.name,
                    position=db_player.position,
                    team=db_player.nfl_team,
                )
                total = service.calculate_season_projection(domain, criteria)
            except Exception:
                logger.exception(
                    "season projection failed for player %s", db_player.id,
                )
                skipped += 1
                continue

            self._upsert(
                db_player.id, year, None, "model", total,
                expected_games=criteria.expected_games,
            )
            modelled += 1

            espn = self._stored(db_player.id, year, None, "espn")
            adp_pts = self._adp_implied_total(
                db_player, year, adp_by_player.get(db_player.id),
                criteria.expected_games,
            )
            if adp_pts is not None:
                self._upsert(
                    db_player.id, year, None, "adp", adp_pts,
                    expected_games=criteria.expected_games,
                )

            result = blend(
                {"model": total, "espn": espn, "adp": adp_pts},
                SEASON_WEIGHTS,
            )
            if result is not None:
                self._upsert(
                    db_player.id, year, None, BLEND_SOURCE, result.points,
                    expected_games=criteria.expected_games,
                    components={
                        "sources": result.sources,
                        "weights": result.weights_used,
                    },
                )
                blended += 1

        self.db.commit()
        return {
            "players": len(players), "model": modelled,
            "blended": blended, "skipped": skipped,
        }

    def refresh_week(
        self,
        year: int,
        week: int,
        player_ids: Optional[List[int]] = None,
    ) -> Dict[str, int]:
        """Compute model + blended projections for one week."""
        from pigskin_mastermind.services.projection_criteria_builder import (
            ProjectionCriteriaBuilder,
        )
        from pigskin_mastermind.services.projection_service import (
            WeeklyProjectionService,
        )
        from pigskin_mastermind.services.master_coefficients import (
            get_effective_coefficients,
        )
        from pigskin_mastermind.services.sportsbook_projection_service import (
            SportsbookProjectionService,
        )

        players = self._target_players(player_ids)
        if not players:
            return {"players": 0, "model": 0, "blended": 0, "skipped": 0}

        builder = ProjectionCriteriaBuilder(self.db)
        builder.ensure_players_stats([p.id for p in players], year)
        book = SportsbookProjectionService(self.db)

        coeffs = get_effective_coefficients()
        modelled = 0
        blended = 0
        skipped = 0

        for db_player in players:
            try:
                criteria = builder.build_weekly_criteria(
                    db_player.id, week=week, year=year,
                )
                service = WeeklyProjectionService(
                    coefficients=coeffs.get_for_position(db_player.position),
                )
                domain = Player(
                    player_id=db_player.player_id,
                    name=db_player.name,
                    position=db_player.position,
                    team=db_player.nfl_team,
                )
                points = service.calculate_projection(domain, criteria)
            except Exception:
                logger.exception(
                    "weekly projection failed for player %s", db_player.id,
                )
                skipped += 1
                continue

            self._upsert(db_player.id, year, week, "model", points)
            modelled += 1

            book_pts = None
            try:
                quote = book.project_player_by_id(db_player.id)
                total = float(quote.get("total_projected_points") or 0.0)
                # Zero means "no props for this player", not "zero points".
                book_pts = total if total > 0 else None
            except Exception:
                logger.debug("no sportsbook quote for %s", db_player.id)

            if book_pts is not None:
                self._upsert(
                    db_player.id, year, week, "sportsbook", book_pts,
                )

            result = blend(
                {"model": points, "sportsbook": book_pts}, WEEKLY_WEIGHTS,
            )
            if result is not None:
                self._upsert(
                    db_player.id, year, week, BLEND_SOURCE, result.points,
                    components={
                        "sources": result.sources,
                        "weights": result.weights_used,
                    },
                )
                blended += 1

        self.db.commit()
        return {
            "players": len(players), "model": modelled,
            "blended": blended, "skipped": skipped,
        }

    # ── internals ──────────────────────────────────────────────────────

    def _target_players(self, player_ids: Optional[List[int]]) -> List[DBPlayer]:
        """Fantasy-position players only.

        607 rows carry position 'Unknown' (nfl_data_py free agents). They sit
        outside FANTASY_POSITIONS and are deliberately not projected.
        """
        query = self.db.query(DBPlayer).filter(
            DBPlayer.position.in_(sorted(FANTASY_POSITIONS)),
        )
        if player_ids:
            query = query.filter(DBPlayer.id.in_(player_ids))
        return query.all()

    def _adp_by_player(self, year: int) -> Dict[int, float]:
        rows = (
            self.db.query(
                DBPlayerSeasonStats.player_id, DBPlayerSeasonStats.adp,
            )
            .filter(
                DBPlayerSeasonStats.year == year,
                DBPlayerSeasonStats.adp.isnot(None),
            )
            .all()
        )
        return {pid: adp for pid, adp in rows}

    def _adp_implied_total(
        self,
        db_player: DBPlayer,
        year: int,
        adp: Optional[float],
        expected_games: float,
    ) -> Optional[float]:
        """Season total implied by the positional ADP curve."""
        if not adp or expected_games <= 0:
            return None
        from pigskin_mastermind.services.projection_baseline import (
            ProjectionBaselines,
        )
        ppg = ProjectionBaselines(self.db).adp_implied_ppg(
            db_player.position, year, adp,
        )
        return None if ppg is None else ppg * expected_games

    def _stored(
        self, player_id: int, year: int, week: Optional[int], source: str,
    ) -> Optional[float]:
        row = (
            self.db.query(DBPlayerProjection.projected_points)
            .filter(
                DBPlayerProjection.player_id == player_id,
                DBPlayerProjection.year == year,
                DBPlayerProjection.week.is_(None) if week is None
                else DBPlayerProjection.week == week,
                DBPlayerProjection.source == source,
            )
            .first()
        )
        return row[0] if row else None

    def _upsert(
        self,
        player_id: int,
        year: int,
        week: Optional[int],
        source: str,
        points: float,
        expected_games: Optional[float] = None,
        components: Optional[dict] = None,
    ) -> None:
        row = (
            self.db.query(DBPlayerProjection)
            .filter(
                DBPlayerProjection.player_id == player_id,
                DBPlayerProjection.year == year,
                DBPlayerProjection.week.is_(None) if week is None
                else DBPlayerProjection.week == week,
                DBPlayerProjection.source == source,
            )
            .first()
        )
        if row is None:
            row = DBPlayerProjection(
                player_id=player_id, year=year, week=week, source=source,
            )
            self.db.add(row)
        row.projected_points = round(float(points), 2)
        if expected_games is not None:
            row.expected_games = expected_games
        if components is not None:
            row.components = components
        row.computed_at = datetime.utcnow()
```

- [ ] **Step 6: Add an integration test for the refresh**

```python
def test_refresh_season_writes_model_and_blend_rows(db, player):
    from pigskin_mastermind.services.projection_refresh import (
        ProjectionRefreshService,
    )

    stats = ProjectionRefreshService(db).refresh_season(2026)

    assert stats["players"] >= 1
    sources = {
        r.source for r in db.query(DBPlayerProjection)
        .filter_by(player_id=player.id, year=2026, week=None).all()
    }
    assert "model" in sources
    assert "blend" in sources
```

- [ ] **Step 7: Run**

```bash
.venv/Scripts/python -m pytest tests/test_projection_refresh.py -v
```

Expected: all pass.

- [ ] **Step 8: Add the CLI group**

In `cli.py`, after the `players` group definition (around line 191):

```python
@cli.group()
def projections():
    """Compute and persist blended player projections."""
    pass


@projections.command('refresh')
@click.option('--year', type=int, default=None, help='Season year')
@click.option('--week', type=int, default=None,
              help='Week number; omit for the season projection')
def projections_refresh(year, week):
    """Recompute model and blended projections and store them."""
    from pigskin_mastermind.services.projection_refresh import (
        ProjectionRefreshService,
    )
    from pigskin_mastermind.utils.season import current_fantasy_season

    year = year or current_fantasy_season()
    db = _get_stats_db()
    try:
        svc = ProjectionRefreshService(db)
        if week is None:
            result = svc.refresh_season(year)
            click.echo(f"Season {year}: {result}")
        else:
            result = svc.refresh_week(year, week)
            click.echo(f"Season {year} week {week}: {result}")
    finally:
        db.close()


@projections.command('import-espn')
@click.option('--year', type=int, default=None, help='Season year')
def projections_import_espn(year):
    """Pull ESPN's season point projections from the public board."""
    from pigskin_mastermind.services.adp_service import ADPService
    from pigskin_mastermind.utils.season import current_fantasy_season

    year = year or current_fantasy_season()
    db = _get_stats_db()
    try:
        result = ADPService(db).import_espn_projections(year=year)
        click.echo(
            f"{result['imported']} imported, {result['skipped']} skipped "
            f"of {result['total']} board entries"
        )
    finally:
        db.close()
```

`current_fantasy_season` lives at `pigskin_mastermind.utils.season` — confirmed against `adp_service.py:35`.

- [ ] **Step 9: Verify the CLI wiring**

```bash
.venv/Scripts/python -m pigskin_mastermind.cli projections --help
```

Expected: both `refresh` and `import-espn` listed.

- [ ] **Step 10: Add the POST route**

In `api/routes/stats.py`, add:

```python
@router.post("/api/stats/projections/refresh")
def refresh_projections(
    year: Optional[int] = None,
    week: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """Recompute and persist blended projections."""
    from pigskin_mastermind.services.projection_refresh import (
        ProjectionRefreshService,
    )
    from pigskin_mastermind.utils.season import current_fantasy_season

    year = year or current_fantasy_season()
    svc = ProjectionRefreshService(db)
    result = (
        svc.refresh_season(year) if week is None
        else svc.refresh_week(year, week)
    )
    return {"year": year, "week": week, **result}
```

- [ ] **Step 11: Full suite check**

```bash
.venv/Scripts/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: still 17 failures — no new ones.

- [ ] **Step 12: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add src/pigskin_mastermind/services/projection_refresh.py src/pigskin_mastermind/cli.py src/pigskin_mastermind/api/routes/stats.py tests/test_projection_refresh.py
git commit -m "feat(projections): add batch refresh service, get_projection, CLI and route"
```

---

## Task 9: Populate real data and confirm the numbers are sane

A checkpoint before touching consumers. If the stored numbers are wrong, wiring them everywhere makes things worse, not better.

**Files:** none — verification only.

- [ ] **Step 1: Back up the database**

```bash
cp pigskin_mastermind.db pigskin_mastermind.db.bak-preblend
```

- [ ] **Step 2: Import schedules and ESPN projections**

```bash
.venv/Scripts/python -m pigskin_mastermind.cli stats import-schedules --years 2025,2026
.venv/Scripts/python -m pigskin_mastermind.cli projections import-espn --year 2026
```

- [ ] **Step 3: Refresh season projections**

```bash
.venv/Scripts/python -m pigskin_mastermind.cli projections refresh --year 2026
```

- [ ] **Step 4: Sanity-check the output**

```bash
.venv/Scripts/python -c "
import sqlite3
c = sqlite3.connect('pigskin_mastermind.db')
q = lambda s: c.execute(s).fetchall()
print('blend rows:', q(\"select count(*) from player_projections where source='blend' and week is null\")[0][0])
print()
print('top 15 season blend:')
for r in q('''select p.name, p.position, p.nfl_team, round(pp.projected_points,1)
              from player_projections pp join players p on p.id=pp.player_id
              where pp.source='blend' and pp.week is null and pp.year=2026
              order by pp.projected_points desc limit 15'''): print('  ', r)
print()
print('per position max:')
for r in q('''select p.position, count(*), round(max(pp.projected_points),1)
              from player_projections pp join players p on p.id=pp.player_id
              where pp.source='blend' and pp.week is null group by p.position'''): print('  ', r)
"
```

Expected: elite RB/WR in the 250–400 range; the top 15 dominated by recognizable starters, **not** backups or retirees. Josh Allen, Bijan Robinson, and CeeDee Lamb must all appear with non-zero values.

**If the top of the list still looks wrong, stop and diagnose before Task 10.** Wiring consumers to a bad ranking is the failure this whole plan exists to prevent.

- [ ] **Step 5: Confirm rookies are not near zero**

```bash
.venv/Scripts/python -c "
import sqlite3
c = sqlite3.connect('pigskin_mastermind.db')
print(c.execute('''select count(*) from player_projections pp
    join players p on p.id=pp.player_id
    join player_season_stats s on s.player_id=p.id and s.year=2026
    where pp.source='blend' and pp.week is null and pp.projected_points < 10
      and s.adp < 150''').fetchone()[0], 'players with ADP<150 projected under 10 pts')
"
```

Expected: a small number. A large count means the ADP prior is not firing.

- [ ] **Step 6: Commit nothing; record the numbers**

Paste the top-15 list into the task notes for the before/after comparison in Task 13.

---

## Task 10: Move the draft pool to season totals

**Files:**
- Modify: `src/pigskin_mastermind/services/adp_service.py::_pool_projection` (~line 675)
- Test: `tests/test_adp_service.py`

**Interfaces:**
- Consumes: `get_projection(db, player_id, year, week=None)`

- [ ] **Step 1: Write the failing test**

```python
def test_pool_projection_prefers_the_season_blend(db):
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
    from pigskin_mastermind.services.adp_service import ADPService

    p = DBPlayer(player_id="espn_1", name="Star Back", position="RB",
                 nfl_team="ATL", projected_points=19.6)
    db.add(p)
    db.commit()
    db.add(DBPlayerProjection(player_id=p.id, year=2026, week=None,
                              source="blend", projected_points=298.5,
                              expected_games=16.0))
    db.commit()

    assert ADPService(db)._pool_projection(p, year=2026) == 298.5


def test_pool_entries_are_all_season_scale(db):
    """A per-game leak shows up as a ~16 sitting beside a ~300.

    The drafter normalizes within position, so one per-game value among
    season totals hands that player a ~17x disadvantage silently.
    """
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection
    from pigskin_mastermind.services.adp_service import ADPService

    for i in range(5):
        p = DBPlayer(player_id=f"espn_{i}", name=f"RB {i}", position="RB",
                     nfl_team="ATL", projected_points=12.0)
        db.add(p)
        db.flush()
        db.add(DBPlayerProjection(player_id=p.id, year=2026, week=None,
                                  source="blend", projected_points=200.0 + i,
                                  expected_games=16.0))
    db.commit()

    svc = ADPService(db)
    values = [
        svc._pool_projection(p, year=2026)
        for p in db.query(DBPlayer).all()
    ]
    assert all(v >= 20 for v in values), f"per-game leak in pool: {values}"
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/test_adp_service.py -k pool -q
```

Expected: FAIL — `_pool_projection()` takes no `year` argument.

- [ ] **Step 3: Rewrite `_pool_projection`**

```python
    def _pool_projection(
        self, player: DBPlayer, year: Optional[int] = None,
    ) -> float:
        """Season-total projection for the draft pool.

        Every entry must be on one scale. The drafter normalizes within
        position, so mixing a per-game value into a field of season totals
        hands that player a ~17x disadvantage without any visible error --
        which is what happened while this read DBPlayer.projected_points,
        a column espn_sync fills per game and adp_service fills per season.

        Falls back to last season's total when no blend row exists yet, so a
        database that has not been refreshed still produces a usable board.
        """
        from pigskin_mastermind.services.projection_refresh import get_projection

        year = year or current_fantasy_season()
        blended = get_projection(self.db, player.id, year)
        if blended is not None:
            return blended

        latest = (
            self.db.query(DBPlayerSeasonStats)
            .filter(
                DBPlayerSeasonStats.player_id == player.id,
                DBPlayerSeasonStats.fantasy_points_avg > 0,
                DBPlayerSeasonStats.games_played >= _MIN_GAMES_FOR_AVERAGE,
            )
            .order_by(DBPlayerSeasonStats.year.desc())
            .first()
        )
        if latest is None:
            return 0.0
        # Season total, to match the blend's scale.
        return round(
            (latest.fantasy_points_avg or 0.0) * (latest.games_played or 0), 2,
        )
```

- [ ] **Step 4: Update the call site**

In `get_adp_for_draft_pool`, change `self._pool_projection(player)` to `self._pool_projection(player, year=year)`. Confirm `year` is in scope there; if not, thread it through from the method's own `year` parameter.

- [ ] **Step 5: Run**

```bash
.venv/Scripts/python -m pytest tests/test_adp_service.py tests/test_mock_draft.py -q
```

Expected: the new tests pass. The four baseline `test_mock_draft` failures may now pass or may need their fixtures updated to the season scale — update them, do not weaken them.

- [ ] **Step 6: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add src/pigskin_mastermind/services/adp_service.py tests/test_adp_service.py tests/test_mock_draft.py
git commit -m "feat(draft): rank the pool on blended season totals"
```

---

## Task 11: Wire the weekly consumers — lineups and trades

**Files:**
- Modify: `src/pigskin_mastermind/api/routes/lineups.py:36`, `src/pigskin_mastermind/api/routes/trades.py:92,103`
- Test: `tests/integration/test_api_lineups.py`, `tests/integration/test_api_trades.py`

**Interfaces:**
- Consumes: `get_projections_bulk(db, player_ids, year, week)`

- [ ] **Step 1: Write the failing test**

Append to `tests/integration/test_api_lineups.py`:

```python
def test_optimizer_ranks_on_the_weekly_blend(client, db):
    """The optimizer must start a player the blend rates highly even when
    the legacy column says zero -- which it does for every star."""
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection

    good = db.query(DBPlayer).filter_by(position="RB").first()
    assert good is not None
    good.projected_points = 0.0
    db.add(DBPlayerProjection(
        player_id=good.id, year=2026, week=1, source="blend",
        projected_points=24.0,
    ))
    db.commit()

    resp = client.post("/api/lineups/optimize",
                       json={"team_id": 1, "week": 1, "year": 2026})
    assert resp.status_code == 200
    starters = [p["name"] for p in resp.json().get("starters", [])]
    assert good.name in starters
```

Adjust the request shape to match the route's real contract — read `lineups.py` first.

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/integration/test_api_lineups.py -q
```

- [ ] **Step 3: Change `_db_team_to_domain` in `lineups.py:17`**

The current signature is `_db_team_to_domain(db_team, db_players)` with one call site, at line 97. Add `db`, `year`, and `week`, and do one bulk lookup before the loop:

```python
def _db_team_to_domain(db, db_team, db_players, year, week=None):
    """Convert DB models to domain models for service layer.

    Positions are normalized on the way through — rows imported before the
    normalizer existed still say ``D/ST``, which ``Player.__post_init__``
    rejects. Anything that isn't a fantasy position is skipped rather than
    raising and taking the whole page down.

    Projected points come from the blended weekly projection rather than
    ``DBPlayer.projected_points``: that column reads 0.0 for every star, so
    optimizing against it started whoever ESPN happened to have a stale
    scoring-period value for.
    """
    from pigskin_mastermind.services.projection_refresh import (
        get_projections_bulk,
    )

    projections = get_projections_bulk(
        db, [p.id for p in db_players], year, week,
    )

    players = []
    for p in db_players:
        position = normalize_position(p.position)
        if position is None:
            continue
        players.append(
            Player(
                player_id=p.player_id,
                name=p.name,
                position=position,
                team=p.nfl_team,
                projected_points=projections.get(p.id, 0.0),
                actual_points=p.actual_points,
                stats=p.stats or {},
                headshot_url=p.headshot_url,
                db_id=p.id,
                bye_week=p.bye_week,
                injury_status=p.injury_status,
            )
        )

    return Team(
        team_id=db_team.team_id,
        name=db_team.name,
        owner=db_team.owner,
        players=players,
        record={"wins": db_team.wins, "losses": db_team.losses,
                "ties": db_team.ties},
        total_points=db_team.total_points,
        league_id=db_team.league_id,
    )
```

Update the call site at line 97 to `_db_team_to_domain(db, db_team, db_players, year, week)`. The handler has no `year`/`week` today — add them as `Query(None)` parameters defaulting to `current_fantasy_season()` and the current week, imported from `pigskin_mastermind.utils.season`.

- [ ] **Step 4: Apply the same change in `trades.py`**

Both the `gives` and `receives` comprehensions at lines 92 and 103 read `p.projected_points`. Replace with a single bulk lookup:

```python
    from pigskin_mastermind.services.projection_refresh import (
        get_projections_bulk,
    )

    all_ids = [p.id for p in gives_players] + [p.id for p in receives_players]
    projections = get_projections_bulk(db, all_ids, year, week)

    gives = [
        Player(
            player_id=p.player_id,
            name=p.name,
            position=p.position,
            team=p.nfl_team,
            projected_points=projections.get(p.id, 0.0),
        )
        for p in gives_players
    ]

    receives = [
        Player(
            player_id=p.player_id,
            name=p.name,
            position=p.position,
            team=p.nfl_team,
            projected_points=projections.get(p.id, 0.0),
        )
        for p in receives_players
    ]
```

Thread `year` and `week` into the handler if not already present, defaulting to `current_fantasy_season()` and the current week.

- [ ] **Step 5: Run both integration suites**

```bash
.venv/Scripts/python -m pytest tests/integration/test_api_lineups.py tests/integration/test_api_trades.py -q
```

Expected: all pass, including the two baseline failures.

- [ ] **Step 6: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add src/pigskin_mastermind/api/routes/lineups.py src/pigskin_mastermind/api/routes/trades.py tests/integration/
git commit -m "feat(lineups,trades): rank on blended weekly projections"
```

---

## Task 12: Wire the remaining consumers and surface components in the UI

`players.py` orders in **SQL** (`DBPlayer.projected_points.desc()`), so a Python lookup cannot drive it — this needs an outer join.

**Files:**
- Modify: `src/pigskin_mastermind/api/routes/players.py:191,237`, `src/pigskin_mastermind/api/routes/teams.py:137`, `src/pigskin_mastermind/templates/players/details.html`, `src/pigskin_mastermind/templates/teams/_weekly_lineup.html`
- Test: `tests/integration/test_api_players.py`, `tests/integration/test_api_teams.py`

**Interfaces:**
- Consumes: `DBPlayerProjection`, `get_projections_bulk`

- [ ] **Step 1: Write the failing test**

```python
def test_player_list_orders_by_blended_projection(client, db):
    """Ordering happens in SQL, so the blend has to reach the query."""
    from pigskin_mastermind.models.database import DBPlayer, DBPlayerProjection

    low = DBPlayer(player_id="espn_low", name="Aaa Backup", position="RB",
                   nfl_team="ATL", projected_points=19.6)
    high = DBPlayer(player_id="espn_high", name="Zzz Star", position="RB",
                    nfl_team="ATL", projected_points=0.0)
    db.add_all([low, high])
    db.commit()
    db.add(DBPlayerProjection(player_id=low.id, year=2026, week=None,
                              source="blend", projected_points=40.0))
    db.add(DBPlayerProjection(player_id=high.id, year=2026, week=None,
                              source="blend", projected_points=310.0))
    db.commit()

    body = client.get("/players/list?position=RB").text
    assert body.index("Zzz Star") < body.index("Aaa Backup")
```

- [ ] **Step 2: Run to verify it fails**

```bash
.venv/Scripts/python -m pytest tests/integration/test_api_players.py -k blended -q
```

- [ ] **Step 3: Add the ordering join in `players.py`**

At both line 191 and line 237, replace the `DBPlayer.projected_points.desc()` ordering with an outer join against the season blend:

```python
from sqlalchemy import and_
from pigskin_mastermind.models.database import DBPlayerProjection

# Outer join so players without a computed projection still appear, sorted
# last rather than dropped.
proj = (
    db.query(
        DBPlayerProjection.player_id.label("pid"),
        DBPlayerProjection.projected_points.label("pts"),
    )
    .filter(
        DBPlayerProjection.year == year,
        DBPlayerProjection.week.is_(None),
        DBPlayerProjection.source == "blend",
    )
    .subquery()
)

query = query.outerjoin(proj, proj.c.pid == DBPlayer.id)
query = query.order_by(proj.c.pts.desc().nullslast())
```

For line 191, keep ADP as the primary sort and make the blend the tiebreaker:
`query.order_by(DBPlayerSeasonStats.adp.asc().nullslast(), proj.c.pts.desc().nullslast())`.

Thread a `year` value into both handlers via `current_fantasy_season()` if absent.

- [ ] **Step 4: Change the roster sort in `teams.py:137`**

```python
    from pigskin_mastermind.services.projection_refresh import (
        get_projections_bulk,
    )

    roster = db.query(DBPlayer).filter(DBPlayer.team_id == team_db_id).all()
    roster_projections = get_projections_bulk(
        db, [p.id for p in roster], year,
    )
    players = sorted(
        roster,
        key=lambda p: (
            _POSITION_ORDER.get(p.position, 7),
            -roster_projections.get(p.id, 0.0),
        ),
    )
```

- [ ] **Step 5: Surface the components in `players/details.html`**

Add a projection block that shows the blend and each contributing source, so a projection is inspectable rather than a bare number:

```html
{% if projection %}
<div class="mt-4 rounded-lg border border-gray-200 p-4">
  <div class="flex items-baseline justify-between">
    <span class="text-sm text-gray-500">Projected ({{ projection.year }} season)</span>
    <span class="text-2xl font-semibold">{{ "%.1f"|format(projection.points) }}</span>
  </div>
  {% if projection.components and projection.components.sources %}
  <table class="mt-3 w-full text-sm">
    <thead><tr class="text-left text-gray-500">
      <th class="py-1">Source</th><th class="py-1">Points</th><th class="py-1">Weight</th>
    </tr></thead>
    <tbody>
      {% for name, value in projection.components.sources.items() %}
      <tr class="border-t border-gray-100">
        <td class="py-1 capitalize">{{ name }}</td>
        <td class="py-1">{{ "%.1f"|format(value) }}</td>
        <td class="py-1 text-gray-500">
          {{ "%.0f%%"|format(projection.components.weights[name] * 100) }}
        </td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
  {% endif %}
</div>
{% endif %}
```

Pass a `projection` dict from the player-detail handler containing `year`, `points`, and `components`, read from the `blend` row.

- [ ] **Step 6: Show the weekly blend in `teams/_weekly_lineup.html`**

Replace the template's `player.projected_points` reference with the weekly blend value the handler now supplies. Read the template first to match its existing variable naming.

- [ ] **Step 7: Run the affected suites**

```bash
.venv/Scripts/python -m pytest tests/integration/ -q
```

Expected: all pass, including the baseline `test_api_teams` and the two `test_api_projection_tuner` failures.

- [ ] **Step 8: Full suite — the Stage 4 bar**

```bash
.venv/Scripts/python -m pytest tests/ -q 2>&1 | tail -3
```

Expected: **8 failures** — only `TestGetPlayByPlay` (7) and `test_import_team_from_espn` (1).

- [ ] **Step 9: Commit**

```bash
black src/ tests/ && flake8 src/ tests/
git add src/pigskin_mastermind/api/routes/players.py src/pigskin_mastermind/api/routes/teams.py src/pigskin_mastermind/templates/ tests/integration/
git commit -m "feat(ui): rank and display blended projections with source breakdown"
```

---

## Not a task: `entertainment/__init__.py:92`

The spec lists power rankings as a consumer that "inherits it through the domain `Player`". That is true but moot — `grep` finds **no route, service, or CLI command that imports `entertainment/`**. It is unreachable from the running app, and its `sum(p.projected_points for p in team.players)` reads whatever a caller hands it.

No task, no change. Recorded here so a later reader does not go looking for the wiring and assume it was missed. If `entertainment/` is ever mounted, it inherits correct values automatically from whichever code path builds its `Team`.

---

## Task 13: Measure accuracy honestly and document

**Files:**
- Create: `docs/PROJECTION_ACCURACY.md`
- Modify: `docs/PROJECT_STATUS.md`, `CLAUDE.md`

- [ ] **Step 1: Backtest weekly and yearly for 2025**

```bash
.venv/Scripts/python -c "
from pigskin_mastermind.api.database import SessionLocal
from pigskin_mastermind.services.projection_tuner import ProjectionTunerService
db = SessionLocal()
svc = ProjectionTunerService(db)
for pos in [None, 'QB', 'RB', 'WR', 'TE']:
    w = svc.backtest_weekly(position=pos, year=2025)
    y = svc.backtest_yearly(pos, 2025)
    print(f'{pos or \"ALL\":<4} weekly MAE={w.get(\"mae\")} RMSE={w.get(\"rmse\")} n={w.get(\"sample_size\")}')
    print(f'{pos or \"ALL\":<4} yearly MAE={y.get(\"mae\")} RMSE={y.get(\"rmse\")} n={y.get(\"sample_size\")}')
db.close()
"
```

Adjust the result-key names to match what `backtest_weekly` actually returns — read its return statement first.

- [ ] **Step 2: Compute the naive baseline for comparison**

```bash
.venv/Scripts/python -c "
from pigskin_mastermind.api.database import SessionLocal
from pigskin_mastermind.models.database import DBPlayerGameLog
from collections import defaultdict
db = SessionLocal()
logs = db.query(DBPlayerGameLog).filter(DBPlayerGameLog.year == 2025).all()
by_player = defaultdict(list)
for g in logs:
    by_player[g.player_id].append((g.week, g.fantasy_points or 0.0))
errs = []
for pid, games in by_player.items():
    games.sort()
    for i, (wk, actual) in enumerate(games):
        if i < 2:
            continue
        prior = [p for _, p in games[:i]]
        errs.append(abs(sum(prior) / len(prior) - actual))
print(f'naive prior-weeks-average MAE={sum(errs)/len(errs):.3f} n={len(errs)}')
db.close()
"
```

- [ ] **Step 3: Write `docs/PROJECTION_ACCURACY.md`**

Record all three numbers in one table — old leaky, new model, naive baseline — with a paragraph stating plainly that weekly MAE is expected to be worse than the old number because the old backtest had look-ahead access to the week it was predicting, and that the naive baseline is the honest comparator.

- [ ] **Step 4: Update `CLAUDE.md`**

Add to the Projection pipeline section:

```markdown
### Persisted projections

`DBPlayerProjection` holds one row per (player, year, week, source), where
`week IS NULL` is the season scope. **Season rows are season totals; weekly
rows are that week's points.** Per-game at season scope is
`projected_points / expected_games`.

`source` ∈ `blend` | `model` | `espn` | `sportsbook` | `adp`. Consumers read
`services/projection_refresh.py::get_projection()` (or `get_projections_bulk`
for lists) — **never `DBPlayer.projected_points`**. Two importers write that
column in different units (`espn_sync` per game, `adp_service` per season) and
its non-zero values land on backups, so ranking on it puts Philip Rivers above
Josh Allen. `get_projection` returns `None` when nothing is computed rather
than falling back to it.

`services/projection_blender.py` renormalizes weights over whichever sources
are present, so a missing source redistributes instead of voting zero.
Refresh with `pigskin projections refresh [--year] [--week]`.
```

- [ ] **Step 5: Update `docs/PROJECT_STATUS.md`**

Note that projections are now persisted and consumed app-wide, and that weekly sportsbook blending is wired but dormant until odds are refreshed in-season.

- [ ] **Step 6: Final verification**

```bash
.venv/Scripts/python -m pytest tests/ -q 2>&1 | tail -3
black src/ tests/ && flake8 src/ tests/
```

Expected: 8 failures (the documented unrelated ones), clean formatting.

- [ ] **Step 7: Commit**

```bash
git add docs/ CLAUDE.md
git commit -m "docs: record projection accuracy baseline and the persisted-projection contract"
```

---

## Manual end-to-end verification

Run the app on port 8000. **Do not run it alongside the Electron app** — two SQLite writers deadlock.

```bash
.venv/Scripts/python -m uvicorn pigskin_mastermind.api.main:app --reload
```

- [ ] `/players` — list ranked by real projections, stars at the top, not mostly zeros
- [ ] `/players/<id>` — projection card shows the blend plus each source's points and weight
- [ ] `/draft` — start a mock draft; pool projections are season-scale (200–400 for early picks) and rookies are not ~0
- [ ] `/lineups` — optimize a roster; ordering differs from the ESPN-only baseline
- [ ] `/teams/<id>` — roster sorted by blended projection
- [ ] `/projection-tuner` — move the **defense-rank** and **efficiency-cap** sliders and confirm the projected number moves (it does not before this work)
