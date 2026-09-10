"""The payload contract for the full-NFL-game animation.

Consumed by ``templates/games/detail.html`` and ``simulation-field.js``,
neither of which validates it.  The key sets below were captured from the
nflverse implementation before the play-by-play source was swapped.
"""

import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBNFLGame, DBPlayer
from pigskin_mastermind.services.play_by_play.espn_source import plays_from_summary
from pigskin_mastermind.services.nfl_game_simulation_service import (
    NFLGameSimulationService,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "espn_pbp")
JAGS = "20250914_JacksonvilleJaguars_at_CincinnatiBengals.json"
GAME_ID = "2025_02_JAX_CIN"

# --- captured from the nflverse implementation, 2026-09-10 -------------------
TOP_KEYS = {"events", "game_id", "game_summary", "total_events", "week", "year"}
GAME_SUMMARY_KEYS = {"away_score", "away_team", "game_id", "home_score", "home_team"}
EVENT_KEYS = {
    "away_score",
    "badges",
    "defteam",
    "description",
    "down_distance",
    "end_x",
    "epa",
    "home_score",
    "index",
    "is_complete",
    "is_sack",
    "lane_pct",
    "passer_gsis_id",
    "passer_headshot_url",
    "passer_name",
    "play_id",
    "play_type",
    "posteam",
    "quarter",
    "receiver_gsis_id",
    "receiver_headshot_url",
    "receiver_name",
    "role",
    "route_path",
    "rusher_gsis_id",
    "rusher_headshot_url",
    "rusher_name",
    "start_x",
    "time_label",
    "yards_gained",
}
ROUTE_KEYS = {"is_complete", "is_sack", "is_touchdown", "segments"}
BADGE_KEYS = {"big_play", "first_down", "touchdown", "turnover"}
# -----------------------------------------------------------------------------

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    session = Session()
    yield session
    session.close()


def _summary():
    with open(os.path.join(FIXTURES, JAGS), encoding="utf-8") as fh:
        return json.load(fh)


class _FixtureSource:
    name = "fixture"

    def plays(self, year, week, team):
        return plays_from_summary(_summary())


@pytest.fixture
def game(db):
    db.add(
        DBNFLGame(game_id=GAME_ID, year=2025, week=2, home_team="CIN", away_team="JAX")
    )
    # Trevor Lawrence, by the ESPN athlete id the fixture's boxscore carries.
    db.add(
        DBPlayer(
            player_id="espn_4360310",
            espn_id="4360310",
            name="Trevor Lawrence",
            position="QB",
            nfl_team="JAX",
            headshot_url="https://example.test/lawrence.png",
        )
    )
    db.commit()


@pytest.fixture
def payload(db, game):
    service = NFLGameSimulationService(db, plays_source=_FixtureSource())
    return service.build_game_simulation(GAME_ID, 2025, 2)


class TestPayloadShape:
    def test_top_level_keys_are_unchanged(self, payload):
        assert set(payload.keys()) == TOP_KEYS

    def test_game_summary_keys_are_unchanged(self, payload):
        assert set(payload["game_summary"].keys()) == GAME_SUMMARY_KEYS

    def test_event_keys_are_unchanged(self, payload):
        assert payload["events"]
        for event in payload["events"]:
            assert set(event.keys()) == EVENT_KEYS

    def test_nested_structures_are_unchanged(self, payload):
        for event in payload["events"]:
            assert set(event["route_path"].keys()) == ROUTE_KEYS
            assert set(event["badges"].keys()) == BADGE_KEYS


class TestWholeGameContent:
    def test_every_play_in_the_game_is_present(self, payload):
        """Unlike the player view, nothing is filtered out."""
        assert payload["total_events"] == len(plays_from_summary(_summary()))

    def test_possession_and_defence_are_the_two_teams(self, payload):
        posteams = {e["posteam"] for e in payload["events"] if e["posteam"]}
        defteams = {e["defteam"] for e in payload["events"] if e["defteam"]}

        assert posteams == {"JAX", "CIN"}
        assert defteams == {"JAX", "CIN"}

    def test_possession_and_defence_are_never_the_same_team(self, payload):
        for event in payload["events"]:
            if event["posteam"] and event["defteam"]:
                assert event["posteam"] != event["defteam"]

    def test_special_teams_plays_survive(self, payload):
        """A full game is not only scrimmage downs."""
        roles = {e["role"] for e in payload["events"]}

        assert "kickoff" in roles
        assert "punt" in roles
        assert "field_goal" in roles

    def test_the_game_summary_reports_the_final_score(self, payload):
        every = plays_from_summary(_summary())
        assert payload["game_summary"]["home_score"] == max(
            p.home_score for p in every if p.home_score is not None
        )

    def test_epa_is_absent_rather_than_zero(self, payload):
        assert all(e["epa"] is None for e in payload["events"])


class TestHeadshots:
    def test_a_known_player_gets_their_headshot(self, payload):
        """Resolved through DBPlayer.espn_id -- ESPN hands us athlete ids
        directly, so the old GSIS->ESPN mapping via nfl_data_py is gone."""
        withpasser = [
            e for e in payload["events"] if e["passer_name"] == "Trevor Lawrence"
        ]

        assert withpasser
        assert any(e["passer_headshot_url"] for e in withpasser)

    def test_an_unknown_player_gets_an_empty_string_not_none(self, payload):
        """The template interpolates this straight into an img src."""
        for event in payload["events"]:
            assert isinstance(event["passer_headshot_url"], str)
            assert isinstance(event["rusher_headshot_url"], str)
            assert isinstance(event["receiver_headshot_url"], str)
