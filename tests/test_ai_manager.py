"""AI managers set their own lineups; auto-fill is the floor for everyone else."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBLineupSlot, DBNFLGame, DBPlayer, DBPlayerProjection,
    DBRosterSpot, DBTeam,
)
from pigskin_mastermind.services.ai_manager import (
    autofill_missing_lineups, set_ai_lineups,
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

SLOTS = {"QB": 1, "RB": 1, "FLEX": 0, "BENCH": 2}


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
    lg = DBLeague(league_id="s1", name="S", year=YEAR, kind="season",
                  roster_slots=SLOTS, current_week=WEEK)
    db.add(lg)
    db.add(DBNFLGame(year=YEAR, week=WEEK, home_team="ATL", away_team="NO",
                     kickoff_at=KICKOFF))
    db.commit()
    return lg


def make_team(db, league, name, manager_type, slot):
    team = DBTeam(team_id=f"s1-{slot}", name=name, owner="o",
                  league_id=league.league_id, manager_type=manager_type,
                  is_user_team=(manager_type == "human"), draft_slot=slot)
    db.add(team)
    db.commit()
    for i, position in enumerate(["QB", "RB", "RB"]):
        p = DBPlayer(player_id=f"{name}_{i}", name=f"{name}{i}",
                     position=position, nfl_team="ATL")
        db.add(p)
        db.commit()
        db.add(DBRosterSpot(league_id=league.id, team_id=team.id,
                            player_id=p.id, acquired_via="draft"))
        db.add(DBPlayerProjection(player_id=p.id, year=YEAR, week=WEEK,
                                  source="model", projected_points=20.0 - i))
    db.commit()
    return team


class TestSetAiLineups:
    def test_sets_lineups_for_ai_teams_only(self, db, league):
        bot = make_team(db, league, "Bot", "ai", 1)
        human = make_team(db, league, "Human", "human", 2)

        result = set_ai_lineups(db, league, WEEK, BEFORE)

        assert result["teams"] == 1
        assert db.query(DBLineupSlot).filter_by(team_id=bot.id).count() == 3
        assert db.query(DBLineupSlot).filter_by(team_id=human.id).count() == 0

    def test_rows_are_stamped_ai(self, db, league):
        bot = make_team(db, league, "Bot", "ai", 1)
        set_ai_lineups(db, league, WEEK, BEFORE)
        rows = db.query(DBLineupSlot).filter_by(team_id=bot.id).all()
        assert all(r.set_by == "ai" for r in rows)

    def test_rerunning_is_idempotent(self, db, league):
        bot = make_team(db, league, "Bot", "ai", 1)
        set_ai_lineups(db, league, WEEK, BEFORE)
        set_ai_lineups(db, league, WEEK, BEFORE)
        assert db.query(DBLineupSlot).filter_by(team_id=bot.id).count() == 3

    def test_produces_the_same_lineup_every_time(self, db, league):
        bot = make_team(db, league, "Bot", "ai", 1)
        set_ai_lineups(db, league, WEEK, BEFORE)
        first = {r.player_id: r.slot for r in
                 db.query(DBLineupSlot).filter_by(team_id=bot.id)}
        set_ai_lineups(db, league, WEEK, BEFORE)
        second = {r.player_id: r.slot for r in
                  db.query(DBLineupSlot).filter_by(team_id=bot.id)}
        assert first == second

    def test_a_team_with_no_roster_is_skipped_not_fatal(self, db, league):
        empty = DBTeam(team_id="s1-9", name="Empty", owner="o",
                       league_id=league.league_id, manager_type="ai")
        db.add(empty)
        db.commit()
        result = set_ai_lineups(db, league, WEEK, BEFORE)
        assert result["skipped"] == 1
        assert result["teams"] == 0

    def test_one_failing_team_does_not_abort_the_league(self, db, league, monkeypatch):
        """The failure mode this try/except exists for. Without db.rollback(),
        the next team's plan_lineup raises PendingRollbackError and every
        remaining team is silently abandoned. We simulate a failed commit by
        injecting an exception into the session after a successful apply."""
        import pigskin_mastermind.services.ai_manager as ai

        first = make_team(db, league, "BotA", "ai", 1)
        second = make_team(db, league, "BotB", "ai", 2)

        real_apply = ai.apply_plan
        apply_calls = {"n": 0}

        def flaky_apply(db_, plan, set_by):
            apply_calls["n"] += 1
            if plan.team_id == first.id:
                real_apply(db_, plan, set_by)
                db_.execute(text("INSERT INTO players VALUES (9999, 'bad', 'QB', NULL)"))
                db_.commit()
            return real_apply(db_, plan, set_by)

        monkeypatch.setattr(ai, "apply_plan", flaky_apply)

        result = set_ai_lineups(db, league, WEEK, BEFORE)

        assert apply_calls["n"] == 2, "loop must attempt both teams"
        assert result["skipped"] == 1
        assert result["teams"] == 1
        assert db.query(DBLineupSlot).filter_by(team_id=second.id).count() == 3


class TestAutofill:
    def test_fills_a_team_with_no_lineup_at_all(self, db, league):
        human = make_team(db, league, "Human", "human", 2)
        result = autofill_missing_lineups(db, league, WEEK, BEFORE)
        assert result["teams"] == 1
        rows = db.query(DBLineupSlot).filter_by(team_id=human.id).all()
        assert len(rows) == 3
        assert all(r.set_by == "auto" for r in rows)

    def test_leaves_a_partially_set_lineup_alone(self, db, league):
        """Auto-fill is a floor against forgetting, not a second opinion."""
        human = make_team(db, league, "Human", "human", 2)
        player = db.query(DBPlayer).filter_by(name="Human2").one()
        db.add(DBLineupSlot(team_id=human.id, year=YEAR, week=WEEK,
                            player_id=player.id, slot="QB", set_by="user"))
        db.commit()

        result = autofill_missing_lineups(db, league, WEEK, BEFORE)

        assert result["teams"] == 0
        rows = db.query(DBLineupSlot).filter_by(team_id=human.id).all()
        assert len(rows) == 1
        assert rows[0].set_by == "user"

    def test_covers_ai_teams_too(self, db, league):
        """Belt and braces — an AI team that somehow missed its run."""
        make_team(db, league, "Bot", "ai", 1)
        result = autofill_missing_lineups(db, league, WEEK, BEFORE)
        assert result["teams"] == 1

    def test_autofill_is_scoped_to_the_target_week(self, db, league):
        """Rows for another week must not make a team look already-set."""
        team = make_team(db, league, "Human", "human", 2)
        player = db.query(DBPlayer).filter_by(name="Human0").one()
        db.add(DBLineupSlot(team_id=team.id, year=YEAR, week=WEEK + 1,
                            player_id=player.id, slot="QB", set_by="user"))
        db.commit()

        result = autofill_missing_lineups(db, league, WEEK, BEFORE)

        assert result["teams"] == 1
        assert db.query(DBLineupSlot).filter_by(team_id=team.id, week=WEEK).count() == 3
        assert db.query(DBLineupSlot).filter_by(team_id=team.id, week=WEEK + 1).count() == 1
