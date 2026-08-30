"""The evidence pack handed to the team-manager agent.

News headlines in this document are untrusted third-party text. They are data,
never instructions — the guarantee that matters is enforced by the validator in
Task 16, not by wording here.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBMatchup, DBNFLGame, DBPlayer, DBPlayerNews,
    DBPlayerProjection, DBRosterSpot, DBTeam,
)
from pigskin_mastermind.services.season_agent import build_team_evidence
from pigskin_mastermind.services.season_scheduler import league_now

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
    league = DBLeague(league_id="s1", name="Sunday Money", year=YEAR,
                      kind="season", status="in_season", current_week=WEEK,
                      regular_season_weeks=14, playoff_teams=6,
                      roster_slots={"QB": 1, "RB": 1, "FLEX": 1, "BENCH": 2})
    db.add(league)
    db.add(DBNFLGame(year=YEAR, week=WEEK, home_team="ATL", away_team="NO",
                     kickoff_at=KICKOFF))
    db.commit()

    mine = DBTeam(team_id="s1-1", name="Mine", owner="B", league_id="s1",
                  manager_type="human", is_user_team=True, wins=3, losses=1,
                  total_points=450.0)
    theirs = DBTeam(team_id="s1-2", name="Theirs", owner="AI", league_id="s1",
                    manager_type="ai", wins=2, losses=2, total_points=420.0)
    db.add_all([mine, theirs])
    db.commit()

    for team, prefix in ((mine, "M"), (theirs, "T")):
        for i, position in enumerate(["QB", "RB", "RB", "WR"]):
            p = DBPlayer(player_id=f"{prefix}{i}", name=f"{prefix} Player {i}",
                         position=position, nfl_team="ATL", espn_id=f"{prefix}{i}")
            db.add(p)
            db.commit()
            db.add(DBRosterSpot(league_id=league.id, team_id=team.id,
                                player_id=p.id, acquired_via="draft"))
            db.add(DBPlayerProjection(player_id=p.id, year=YEAR, week=WEEK,
                                      source="model",
                                      projected_points=20.0 - i,
                                      floor=10.0 - i, ceiling=30.0 - i))
        db.commit()

    db.add(DBMatchup(league_id=league.id, year=YEAR, week=WEEK, bracket_slot=0,
                     home_team_id=mine.id, away_team_id=theirs.id))
    db.commit()
    return league, mine, theirs


class TestContext:
    def test_names_the_scope(self, db, setup):
        _league, mine, _theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        assert pack["context"]["year"] == YEAR
        assert pack["context"]["week"] == WEEK
        assert pack["context"]["team_id"] == mine.id

    def test_carries_league_rules(self, db, setup):
        _league, mine, _theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        assert pack["league"]["roster_slots"]["QB"] == 1
        assert pack["league"]["scoring_settings"]["rec"] == 0.5

    def test_carries_the_teams_record(self, db, setup):
        _league, mine, _theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        assert pack["team"]["record"] == {"wins": 3, "losses": 1, "ties": 0}


class TestDefaultClock:
    def test_the_default_now_is_eastern_not_utc(self, db, setup):
        """Locks are decided against kickoff_at, which is naive US Eastern.

        Defaulting to utcnow() would place 'now' 4-5 hours ahead of the frame
        the schedule is stored in, so a pack built on Sunday morning would
        report every player already locked. See Ruling T14-1.
        """
        _league, mine, _theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR)
        as_of = datetime.fromisoformat(pack["context"]["as_of"])
        assert abs((as_of - league_now()).total_seconds()) < 5


class TestRoster:
    def test_lists_every_rostered_player(self, db, setup):
        _league, mine, _theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        assert len(pack["roster"]) == 4

    def test_each_player_has_a_projection_band(self, db, setup):
        _league, mine, _theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        top = pack["roster"][0]
        assert top["projected_points"] == 20.0
        assert top["floor"] == 10.0
        assert top["ceiling"] == 30.0

    def test_each_player_reports_lock_state_and_kickoff(self, db, setup):
        _league, mine, _theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        assert all(p["locked"] is False for p in pack["roster"])
        assert all(p["kickoff_at"] for p in pack["roster"])

    def test_locked_players_are_flagged_after_kickoff(self, db, setup):
        _league, mine, _theirs = setup
        after = KICKOFF.replace(hour=16)
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=after)
        assert all(p["locked"] is True for p in pack["roster"])

    def test_news_headlines_are_included(self, db, setup):
        _league, mine, _theirs = setup
        player = db.query(DBPlayer).filter_by(player_id="M0").one()
        db.add(DBPlayerNews(player_id=player.id, espn_headline_id="h1",
                            headline="Limited in practice",
                            published_at=datetime(2026, 10, 9)))
        db.commit()
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        entry = next(p for p in pack["roster"] if p["player_id"] == player.id)
        assert entry["news"][0]["headline"] == "Limited in practice"


class TestMatchupAndBaseline:
    def test_names_the_opponent(self, db, setup):
        _league, mine, theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        assert pack["matchup"]["opponent_name"] == "Theirs"
        assert pack["matchup"]["opponent_projected_total"] > 0

    def test_includes_the_deterministic_baseline(self, db, setup):
        """The agent should improve on the model, not re-derive it."""
        _league, mine, _theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        assert pack["baseline"]["projected_total"] > 0
        slots = {s["slot"] for s in pack["baseline"]["slots"]}
        assert "QB" in slots and "RB" in slots

    def test_a_bye_week_opponent_is_handled(self, db, setup):
        league, mine, _theirs = setup
        db.query(DBMatchup).delete()
        db.commit()
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        assert pack["matchup"]["opponent_name"] is None


class TestSerialisable:
    def test_the_pack_is_json_serialisable(self, db, setup):
        import json
        _league, mine, _theirs = setup
        pack = build_team_evidence(db, mine, WEEK, year=YEAR, now=BEFORE)
        assert json.loads(json.dumps(pack))["context"]["week"] == WEEK
