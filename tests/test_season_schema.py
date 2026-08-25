"""Schema guarantees for season leagues.

The partial unique index is the important one: it is what enforces "a player is
on exactly one team in this league" without touching DBPlayer.team_id, which
can only express one assignment across the entire application.
"""

import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBLineupSlot, DBManagerRun, DBMatchup, DBPlayer,
    DBRosterSpot, DBTeam,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


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
def league(db):
    lg = DBLeague(league_id="season-1", name="Test League", year=2026, kind="season")
    db.add(lg)
    db.commit()
    return lg


def _team(db, league, name, manager_type="ai", slot=1):
    t = DBTeam(
        team_id=f"{league.league_id}-{slot}", name=name, owner="AI",
        league_id=league.league_id, manager_type=manager_type, draft_slot=slot,
    )
    db.add(t)
    db.commit()
    return t


def _player(db, name, position="RB"):
    p = DBPlayer(player_id=f"test_{name}", name=name, position=position, nfl_team="ATL")
    db.add(p)
    db.commit()
    return p


class TestLeagueDefaults:
    def test_existing_leagues_default_to_espn_kind(self, db):
        lg = DBLeague(league_id="espn-1", name="Old", year=2025)
        db.add(lg)
        db.commit()
        assert lg.kind == "espn"

    def test_season_league_carries_format_settings(self, db):
        lg = DBLeague(league_id="s", name="S", year=2026, kind="season")
        db.add(lg)
        db.commit()
        assert lg.regular_season_weeks == 14
        assert lg.playoff_teams == 6
        assert lg.playoff_start_week == 15
        assert lg.current_week == 1


class TestTeamManagerType:
    def test_teams_default_to_human(self, db, league):
        t = DBTeam(team_id="t1", name="Mine", owner="me", league_id=league.league_id)
        db.add(t)
        db.commit()
        assert t.manager_type == "human"
        assert t.owner_user_id is None

    def test_ai_team_carries_its_draft_persona(self, db, league):
        t = _team(db, league, "Bot", slot=3)
        t.ai_strategy = "best_available"
        t.ai_profile = {"aggressiveness": 0.7}
        db.commit()
        assert t.ai_profile["aggressiveness"] == 0.7


class TestRosterSpotUniqueness:
    def test_one_active_spot_per_player_per_league(self, db, league):
        a = _team(db, league, "A", slot=1)
        b = _team(db, league, "B", slot=2)
        p = _player(db, "Bijan")
        db.add(DBRosterSpot(league_id=league.id, team_id=a.id, player_id=p.id,
                            acquired_via="draft"))
        db.commit()
        db.add(DBRosterSpot(league_id=league.id, team_id=b.id, player_id=p.id,
                            acquired_via="draft"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_a_dropped_player_can_be_picked_up_again(self, db, league):
        """The index is partial on dropped_at IS NULL — this is what Cycle 2 needs."""
        a = _team(db, league, "A", slot=1)
        b = _team(db, league, "B", slot=2)
        p = _player(db, "Bijan")
        old = DBRosterSpot(league_id=league.id, team_id=a.id, player_id=p.id,
                           acquired_via="draft")
        db.add(old)
        db.commit()
        old.dropped_at = datetime(2026, 10, 1)
        db.commit()
        db.add(DBRosterSpot(league_id=league.id, team_id=b.id, player_id=p.id,
                            acquired_via="waiver"))
        db.commit()  # must not raise
        assert db.query(DBRosterSpot).count() == 2

    def test_the_same_player_may_be_in_two_different_leagues(self, db, league):
        other = DBLeague(league_id="season-2", name="Other", year=2026, kind="season")
        db.add(other)
        db.commit()
        a = _team(db, league, "A", slot=1)
        b = _team(db, other, "B", slot=1)
        p = _player(db, "Bijan")
        db.add(DBRosterSpot(league_id=league.id, team_id=a.id, player_id=p.id,
                            acquired_via="draft"))
        db.add(DBRosterSpot(league_id=other.id, team_id=b.id, player_id=p.id,
                            acquired_via="draft"))
        db.commit()  # must not raise
        assert db.query(DBRosterSpot).count() == 2


class TestMatchup:
    def test_bracket_slot_keys_the_week(self, db, league):
        a = _team(db, league, "A", slot=1)
        b = _team(db, league, "B", slot=2)
        db.add(DBMatchup(league_id=league.id, year=2026, week=1, bracket_slot=0,
                         home_team_id=a.id, away_team_id=b.id))
        db.commit()
        db.add(DBMatchup(league_id=league.id, year=2026, week=1, bracket_slot=0,
                         home_team_id=b.id, away_team_id=a.id))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_playoff_rows_may_be_unseeded(self, db, league):
        """Null team ids are how weeks 15-17 exist before seeding."""
        db.add(DBMatchup(league_id=league.id, year=2026, week=15, bracket_slot=0,
                         is_playoff=True, round_name="quarterfinal"))
        db.add(DBMatchup(league_id=league.id, year=2026, week=15, bracket_slot=1,
                         is_playoff=True, round_name="quarterfinal"))
        db.commit()
        assert db.query(DBMatchup).filter_by(is_playoff=True).count() == 2

    def test_status_defaults_to_scheduled(self, db, league):
        m = DBMatchup(league_id=league.id, year=2026, week=1, bracket_slot=0)
        db.add(m)
        db.commit()
        assert m.status == "scheduled"


class TestLineupSlot:
    def test_one_row_per_player_per_team_week(self, db, league):
        t = _team(db, league, "A", slot=1)
        p = _player(db, "Bijan")
        db.add(DBLineupSlot(team_id=t.id, year=2026, week=3, player_id=p.id, slot="RB"))
        db.commit()
        db.add(DBLineupSlot(team_id=t.id, year=2026, week=3, player_id=p.id, slot="BENCH"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_same_player_different_weeks_is_fine(self, db, league):
        t = _team(db, league, "A", slot=1)
        p = _player(db, "Bijan")
        db.add(DBLineupSlot(team_id=t.id, year=2026, week=3, player_id=p.id, slot="RB"))
        db.add(DBLineupSlot(team_id=t.id, year=2026, week=4, player_id=p.id, slot="RB"))
        db.commit()
        assert db.query(DBLineupSlot).count() == 2


class TestManagerRun:
    def test_run_records_a_proposal(self, db, league):
        t = _team(db, league, "A", manager_type="human", slot=1)
        run = DBManagerRun(
            league_id=league.id, team_id=t.id, year=2026, week=5,
            kind="lineup", status="proposed",
            proposal={"slots": [], "changes": []}, rationale="because",
        )
        db.add(run)
        db.commit()
        assert run.status == "proposed"
        assert run.proposal["changes"] == []
