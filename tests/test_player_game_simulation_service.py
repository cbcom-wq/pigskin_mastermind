"""Tests for PlayerGameSimulationService."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer
from pigskin_mastermind.services.player_game_simulation_service import (
    PlayerGameSimulationService,
    _deterministic_jitter,
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


class _StubNFLDataService:
    def __init__(self, payload):
        self.payload = payload

    def get_play_by_play(self, player_db_id, year, week):
        return self.payload


class TestPlayerGameSimulationService:
    def test_builds_simulation_events_with_running_stats(self, db):
        player = DBPlayer(
            player_id="nfl_GSIS001",
            name="Test QB",
            position="QB",
            nfl_team="KC",
        )
        db.add(player)
        db.commit()

        pbp_payload = {
            "game_summary": {"home_team": "KC", "away_team": "DEN"},
            "player_stats": {"pass_yards": 12, "rush_yards": 8},
            "plays": [
                {
                    "play_id": 1,
                    "yardline_100": 75,
                    "yards_gained": 12,
                    "player_role": "pass",
                    "play_type": "pass",
                    "desc": "Pass complete for 12 yards",
                    "qtr": 1,
                    "quarter_seconds_remaining": 600,
                    "down": 1,
                    "ydstogo": 10,
                    "epa": 0.4,
                    "complete_pass": 1,
                    "touchdown": 0,
                    "interception": 0,
                    "first_down_pass": 1,
                    "first_down_rush": 0,
                    "total_home_score": 0,
                    "total_away_score": 0,
                    "air_yards": 10,
                    "yards_after_catch": 2,
                    "pass_location": "left",
                    "sack": 0,
                    "qb_scramble": 0,
                },
                {
                    "play_id": 2,
                    "yardline_100": 63,
                    "yards_gained": 8,
                    "player_role": "rush",
                    "play_type": "run",
                    "desc": "QB scramble for touchdown",
                    "qtr": 1,
                    "quarter_seconds_remaining": 540,
                    "down": 1,
                    "ydstogo": 10,
                    "epa": 1.2,
                    "complete_pass": 0,
                    "touchdown": 1,
                    "interception": 0,
                    "first_down_pass": 0,
                    "first_down_rush": 1,
                    "total_home_score": 7,
                    "total_away_score": 0,
                    "run_location": "right",
                    "run_gap": "end",
                    "sack": 0,
                },
            ],
        }

        service = PlayerGameSimulationService(
            db=db,
            nfl_data_service=_StubNFLDataService(pbp_payload),
        )
        result = service.build_simulation(player.id, year=2024, week=1)

        assert result["total_events"] == 2
        assert result["player_name"] == "Test QB"
        assert result["events"][0]["start_x"] == 25.0
        assert result["events"][0]["end_x"] == 37.0
        assert result["events"][1]["start_x"] == 37.0
        assert result["events"][1]["end_x"] == 45.0

        first_snapshot = result["events"][0]["stats_snapshot"]
        assert first_snapshot["pass_attempts"] == 1
        assert first_snapshot["pass_completions"] == 1
        assert first_snapshot["pass_yards"] == 12
        assert first_snapshot["first_downs"] == 1

        second_snapshot = result["events"][1]["stats_snapshot"]
        assert second_snapshot["rush_attempts"] == 1
        assert second_snapshot["rush_yards"] == 8
        assert second_snapshot["rush_tds"] == 1
        assert second_snapshot["total_tds"] == 1
        assert second_snapshot["total_plays"] == 2
        assert second_snapshot["total_epa"] == pytest.approx(1.6)

    def test_raises_for_unknown_player(self, db):
        service = PlayerGameSimulationService(
            db=db,
            nfl_data_service=_StubNFLDataService({"plays": []}),
        )
        with pytest.raises(ValueError, match="not found"):
            service.build_simulation(player_db_id=9999, year=2024, week=1)

    def test_route_path_present_on_events(self, db):
        """Every event should contain a route_path dict with segments."""
        player = DBPlayer(
            player_id="nfl_GSIS002",
            name="Route QB",
            position="QB",
            nfl_team="BUF",
        )
        db.add(player)
        db.commit()

        pbp_payload = {
            "game_summary": {},
            "player_stats": {},
            "plays": [
                {
                    "play_id": 100,
                    "yardline_100": 70,
                    "yards_gained": 15,
                    "player_role": "pass",
                    "play_type": "pass",
                    "desc": "Pass deep left",
                    "qtr": 1,
                    "quarter_seconds_remaining": 800,
                    "down": 1,
                    "ydstogo": 10,
                    "epa": 1.0,
                    "complete_pass": 1,
                    "touchdown": 0,
                    "interception": 0,
                    "first_down_pass": 1,
                    "first_down_rush": 0,
                    "total_home_score": 0,
                    "total_away_score": 0,
                    "air_yards": 12,
                    "yards_after_catch": 3,
                    "pass_location": "left",
                    "sack": 0,
                    "qb_scramble": 0,
                },
            ],
        }

        service = PlayerGameSimulationService(
            db=db,
            nfl_data_service=_StubNFLDataService(pbp_payload),
        )
        result = service.build_simulation(player.id, year=2024, week=5)
        evt = result["events"][0]

        rp = evt["route_path"]
        assert "segments" in rp
        assert "is_complete" in rp
        assert "is_touchdown" in rp
        assert "is_sack" in rp
        assert rp["is_complete"] is True
        assert rp["is_sack"] is False
        assert len(rp["segments"]) >= 3  # LOS, drop, target (+ catch_end)

        # First segment should be the LOS
        assert rp["segments"][0]["type"] == "los"

    def test_pass_route_has_drop_and_target(self, db):
        """A completed pass route should have LOS → drop → target → catch_end."""
        player = DBPlayer(
            player_id="nfl_GSIS003",
            name="Pass QB",
            position="QB",
            nfl_team="SF",
        )
        db.add(player)
        db.commit()

        pbp_payload = {
            "game_summary": {},
            "player_stats": {},
            "plays": [
                {
                    "play_id": 200,
                    "yardline_100": 60,
                    "yards_gained": 20,
                    "player_role": "pass",
                    "play_type": "pass",
                    "desc": "Complete pass middle",
                    "qtr": 2,
                    "quarter_seconds_remaining": 300,
                    "down": 2,
                    "ydstogo": 8,
                    "epa": 2.0,
                    "complete_pass": 1,
                    "touchdown": 1,
                    "interception": 0,
                    "first_down_pass": 1,
                    "first_down_rush": 0,
                    "total_home_score": 7,
                    "total_away_score": 0,
                    "air_yards": 15,
                    "yards_after_catch": 5,
                    "pass_location": "middle",
                    "sack": 0,
                    "qb_scramble": 0,
                },
            ],
        }

        service = PlayerGameSimulationService(
            db=db,
            nfl_data_service=_StubNFLDataService(pbp_payload),
        )
        result = service.build_simulation(player.id, year=2024, week=3)
        rp = result["events"][0]["route_path"]

        types = [s["type"] for s in rp["segments"]]
        assert "los" in types
        assert "drop" in types
        assert "target" in types
        assert "catch_end" in types
        assert rp["is_touchdown"] is True

    def test_rush_route_has_gap_direction(self, db):
        """Rush plays produce segments with run_start, run_gap, run_end."""
        player = DBPlayer(
            player_id="nfl_GSIS004",
            name="Rush RB",
            position="RB",
            nfl_team="DAL",
        )
        db.add(player)
        db.commit()

        pbp_payload = {
            "game_summary": {},
            "player_stats": {},
            "plays": [
                {
                    "play_id": 300,
                    "yardline_100": 50,
                    "yards_gained": 6,
                    "player_role": "rush",
                    "play_type": "run",
                    "desc": "Rush right end",
                    "qtr": 3,
                    "quarter_seconds_remaining": 400,
                    "down": 1,
                    "ydstogo": 10,
                    "epa": 0.5,
                    "complete_pass": 0,
                    "touchdown": 0,
                    "interception": 0,
                    "first_down_pass": 0,
                    "first_down_rush": 1,
                    "total_home_score": 14,
                    "total_away_score": 7,
                    "run_location": "right",
                    "run_gap": "end",
                    "sack": 0,
                },
            ],
        }

        service = PlayerGameSimulationService(
            db=db,
            nfl_data_service=_StubNFLDataService(pbp_payload),
        )
        result = service.build_simulation(player.id, year=2024, week=6)
        rp = result["events"][0]["route_path"]

        types = [s["type"] for s in rp["segments"]]
        assert "los" in types
        assert "run_start" in types
        assert "run_gap" in types
        assert "run_end" in types

        # The lateral should trend rightward (>50) due to run_location=right
        gap_seg = [s for s in rp["segments"] if s["type"] == "run_gap"][0]
        assert gap_seg["lateral"] > 50

    def test_sack_route_goes_behind_los(self, db):
        """A sack should produce a route that ends behind the LOS."""
        player = DBPlayer(
            player_id="nfl_GSIS005",
            name="Sacked QB",
            position="QB",
            nfl_team="NYJ",
        )
        db.add(player)
        db.commit()

        pbp_payload = {
            "game_summary": {},
            "player_stats": {},
            "plays": [
                {
                    "play_id": 400,
                    "yardline_100": 65,
                    "yards_gained": -7,
                    "player_role": "pass",
                    "play_type": "pass",
                    "desc": "Sacked for loss of 7",
                    "qtr": 1,
                    "quarter_seconds_remaining": 700,
                    "down": 3,
                    "ydstogo": 12,
                    "epa": -2.5,
                    "complete_pass": 0,
                    "touchdown": 0,
                    "interception": 0,
                    "first_down_pass": 0,
                    "first_down_rush": 0,
                    "total_home_score": 0,
                    "total_away_score": 3,
                    "air_yards": None,
                    "yards_after_catch": None,
                    "pass_location": None,
                    "sack": 1,
                    "qb_scramble": 0,
                },
            ],
        }

        service = PlayerGameSimulationService(
            db=db,
            nfl_data_service=_StubNFLDataService(pbp_payload),
        )
        result = service.build_simulation(player.id, year=2024, week=2)
        rp = result["events"][0]["route_path"]

        assert rp["is_sack"] is True
        types = [s["type"] for s in rp["segments"]]
        assert "sack_end" in types

        # Sack endpoint depth should be behind the LOS (< start_x)
        sack_seg = [s for s in rp["segments"] if s["type"] == "sack_end"][0]
        los_seg = [s for s in rp["segments"] if s["type"] == "los"][0]
        assert sack_seg["depth"] < los_seg["depth"]

    def test_incomplete_pass_route(self, db):
        """An incomplete pass route should have is_complete=False and no catch_end."""
        player = DBPlayer(
            player_id="nfl_GSIS006",
            name="Incomplete QB",
            position="QB",
            nfl_team="MIA",
        )
        db.add(player)
        db.commit()

        pbp_payload = {
            "game_summary": {},
            "player_stats": {},
            "plays": [
                {
                    "play_id": 500,
                    "yardline_100": 55,
                    "yards_gained": 0,
                    "player_role": "pass",
                    "play_type": "pass",
                    "desc": "Incomplete pass right",
                    "qtr": 2,
                    "quarter_seconds_remaining": 200,
                    "down": 2,
                    "ydstogo": 7,
                    "epa": -0.3,
                    "complete_pass": 0,
                    "touchdown": 0,
                    "interception": 0,
                    "first_down_pass": 0,
                    "first_down_rush": 0,
                    "total_home_score": 3,
                    "total_away_score": 3,
                    "air_yards": 8,
                    "yards_after_catch": 0,
                    "pass_location": "right",
                    "sack": 0,
                    "qb_scramble": 0,
                },
            ],
        }

        service = PlayerGameSimulationService(
            db=db,
            nfl_data_service=_StubNFLDataService(pbp_payload),
        )
        result = service.build_simulation(player.id, year=2024, week=4)
        rp = result["events"][0]["route_path"]

        assert rp["is_complete"] is False
        types = [s["type"] for s in rp["segments"]]
        assert "catch_end" not in types
        assert "target" in types

    def test_receive_route_has_route_break(self, db):
        """A receiver route should include a route_break segment."""
        player = DBPlayer(
            player_id="nfl_GSIS007",
            name="Fast WR",
            position="WR",
            nfl_team="PHI",
        )
        db.add(player)
        db.commit()

        pbp_payload = {
            "game_summary": {},
            "player_stats": {},
            "plays": [
                {
                    "play_id": 600,
                    "yardline_100": 40,
                    "yards_gained": 25,
                    "player_role": "receive",
                    "play_type": "pass",
                    "desc": "Complete deep pass to WR",
                    "qtr": 4,
                    "quarter_seconds_remaining": 120,
                    "down": 1,
                    "ydstogo": 10,
                    "epa": 3.0,
                    "complete_pass": 1,
                    "touchdown": 1,
                    "interception": 0,
                    "first_down_pass": 1,
                    "first_down_rush": 0,
                    "total_home_score": 21,
                    "total_away_score": 17,
                    "air_yards": 20,
                    "yards_after_catch": 5,
                    "pass_location": "left",
                    "sack": 0,
                },
            ],
        }

        service = PlayerGameSimulationService(
            db=db,
            nfl_data_service=_StubNFLDataService(pbp_payload),
        )
        result = service.build_simulation(player.id, year=2024, week=8)
        rp = result["events"][0]["route_path"]

        types = [s["type"] for s in rp["segments"]]
        assert "route_break" in types
        assert "catch_end" in types
        assert rp["is_touchdown"] is True

    def test_missing_location_uses_jitter(self, db):
        """When pass_location is None, lateral should still be deterministic."""
        player = DBPlayer(
            player_id="nfl_GSIS008",
            name="Jitter QB",
            position="QB",
            nfl_team="LAR",
        )
        db.add(player)
        db.commit()

        pbp_payload = {
            "game_summary": {},
            "player_stats": {},
            "plays": [
                {
                    "play_id": 700,
                    "yardline_100": 50,
                    "yards_gained": 5,
                    "player_role": "pass",
                    "play_type": "pass",
                    "desc": "Short pass",
                    "qtr": 1,
                    "quarter_seconds_remaining": 900,
                    "down": 1,
                    "ydstogo": 10,
                    "epa": 0.2,
                    "complete_pass": 1,
                    "touchdown": 0,
                    "interception": 0,
                    "first_down_pass": 0,
                    "first_down_rush": 0,
                    "total_home_score": 0,
                    "total_away_score": 0,
                    "air_yards": 3,
                    "yards_after_catch": 2,
                    "pass_location": None,
                    "sack": 0,
                    "qb_scramble": 0,
                },
            ],
        }

        service = PlayerGameSimulationService(
            db=db,
            nfl_data_service=_StubNFLDataService(pbp_payload),
        )
        r1 = service.build_simulation(player.id, year=2024, week=1)
        r2 = service.build_simulation(player.id, year=2024, week=1)

        # Same play_id → same route geometry (deterministic jitter)
        segs1 = r1["events"][0]["route_path"]["segments"]
        segs2 = r2["events"][0]["route_path"]["segments"]
        assert len(segs1) == len(segs2)
        for s1, s2 in zip(segs1, segs2):
            assert s1["lateral"] == pytest.approx(s2["lateral"])
            assert s1["depth"] == pytest.approx(s2["depth"])


class TestDeterministicJitter:
    def test_same_seed_same_result(self):
        assert _deterministic_jitter(42) == _deterministic_jitter(42)

    def test_different_seeds_differ(self):
        assert _deterministic_jitter(1) != _deterministic_jitter(2)

    def test_within_amplitude(self):
        for seed in range(100):
            val = _deterministic_jitter(seed, amplitude=10.0)
            assert -10.0 <= val <= 10.0
