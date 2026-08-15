"""Validation and persistence of agent-produced projections."""

import copy

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBPlayer,
    DBPlayerProjection,
)
from pigskin_mastermind.services.agent_projection import (
    ResultRejected,
    validate_result,
)


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


@pytest.fixture
def player(db):
    p = DBPlayer(
        player_id="espn_1",
        name="Test Back",
        position="RB",
        nfl_team="ATL",
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
            {
                "factor": "Target share up 4pts",
                "direction": "+",
                "magnitude_pts": 8.0,
                "source": "db",
            },
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
    db.add(
        DBPlayerProjection(
            player_id=player.id,
            year=2026,
            week=None,
            source="model",
            projected_points=240.0,
        )
    )
    db.commit()
    result["projected_points"] = 40.0
    result["floor"] = 20.0
    with pytest.raises(ResultRejected, match="model projection"):
        validate_result(db, result)


def test_accepts_a_large_but_defensible_departure(db, player, result):
    db.add(
        DBPlayerProjection(
            player_id=player.id,
            year=2026,
            week=None,
            source="model",
            projected_points=140.0,
        )
    )
    db.commit()
    result["projected_points"] = 280.0
    result["ceiling"] = 320.0
    assert validate_result(db, result)["projected_points"] == 280.0


def test_rejects_web_factor_without_a_url(db, result):
    result["key_factors"].append(
        {
            "factor": "Named starter in camp",
            "direction": "+",
            "magnitude_pts": 12.0,
            "source": "web",
        },
    )
    result["web_used"] = True
    with pytest.raises(ResultRejected, match="url"):
        validate_result(db, result)


def test_accepts_web_factor_with_a_url(db, result):
    result["key_factors"].append(
        {
            "factor": "Named starter in camp",
            "direction": "+",
            "magnitude_pts": 12.0,
            "source": "web",
            "url": "https://example.com/report",
        },
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
