# Draft Board Projection Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the mock draft board rank players on real model projections stored in `player_projections`, instead of the mixed-unit `DBPlayer.projected_points` column.

**Architecture:** A new `ProjectionRefreshService` runs the existing yearly projection model over the draft pool and persists `source="model"` season rows, then blends them with any `source="espn"` rows through the existing `projection_blender` and persists `source="blend"`. A batch read helper resolves blend → model → last-season-total, and `ADPService.get_adp_for_draft_pool` uses it instead of `DBPlayer.projected_points`. A CLI command and a hook at the end of `refresh_draft_data()` trigger the refresh.

**Tech Stack:** Python 3, SQLAlchemy 2.x ORM, Click (CLI), pytest, SQLite.

## Global Constraints

- **Season rows store season TOTALS.** `week=None` for every row written here. A top RB reads ~330, not ~19.5. Per-game at season scope is `projected_points / expected_games`.
- **Never fall back to `DBPlayer.projected_points`.** It holds two different units from two writers and is what this work exists to stop reading. It must not appear in any code path this plan touches.
- **`get_effective_coefficients()`** is the only way to obtain the active coefficient set. Never construct `AlgorithmCoefficients()` directly in production code.
- **ADP is not a blend source.** Pass only `model` and `espn` to `blend()`. `ProjectionBaselines.season_baseline()` already folds ADP into the model's baseline; blending it again double-counts the market.
- **Positions:** only `QB, RB, WR, TE, K, DEF` are projectable. `models/player.py::Player.__post_init__` raises `ValueError` on anything else, and `Player.team` must be a string — use `player.nfl_team or "FA"`.
- **Run pytest scoped to `tests/`.** Bare `pytest` fails during collection on the vendored ESPN library.
- **Test baseline:** the suite has **17 known pre-existing failures** (843 pass). No task may add to that count. `tests/test_mock_draft.py` run alone must stay **90 passed**.
- On Windows the interpreter is `.venv/Scripts/python`.

---

### Task 1: `ProjectionRefreshService` writing model rows

**Files:**
- Create: `src/pigskin_mastermind/services/projection_refresh.py`
- Test: `tests/test_projection_refresh_service.py`

**Interfaces:**
- Consumes: `ProjectionCriteriaBuilder.build_yearly_criteria(player_id, year)`, `YearlyProjectionService.calculate_season_projection(player, criteria)`, `get_effective_coefficients()`, `DBPlayerProjection`.
- Produces: `ProjectionRefreshService(db, builder=None, service=None)` with `refresh_season(year, limit=None) -> dict` returning keys `{"model", "espn", "blend", "skipped", "year"}`. Task 2 extends the same method; Tasks 3–5 depend on the rows it writes.

The constructor takes optional `builder` and `service` so tests can inject stubs. The real `ProjectionCriteriaBuilder` issues dozens of queries per player and can attempt network fetches, which would make these tests slow and non-hermetic.

- [ ] **Step 1: Write the failing test**

Create `tests/test_projection_refresh_service.py`:

```python
"""Tests for ProjectionRefreshService — writing persisted projections."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerProjection, DBPlayerSeasonStats,
)
from pigskin_mastermind.models.projection_criteria import YearlyProjectionCriteria
from pigskin_mastermind.services.projection_refresh import ProjectionRefreshService


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


class StubBuilder:
    """Returns fixed criteria so tests never touch the real builder."""

    def __init__(self, ppg=20.0, games=16.0):
        self.ppg = ppg
        self.games = games

    def ensure_players_stats(self, player_ids, year):
        return None

    def build_yearly_criteria(self, player_id, year):
        return YearlyProjectionCriteria(
            historical_average_points=self.ppg,
            expected_games=self.games,
        )


def _seed_pool_player(db, name="Bijan Robinson", position="RB", adp=1.8):
    player = DBPlayer(
        player_id=f"espn_{name.replace(' ', '')}", name=name,
        position=position, nfl_team="ATL",
    )
    db.add(player)
    db.flush()
    db.add(DBPlayerSeasonStats(
        player_id=player.id, year=2026, adp=adp,
        adp_source="fantasyfootballcalculator",
    ))
    db.commit()
    return player


def test_writes_model_row_at_season_scale(db):
    player = _seed_pool_player(db)
    svc = ProjectionRefreshService(db, builder=StubBuilder(ppg=20.0, games=16.0))

    result = svc.refresh_season(2026)

    assert result["model"] == 1
    row = db.query(DBPlayerProjection).filter_by(
        player_id=player.id, year=2026, week=None, source="model",
    ).one()
    # 20 ppg x 16 games = a season TOTAL, not a per-game rate.
    assert row.projected_points == pytest.approx(320.0, abs=1.0)
    assert row.expected_games == pytest.approx(16.0)


def test_rerunning_updates_rather_than_duplicating(db):
    player = _seed_pool_player(db)
    ProjectionRefreshService(db, builder=StubBuilder(ppg=20.0)).refresh_season(2026)
    ProjectionRefreshService(db, builder=StubBuilder(ppg=10.0)).refresh_season(2026)

    rows = db.query(DBPlayerProjection).filter_by(
        player_id=player.id, year=2026, source="model",
    ).all()
    assert len(rows) == 1
    assert rows[0].projected_points == pytest.approx(160.0, abs=1.0)


def test_skips_non_fantasy_positions(db):
    player = _seed_pool_player(db, name="Some Guy", position="Unknown")
    result = ProjectionRefreshService(db, builder=StubBuilder()).refresh_season(2026)

    assert result["model"] == 0
    assert result["skipped"] == 1
    assert db.query(DBPlayerProjection).count() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_projection_refresh_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pigskin_mastermind.services.projection_refresh'`

- [ ] **Step 3: Write minimal implementation**

Create `src/pigskin_mastermind/services/projection_refresh.py`:

```python
"""Persist projections so consumers read one number with one unit.

The model has always been able to produce a projection; nothing stored it, so
every ranking in the app fell back to ``DBPlayer.projected_points`` — a column
two importers write in two different units. This service is the write path that
makes ``player_projections`` the single source of truth.

Season rows (``week=None``) store season TOTALS.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayer,
    DBPlayerProjection,
    DBPlayerSeasonStats,
)
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.services.master_coefficients import (
    get_effective_coefficients,
)
from pigskin_mastermind.services.player_identity import ESPN_TAIL_ADP_SOURCE
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder,
)
from pigskin_mastermind.services.projection_service import YearlyProjectionService

logger = logging.getLogger(__name__)

#: Positions the projection model accepts. ``Player`` raises on anything else.
PROJECTABLE_POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})

#: ADP sources that define membership of the draft pool. Deliberately not
#: imported from ``ADPService.DRAFT_POOL_SOURCES``: adp_service imports
#: ``season_projection_map`` from this module, so importing back would create a
#: cycle. ``ESPN_TAIL_ADP_SOURCE`` comes from player_identity, which imports
#: only models and utils and is safe from either side.
DRAFT_POOL_SOURCES = ("fantasyfootballcalculator", ESPN_TAIL_ADP_SOURCE)

MODEL_SOURCE = "model"
ESPN_SOURCE = "espn"
BLEND_SOURCE = "blend"


class ProjectionRefreshService:
    """Runs the projection model over the draft pool and persists the results.

    ``builder`` and ``service`` are injectable so tests can supply stubs; the
    real criteria builder issues dozens of queries per player and may reach out
    to ESPN, which makes it unusable in a unit test.
    """

    def __init__(
        self,
        db: Session,
        builder: Optional[ProjectionCriteriaBuilder] = None,
        service: Optional[YearlyProjectionService] = None,
    ) -> None:
        self.db = db
        self.builder = builder or ProjectionCriteriaBuilder(db)
        self.service = service or YearlyProjectionService(
            coefficients=get_effective_coefficients(),
        )

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def refresh_season(
        self, year: int, limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Project every draft-pool player for *year* and persist the rows."""
        players = self._pool_players(year, limit)

        # Batch-prime stats: the criteria builder issues dozens of queries per
        # player, so this is load-bearing rather than an optimization.
        self.builder.ensure_players_stats([p.id for p in players], year - 1)

        counts = {"model": 0, "espn": 0, "blend": 0, "skipped": 0, "year": year}
        now = datetime.utcnow()

        for player in players:
            if player.position not in PROJECTABLE_POSITIONS:
                counts["skipped"] += 1
                continue
            try:
                criteria = self.builder.build_yearly_criteria(player.id, year)
                domain = Player(
                    player_id=player.player_id,
                    name=player.name,
                    position=player.position,
                    team=player.nfl_team or "FA",
                )
                total = self.service.calculate_season_projection(domain, criteria)
            except Exception:
                logger.exception("Projection failed for player %s", player.id)
                counts["skipped"] += 1
                continue

            self._upsert(
                player_id=player.id,
                year=year,
                source=MODEL_SOURCE,
                points=total,
                expected_games=criteria.expected_games,
                components={
                    "historical_average_points": criteria.historical_average_points,
                    "player_skill_level": criteria.player_skill_level,
                    "positional_touch_percentage": criteria.positional_touch_percentage,
                    "team_offense_level": criteria.team_offense_level,
                },
                now=now,
            )
            counts["model"] += 1

        self.db.commit()
        return counts

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _pool_players(self, year: int, limit: Optional[int]) -> List[DBPlayer]:
        """Players holding a draft-pool ADP row for *year*."""
        query = (
            self.db.query(DBPlayer)
            .join(DBPlayerSeasonStats, DBPlayerSeasonStats.player_id == DBPlayer.id)
            .filter(
                DBPlayerSeasonStats.year == year,
                DBPlayerSeasonStats.adp.isnot(None),
                DBPlayerSeasonStats.adp_source.in_(DRAFT_POOL_SOURCES),
            )
            .order_by(DBPlayerSeasonStats.adp.asc())
        )
        if limit:
            query = query.limit(limit)
        return query.all()

    def _upsert(
        self,
        *,
        player_id: int,
        year: int,
        source: str,
        points: float,
        expected_games: Optional[float],
        components: Optional[Dict[str, Any]],
        now: datetime,
    ) -> None:
        """Insert or update one season row.

        Keyed on ``week=None``. The table-level unique constraint never fires
        for season rows because SQL treats NULL as distinct from NULL — the
        partial index ``uq_player_projection_season`` is what enforces this, so
        the read-then-write here must stay.
        """
        row = (
            self.db.query(DBPlayerProjection)
            .filter_by(player_id=player_id, year=year, week=None, source=source)
            .first()
        )
        if row is None:
            row = DBPlayerProjection(
                player_id=player_id, year=year, week=None, source=source,
            )
            self.db.add(row)

        row.projected_points = points
        row.expected_games = expected_games
        row.components = components or {}
        row.computed_at = now
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_projection_refresh_service.py -v`
Expected: PASS — 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/projection_refresh.py tests/test_projection_refresh_service.py
git commit -m "feat(projections): persist model season projections to player_projections"
```

---

### Task 2: Blend model with ESPN

**Files:**
- Modify: `src/pigskin_mastermind/services/projection_refresh.py`
- Test: `tests/test_projection_refresh_service.py`

**Interfaces:**
- Consumes: `projection_blender.blend(sources, weights)` and `SEASON_WEIGHTS` from Task 1's module imports.
- Produces: `refresh_season` additionally writes `source="blend"` rows and populates the `"blend"` count. Task 3 reads these rows.

`SEASON_WEIGHTS` is `{"model": 0.50, "espn": 0.30, "adp": 0.20}`. Passing only `model` and `espn` makes `blend()` renormalize over those two — 0.625 / 0.375. Do **not** pass an `adp` key.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_projection_refresh_service.py`:

```python
def test_blend_equals_model_when_no_espn_row(db):
    """Renormalization, not a special case: one source blends to itself."""
    player = _seed_pool_player(db)
    ProjectionRefreshService(db, builder=StubBuilder(ppg=20.0, games=16.0)).refresh_season(2026)

    model = db.query(DBPlayerProjection).filter_by(
        player_id=player.id, source="model",
    ).one()
    blend_row = db.query(DBPlayerProjection).filter_by(
        player_id=player.id, source="blend",
    ).one()
    assert blend_row.projected_points == pytest.approx(model.projected_points)


def test_blend_weights_model_and_espn(db):
    player = _seed_pool_player(db)
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="espn",
        projected_points=400.0,
    ))
    db.commit()

    svc = ProjectionRefreshService(db, builder=StubBuilder(ppg=20.0, games=16.0))
    result = svc.refresh_season(2026)

    assert result["blend"] == 1
    blend_row = db.query(DBPlayerProjection).filter_by(
        player_id=player.id, source="blend",
    ).one()
    # model 320 at 0.5/0.8, espn 400 at 0.3/0.8 => 350.0
    assert blend_row.projected_points == pytest.approx(350.0, abs=1.0)
    assert blend_row.components["weights_used"]["model"] == pytest.approx(0.625)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_projection_refresh_service.py -k blend -v`
Expected: FAIL — `sqlalchemy.exc.NoResultFound` (no blend row is written yet)

- [ ] **Step 3: Write minimal implementation**

Add the import at the top of `projection_refresh.py`, below the existing service imports:

```python
from pigskin_mastermind.services.projection_blender import SEASON_WEIGHTS, blend
```

In `refresh_season`, replace `counts["model"] += 1` with:

```python
            counts["model"] += 1

            espn_row = (
                self.db.query(DBPlayerProjection)
                .filter_by(
                    player_id=player.id, year=year, week=None, source=ESPN_SOURCE,
                )
                .first()
            )
            if espn_row is not None:
                counts["espn"] += 1

            # Only model and espn. ADP is already folded into the model's
            # baseline by ProjectionBaselines.season_baseline(), so blending it
            # again would double-count the market for exactly the players whose
            # projection is most market-derived.
            blended = blend(
                {
                    MODEL_SOURCE: total,
                    ESPN_SOURCE: espn_row.projected_points if espn_row else None,
                },
                SEASON_WEIGHTS,
            )
            if blended is not None:
                self._upsert(
                    player_id=player.id,
                    year=year,
                    source=BLEND_SOURCE,
                    points=blended.points,
                    expected_games=criteria.expected_games,
                    components={
                        "sources": blended.sources,
                        "weights_used": blended.weights_used,
                    },
                    now=now,
                )
                counts["blend"] += 1
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_projection_refresh_service.py -v`
Expected: PASS — 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/projection_refresh.py tests/test_projection_refresh_service.py
git commit -m "feat(projections): blend model with ESPN into a consensus season row"
```

---

### Task 3: Read helpers

**Files:**
- Modify: `src/pigskin_mastermind/services/projection_refresh.py`
- Test: `tests/test_projection_refresh_service.py`

**Interfaces:**
- Produces: two module-level functions Task 4 depends on:
  - `get_projection(db, player_id, year, week=None) -> Optional[float]`
  - `season_projection_map(db, player_ids, year) -> Dict[int, float]`

Both prefer `blend`, fall back to `model`, and return nothing when neither exists. `season_projection_map` is the batch form — the draft pool resolves ~1000 players per page load and must not issue a query each.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_projection_refresh_service.py`:

```python
from pigskin_mastermind.services.projection_refresh import (
    get_projection, season_projection_map,
)


def test_get_projection_prefers_blend_over_model(db):
    player = _seed_pool_player(db)
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="model",
        projected_points=300.0,
    ))
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="blend",
        projected_points=350.0,
    ))
    db.commit()

    assert get_projection(db, player.id, 2026) == pytest.approx(350.0)


def test_get_projection_falls_back_to_model(db):
    player = _seed_pool_player(db)
    db.add(DBPlayerProjection(
        player_id=player.id, year=2026, week=None, source="model",
        projected_points=300.0,
    ))
    db.commit()

    assert get_projection(db, player.id, 2026) == pytest.approx(300.0)


def test_get_projection_returns_none_when_absent(db):
    player = _seed_pool_player(db)
    assert get_projection(db, player.id, 2026) is None


def test_season_projection_map_batches(db):
    a = _seed_pool_player(db, name="Player A", adp=1.0)
    b = _seed_pool_player(db, name="Player B", adp=2.0)
    db.add(DBPlayerProjection(
        player_id=a.id, year=2026, week=None, source="blend",
        projected_points=350.0,
    ))
    db.add(DBPlayerProjection(
        player_id=b.id, year=2026, week=None, source="model",
        projected_points=200.0,
    ))
    db.commit()

    result = season_projection_map(db, [a.id, b.id], 2026)
    assert result == {a.id: pytest.approx(350.0), b.id: pytest.approx(200.0)}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_projection_refresh_service.py -k "get_projection or season_projection_map" -v`
Expected: FAIL — `ImportError: cannot import name 'get_projection'`

- [ ] **Step 3: Write minimal implementation**

Append to `src/pigskin_mastermind/services/projection_refresh.py`:

```python
# ---------------------------------------------------------------------------
# Read path
# ---------------------------------------------------------------------------

#: Preference order. The blend is the consensus; the model alone is the
#: fallback when no blend row was written.
_READ_PRIORITY = (BLEND_SOURCE, MODEL_SOURCE)


def get_projection(
    db: Session,
    player_id: int,
    year: int,
    week: Optional[int] = None,
) -> Optional[float]:
    """Persisted projection for one player, or ``None`` when absent.

    Deliberately returns ``None`` rather than falling back to
    ``DBPlayer.projected_points``: that column mixes per-game and season units,
    and ranking on it is what this module exists to stop. Callers decide what
    absence means.
    """
    rows = (
        db.query(DBPlayerProjection)
        .filter(
            DBPlayerProjection.player_id == player_id,
            DBPlayerProjection.year == year,
            DBPlayerProjection.week.is_(week) if week is None
            else DBPlayerProjection.week == week,
            DBPlayerProjection.source.in_(_READ_PRIORITY),
        )
        .all()
    )
    by_source = {r.source: r.projected_points for r in rows}
    for source in _READ_PRIORITY:
        if source in by_source:
            return by_source[source]
    return None


def season_projection_map(
    db: Session,
    player_ids: List[int],
    year: int,
) -> Dict[int, float]:
    """Batch form of :func:`get_projection` for season scope.

    The draft pool resolves ~1000 players per page load; one query per player
    is what this avoids.
    """
    if not player_ids:
        return {}

    rows = (
        db.query(DBPlayerProjection)
        .filter(
            DBPlayerProjection.player_id.in_(player_ids),
            DBPlayerProjection.year == year,
            DBPlayerProjection.week.is_(None),
            DBPlayerProjection.source.in_(_READ_PRIORITY),
        )
        .all()
    )

    best: Dict[int, tuple] = {}
    for row in rows:
        rank = _READ_PRIORITY.index(row.source)
        current = best.get(row.player_id)
        if current is None or rank < current[0]:
            best[row.player_id] = (rank, row.projected_points)

    return {pid: points for pid, (_rank, points) in best.items()}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python -m pytest tests/test_projection_refresh_service.py -v`
Expected: PASS — 9 passed

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/projection_refresh.py tests/test_projection_refresh_service.py
git commit -m "feat(projections): add blend-preferring read helpers"
```

---

### Task 4: Rewire the draft pool

**Files:**
- Modify: `src/pigskin_mastermind/services/adp_service.py` (`get_adp_for_draft_pool` ~line 705, `_pool_projection` ~line 762)
- Test: `tests/test_adp_service.py`

**Interfaces:**
- Consumes: `season_projection_map(db, player_ids, year)` from Task 3.
- Produces: `get_adp_for_draft_pool` entries whose `projected_points` is a season total. No later task depends on further changes here.

This is the task that changes user-visible behavior. Two changes inside `_pool_projection`'s resolution chain:

1. `DBPlayer.projected_points` leaves the path entirely — it is consulted *first* today and must not be consulted at all.
2. The last-season fallback moves from `fantasy_points_avg` (per game) to `fantasy_points_total` (a season total), so every value on this path shares one unit.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_adp_service.py`. Add `DBPlayerProjection` to the existing `models.database` import at the top of the file first:

```python
class TestDraftPoolProjections:
    """The pool must serve season totals from player_projections only."""

    def _seed_with_adp(self, db):
        players = _seed_players(db)
        for i, p in enumerate(players[:3]):
            db.add(DBPlayerSeasonStats(
                player_id=p.id, year=2026, adp=float(i + 1),
                adp_source="fantasyfootballcalculator",
            ))
        db.commit()
        return players

    def test_prefers_blend_row(self, db):
        players = self._seed_with_adp(db)
        db.add(DBPlayerProjection(
            player_id=players[0].id, year=2026, week=None, source="model",
            projected_points=300.0,
        ))
        db.add(DBPlayerProjection(
            player_id=players[0].id, year=2026, week=None, source="blend",
            projected_points=355.0,
        ))
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        entry = next(p for p in pool if p["db_id"] == players[0].id)
        assert entry["projected_points"] == pytest.approx(355.0)

    def test_ignores_db_player_projected_points(self, db):
        """The mixed-unit column must not reach the pool."""
        players = self._seed_with_adp(db)
        players[0].projected_points = 19.5  # a per-game value
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        entry = next(p for p in pool if p["db_id"] == players[0].id)
        assert entry["projected_points"] != pytest.approx(19.5)

    def test_falls_back_to_last_season_total(self, db):
        players = self._seed_with_adp(db)
        db.add(DBPlayerSeasonStats(
            player_id=players[0].id, year=2025, games_played=16,
            fantasy_points_total=280.0, fantasy_points_avg=17.5,
        ))
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        entry = next(p for p in pool if p["db_id"] == players[0].id)
        # The season TOTAL, not the 17.5 per-game average.
        assert entry["projected_points"] == pytest.approx(280.0)

    def test_no_entry_lands_in_the_per_game_band(self, db):
        """A per-game leak shows up as a ~16 beside a ~300."""
        players = self._seed_with_adp(db)
        db.add(DBPlayerProjection(
            player_id=players[0].id, year=2026, week=None, source="blend",
            projected_points=355.0,
        ))
        db.add(DBPlayerSeasonStats(
            player_id=players[1].id, year=2025, games_played=16,
            fantasy_points_total=280.0, fantasy_points_avg=17.5,
        ))
        db.commit()

        pool = ADPService(db).get_adp_for_draft_pool(year=2026)
        assert not [p for p in pool if 0 < p["projected_points"] < 20]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_adp_service.py::TestDraftPoolProjections -v`
Expected: FAIL — `test_ignores_db_player_projected_points` and `test_falls_back_to_last_season_total` fail, because `_pool_projection` returns `player.projected_points` (19.5) then `fantasy_points_avg` (17.5).

- [ ] **Step 3: Write minimal implementation**

In `src/pigskin_mastermind/services/adp_service.py`, add the import near the other service imports at the top:

```python
from pigskin_mastermind.services.projection_refresh import season_projection_map
```

In `get_adp_for_draft_pool`, replace the `return [...]` list comprehension with a preloaded map:

```python
        query = query.order_by(DBPlayerSeasonStats.adp.asc())
        rows = query.all()

        # One query for every player's persisted projection, rather than one
        # per player inside the comprehension below.
        projections = season_projection_map(
            self.db, [player.id for player, _ in rows], year,
        ) if year is not None else {}

        return [
            {
                "id": player.player_id,
                "db_id": player.id,
                "name": player.name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "projected_points": self._pool_projection(player, projections),
                "adp_rank": season.adp,
                # Carried into the draft so the value verdicts can tell a real
                # consensus ADP from the synthetic espn_tail sort key.
                "adp_source": season.adp_source,
                "adp_stdev": season.adp_stdev,
                "headshot_url": player.headshot_url or "",
                "bye_week": player.bye_week,
                "injury_status": player.injury_status,
            }
            for player, season in rows
            if player.position in FANTASY_POSITIONS
        ]
```

Replace the whole `_pool_projection` method with:

```python
    def _pool_projection(
        self,
        player: DBPlayer,
        projections: Dict[int, float],
    ) -> float:
        """Season-total projection for the draft pool.

        Resolution order is persisted blend, persisted model, then last
        season's actual total. ``DBPlayer.projected_points`` is deliberately
        absent: two importers write it in two different units, so it ranks
        Philip Rivers above Josh Allen.

        Everything returned here is a season TOTAL. Mixing in a per-game value
        would hand that player a ~17x advantage in the AI drafter's
        within-position normalization.
        """
        persisted = projections.get(player.id)
        if persisted is not None:
            return round(persisted, 1)

        latest = (
            self.db.query(DBPlayerSeasonStats)
            .filter(
                DBPlayerSeasonStats.player_id == player.id,
                DBPlayerSeasonStats.fantasy_points_total > 0,
                # Some ESPN-sourced season rows record a full-season total
                # against games_played=1, which makes avg == total. Requiring a
                # real sample keeps those out of the pool.
                DBPlayerSeasonStats.games_played >= _MIN_GAMES_FOR_AVERAGE,
            )
            .order_by(DBPlayerSeasonStats.year.desc())
            .first()
        )
        return round(latest.fantasy_points_total, 1) if latest else 0.0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_adp_service.py tests/test_adp_espn_tail.py tests/test_adp_refresh.py tests/test_mock_draft.py -v`
Expected: PASS — including the existing 90 in `test_mock_draft.py`. If a pre-existing `test_adp_service` test asserted the per-game fallback, update it to assert the total; that assertion was encoding the bug.

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/adp_service.py tests/test_adp_service.py
git commit -m "feat(draft): rank the draft pool on persisted season projections"
```

---

### Task 5: CLI command and refresh hook

**Files:**
- Modify: `src/pigskin_mastermind/cli.py`
- Modify: `src/pigskin_mastermind/services/adp_service.py` (`refresh_draft_data`, ends ~line 362)
- Test: `tests/test_adp_refresh.py`

**Interfaces:**
- Consumes: `ProjectionRefreshService.refresh_season(year, limit=None)` from Task 1.
- Produces: `pigskin projections refresh` CLI command; `refresh_draft_data()` return dict gains `projections_model`, `projections_blend`, and `projections_error` on failure.

Ordering is load-bearing: projections read the ADP rows FFC and the ESPN tail write, so the hook must run **last**, after `canonicalize_stored_teams()`. Like the ESPN tail leg, it is **non-fatal** — a refresh must never leave the pool worse than it started.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_adp_refresh.py`:

```python
def test_refresh_draft_data_runs_projections_last(db):
    """Projections must run after ADP rows exist, and never break the refresh."""
    svc = ADPService(db)
    calls = []

    with patch.object(svc, "import_from_ffc", return_value={"imported": 1}), \
         patch.object(svc, "import_espn_tail", return_value={"imported": 0}), \
         patch.object(svc, "canonicalize_stored_teams",
                      side_effect=lambda: calls.append("teams") or 0), \
         patch(
             "pigskin_mastermind.services.projection_refresh."
             "ProjectionRefreshService.refresh_season",
             side_effect=lambda year, **kw: calls.append("projections") or {
                 "model": 5, "espn": 0, "blend": 5, "skipped": 0, "year": year,
             },
         ):
        result = svc.refresh_draft_data(year=2026)

    assert calls == ["teams", "projections"]
    assert result["projections_model"] == 5
    assert result["projections_blend"] == 5


def test_refresh_draft_data_survives_projection_failure(db):
    svc = ADPService(db)

    with patch.object(svc, "import_from_ffc", return_value={"imported": 1}), \
         patch.object(svc, "import_espn_tail", return_value={"imported": 0}), \
         patch.object(svc, "canonicalize_stored_teams", return_value=0), \
         patch(
             "pigskin_mastermind.services.projection_refresh."
             "ProjectionRefreshService.refresh_season",
             side_effect=RuntimeError("model blew up"),
         ):
        result = svc.refresh_draft_data(year=2026)

    assert "projections_error" in result
    assert result["imported"] == 1
```

Check the top of `tests/test_adp_refresh.py` for an existing `patch` import; add `from unittest.mock import patch` if absent.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python -m pytest tests/test_adp_refresh.py -k "projections" -v`
Expected: FAIL — `AssertionError: assert ['teams'] == ['teams', 'projections']`

- [ ] **Step 3: Write minimal implementation**

In `adp_service.py`, replace the final two lines of `refresh_draft_data` (`result["teams_canonicalized"] = ...` / `return result`) with:

```python
        result["teams_canonicalized"] = self.canonicalize_stored_teams()

        # Projections run last: they read the ADP rows the legs above write.
        # Non-fatal for the same reason the ESPN tail is — a refresh must never
        # leave the pool worse than it started.
        try:
            from pigskin_mastermind.services.projection_refresh import (
                ProjectionRefreshService,
            )

            projections = ProjectionRefreshService(self.db).refresh_season(year)
            result["projections_model"] = projections.get("model", 0)
            result["projections_blend"] = projections.get("blend", 0)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Projection refresh failed for %s", year)
            result["projections_error"] = str(exc)

        return result
```

The import is function-local to avoid a circular import: `projection_refresh` imports nothing from `adp_service`, but `adp_service` already imports `season_projection_map` from it at module level (Task 4), and the service class pulls in the criteria builder.

Add the CLI group to `src/pigskin_mastermind/cli.py`, after the `players` group:

```python
@main.group()
def projections():
    """Projection generation commands."""
    pass


@projections.command('refresh')
@click.option('--year', type=int, default=None,
              help='Season to project (defaults to the current fantasy season)')
@click.option('--limit', type=int, default=None,
              help='Only project the first N players by ADP')
def projections_refresh(year, limit):
    """Run the projection model over the draft pool and persist the results.

    Writes season rows to ``player_projections``: one ``model`` row per
    player, and a ``blend`` row combining it with ESPN's board projection
    where one has been imported.

    Roughly 117 ms per player, so a full ~1000-player pool takes ~2 minutes::

        pigskin projections refresh --year 2026
    """
    from pigskin_mastermind.services.projection_refresh import (
        ProjectionRefreshService,
    )
    from pigskin_mastermind.utils.season import current_fantasy_season

    year = year or current_fantasy_season()
    db = _get_stats_db()
    try:
        click.echo(f"Projecting draft pool for {year}...")
        result = ProjectionRefreshService(db).refresh_season(year, limit=limit)
        click.echo(f"  {result['model']} model projections written")
        click.echo(f"  {result['espn']} matched an ESPN projection")
        click.echo(f"  {result['blend']} blended rows written")
        if result['skipped']:
            click.echo(f"  {result['skipped']} skipped")
    finally:
        db.close()
```

`adp_service.py` already has `import logging` (line 17) and `logger = logging.getLogger(__name__)` (line 41), so the `logger.exception` call above needs no new import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python -m pytest tests/test_adp_refresh.py -v`
Expected: PASS

Then confirm the command is registered:

Run: `.venv/Scripts/python -m pigskin_mastermind.cli projections refresh --help`
Expected: the help text above, exit 0

- [ ] **Step 5: Commit**

```bash
git add src/pigskin_mastermind/cli.py src/pigskin_mastermind/services/adp_service.py tests/test_adp_refresh.py
git commit -m "feat(projections): add refresh CLI and hook it into draft data refresh"
```

---

### Task 6: End-to-end verification against the real database

**Files:**
- Modify: none (verification only; fix forward if something fails)

**Interfaces:**
- Consumes: everything above.

No code is expected here. This task proves the wiring works on the real 1013-player pool, where the unit tests use stubs.

- [ ] **Step 1: Back up the database**

```bash
cp pigskin_mastermind.db pigskin_mastermind.db.bak-prewiring
```

- [ ] **Step 2: Confirm the full suite has no new failures**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: **17 failed, 849+ passed.** The 17 must be the same pre-existing set (7 `test_nfl_data_service`, 4 `test_mock_draft`, 3 integration `lineups`/`trades`/`teams`, 2 `test_api_projection_tuner`, 1 `test_espn_sync`). Any other failure is a regression — fix it before continuing.

Run: `.venv/Scripts/python -m pytest tests/test_mock_draft.py -q`
Expected: **90 passed** (this file is green in isolation; its 4 full-run failures are pre-existing cross-test pollution).

- [ ] **Step 3: Run the refresh**

Run: `.venv/Scripts/python -m pigskin_mastermind.cli projections refresh --year 2026`
Expected: ~2 minutes, then a non-zero model count near 1000.

- [ ] **Step 4: Verify the pool**

```bash
.venv/Scripts/python -c "
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pigskin_mastermind.services.adp_service import ADPService
db = sessionmaker(bind=create_engine('sqlite:///./pigskin_mastermind.db'))()
pool = ADPService(db).get_adp_for_draft_pool(year=2026)
zeros = [p for p in pool if not p['projected_points']]
leaks = [p for p in pool if 0 < p['projected_points'] < 20]
print(f'pool={len(pool)} zeros={len(zeros)} ({100*len(zeros)/len(pool):.1f}%) per-game leaks={len(leaks)}')
for p in sorted(pool, key=lambda x: -x['projected_points'])[:8]:
    print(f\"  {p['projected_points']:>7.1f}  {p['name'][:24]:<24} {p['position']:<4} ADP={p['adp_rank']}\")
"
```

Expected: zero-rate **well under 50.6%**, `per-game leaks=0`, and the top of the list dominated by RB/WR at ~300–450 rather than five quarterbacks. Record the before/after numbers in the commit message.

- [ ] **Step 5: Verify in the running app**

Start the dev server on **port 8010** (the user's own app usually holds 8000, and never run this alongside the Electron app — two SQLite writers deadlock). Open `/draft`, start a mock draft, and confirm the board shows season-scale projections with stars at the top.

- [ ] **Step 6: Commit**

```bash
git commit --allow-empty -m "test: verify draft board reads season-scale model projections end to end"
```
