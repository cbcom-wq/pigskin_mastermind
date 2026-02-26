"""Tests for TeamGameSimulationService."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBTeam, DBWeeklyTeamStats, DBWeeklyPlayerStats,
)
from pigskin_mastermind.services.team_game_simulation_service import (
    TeamGameSimulationService, _sort_key_for_event,
)
from pigskin_mastermind.services.player_game_simulation_service import (
    PlayerGameSimulationService,
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


def _make_team(db, name="Test Team"):
    team = DBTeam(team_id="t1", name=name, owner="Owner", is_user_team=True)
    db.add(team)
    db.flush()
    return team


def _make_player(db, team, name, position, nfl_team="KC", pid=None):
    player = DBPlayer(
        player_id=pid or f"nfl_{name.replace(' ', '_')}",
        name=name, position=position, nfl_team=nfl_team,
        team_id=team.id,
    )
    db.add(player)
    db.flush()
    return player


def _make_weekly(db, team, week, players_slots):
    """Create weekly stats and player entries.

    players_slots: list of (player, slot_position) tuples
    """
    wts = DBWeeklyTeamStats(team_id=team.id, week=week)
    db.add(wts)
    db.flush()
    for player, slot in players_slots:
        wps = DBWeeklyPlayerStats(
            player_id=player.id,
            weekly_team_stats_id=wts.id,
            week=week,
            slot_position=slot,
            projected_points=10.0,
            actual_points=8.0,
        )
        db.add(wps)
    db.flush()
    return wts


class _StubPlayerSimService:
    """Stub that returns canned simulation data per player."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.called_ids = []

    def build_simulation(self, player_db_id, year, week):
        self.called_ids.append(player_db_id)
        if player_db_id in self.responses:
            return self.responses[player_db_id]
        # Default: return one event
        return {
            "player_id": player_db_id,
            "total_events": 1,
            "player_stats": {"pass_yards": 20},
            "events": [
                {
                    "index": 0,
                    "quarter": 1,
                    "time_label": "Q1 10:00",
                    "play_type": "pass",
                    "role": "pass",
                    "description": "Test play",
                    "yards_gained": 10,
                    "start_x": 25,
                    "epa": 0.5,
                    "route_path": {
                        "segments": [
                            {"depth": 25, "lateral": 50, "type": "los"},
                            {"depth": 35, "lateral": 55, "type": "target"},
                        ],
                        "is_complete": True,
                        "is_touchdown": False,
                        "is_sack": False,
                    },
                    "stats_snapshot": {
                        "total_plays": 1,
                        "pass_yards": 20,
                        "rush_yards": 0,
                        "rec_yards": 0,
                        "total_tds": 0,
                        "first_downs": 1,
                        "total_epa": 0.5,
                    },
                    "badges": {"touchdown": False, "first_down": True, "turnover": False, "big_play": False},
                }
            ],
        }


class TestTeamGameSimulationService:
    def test_builds_team_simulation_with_multiple_players(self, db):
        """Merges events from multiple players into unified timeline."""
        team = _make_team(db)
        qb = _make_player(db, team, "Pat Mahomes", "QB")
        rb = _make_player(db, team, "Isiah Pacheco", "RB")
        _make_weekly(db, team, 5, [(qb, "QB"), (rb, "RB")])
        db.commit()

        stub = _StubPlayerSimService(responses={
            qb.id: {
                "player_id": qb.id, "total_events": 1, "player_stats": {"pass_yards": 50},
                "events": [{
                    "index": 0, "quarter": 1, "time_label": "Q1 12:00",
                    "play_type": "pass", "role": "pass", "description": "QB pass",
                    "yards_gained": 15, "start_x": 30, "epa": 0.8,
                    "route_path": {"segments": [{"depth": 30, "lateral": 50, "type": "los"}, {"depth": 45, "lateral": 55, "type": "target"}], "is_complete": True, "is_touchdown": False, "is_sack": False},
                    "stats_snapshot": {"total_plays": 1, "pass_yards": 50, "rush_yards": 0, "rec_yards": 0, "total_tds": 0, "first_downs": 1, "total_epa": 0.8},
                    "badges": {},
                }],
            },
            rb.id: {
                "player_id": rb.id, "total_events": 1, "player_stats": {"rush_yards": 30},
                "events": [{
                    "index": 0, "quarter": 1, "time_label": "Q1 11:30",
                    "play_type": "run", "role": "rush", "description": "RB rush",
                    "yards_gained": 8, "start_x": 45, "epa": 0.3,
                    "route_path": {"segments": [{"depth": 45, "lateral": 50, "type": "los"}, {"depth": 53, "lateral": 48, "type": "run_end"}], "is_complete": False, "is_touchdown": False, "is_sack": False},
                    "stats_snapshot": {"total_plays": 1, "pass_yards": 0, "rush_yards": 30, "rec_yards": 0, "total_tds": 0, "first_downs": 0, "total_epa": 0.3},
                    "badges": {},
                }],
            },
        })

        service = TeamGameSimulationService(db, player_sim_service=stub)
        result = service.build_team_simulation(team.id, 2024, 5)

        assert result["team_name"] == "Test Team"
        assert result["year"] == 2024
        assert result["week"] == 5
        assert result["total_events"] == 2
        assert len(result["events"]) == 2
        assert len(result["players"]) == 2

        # Events should be chronological: Q1 12:00 before Q1 11:30
        first_event = result["events"][0]
        second_event = result["events"][1]
        assert first_event["time_label"] == "Q1 12:00"
        assert second_event["time_label"] == "Q1 11:30"

    def test_player_identity_tags_on_events(self, db):
        """Each event should have player_id, player_name, player_position, player_color."""
        team = _make_team(db)
        wr = _make_player(db, team, "Tyreek Hill", "WR", "MIA")
        _make_weekly(db, team, 3, [(wr, "WR")])
        db.commit()

        stub = _StubPlayerSimService()
        service = TeamGameSimulationService(db, player_sim_service=stub)
        result = service.build_team_simulation(team.id, 2024, 3)

        assert len(result["events"]) == 1
        evt = result["events"][0]
        assert evt["player_id"] == wr.id
        assert evt["player_name"] == "Tyreek Hill"
        assert evt["player_position"] == "WR"
        assert evt["player_color"] == "#3b82f6"  # WR blue

    def test_active_only_filtering(self, db):
        """Bench (BE/IR) players should be excluded from simulation."""
        team = _make_team(db)
        qb = _make_player(db, team, "Starter QB", "QB")
        bench = _make_player(db, team, "Bench RB", "RB", pid="nfl_bench_rb")
        ir_player = _make_player(db, team, "IR WR", "WR", pid="nfl_ir_wr")
        _make_weekly(db, team, 1, [
            (qb, "QB"), (bench, "BE"), (ir_player, "IR")
        ])
        db.commit()

        stub = _StubPlayerSimService()
        service = TeamGameSimulationService(db, player_sim_service=stub)
        result = service.build_team_simulation(team.id, 2024, 1)

        # Only the starter should have been simulated
        assert len(result["players"]) == 1
        assert result["players"][0]["player_name"] == "Starter QB"
        assert stub.called_ids == [qb.id]

    def test_empty_roster_handling(self, db):
        """Team with no weekly data should still return a valid response."""
        team = _make_team(db)
        db.commit()

        stub = _StubPlayerSimService()
        service = TeamGameSimulationService(db, player_sim_service=stub)
        result = service.build_team_simulation(team.id, 2024, 1)

        assert result["total_events"] == 0
        assert result["events"] == []
        assert result["players"] == []

    def test_team_stats_accumulation(self, db):
        """team_stats should aggregate final snapshots from all players."""
        team = _make_team(db)
        qb = _make_player(db, team, "QB1", "QB")
        wr = _make_player(db, team, "WR1", "WR", pid="nfl_wr1")
        _make_weekly(db, team, 2, [(qb, "QB"), (wr, "WR")])
        db.commit()

        stub = _StubPlayerSimService(responses={
            qb.id: {
                "player_id": qb.id, "total_events": 1, "player_stats": {},
                "events": [{
                    "index": 0, "quarter": 2, "time_label": "Q2 5:00",
                    "play_type": "pass", "role": "pass", "description": "Pass",
                    "yards_gained": 20, "start_x": 30, "epa": 1.0,
                    "route_path": {"segments": [{"depth": 30, "lateral": 50, "type": "los"}, {"depth": 50, "lateral": 55, "type": "target"}], "is_complete": True, "is_touchdown": False, "is_sack": False},
                    "stats_snapshot": {"total_plays": 5, "pass_yards": 100, "rush_yards": 0, "rec_yards": 0, "total_tds": 1, "first_downs": 3, "total_epa": 2.5, "pass_tds": 1},
                    "badges": {},
                }],
            },
            wr.id: {
                "player_id": wr.id, "total_events": 1, "player_stats": {},
                "events": [{
                    "index": 0, "quarter": 2, "time_label": "Q2 4:00",
                    "play_type": "pass", "role": "receive", "description": "Catch",
                    "yards_gained": 15, "start_x": 40, "epa": 0.6,
                    "route_path": {"segments": [{"depth": 40, "lateral": 45, "type": "los"}, {"depth": 55, "lateral": 50, "type": "catch_end"}], "is_complete": True, "is_touchdown": False, "is_sack": False},
                    "stats_snapshot": {"total_plays": 4, "pass_yards": 0, "rush_yards": 0, "rec_yards": 80, "receptions": 4, "total_tds": 0, "first_downs": 2, "total_epa": 1.8},
                    "badges": {},
                }],
            },
        })

        service = TeamGameSimulationService(db, player_sim_service=stub)
        result = service.build_team_simulation(team.id, 2024, 2)

        ts = result["team_stats"]
        assert ts["total_plays"] == 9  # 5 + 4
        assert ts["total_pass_yards"] == 100
        assert ts["total_rec_yards"] == 80
        assert ts["total_tds"] == 1

    def test_team_fantasy_points_accumulate(self, db):
        """Each event should have team_fantasy_points showing running total."""
        team = _make_team(db)
        qb = _make_player(db, team, "QB1", "QB")
        _make_weekly(db, team, 1, [(qb, "QB")])
        db.commit()

        stub = _StubPlayerSimService(responses={
            qb.id: {
                "player_id": qb.id, "total_events": 2, "player_stats": {},
                "events": [
                    {
                        "index": 0, "quarter": 1, "time_label": "Q1 14:00",
                        "play_type": "pass", "role": "pass", "description": "P1",
                        "yards_gained": 10, "start_x": 25, "epa": 0.3,
                        "route_path": {"segments": [{"depth": 25, "lateral": 50, "type": "los"}, {"depth": 35, "lateral": 55, "type": "target"}], "is_complete": True, "is_touchdown": False, "is_sack": False},
                        "stats_snapshot": {"total_plays": 1, "pass_yards": 25, "rush_yards": 0, "rec_yards": 0, "total_tds": 0, "first_downs": 1, "total_epa": 0.3},
                        "badges": {},
                    },
                    {
                        "index": 1, "quarter": 1, "time_label": "Q1 13:00",
                        "play_type": "pass", "role": "pass", "description": "P2",
                        "yards_gained": 30, "start_x": 35, "epa": 1.2,
                        "route_path": {"segments": [{"depth": 35, "lateral": 50, "type": "los"}, {"depth": 65, "lateral": 45, "type": "target"}], "is_complete": True, "is_touchdown": True, "is_sack": False},
                        "stats_snapshot": {"total_plays": 2, "pass_yards": 55, "rush_yards": 0, "rec_yards": 0, "total_tds": 1, "pass_tds": 1, "first_downs": 2, "total_epa": 1.5},
                        "badges": {"touchdown": True},
                    },
                ],
            },
        })

        service = TeamGameSimulationService(db, player_sim_service=stub)
        result = service.build_team_simulation(team.id, 2024, 1)

        assert len(result["events"]) == 2
        # Both events should have team_fantasy_points
        for evt in result["events"]:
            assert "team_fantasy_points" in evt
        # Second event should be >= first
        assert result["events"][1]["team_fantasy_points"] >= result["events"][0]["team_fantasy_points"]

    def test_player_sim_failure_graceful(self, db):
        """If a player's simulation fails, they should still appear in roster with 0 events."""
        team = _make_team(db)
        qb = _make_player(db, team, "QB1", "QB")
        _make_weekly(db, team, 1, [(qb, "QB")])
        db.commit()

        class _FailingStub:
            def build_simulation(self, player_db_id, year, week):
                raise ValueError("No PBP data")

        service = TeamGameSimulationService(db, player_sim_service=_FailingStub())
        result = service.build_team_simulation(team.id, 2024, 1)

        assert len(result["players"]) == 1
        assert result["players"][0]["total_events"] == 0
        assert result["total_events"] == 0

    def test_invalid_team_raises(self, db):
        """Should raise ValueError for non-existent team."""
        stub = _StubPlayerSimService()
        service = TeamGameSimulationService(db, player_sim_service=stub)
        with pytest.raises(ValueError, match="not found"):
            service.build_team_simulation(9999, 2024, 1)


class TestSortKeyForEvent:
    def test_chronological_ordering(self):
        """Events should sort Q1 before Q2, and higher seconds before lower."""
        events = [
            {"quarter": 2, "time_label": "Q2 10:00"},
            {"quarter": 1, "time_label": "Q1 5:00"},
            {"quarter": 1, "time_label": "Q1 12:00"},
        ]
        sorted_events = sorted(events, key=_sort_key_for_event)
        assert sorted_events[0]["time_label"] == "Q1 12:00"
        assert sorted_events[1]["time_label"] == "Q1 5:00"
        assert sorted_events[2]["time_label"] == "Q2 10:00"

    def test_missing_time_label(self):
        """Events with no time_label should still sort."""
        events = [
            {"quarter": 1, "time_label": "Q1 10:00"},
            {"quarter": 1, "time_label": ""},
            {"quarter": 1},
        ]
        sorted_events = sorted(events, key=_sort_key_for_event)
        # Should not raise
        assert len(sorted_events) == 3


class TestEstimateFantasyPoints:
    def test_ppr_scoring(self):
        """Check PPR fantasy points calculation."""
        pts = TeamGameSimulationService._estimate_fantasy_points({
            "pass_yards": 300,
            "pass_tds": 2,
            "rush_yards": 30,
            "receptions": 5,
            "rec_yards": 50,
            "rec_tds": 1,
        })
        expected = 300 * 0.04 + 2 * 4 + 30 * 0.1 + 5 * 1 + 50 * 0.1 + 1 * 6
        assert pts == round(expected, 1)

    def test_empty_snapshot(self):
        """Empty snapshot should return 0."""
        assert TeamGameSimulationService._estimate_fantasy_points({}) == 0.0
