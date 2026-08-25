"""The deterministic lineup planner.

Determinism is the load-bearing property: the AI must produce the same lineup
from the same roster every time, or nobody can reproduce a bad week.
"""

from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBLineupSlot, DBNFLGame, DBPlayer, DBPlayerProjection,
    DBRosterSpot, DBTeam,
)
from pigskin_mastermind.services.lineup_manager import (
    _rank_key, apply_plan, plan_lineup,
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

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1, "BENCH": 6}


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
                  roster_slots=SLOTS)
    db.add(lg)
    db.commit()
    return lg


@pytest.fixture
def team(db, league):
    t = DBTeam(team_id="s1-1", name="A", owner="B", league_id=league.league_id)
    db.add(t)
    db.commit()
    return t


@pytest.fixture
def schedule(db):
    """Every team plays week 5 except DAL, which is on bye."""
    for home, away in [("ATL", "NO"), ("KC", "BAL"), ("SF", "SEA"), ("BUF", "MIA")]:
        db.add(DBNFLGame(year=YEAR, week=WEEK, home_team=home, away_team=away,
                         kickoff_at=KICKOFF))
    db.commit()


def add_player(db, league, team, name, position, nfl_team, points,
               injury_status=None):
    p = DBPlayer(player_id=f"p_{name}", name=name, position=position,
                 nfl_team=nfl_team, injury_status=injury_status)
    db.add(p)
    db.commit()
    db.add(DBRosterSpot(league_id=league.id, team_id=team.id, player_id=p.id,
                        acquired_via="draft"))
    db.add(DBPlayerProjection(player_id=p.id, year=YEAR, week=WEEK,
                              source="model", projected_points=points))
    db.commit()
    return p


@pytest.fixture
def full_roster(db, league, team, schedule):
    """A legal roster with a clear best lineup."""
    return {
        "qb": add_player(db, league, team, "QB1", "QB", "KC", 22.0),
        "qb2": add_player(db, league, team, "QB2", "QB", "BAL", 15.0),
        "rb1": add_player(db, league, team, "RB1", "RB", "ATL", 18.0),
        "rb2": add_player(db, league, team, "RB2", "RB", "SF", 14.0),
        "rb3": add_player(db, league, team, "RB3", "RB", "BUF", 9.0),
        "wr1": add_player(db, league, team, "WR1", "WR", "NO", 17.0),
        "wr2": add_player(db, league, team, "WR2", "WR", "SEA", 13.0),
        "wr3": add_player(db, league, team, "WR3", "WR", "MIA", 11.0),
        "te1": add_player(db, league, team, "TE1", "TE", "KC", 10.0),
        "k1": add_player(db, league, team, "K1", "K", "BAL", 8.0),
        "def1": add_player(db, league, team, "DEF1", "DEF", "SF", 7.0),
    }


def slot_of(plan, player):
    return next(d.slot for d in plan.decisions if d.player_id == player.id)


class TestBasicSlotting:
    def test_fills_every_required_slot(self, db, team, league, full_roster):
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        starters = [d.slot for d in plan.starters()]
        assert sorted(starters) == sorted(
            ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
        )

    def test_starts_the_best_at_each_position(self, db, team, league, full_roster):
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        assert slot_of(plan, full_roster["qb"]) == "QB"
        assert slot_of(plan, full_roster["qb2"]) == "BENCH"

    def test_flex_takes_the_best_remaining_eligible_player(
        self, db, team, league, full_roster,
    ):
        """RB3 at 9.0 loses to WR3 at 11.0 for the flex."""
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        assert slot_of(plan, full_roster["wr3"]) == "FLEX"
        assert slot_of(plan, full_roster["rb3"]) == "BENCH"

    def test_flex_never_takes_a_quarterback_or_kicker(
        self, db, team, league, full_roster,
    ):
        """Even a 22-point QB2 is not flex-eligible."""
        for name in ("QB2", "K1", "DEF1"):
            player = db.query(DBPlayer).filter_by(name=name).one()
            row = db.query(DBPlayerProjection).filter_by(
                player_id=player.id, week=WEEK,
            ).one()
            row.projected_points = 99.0
        db.commit()
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        flex = [d for d in plan.decisions if d.slot == "FLEX"]
        assert flex[0].position in {"RB", "WR", "TE"}

    def test_projected_total_counts_starters_only(
        self, db, team, league, full_roster,
    ):
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        expected = sum(d.projected_points for d in plan.starters())
        assert plan.projected_total == pytest.approx(expected)


class TestDeterminism:
    def test_the_same_roster_yields_the_same_lineup(
        self, db, team, league, full_roster,
    ):
        first = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        for _ in range(5):
            again = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
            assert [(d.player_id, d.slot) for d in first.decisions] == \
                   [(d.player_id, d.slot) for d in again.decisions]

    def test_ties_break_on_player_id_not_at_random(
        self, db, team, league, schedule,
    ):
        a = add_player(db, league, team, "TieA", "QB", "KC", 15.0)
        b = add_player(db, league, team, "TieB", "QB", "BAL", 15.0)
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        starter = next(d.player_id for d in plan.decisions if d.slot == "QB")
        assert starter == min(a.id, b.id)


class TestRankKey:
    """Unit-tests the sort key directly, with stub candidates whose input
    order the test genuinely controls. A DB-backed test can pass even
    without an id tiebreak, because SQLite happens to return rows in
    ascending-id order and Python's sort is stable — this does not.
    """

    def test_ties_sort_by_id_ascending_regardless_of_input_order(self):
        shuffled_ids = [40, 10, 30, 50, 20]
        candidates = [
            {"points": 10.0, "player": SimpleNamespace(id=pid)}
            for pid in shuffled_ids
        ]
        ordered = sorted(candidates, key=_rank_key)
        assert [c["player"].id for c in ordered] == [10, 20, 30, 40, 50]

    def test_higher_points_always_sorts_before_lower_points(self):
        candidates = [
            {"points": 5.0, "player": SimpleNamespace(id=1)},
            {"points": 25.0, "player": SimpleNamespace(id=99)},
            {"points": 15.0, "player": SimpleNamespace(id=2)},
        ]
        ordered = sorted(candidates, key=_rank_key)
        assert [c["player"].id for c in ordered] == [99, 2, 1]


class TestByesAndInjuries:
    def test_a_player_on_bye_is_benched(self, db, team, league, full_roster):
        """DAL has no week 5 game in the fixture schedule."""
        bye_rb = add_player(db, league, team, "ByeRB", "RB", "DAL", 30.0)
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        assert slot_of(plan, bye_rb) == "BENCH"
        assert "bye" in next(
            d.reason for d in plan.decisions if d.player_id == bye_rb.id
        ).lower()

    def test_an_out_player_is_benched_however_good(
        self, db, team, league, full_roster,
    ):
        hurt = add_player(db, league, team, "OutRB", "RB", "ATL", 30.0,
                          injury_status="OUT")
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        assert slot_of(plan, hurt) == "BENCH"

    def test_ir_and_suspended_are_treated_the_same_as_out(
        self, db, team, league, full_roster,
    ):
        for status in ("IR", "SUSPENDED"):
            player = add_player(db, league, team, f"{status}RB", "RB", "ATL",
                                30.0, injury_status=status)
            plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
            assert slot_of(plan, player) == "BENCH"

    def test_a_questionable_star_still_beats_a_healthy_backup(
        self, db, team, league, schedule,
    ):
        """Hard-benching every tag is how an AI starts nobody in November."""
        star = add_player(db, league, team, "StarRB", "RB", "ATL", 20.0,
                          injury_status="QUESTIONABLE")
        scrub = add_player(db, league, team, "ScrubRB", "RB", "SF", 8.0)
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        assert slot_of(plan, star) == "RB"
        assert slot_of(plan, scrub) == "RB"
        star_decision = next(d for d in plan.decisions if d.player_id == star.id)
        assert star_decision.projected_points < 20.0  # haircut applied

    def test_a_doubtful_player_loses_to_a_close_healthy_one(
        self, db, team, league, schedule,
    ):
        doubtful = add_player(db, league, team, "DoubtQB", "QB", "ATL", 20.0,
                              injury_status="DOUBTFUL")
        healthy = add_player(db, league, team, "HealthyQB", "QB", "SF", 14.0)
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        assert slot_of(plan, healthy) == "QB"
        assert slot_of(plan, doubtful) == "BENCH"


class TestLocks:
    def test_a_locked_starter_keeps_his_slot(self, db, team, league, full_roster):
        """Even when a better option appears after kickoff."""
        apply_plan(db, plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league),
                   set_by="auto")
        better = add_player(db, league, team, "LateRB", "RB", "BUF", 40.0)
        plan = plan_lineup(db, team, YEAR, WEEK, AFTER, league=league)
        assert slot_of(plan, full_roster["rb1"]) == "RB"
        assert slot_of(plan, better) == "BENCH"

    def test_a_locked_bench_player_cannot_be_promoted(
        self, db, team, league, full_roster,
    ):
        apply_plan(db, plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league),
                   set_by="auto")
        plan = plan_lineup(db, team, YEAR, WEEK, AFTER, league=league)
        assert slot_of(plan, full_roster["rb3"]) == "BENCH"

    def test_before_kickoff_everything_is_movable(
        self, db, team, league, full_roster,
    ):
        apply_plan(db, plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league),
                   set_by="auto")
        better = add_player(db, league, team, "LateRB", "RB", "BUF", 40.0)
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        assert slot_of(plan, better) == "RB"


class TestColdStartAfterKickoff:
    """The auto-fill fallback runs AT first kickoff, when Thursday players are
    already locked. A locked player with no prior row has no placement to
    preserve and must be assignable."""

    def test_a_locked_player_with_no_prior_row_still_fills_his_slot(
        self, db, team, league, full_roster,
    ):
        plan = plan_lineup(db, team, YEAR, WEEK, AFTER, league=league)
        assert slot_of(plan, full_roster["qb"]) == "QB"
        assert plan.projected_total > 0

    def test_cold_start_after_kickoff_fills_every_required_slot(
        self, db, team, league, full_roster,
    ):
        plan = plan_lineup(db, team, YEAR, WEEK, AFTER, league=league)
        starters = sorted(d.slot for d in plan.starters())
        assert starters == sorted(
            ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
        )

    def test_a_locked_player_already_placed_still_cannot_move(
        self, db, team, league, full_roster,
    ):
        """The anti-move rule must survive the fix."""
        apply_plan(db, plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league),
                   set_by="auto")
        better = add_player(db, league, team, "LateRB", "RB", "BUF", 40.0)
        plan = plan_lineup(db, team, YEAR, WEEK, AFTER, league=league)
        assert slot_of(plan, full_roster["rb1"]) == "RB"
        assert slot_of(plan, better) == "BENCH"


class TestApplyPlan:
    def test_writes_one_row_per_rostered_player(
        self, db, team, league, full_roster,
    ):
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        written = apply_plan(db, plan, set_by="ai")
        assert written == 11
        rows = db.query(DBLineupSlot).filter_by(team_id=team.id, week=WEEK).all()
        assert len(rows) == 11
        assert all(r.set_by == "ai" for r in rows)

    def test_reapplying_updates_rather_than_duplicating(
        self, db, team, league, full_roster,
    ):
        apply_plan(db, plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league),
                   set_by="auto")
        apply_plan(db, plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league),
                   set_by="user")
        rows = db.query(DBLineupSlot).filter_by(team_id=team.id, week=WEEK).all()
        assert len(rows) == 11
        assert all(r.set_by == "user" for r in rows)

    def test_stamps_locked_at_for_players_whose_game_started(
        self, db, team, league, full_roster,
    ):
        plan = plan_lineup(db, team, YEAR, WEEK, AFTER, league=league)
        apply_plan(db, plan, set_by="auto")
        rows = db.query(DBLineupSlot).filter_by(team_id=team.id, week=WEEK).all()
        assert all(r.locked_at == KICKOFF for r in rows)

    def test_does_not_stamp_locked_at_before_kickoff(
        self, db, team, league, full_roster,
    ):
        apply_plan(db, plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league),
                   set_by="auto")
        rows = db.query(DBLineupSlot).filter_by(team_id=team.id, week=WEEK).all()
        assert all(r.locked_at is None for r in rows)


class TestThinRosters:
    def test_an_unfillable_slot_is_left_empty_rather_than_crashing(
        self, db, team, league, schedule,
    ):
        """A roster with no kicker still produces a usable lineup."""
        add_player(db, league, team, "OnlyQB", "QB", "KC", 20.0)
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        assert [d.slot for d in plan.starters()] == ["QB"]

    def test_a_player_with_no_projection_is_ranked_last_not_dropped(
        self, db, team, league, schedule,
    ):
        known = add_player(db, league, team, "KnownQB", "QB", "KC", 20.0)
        unknown = DBPlayer(player_id="p_unknown", name="UnknownQB",
                           position="QB", nfl_team="SF")
        db.add(unknown)
        db.commit()
        db.add(DBRosterSpot(league_id=league.id, team_id=team.id,
                            player_id=unknown.id, acquired_via="draft"))
        db.commit()
        plan = plan_lineup(db, team, YEAR, WEEK, BEFORE, league=league)
        assert slot_of(plan, known) == "QB"
        assert slot_of(plan, unknown) == "BENCH"
