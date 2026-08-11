"""Tests for ProjectionRefreshService — writing persisted projections."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerProjection, DBPlayerSeasonStats,
)
from pigskin_mastermind.models.projection_criteria import YearlyProjectionCriteria
from pigskin_mastermind.services.projection_refresh import (
    ProjectionRefreshService, get_projection, season_projection_map,
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


def test_season_projection_map_prefers_blend_over_model_for_same_player(db):
    """One player with both rows — the batch path must resolve preference
    per-player, not just take whichever row the query happens to return
    first. Covers a regression the two-different-players fixture above
    cannot: an inverted rank comparison would still pass that one.
    """
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

    result = season_projection_map(db, [player.id], 2026)
    assert result == {player.id: pytest.approx(350.0)}


from unittest.mock import patch

from pigskin_mastermind.models.database import DBLeague
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder,
)


def _seed_espn_league(db):
    """A league with credentials — what enables the ESPN fetch path."""
    db.add(DBLeague(
        league_id="1", name="Test", year=2025, swid="{SWID}", espn_s2="s2cookie",
    ))
    db.commit()


def test_builder_defaults_to_allowing_network(db):
    """Existing callers (tuner, stats routes) must keep the fetch path."""
    assert ProjectionCriteriaBuilder(db).allow_network is True


def test_offline_builder_skips_espn_fetch(db):
    """The bulk refresh must not make a network call per player."""
    _seed_espn_league(db)
    player = _seed_pool_player(db, name="No Logs Guy", position="WR")

    builder = ProjectionCriteriaBuilder(db, allow_network=False)
    with patch(
        "pigskin_mastermind.services.espn_sync.ESPNSyncService.fetch_player_full_stats"
    ) as mock_fetch:
        builder.ensure_players_stats([player.id], 2025)

    mock_fetch.assert_not_called()


def test_refresh_service_builder_is_offline(db):
    """ProjectionRefreshService must construct an offline builder by default."""
    svc = ProjectionRefreshService(db)
    assert svc.builder.allow_network is False
