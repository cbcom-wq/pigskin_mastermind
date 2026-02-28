"""Tests for NFLGameSimulationService."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer
from pigskin_mastermind.services.nfl_game_simulation_service import (
    NFLGameSimulationService,
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
    """Stub that returns canned game PBP data."""

    def __init__(self, payload):
        self.payload = payload

    def get_game_play_by_play(self, game_id, year, week):
        return self.payload


class TestNFLGameSimulationService:
    def test_builds_game_simulation_events(self, db):
        """Basic pass play should produce an event with expected fields."""
        pbp_payload = {
            "game_summary": {
                "game_id": "2024_01_KC_BAL",
                "home_team": "KC",
                "away_team": "BAL",
                "home_score": 27,
                "away_score": 20,
            },
            "plays": [
                {
                    "play_id": 100,
                    "play_type": "pass",
                    "desc": "P.Mahomes pass short left to T.Kelce for 12 yards",
                    "qtr": 1,
                    "quarter_seconds_remaining": 600,
                    "game_seconds_remaining": 3300,
                    "down": 1,
                    "ydstogo": 10,
                    "yardline_100": 75,
                    "yards_gained": 12,
                    "air_yards": 5,
                    "yards_after_catch": 7,
                    "epa": 1.2,
                    "pass_location": "left",
                    "complete_pass": 1,
                    "touchdown": 0,
                    "interception": 0,
                    "sack": 0,
                    "first_down_pass": 1,
                    "first_down_rush": 0,
                    "posteam": "KC",
                    "defteam": "BAL",
                    "total_home_score": 7,
                    "total_away_score": 3,
                    "passer_player_id": "GSIS_001",
                    "passer_player_name": "P.Mahomes",
                    "receiver_player_id": "GSIS_002",
                    "receiver_player_name": "T.Kelce",
                    "rusher_player_id": None,
                    "rusher_player_name": None,
                    "primary_player": "P.Mahomes",
                    "primary_role": "pass",
                },
            ],
        }

        stub = _StubNFLDataService(pbp_payload)
        service = NFLGameSimulationService(db, nfl_data_service=stub)
        result = service.build_game_simulation("2024_01_KC_BAL", 2024, 1)

        assert result["game_id"] == "2024_01_KC_BAL"
        assert result["total_events"] == 1
        assert result["game_summary"]["home_team"] == "KC"

        event = result["events"][0]
        assert event["role"] == "pass"
        assert event["yards_gained"] == 12
        assert event["passer_name"] == "P.Mahomes"
        assert event["receiver_name"] == "T.Kelce"
        assert event["posteam"] == "KC"
        assert event["badges"]["first_down"] is True
        assert event["route_path"]["is_complete"] is True

    def test_rush_play_event(self, db):
        """Rush play should have correct role and rusher info."""
        pbp_payload = {
            "game_summary": {"game_id": "2024_01_KC_BAL"},
            "plays": [
                {
                    "play_id": 200,
                    "play_type": "run",
                    "desc": "I.Pacheco up the middle for 5 yards",
                    "qtr": 2,
                    "quarter_seconds_remaining": 300,
                    "game_seconds_remaining": 2100,
                    "down": 2,
                    "ydstogo": 8,
                    "yardline_100": 60,
                    "yards_gained": 5,
                    "epa": 0.3,
                    "run_location": "middle",
                    "run_gap": "guard",
                    "complete_pass": 0,
                    "touchdown": 0,
                    "interception": 0,
                    "sack": 0,
                    "first_down_pass": 0,
                    "first_down_rush": 0,
                    "passer_player_id": None,
                    "passer_player_name": None,
                    "receiver_player_id": None,
                    "receiver_player_name": None,
                    "rusher_player_id": "GSIS_003",
                    "rusher_player_name": "I.Pacheco",
                    "primary_player": "I.Pacheco",
                    "primary_role": "rush",
                },
            ],
        }

        stub = _StubNFLDataService(pbp_payload)
        service = NFLGameSimulationService(db, nfl_data_service=stub)
        result = service.build_game_simulation("2024_01_KC_BAL", 2024, 1)

        event = result["events"][0]
        assert event["role"] == "rush"
        assert event["rusher_name"] == "I.Pacheco"

    def test_touchdown_badge(self, db):
        """Touchdown plays should have the touchdown badge set."""
        pbp_payload = {
            "game_summary": {"game_id": "2024_01_KC_BAL"},
            "plays": [
                {
                    "play_id": 300,
                    "play_type": "pass",
                    "desc": "Touchdown pass",
                    "qtr": 1,
                    "quarter_seconds_remaining": 100,
                    "yardline_100": 10,
                    "yards_gained": 10,
                    "touchdown": 1,
                    "complete_pass": 1,
                    "interception": 0,
                    "sack": 0,
                    "first_down_pass": 0,
                    "first_down_rush": 0,
                    "passer_player_id": "GSIS_001",
                    "passer_player_name": "P.Mahomes",
                    "primary_player": "P.Mahomes",
                    "primary_role": "pass",
                },
            ],
        }

        stub = _StubNFLDataService(pbp_payload)
        service = NFLGameSimulationService(db, nfl_data_service=stub)
        result = service.build_game_simulation("2024_01_KC_BAL", 2024, 1)

        assert result["events"][0]["badges"]["touchdown"] is True

    def test_empty_game(self, db):
        """Empty plays should produce zero events."""
        stub = _StubNFLDataService({"game_summary": {}, "plays": []})
        service = NFLGameSimulationService(db, nfl_data_service=stub)
        result = service.build_game_simulation("2024_01_KC_BAL", 2024, 1)

        assert result["total_events"] == 0
        assert result["events"] == []

    def test_headshot_resolution(self, db):
        """Players in the DB should have their headshots resolved."""
        player = DBPlayer(
            player_id="nfl_GSIS_001",
            name="P.Mahomes",
            position="QB",
            nfl_team="KC",
            headshot_url="https://example.com/mahomes.png",
        )
        db.add(player)
        db.commit()

        pbp_payload = {
            "game_summary": {"game_id": "2024_01_KC_BAL"},
            "plays": [
                {
                    "play_id": 100,
                    "play_type": "pass",
                    "desc": "Pass play",
                    "yardline_100": 50,
                    "yards_gained": 10,
                    "complete_pass": 1,
                    "touchdown": 0,
                    "interception": 0,
                    "sack": 0,
                    "first_down_pass": 0,
                    "first_down_rush": 0,
                    "passer_player_id": "GSIS_001",
                    "passer_player_name": "P.Mahomes",
                    "primary_player": "P.Mahomes",
                    "primary_role": "pass",
                },
            ],
        }

        stub = _StubNFLDataService(pbp_payload)
        service = NFLGameSimulationService(db, nfl_data_service=stub)
        result = service.build_game_simulation("2024_01_KC_BAL", 2024, 1)

        assert result["events"][0]["passer_headshot_url"] == "https://example.com/mahomes.png"

    def test_multiple_plays_indexing(self, db):
        """Multiple plays should be properly indexed."""
        plays = []
        for i in range(5):
            plays.append({
                "play_id": i + 1,
                "play_type": "pass",
                "desc": f"Play {i + 1}",
                "yardline_100": 50 - i * 5,
                "yards_gained": 5,
                "complete_pass": 1,
                "touchdown": 0,
                "interception": 0,
                "sack": 0,
                "first_down_pass": 0,
                "first_down_rush": 0,
                "passer_player_id": "GSIS_001",
                "passer_player_name": "QB",
                "primary_player": "QB",
                "primary_role": "pass",
            })

        stub = _StubNFLDataService({"game_summary": {}, "plays": plays})
        service = NFLGameSimulationService(db, nfl_data_service=stub)
        result = service.build_game_simulation("2024_01_KC_BAL", 2024, 1)

        assert result["total_events"] == 5
        for i, event in enumerate(result["events"]):
            assert event["index"] == i


class TestDeterministicJitter:
    def test_returns_float(self):
        assert isinstance(_deterministic_jitter("seed"), float)

    def test_bounded(self):
        for seed in range(100):
            val = _deterministic_jitter(seed, 10.0)
            assert -10.0 <= val <= 10.0

    def test_deterministic(self):
        assert _deterministic_jitter("abc") == _deterministic_jitter("abc")
