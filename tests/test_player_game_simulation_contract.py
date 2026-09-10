"""The payload contract for the player game animation.

``build_simulation``'s output is consumed by ``static/js/simulation-field.js``
and by three templates -- ``players/simulation.html``,
``players/details.html``, ``games/detail.html``. None of them validate it, so
a dropped or renamed key fails silently in a browser rather than loudly here.

The key sets below were captured from the **nflverse** implementation before
the play-by-play source was swapped, and are asserted verbatim. They are a
regression net for that swap: the data changes, the shape must not.
"""

import json
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer
from pigskin_mastermind.services.play_by_play.espn_source import plays_from_summary
from pigskin_mastermind.services.player_game_simulation_service import (
    PlayerGameSimulationService,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "espn_pbp")
JAGS = "20250914_JacksonvilleJaguars_at_CincinnatiBengals.json"

# --- captured from the nflverse implementation, 2026-09-10 -------------------
TOP_KEYS = {
    "events",
    "game_summary",
    "player_headshot_url",
    "player_id",
    "player_name",
    "player_position",
    "player_stats",
    "total_events",
    "week",
    "year",
}
EVENT_KEYS = {
    "away_score",
    "badges",
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
    "passer_name",
    "play_id",
    "play_type",
    "player_color",
    "player_headshot_url",
    "player_name",
    "quarter",
    "receiver_gsis_id",
    "receiver_name",
    "role",
    "route_path",
    "start_x",
    "stats_snapshot",
    "time_label",
    "yards_gained",
}
BADGE_KEYS = {"touchdown", "first_down", "turnover", "big_play"}
ROUTE_KEYS = {"segments", "is_complete", "is_touchdown", "is_sack"}
STATS_KEYS = {
    "total_plays",
    "pass_attempts",
    "pass_completions",
    "pass_yards",
    "pass_tds",
    "pass_interceptions",
    "rush_attempts",
    "rush_yards",
    "rush_tds",
    "targets",
    "receptions",
    "rec_yards",
    "rec_tds",
    "first_downs",
    "total_tds",
    "total_epa",
}
GAME_SUMMARY_KEYS = {
    "game_id",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "player_team",
}
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
    """A play source pinned to one captured game."""

    name = "fixture"

    def __init__(self, summary):
        self.summary = summary

    def plays(self, year, week, team):
        return plays_from_summary(self.summary)


@pytest.fixture
def lawrence(db):
    """Trevor Lawrence, by the ESPN athlete id the fixture's boxscore uses."""
    player = DBPlayer(
        player_id="espn_4360310",
        espn_id="4360310",
        name="Trevor Lawrence",
        position="QB",
        nfl_team="JAX",
    )
    db.add(player)
    db.commit()
    return player


@pytest.fixture
def payload(db, lawrence):
    service = PlayerGameSimulationService(db, plays_source=_FixtureSource(_summary()))
    return service.build_simulation(lawrence.id, 2025, 2)


class TestPayloadShape:
    def test_top_level_keys_are_unchanged(self, payload):
        assert set(payload.keys()) == TOP_KEYS

    def test_game_summary_keys_are_unchanged(self, payload):
        assert set(payload["game_summary"].keys()) == GAME_SUMMARY_KEYS

    def test_player_stats_keys_are_unchanged(self, payload):
        assert set(payload["player_stats"].keys()) == STATS_KEYS

    def test_the_player_has_events(self, payload):
        assert payload["events"], "no plays attributed to the player"
        assert payload["total_events"] == len(payload["events"])

    def test_event_keys_are_unchanged(self, payload):
        for event in payload["events"]:
            assert set(event.keys()) == EVENT_KEYS

    def test_nested_event_structures_are_unchanged(self, payload):
        for event in payload["events"]:
            assert set(event["badges"].keys()) == BADGE_KEYS
            assert set(event["route_path"].keys()) == ROUTE_KEYS
            assert set(event["stats_snapshot"].keys()) == STATS_KEYS


class TestFieldInvariants:
    def test_field_positions_stay_on_the_field(self, payload):
        """start_x/end_x drive the marker's position, so out-of-range values
        put a player off the pitch rather than raising."""
        for event in payload["events"]:
            assert 0.0 <= event["start_x"] <= 100.0
            assert 0.0 <= event["end_x"] <= 100.0

    def test_only_the_requested_players_plays_are_returned(self, payload, lawrence):
        """The animation is one player's game.  A team-wide feed leaking in
        would silently animate other people's plays under his name."""
        for event in payload["events"]:
            assert event["player_name"] == lawrence.name

    def test_roles_are_the_values_the_front_end_branches_on(self, payload):
        """simulation-field.js switches on role 17 times; an unexpected value
        renders nothing and reports no error."""
        for event in payload["events"]:
            assert event["role"] in ("pass", "rush", "receive", "unknown")

    def test_epa_is_absent_rather_than_zero(self, payload):
        """ESPN publishes no EPA.  Zero is a real EPA value, so the templates
        must be able to tell "no signal" from "neutral play"."""
        assert all(e["epa"] is None for e in payload["events"])


class TestSourceIsolation:
    def test_an_injected_source_is_never_reached_past(self, db, lawrence):
        """A fixture source that omits game_context must not cause a live
        ESPN lookup.  That failure is silent -- the tests still pass, just
        slowly and against the network."""

        class _NoContextSource:
            name = "fixture"

            def plays(self, year, week, team):
                return plays_from_summary(_summary())

        payload = PlayerGameSimulationService(
            db, plays_source=_NoContextSource()
        ).build_simulation(lawrence.id, 2025, 2)

        assert set(payload["game_summary"].keys()) == GAME_SUMMARY_KEYS
        assert payload["game_summary"]["home_team"] is None
        assert payload["events"], "plays should still come from the source"

    def test_a_player_with_no_team_yields_an_empty_payload(self, db):
        """A free agent has no game that week.  Empty, not an exception.

        ``players.nfl_team`` is NOT NULL, so an unsigned player carries an
        empty string rather than NULL.
        """
        player = DBPlayer(
            player_id="espn_1", espn_id="1", name="Nobody", position="WR", nfl_team=""
        )
        db.add(player)
        db.commit()

        payload = PlayerGameSimulationService(db).build_simulation(player.id, 2025, 2)

        assert payload["events"] == []
        assert payload["total_events"] == 0


class TestTeamResolution:
    """Which team the player was on *that week*, not which he is on now.

    ``DBPlayer.nfl_team`` is the player's current club. For a past season it
    is routinely the wrong franchise -- Travis Etienne's row says NO while his
    2025 games were played for JAX -- and asking ESPN for the wrong team's
    game returns an empty animation for a game that certainly happened.

    A team plays once a week, so the game log's opponent identifies exactly
    one scheduled game, and the player was the other side of it. This is the
    same rule the advanced-metrics backfill uses.
    """

    def test_the_week_played_for_beats_the_current_roster(self, db):
        from pigskin_mastermind.models.database import DBNFLGame, DBPlayerGameLog

        player = DBPlayer(
            player_id="espn_9",
            espn_id="9",
            name="Traded Back",
            position="RB",
            nfl_team="NO",
        )
        db.add(player)
        db.commit()
        db.add(DBPlayerGameLog(player_id=player.id, year=2025, week=2, opponent="CIN"))
        db.add(
            DBNFLGame(game_id="g1", year=2025, week=2, home_team="CIN", away_team="JAX")
        )
        db.commit()

        service = PlayerGameSimulationService(db)

        assert service._team_for_week(player, 2025, 2) == "JAX"

    def test_falls_back_to_the_current_team_without_a_game_log(self, db):
        """No log for that week means nothing better is known."""
        player = DBPlayer(
            player_id="espn_10",
            espn_id="10",
            name="Settled",
            position="WR",
            nfl_team="KC",
        )
        db.add(player)
        db.commit()

        assert PlayerGameSimulationService(db)._team_for_week(player, 2025, 2) == "KC"


class TestGameSummaryScores:
    def test_scores_are_the_games_final_not_the_players_last_play(self, db):
        """game_summary describes the game, not the player's slice of it.

        Deriving scores from the filtered plays gives each player a different
        "final" score for the same game -- whatever it happened to be when he
        last touched the ball.
        """
        # Joe Burrow left this game injured -- his last play reads 14-7 while
        # it finished 27-31, so he discriminates where a QB who threw late
        # would not.
        player = DBPlayer(
            player_id="espn_3915511",
            espn_id="3915511",
            name="Joe Burrow",
            position="QB",
            nfl_team="CIN",
        )
        db.add(player)
        db.commit()

        service = PlayerGameSimulationService(
            db, plays_source=_FixtureSource(_summary())
        )
        payload = service.build_simulation(player.id, 2025, 2)

        every_play = plays_from_summary(_summary())
        final_home = max(p.home_score for p in every_play if p.home_score is not None)
        final_away = max(p.away_score for p in every_play if p.away_score is not None)

        assert payload["game_summary"]["home_score"] == final_home
        assert payload["game_summary"]["away_score"] == final_away
