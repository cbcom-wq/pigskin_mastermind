"""Validating an agent's lineup proposal.

Every rejection below is a case where accepting the payload would either
corrupt the league's rules or hand a poisoned news headline real authority.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBLineupSlot, DBManagerRun, DBNFLGame, DBPlayer,
    DBPlayerProjection, DBRosterSpot, DBTeam,
)
from pigskin_mastermind.services.season_agent import (
    LineupRejected, apply_proposal, record_proposal, validate_lineup_result,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
WEEK = 5
KICKOFF = datetime(2026, 10, 11, 13, 0)
BEFORE = datetime(2026, 10, 10, 9, 0)
AFTER = datetime(2026, 10, 11, 16, 0)

SLOTS = {"QB": 1, "RB": 1, "FLEX": 1, "BENCH": 1}


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def db():
    session = TestSessionLocal()
    yield session
    session.close()


@pytest.fixture
def setup(db):
    league = DBLeague(league_id="s1", name="S", year=YEAR, kind="season",
                      status="in_season", current_week=WEEK, roster_slots=SLOTS)
    db.add(league)
    db.add(DBNFLGame(year=YEAR, week=WEEK, home_team="ATL", away_team="NO",
                     kickoff_at=KICKOFF))
    db.commit()

    mine = DBTeam(team_id="s1-1", name="Mine", owner="B", league_id="s1",
                  manager_type="human", is_user_team=True)
    other = DBTeam(team_id="s1-2", name="Other", owner="AI", league_id="s1",
                   manager_type="ai")
    db.add_all([mine, other])
    db.commit()

    roster = {}
    for key, position in [("qb", "QB"), ("rb", "RB"), ("wr", "WR"),
                          ("te", "TE")]:
        p = DBPlayer(player_id=f"m_{key}", name=key.upper(), position=position,
                     nfl_team="ATL")
        db.add(p)
        db.commit()
        db.add(DBRosterSpot(league_id=league.id, team_id=mine.id,
                            player_id=p.id, acquired_via="draft"))
        db.add(DBPlayerProjection(player_id=p.id, year=YEAR, week=WEEK,
                                  source="model", projected_points=15.0))
        roster[key] = p
    foreign = DBPlayer(player_id="o_rb", name="Foreign", position="RB",
                       nfl_team="NO")
    db.add(foreign)
    db.commit()
    db.add(DBRosterSpot(league_id=league.id, team_id=other.id,
                        player_id=foreign.id, acquired_via="draft"))
    db.commit()
    roster["foreign"] = foreign
    return league, mine, roster


def good_result(roster, **overrides):
    result = {
        "year": YEAR,
        "week": WEEK,
        "team_id": None,  # filled by the test
        "slots": [
            {"player_id": roster["qb"].id, "slot": "QB"},
            {"player_id": roster["rb"].id, "slot": "RB"},
            {"player_id": roster["wr"].id, "slot": "FLEX"},
            {"player_id": roster["te"].id, "slot": "BENCH"},
        ],
        "changes": [
            {"player_id": roster["wr"].id, "from_slot": "BENCH",
             "to_slot": "FLEX", "reasoning": "better matchup"},
        ],
        "rationale": "Start the best available flex.",
    }
    result.update(overrides)
    return result


class TestAccepts:
    def test_a_legal_proposal_validates(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id)
        assert validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_recording_stores_a_proposed_run(self, db, setup):
        _league, mine, roster = setup
        run = record_proposal(db, good_result(roster, team_id=mine.id),
                              mine, YEAR, WEEK, now=BEFORE)
        assert run.status == "proposed"
        assert run.rationale.startswith("Start")
        assert db.query(DBManagerRun).count() == 1

    def test_applying_writes_lineup_rows_marked_agent(self, db, setup):
        _league, mine, roster = setup
        run = record_proposal(db, good_result(roster, team_id=mine.id),
                              mine, YEAR, WEEK, now=BEFORE)
        written = apply_proposal(db, run)
        assert written == 4
        rows = db.query(DBLineupSlot).filter_by(team_id=mine.id).all()
        assert all(r.set_by == "agent" for r in rows)
        assert run.status == "applied"

    def test_nothing_is_written_until_applied(self, db, setup):
        _league, mine, roster = setup
        record_proposal(db, good_result(roster, team_id=mine.id),
                        mine, YEAR, WEEK, now=BEFORE)
        assert db.query(DBLineupSlot).count() == 0


class TestRejects:
    def test_wrong_year(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id, year=2025)
        with pytest.raises(LineupRejected, match="year"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_wrong_week(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id, week=9)
        with pytest.raises(LineupRejected, match="week"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_wrong_team(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=9999)
        with pytest.raises(LineupRejected, match="team"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_a_missing_required_slot(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id)
        result["slots"] = [s for s in result["slots"] if s["slot"] != "QB"]
        with pytest.raises(LineupRejected, match="QB"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_too_many_in_a_slot(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id)
        result["slots"] = [
            {"player_id": roster["qb"].id, "slot": "QB"},
            {"player_id": roster["rb"].id, "slot": "RB"},
            {"player_id": roster["wr"].id, "slot": "RB"},
            {"player_id": roster["te"].id, "slot": "FLEX"},
        ]
        with pytest.raises(LineupRejected, match="RB"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_a_player_from_another_team(self, db, setup):
        """The boundary that makes a poisoned news headline harmless."""
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id)
        result["slots"][1]["player_id"] = roster["foreign"].id
        with pytest.raises(LineupRejected, match="not on"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_a_duplicated_player(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id)
        result["slots"][1]["player_id"] = roster["qb"].id
        with pytest.raises(LineupRejected, match="twice"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_a_quarterback_in_the_flex(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id)
        result["slots"] = [
            {"player_id": roster["rb"].id, "slot": "RB"},
            {"player_id": roster["te"].id, "slot": "QB"},
            {"player_id": roster["qb"].id, "slot": "FLEX"},
            {"player_id": roster["wr"].id, "slot": "BENCH"},
        ]
        with pytest.raises(LineupRejected, match="FLEX"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_moving_a_locked_player(self, db, setup):
        _league, mine, roster = setup
        db.add(DBLineupSlot(team_id=mine.id, year=YEAR, week=WEEK,
                            player_id=roster["qb"].id, slot="QB"))
        db.commit()
        result = good_result(roster, team_id=mine.id)
        result["slots"] = [
            {"player_id": roster["qb"].id, "slot": "BENCH"},
            {"player_id": roster["te"].id, "slot": "QB"},
            {"player_id": roster["rb"].id, "slot": "RB"},
            {"player_id": roster["wr"].id, "slot": "FLEX"},
        ]
        with pytest.raises(LineupRejected, match="locked"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, AFTER)

    def test_a_missing_player_from_the_roster(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id)
        result["slots"] = result["slots"][:3]
        with pytest.raises(LineupRejected, match="every rostered player"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_a_change_without_reasoning(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id)
        result["changes"][0].pop("reasoning")
        with pytest.raises(LineupRejected, match="reasoning"):
            validate_lineup_result(db, result, mine, YEAR, WEEK, BEFORE)

    def test_a_rejected_result_records_a_failed_run(self, db, setup):
        _league, mine, roster = setup
        result = good_result(roster, team_id=mine.id, year=2025)
        with pytest.raises(LineupRejected):
            record_proposal(db, result, mine, YEAR, WEEK, now=BEFORE)
        run = db.query(DBManagerRun).one()
        assert run.status == "failed"
        assert "year" in run.error
