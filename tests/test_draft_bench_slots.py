"""Bench slots must survive the trip from a league into the draft setup.

A league's roster format is starters *plus* bench.  Dropping the bench count
leaves the draft guessing at how many rounds to run, so bench is carried as a
first-class ``BENCH`` entry in ``lineup_slots`` — while staying invisible to
every piece of logic that reasons about *starting* slots.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from unittest.mock import Mock

from pigskin_mastermind.api.routes.draft import _build_league_presets, _slot_to_lineup_key
from pigskin_mastermind.models.database import (
    Base,
    DBLeague,
    DBTeam,
    DBWeeklyPlayerStats,
    DBWeeklyTeamStats,
)
from pigskin_mastermind.services.espn_sync import ESPNSyncService
from pigskin_mastermind.services.mock_draft import (
    DEFAULT_LINEUP_SLOTS,
    LINEUP_PRESETS,
    _derive_hard_starter_needs,
    _derive_starter_needs,
    roster_size,
)


# ---------------------------------------------------------------------------
# ESPN import
# ---------------------------------------------------------------------------


def _espn_league(position_slot_counts):
    league = Mock()
    league.settings.position_slot_counts = position_slot_counts
    return league


def test_espn_bench_slots_are_imported():
    slots = ESPNSyncService.extract_roster_slots(
        _espn_league({"QB": 1, "RB": 2, "WR": 2, "TE": 1, "RB/WR/TE": 1,
                      "K": 1, "D/ST": 1, "BE": 7})
    )
    assert slots["BENCH"] == 7


def test_espn_ir_slots_are_still_excluded():
    """IR is not a draftable slot — it must not inflate the roster size."""
    slots = ESPNSyncService.extract_roster_slots(
        _espn_league({"QB": 1, "BE": 6, "IR": 2})
    )
    assert "IR" not in slots
    assert slots == {"QB": 1, "BENCH": 6}


def test_espn_zero_bench_is_omitted():
    slots = ESPNSyncService.extract_roster_slots(_espn_league({"QB": 1, "BE": 0}))
    assert "BENCH" not in slots


# ---------------------------------------------------------------------------
# Inferring slots from a locally stored roster
# ---------------------------------------------------------------------------


def test_stored_bench_slot_labels_map_to_bench():
    for label in ("BE", "BN", "Bench", "bench", "BENCH"):
        assert _slot_to_lineup_key(label) == "BENCH", label


def test_stored_ir_slot_label_is_ignored():
    assert _slot_to_lineup_key("IR") is None


# ---------------------------------------------------------------------------
# Roster size drives the number of draft rounds
# ---------------------------------------------------------------------------


def test_roster_size_counts_starters_and_bench():
    assert roster_size({"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1,
                        "K": 1, "DEF": 1, "BENCH": 6}) == 15


def test_roster_size_of_default_lineup_matches_bench_plus_starters():
    assert DEFAULT_LINEUP_SLOTS["BENCH"] > 0
    starters = sum(v for k, v in DEFAULT_LINEUP_SLOTS.items() if k != "BENCH")
    assert roster_size(DEFAULT_LINEUP_SLOTS) == starters + DEFAULT_LINEUP_SLOTS["BENCH"]


def test_every_preset_declares_a_bench():
    for key, preset in LINEUP_PRESETS.items():
        assert preset["slots"].get("BENCH", 0) > 0, key


def test_every_preset_fits_the_20_round_draft_cap():
    for key, preset in LINEUP_PRESETS.items():
        assert roster_size(preset["slots"]) <= 20, key


# ---------------------------------------------------------------------------
# BENCH must stay out of starting-lineup logic
# ---------------------------------------------------------------------------


def test_bench_does_not_create_starter_needs():
    slots = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1, "BENCH": 7}
    without_bench = {k: v for k, v in slots.items() if k != "BENCH"}

    assert _derive_starter_needs(slots) == _derive_starter_needs(without_bench)
    assert _derive_hard_starter_needs(slots) == _derive_hard_starter_needs(without_bench)
    assert "BENCH" not in _derive_starter_needs(slots)


# ---------------------------------------------------------------------------
# League presets shown in the pre-draft settings
# ---------------------------------------------------------------------------


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _league_with_stored_roster(db, *, roster_slots, slot_labels):
    """A league plus one week of stored roster rows using ``slot_labels``."""
    db.add(DBLeague(league_id="L1", name="Test League", year=2026,
                    roster_slots=roster_slots))
    team = DBTeam(team_id="1", name="My Team", owner="Me",
                  league_id="L1", is_user_team=True)
    db.add(team)
    db.flush()

    weekly = DBWeeklyTeamStats(team_id=team.id, week=1)
    db.add(weekly)
    db.flush()

    for idx, label in enumerate(slot_labels):
        db.add(DBWeeklyPlayerStats(player_id=idx + 1, weekly_team_stats_id=weekly.id,
                                   week=1, slot_position=label))
    db.commit()


def test_league_preset_infers_bench_from_stored_roster(db):
    """No saved roster_slots at all — bench comes from the stored lineup."""
    _league_with_stored_roster(
        db,
        roster_slots=None,
        slot_labels=["QB", "RB", "RB", "WR", "WR", "TE", "RB/WR/TE", "K", "D/ST",
                     "BE", "BE", "BE", "BE", "BE", "BE"],
    )

    preset = _build_league_presets(db)[0]

    assert preset["roster_slots"]["BENCH"] == 6
    assert preset["bench_slots"] == 6
    assert preset["roster_size"] == 15


def test_league_preset_backfills_bench_onto_legacy_saved_slots(db):
    """Leagues synced before bench tracking keep their starters and gain BENCH."""
    _league_with_stored_roster(
        db,
        roster_slots={"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1},
        slot_labels=["QB", "RB", "RB", "WR", "WR", "WR", "TE", "RB/WR/TE", "K", "D/ST",
                     "BE", "BE", "BE", "BE", "BE", "BE", "BE"],
    )

    preset = _build_league_presets(db)[0]

    assert preset["roster_slots"]["WR"] == 3  # saved starters win over inference
    assert preset["bench_slots"] == 7
    assert preset["roster_size"] == 17


def test_league_preset_keeps_saved_bench_untouched(db):
    """A saved BENCH count is authoritative — no re-inference."""
    _league_with_stored_roster(
        db,
        roster_slots={"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1,
                      "K": 1, "DEF": 1, "BENCH": 5},
        slot_labels=["BE"] * 9,
    )

    preset = _build_league_presets(db)[0]

    assert preset["bench_slots"] == 5
    assert preset["roster_size"] == 14
