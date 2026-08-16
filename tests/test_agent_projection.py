"""Validation and persistence of agent-produced projections."""

import copy
import json
from pathlib import Path

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
    _REQUIRED_FIELDS,
    ResultRejected,
    validate_result,
)

FIXTURE = Path(__file__).parent / "fixtures" / "agent_result_season.json"


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


# --- Coverage added in response to review: the confidence enum, the full
# required-fields set, and the weekly branch of _model_projection were not
# pinned by any of the original 13 cases. Each addition below was confirmed
# to fail red under the mutation it targets before being left in place. ---


def test_rejects_invalid_confidence(db, result):
    result["confidence"] = "very high"
    with pytest.raises(ResultRejected, match="confidence"):
        validate_result(db, result)


@pytest.mark.parametrize("confidence", ["low", "medium", "high"])
def test_accepts_valid_confidence_values(db, result, confidence):
    result["confidence"] = confidence
    assert validate_result(db, result)["confidence"] == confidence


# Mirrors _REQUIRED_FIELDS as a literal, not a reference to it. Parametrizing
# directly off the module's tuple would make the shrink-to-one-field mutation
# invisible: if the source tuple shrinks, `parametrize` collects fewer cases
# instead of failing any of them, and the test run goes green with less
# coverage rather than red. The assertion below pins the tuple's actual
# membership, so a shrink (or an unnoticed addition) fails loudly here, and
# the parametrize list is what a developer must deliberately update.
_EXPECTED_REQUIRED_FIELDS = (
    "player_id",
    "year",
    "projected_points",
    "rationale",
    "confidence",
)


def test_required_fields_tuple_has_not_drifted(db, result):
    assert _REQUIRED_FIELDS == _EXPECTED_REQUIRED_FIELDS


@pytest.mark.parametrize("field", _EXPECTED_REQUIRED_FIELDS)
def test_rejects_each_missing_required_field(db, result, field):
    del result[field]
    with pytest.raises(ResultRejected, match=f"Missing required field: {field}"):
        validate_result(db, result)


def test_weekly_scope_matches_against_the_weekly_model_row(db, player, result):
    # A model row scoped to week=5, distinct from the season-scope rows used
    # elsewhere in this file. Its band ([2.5, 30.0]) and the RB weekly
    # position ceiling (50.0) disagree about whether 40.0 is acceptable, so
    # this can only pass if the week filter in _model_projection actually
    # found this row -- a bug there would silently fall through to the
    # (looser) ceiling check and let 40.0 pass.
    db.add(
        DBPlayerProjection(
            player_id=player.id,
            year=2026,
            week=5,
            source="model",
            projected_points=10.0,
        )
    )
    db.commit()
    result["week"] = 5
    result["projected_points"] = 40.0
    result["floor"] = 35.0
    result["ceiling"] = 45.0
    with pytest.raises(ResultRejected, match="model projection"):
        validate_result(db, result)


# --- Task 9: persisting a validated result as source='llm' ---


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
        db.query(DBPlayerProjection).filter_by(player_id=player.id, source="llm").all()
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
        db.query(DBPlayerProjection).filter_by(player_id=player.id, source="llm").all()
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

    record_llm_projection(db, result)

    model = (
        db.query(DBPlayerProjection)
        .filter_by(player_id=player.id, source="model")
        .one()
    )
    assert model.projected_points == 240.0


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
    web_factors = [f for f in row.components["key_factors"] if f["source"] == "web"]
    assert web_factors and all(f.get("url") for f in web_factors)


# --- Task 3b: expected_scope closes the year/week hole ---


def test_scope_check_passes_when_year_and_week_match(db, result):
    assert validate_result(db, result, expected_scope=(2026, None)) == result


def test_rejects_year_that_does_not_match_the_request(db, result):
    with pytest.raises(ResultRejected, match="year"):
        validate_result(db, result, expected_scope=(2025, None))


def test_rejects_season_result_when_a_week_was_requested(db, result):
    """The failure that motivated this task.

    A weekly number stored with week=None lands on the season row and gets
    validated against the season model projection -- a band roughly 17x too
    wide -- so the gate silently stops gating.
    """
    with pytest.raises(ResultRejected, match="week"):
        validate_result(db, result, expected_scope=(2026, 5))


def test_rejects_weekly_result_when_a_season_was_requested(db, result):
    result["week"] = 5
    with pytest.raises(ResultRejected, match="week"):
        validate_result(db, result, expected_scope=(2026, None))


def test_weekly_scope_check_passes_when_the_week_matches(db, player, result):
    result["week"] = 5
    result["projected_points"] = 18.0
    result["floor"] = 9.0
    result["ceiling"] = 31.0
    assert validate_result(db, result, expected_scope=(2026, 5))


def test_no_scope_check_when_expected_scope_is_absent(db, result):
    """Existing callers keep working; the CLI is what makes the check real."""
    result["year"] = 1999
    assert validate_result(db, result) == result


def test_record_threads_the_scope_check_through(db, result):
    from pigskin_mastermind.services.agent_projection import (
        record_llm_projection,
    )

    with pytest.raises(ResultRejected, match="year"):
        record_llm_projection(db, result, expected_scope=(2025, None))

    assert db.query(DBPlayerProjection).count() == 0
