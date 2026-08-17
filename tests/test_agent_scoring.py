"""Scoring stored projections against actuals."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerProjection,
    DBPlayerSeasonStats,
)
from pigskin_mastermind.services.agent_scoring import score_projections


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _player(db, name, position="RB"):
    p = DBPlayer(
        player_id=f"espn_{name}",
        name=name,
        position=position,
        nfl_team="ATL",
    )
    db.add(p)
    db.commit()
    return p


def _proj(db, player, source, points, year=2025, week=None):
    db.add(
        DBPlayerProjection(
            player_id=player.id,
            year=year,
            week=week,
            source=source,
            projected_points=points,
        )
    )
    db.commit()


def _weekly_actual(db, player, points, year=2025, week=5):
    db.add(
        DBPlayerGameLog(
            player_id=player.id,
            year=year,
            week=week,
            fantasy_points=points,
        )
    )
    db.commit()


def _season_actual(db, player, points, year=2025, games_played=17):
    db.add(
        DBPlayerSeasonStats(
            player_id=player.id,
            year=year,
            games_played=games_played,
            fantasy_points_total=points,
        )
    )
    db.commit()


def test_mae_and_bias_for_one_source(db):
    a, b = _player(db, "A"), _player(db, "B")
    _proj(db, a, "model", 12.0, week=5)
    _proj(db, b, "model", 20.0, week=5)
    _weekly_actual(db, a, 10.0)  # over by 2
    _weekly_actual(db, b, 26.0)  # under by 6

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
    _weekly_actual(db, a, 10.0)  # b has no actual

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
    _proj(db, a, "llm", 10.5, week=5)  # llm only projected A
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


def test_head_to_head_zero_players_when_sources_dont_overlap(db):
    """Two sources with disjoint player sets: no ZeroDivisionError, zeros instead.

    This is an entirely ordinary situation once ``llm`` rows exist for
    players ``model`` skipped (or vice versa) -- the intersection can land
    on the empty set. ``_metrics`` must handle an empty pair list rather
    than dividing by zero.
    """
    a, b = _player(db, "A"), _player(db, "B")
    _proj(db, a, "model", 12.0, week=5)
    _proj(db, b, "llm", 20.0, week=5)
    _weekly_actual(db, a, 10.0)
    _weekly_actual(db, b, 26.0)

    result = score_projections(db, 2025, week=5, sources=["model", "llm"])

    # Own coverage: each source scored on its one player.
    assert result["per_source"]["model"]["n"] == 1
    assert result["per_source"]["llm"]["n"] == 1

    # Head to head: two sources were scored, so the block exists, but the
    # intersection of their players is empty.
    h2h = result["head_to_head"]
    assert h2h is not None
    assert h2h["players"] == 0
    assert h2h["sources"]["model"] == {"n": 0, "mae": 0.0, "bias": 0.0, "rmse": 0.0}
    assert h2h["sources"]["llm"] == {"n": 0, "mae": 0.0, "bias": 0.0, "rmse": 0.0}


def test_head_to_head_is_absent_with_fewer_than_two_sources(db):
    a = _player(db, "A")
    _proj(db, a, "model", 12.0, week=5)
    _weekly_actual(db, a, 10.0)

    assert score_projections(db, 2025, week=5)["head_to_head"] is None


def test_season_scope_uses_season_totals(db):
    a = _player(db, "A")
    _proj(db, a, "model", 240.0)  # week=None -> season row
    _season_actual(db, a, 200.0)

    result = score_projections(db, 2025)
    assert result["scope"] == "season"
    assert result["per_source"]["model"]["n"] == 1
    assert result["per_source"]["model"]["mae"] == pytest.approx(40.0)


def test_weekly_scope_ignores_season_rows(db):
    a = _player(db, "A")
    _proj(db, a, "model", 240.0)  # season row
    _weekly_actual(db, a, 10.0)

    result = score_projections(db, 2025, week=5)
    assert result["per_source"] == {}


def test_empty_when_nothing_stored(db):
    result = score_projections(db, 2025, week=5)
    assert result["per_source"] == {}
    assert result["head_to_head"] is None
    assert result["no_actual"] == 0
    assert result["mean_games_played"] is None
    assert result["sources_with_no_rows"] == []


def test_mean_games_played_reflects_partial_season(db):
    a, b = _player(db, "A"), _player(db, "B")
    _proj(db, a, "model", 240.0)
    _proj(db, b, "model", 180.0)
    _season_actual(db, a, 90.0, games_played=8)
    _season_actual(db, b, 70.0, games_played=10)

    result = score_projections(db, 2025)
    assert result["mean_games_played"] == pytest.approx(9.0)


def test_mean_games_played_is_none_at_weekly_scope(db):
    a = _player(db, "A")
    _proj(db, a, "model", 12.0, week=5)
    _weekly_actual(db, a, 10.0)

    result = score_projections(db, 2025, week=5)
    assert result["mean_games_played"] is None


def test_sources_with_no_rows_flags_a_source_that_scored_nothing(db):
    """A typo in --sources (or a real source with no actuals) is named.

    Without this, a typo like ``lmm`` silently scores zero rows and the
    only symptom is a downstream "fewer than two sources" message that
    points at the wrong cause.
    """
    a = _player(db, "A")
    _proj(db, a, "model", 12.0, week=5)
    _weekly_actual(db, a, 10.0)

    result = score_projections(db, 2025, week=5, sources=["model", "lmm"])
    assert result["sources_with_no_rows"] == ["lmm"]
    assert "lmm" not in result["per_source"]


def test_sources_with_no_rows_is_empty_when_no_sources_filter_given(db):
    a = _player(db, "A")
    _proj(db, a, "model", 12.0, week=5)
    _weekly_actual(db, a, 10.0)

    result = score_projections(db, 2025, week=5)
    assert result["sources_with_no_rows"] == []
