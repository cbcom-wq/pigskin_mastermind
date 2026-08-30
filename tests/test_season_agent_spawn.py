"""Launching the manager agent.

No test here runs `claude`. The subprocess is a seam: what matters is that a
run is created, that a second concurrent run is refused, and that a subprocess
which fails leaves a `failed` run rather than one stuck on `running` forever.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBManagerRun, DBTeam,
)
from pigskin_mastermind.services.season_agent import (
    AGENT_TIMEOUT_SECONDS, LineupRejected, start_manager_run,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
WEEK = 5


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
def team(db):
    league = DBLeague(league_id="s1", name="S", year=YEAR, kind="season",
                      status="in_season", current_week=WEEK)
    db.add(league)
    db.commit()
    t = DBTeam(team_id="s1-1", name="Mine", owner="B", league_id="s1",
               manager_type="human", is_user_team=True)
    db.add(t)
    db.commit()
    return t


class TestStartRun:
    def test_creates_a_running_run(self, db, team):
        run = start_manager_run(db, team, WEEK, year=YEAR)
        assert run.status == "running"
        assert run.kind == "lineup"
        assert run.week == WEEK
        assert run.started_at is not None

    def test_refuses_a_second_concurrent_run_for_the_same_team(self, db, team):
        start_manager_run(db, team, WEEK, year=YEAR)
        with pytest.raises(LineupRejected, match="already running"):
            start_manager_run(db, team, WEEK, year=YEAR)

    def test_allows_a_new_run_once_the_previous_finished(self, db, team):
        first = start_manager_run(db, team, WEEK, year=YEAR)
        first.status = "discarded"
        db.commit()
        second = start_manager_run(db, team, WEEK, year=YEAR)
        assert second.id != first.id

    def test_a_run_for_a_different_week_is_allowed(self, db, team):
        start_manager_run(db, team, WEEK, year=YEAR)
        other = start_manager_run(db, team, WEEK + 1, year=YEAR)
        assert other.week == WEEK + 1

    def test_the_timeout_is_generous_but_bounded(self):
        assert 60 <= AGENT_TIMEOUT_SECONDS <= 900


class TestSubprocessFailure:
    def test_a_nonzero_exit_marks_the_run_failed(self, db, team, monkeypatch):
        from pigskin_mastermind.services import season_agent

        run = start_manager_run(db, team, WEEK, year=YEAR)

        class FakeCompleted:
            returncode = 1
            stdout = ""
            stderr = "claude: command not found"

        monkeypatch.setattr(season_agent.subprocess, "run",
                            lambda *a, **k: FakeCompleted())
        monkeypatch.setattr(season_agent, "SessionLocal", TestSessionLocal)

        season_agent.run_agent_subprocess(run.id, team.id, YEAR, WEEK)

        db.expire_all()
        refreshed = db.query(DBManagerRun).filter_by(id=run.id).one()
        assert refreshed.status == "failed"
        assert "command not found" in refreshed.error

    def test_a_timeout_marks_the_run_failed(self, db, team, monkeypatch):
        import subprocess as sp
        from pigskin_mastermind.services import season_agent

        run = start_manager_run(db, team, WEEK, year=YEAR)

        def explode(*_args, **_kwargs):
            raise sp.TimeoutExpired(cmd="claude", timeout=1)

        monkeypatch.setattr(season_agent.subprocess, "run", explode)
        monkeypatch.setattr(season_agent, "SessionLocal", TestSessionLocal)

        season_agent.run_agent_subprocess(run.id, team.id, YEAR, WEEK)

        db.expire_all()
        refreshed = db.query(DBManagerRun).filter_by(id=run.id).one()
        assert refreshed.status == "failed"
        assert "timed out" in refreshed.error.lower()

    def test_a_clean_exit_without_a_proposal_is_still_a_failure(
        self, db, team, monkeypatch,
    ):
        """Exit code 0 does not mean the agent recorded anything."""
        from pigskin_mastermind.services import season_agent

        run = start_manager_run(db, team, WEEK, year=YEAR)

        class FakeCompleted:
            returncode = 0
            stdout = "{}"
            stderr = ""

        monkeypatch.setattr(season_agent.subprocess, "run",
                            lambda *a, **k: FakeCompleted())
        monkeypatch.setattr(season_agent, "SessionLocal", TestSessionLocal)

        season_agent.run_agent_subprocess(run.id, team.id, YEAR, WEEK)

        db.expire_all()
        refreshed = db.query(DBManagerRun).filter_by(id=run.id).one()
        assert refreshed.status == "failed"
        assert "no proposal" in refreshed.error.lower()
