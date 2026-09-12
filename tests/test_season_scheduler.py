"""Polling cadence and one scheduler pass.

next_poll_at is pure so the cadence is testable without a running loop, a real
clock, or a four-hour wait.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBLeague,
    DBLineupSlot,
    DBNFLGame,
    DBPlayer,
    DBPlayerProjection,
    DBRosterSpot,
    DBTeam,
)
from pigskin_mastermind.services.season_scheduler import (
    GAME_WINDOW_HOURS,
    IDLE_MAX_SECONDS,
    LIVE_POLL_SECONDS,
    league_now,
    next_poll_at,
    tick,
    in_game_window,
)

NOW = datetime(2026, 10, 11, 12, 0)


class TestNextPollAt:
    def test_polls_fast_inside_a_game_window(self):
        kickoff = NOW - timedelta(minutes=30)
        assert next_poll_at(NOW, [kickoff]) == NOW + timedelta(
            seconds=LIVE_POLL_SECONDS,
        )

    def test_polls_fast_exactly_at_kickoff(self):
        assert next_poll_at(NOW, [NOW]) == NOW + timedelta(
            seconds=LIVE_POLL_SECONDS,
        )

    def test_sleeps_until_the_next_kickoff_when_nothing_is_live(self):
        # Inside the idle cap, or the cap would (correctly) win instead and
        # this would stop exercising the wake-at-kickoff branch at all.
        kickoff = NOW + timedelta(seconds=IDLE_MAX_SECONDS // 2)
        assert next_poll_at(NOW, [kickoff]) == kickoff

    def test_the_idle_cap_wins_over_a_distant_kickoff(self):
        """The cap is a ceiling, not a floor: a 2h kickoff still wakes at 1h."""
        kickoff = NOW + timedelta(seconds=IDLE_MAX_SECONDS * 2)
        assert next_poll_at(NOW, [kickoff]) == NOW + timedelta(
            seconds=IDLE_MAX_SECONDS,
        )

    def test_never_sleeps_past_the_idle_cap(self):
        """A Tuesday must still wake up occasionally, not sleep for four days."""
        kickoff = NOW + timedelta(days=4)
        assert next_poll_at(NOW, [kickoff]) == NOW + timedelta(
            seconds=IDLE_MAX_SECONDS,
        )

    def test_a_finished_window_is_not_live(self):
        kickoff = NOW - timedelta(hours=GAME_WINDOW_HOURS + 1)
        assert next_poll_at(NOW, [kickoff]) == NOW + timedelta(
            seconds=IDLE_MAX_SECONDS,
        )

    def test_no_games_at_all_falls_back_to_the_idle_cap(self):
        assert next_poll_at(NOW, []) == NOW + timedelta(seconds=IDLE_MAX_SECONDS)

    def test_one_live_game_beats_many_scheduled_ones(self):
        kickoffs = [
            NOW - timedelta(minutes=10),
            NOW + timedelta(hours=3),
            NOW + timedelta(days=2),
        ]
        assert next_poll_at(NOW, kickoffs) == NOW + timedelta(
            seconds=LIVE_POLL_SECONDS,
        )


class TestSchedulerClock:
    """The loop's clock must share the frame ``kickoff_at`` is stored in.

    nflverse gives gameday/gametime as US Eastern wall clock and
    ``_parse_kickoff`` keeps them naive, so the stored 13:00 of a Sunday early
    game is 1pm ET. Driving the loop off ``datetime.utcnow()`` would read that
    game as kicked off at 9am ET, autofilling every unset lineup four hours
    early and polling ESPN through the wrong window.
    """

    def test_the_clock_is_eastern_not_utc(self):
        offset = datetime.now(ZoneInfo("America/New_York")).utcoffset()
        assert offset in (timedelta(hours=-5), timedelta(hours=-4))
        gap = (datetime.utcnow() - league_now()).total_seconds()
        assert gap == pytest.approx(-offset.total_seconds(), abs=5)

    def test_the_clock_is_naive_so_it_compares_against_kickoff_at(self):
        assert league_now().tzinfo is None


test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
KICKOFF = datetime(2026, 10, 11, 13, 0)


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
    lg = DBLeague(
        league_id="s1",
        name="S",
        year=YEAR,
        kind="season",
        status="in_season",
        current_week=1,
        regular_season_weeks=14,
        roster_slots={"RB": 1, "BENCH": 1},
    )
    db.add(lg)
    db.add(
        DBNFLGame(
            year=YEAR, week=1, home_team="ATL", away_team="NO", kickoff_at=KICKOFF
        )
    )
    db.commit()

    for slot, manager in ((1, "ai"), (2, "human")):
        team = DBTeam(
            team_id=f"s1-{slot}",
            name=f"T{slot}",
            owner="o",
            league_id="s1",
            manager_type=manager,
            is_user_team=(manager == "human"),
        )
        db.add(team)
        db.commit()
        for i in range(2):
            p = DBPlayer(
                player_id=f"p{slot}{i}",
                name=f"P{slot}{i}",
                position="RB",
                nfl_team="ATL",
            )
            db.add(p)
            db.commit()
            db.add(
                DBRosterSpot(
                    league_id=lg.id,
                    team_id=team.id,
                    player_id=p.id,
                    acquired_via="draft",
                )
            )
            db.add(
                DBPlayerProjection(
                    player_id=p.id,
                    year=YEAR,
                    week=1,
                    source="model",
                    projected_points=10.0 - i,
                )
            )
        db.commit()
    return lg


class FakeClient:
    def week_events(self, year, week):
        return []

    def event_summary(self, event_id):
        return None


class TestTick:
    def test_sets_ai_lineups_when_the_week_is_open(self, db, league):
        before = KICKOFF - timedelta(days=1)
        result = tick(db, before, client=FakeClient())
        assert result["ai_lineups"] == 1
        bot = db.query(DBTeam).filter_by(manager_type="ai").one()
        assert db.query(DBLineupSlot).filter_by(team_id=bot.id).count() == 2

    def test_does_not_autofill_before_kickoff(self, db, league):
        """The human still has time to set their own lineup."""
        before = KICKOFF - timedelta(days=1)
        tick(db, before, client=FakeClient())
        human = db.query(DBTeam).filter_by(manager_type="human").one()
        assert db.query(DBLineupSlot).filter_by(team_id=human.id).count() == 0

    def test_autofills_at_first_kickoff(self, db, league):
        tick(db, KICKOFF, client=FakeClient())
        human = db.query(DBTeam).filter_by(manager_type="human").one()
        rows = db.query(DBLineupSlot).filter_by(team_id=human.id).all()
        assert len(rows) == 2
        assert all(r.set_by == "auto" for r in rows)

    def test_skips_leagues_that_are_not_in_season(self, db, league):
        league.status = "complete"
        db.commit()
        result = tick(db, KICKOFF, client=FakeClient())
        assert result["leagues"] == 0

    def test_one_broken_league_does_not_stop_the_others(self, db, league, monkeypatch):
        other = DBLeague(
            league_id="s2",
            name="S2",
            year=YEAR,
            kind="season",
            status="in_season",
            current_week=1,
        )
        db.add(other)
        db.commit()

        calls = {"n": 0}
        real = tick.__globals__["set_ai_lineups"]

        def exploding(db_, lg, week, now):
            calls["n"] += 1
            if lg.league_id == "s1":
                raise RuntimeError("boom")
            return real(db_, lg, week, now)

        monkeypatch.setattr(
            "pigskin_mastermind.services.season_scheduler.set_ai_lineups",
            exploding,
        )
        result = tick(db, KICKOFF, client=FakeClient())
        assert calls["n"] == 2
        assert result["errors"] == 1


class TestInGameWindow:
    """The game-window predicate shared by the scheduler and the dashboard."""

    def test_true_at_kickoff(self):
        kickoff = datetime(2026, 9, 13, 13, 0)
        assert in_game_window(kickoff, [kickoff]) is True

    def test_true_inside_the_window(self):
        kickoff = datetime(2026, 9, 13, 13, 0)
        assert in_game_window(kickoff + timedelta(hours=2), [kickoff]) is True

    def test_false_before_kickoff(self):
        kickoff = datetime(2026, 9, 13, 13, 0)
        assert in_game_window(kickoff - timedelta(minutes=1), [kickoff]) is False

    def test_false_at_the_window_edge(self):
        """The window is half-open: a game is over exactly four hours in."""
        kickoff = datetime(2026, 9, 13, 13, 0)
        edge = kickoff + timedelta(hours=GAME_WINDOW_HOURS)
        assert in_game_window(edge, [kickoff]) is False

    def test_false_with_no_kickoffs(self):
        kickoff = datetime(2026, 9, 13, 13, 0)
        assert in_game_window(kickoff, []) is False

    def test_any_one_game_is_enough(self):
        kickoff = datetime(2026, 9, 13, 13, 0)
        later = kickoff + timedelta(hours=7)
        assert in_game_window(later + timedelta(hours=1), [kickoff, later]) is True


class TestNextPollAtStillUsesIt:
    """Verify next_poll_at uses the in_game_window predicate."""

    def test_live_cadence_inside_a_window(self):
        kickoff = datetime(2026, 9, 13, 13, 0)
        now = kickoff + timedelta(hours=1)
        assert next_poll_at(now, [kickoff]) == now + timedelta(
            seconds=LIVE_POLL_SECONDS
        )

    def test_waits_for_the_next_kickoff_outside_a_window(self):
        kickoff = datetime(2026, 9, 13, 13, 0)
        now = kickoff - timedelta(minutes=30)
        assert next_poll_at(now, [kickoff]) == kickoff
