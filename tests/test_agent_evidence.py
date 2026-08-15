"""Assembly of the agent evidence pack."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBNFLGame,
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerProjection,
    DBPlayerSeasonStats,
    DBSportsbookOdds,
)
from pigskin_mastermind.services.agent_evidence import build_evidence


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def player(db):
    p = DBPlayer(
        player_id="espn_1",
        name="Test Back",
        position="RB",
        nfl_team="ATL",
        espn_id="1",
        age=25,
        years_exp=3,
        bye_week=9,
    )
    db.add(p)
    db.commit()
    return p


def test_player_block_carries_identity_and_bio(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["player"]["db_id"] == player.id
    assert ev["player"]["name"] == "Test Back"
    assert ev["player"]["position"] == "RB"
    assert ev["player"]["nfl_team"] == "ATL"
    assert ev["player"]["espn_id"] == "1"
    assert ev["player"]["age"] == 25
    assert ev["player"]["bye_week"] == 9


def test_season_scope_when_no_week_given(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["context"]["scope"] == "season"
    assert ev["context"]["week"] is None
    assert ev["context"]["year"] == 2026


def test_weekly_scope_when_week_given(db, player):
    ev = build_evidence(db, player.id, 2026, week=5)
    assert ev["context"]["scope"] == "weekly"
    assert ev["context"]["week"] == 5


def test_unknown_player_raises(db):
    with pytest.raises(ValueError, match="not found"):
        build_evidence(db, 9999, 2026)


@pytest.fixture
def stats(db, player):
    for year, total in [(2023, 180.0), (2024, 240.0), (2025, 300.0)]:
        db.add(
            DBPlayerSeasonStats(
                player_id=player.id,
                year=year,
                games_played=16,
                rush_att=200,
                rush_yd=900,
                rush_td=8,
                targets=50,
                rec=40,
                fantasy_points_total=total,
                fantasy_points_avg=total / 16,
                snap_pct=72.5,
                adp=24.0,
                adp_source="ffc",
            )
        )
    for wk in range(1, 6):
        db.add(
            DBPlayerGameLog(
                player_id=player.id,
                year=2025,
                week=wk,
                opponent="NO",
                rush_att=15,
                rush_yd=70,
                rush_td=1,
                targets=3,
                rec=2,
                fantasy_points=14.0 + wk,
            )
        )
    db.commit()


def test_season_stats_newest_first_and_capped_at_three(db, player, stats):
    ev = build_evidence(db, player.id, 2026)
    years = [row["year"] for row in ev["season_stats"]]
    assert years == [2025, 2024, 2023]
    assert ev["season_stats"][0]["fantasy_points_total"] == 300.0
    assert ev["season_stats"][0]["snap_pct"] == 72.5


def test_game_logs_ordered_ascending(db, player, stats):
    ev = build_evidence(db, player.id, 2026)
    assert [g["week"] for g in ev["game_logs"]] == [1, 2, 3, 4, 5]
    assert ev["game_logs"][0]["opponent"] == "NO"
    assert ev["game_logs"][4]["fantasy_points"] == 19.0


def test_blocks_are_empty_lists_when_player_has_no_data(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["season_stats"] == []
    assert ev["game_logs"] == []


def test_yearly_criteria_present_for_season_scope(db, player, stats):
    ev = build_evidence(db, player.id, 2026)
    assert ev["criteria"]["scope"] == "yearly"
    fields = ev["criteria"]["fields"]
    assert "expected_games" in fields
    assert "player_skill_level" in fields
    assert "age_deviation_from_optimum" in fields


def test_weekly_criteria_present_for_weekly_scope(db, player, stats):
    ev = build_evidence(db, player.id, 2026, week=5)
    assert ev["criteria"]["scope"] == "weekly"
    fields = ev["criteria"]["fields"]
    assert "opposing_defense_vs_position_rank" in fields
    assert "offensive_momentum_score" in fields


def test_criteria_values_are_json_serializable(db, player, stats):
    import json

    ev = build_evidence(db, player.id, 2026, week=5)
    json.dumps(ev["criteria"])  # raises TypeError if not


def test_existing_projections_keyed_by_source(db, player):
    db.add(
        DBPlayerProjection(
            player_id=player.id,
            year=2026,
            week=None,
            source="model",
            projected_points=248.0,
            expected_games=16.0,
        )
    )
    db.add(
        DBPlayerProjection(
            player_id=player.id,
            year=2026,
            week=None,
            source="espn",
            projected_points=231.5,
        )
    )
    db.commit()

    ev = build_evidence(db, player.id, 2026)
    assert ev["existing_projections"]["model"]["projected_points"] == 248.0
    assert ev["existing_projections"]["model"]["expected_games"] == 16.0
    assert ev["existing_projections"]["espn"]["projected_points"] == 231.5
    assert ev["existing_projections"]["model"]["computed_at"] is not None


def test_season_projections_excluded_from_weekly_scope(db, player):
    db.add(
        DBPlayerProjection(
            player_id=player.id,
            year=2026,
            week=None,
            source="model",
            projected_points=248.0,
        )
    )
    db.commit()

    ev = build_evidence(db, player.id, 2026, week=5)
    assert ev["existing_projections"] == {}


def test_data_freshness_reports_stat_timestamps(db, player, stats):
    # 2025, not 2026: the `stats` fixture's most recent season row (with ADP
    # set) is 2025, and adp_updated_at is intentionally scoped to the exact
    # requested year — see test_adp_freshness_is_null_for_a_season_with_no_adp_row.
    ev = build_evidence(db, player.id, 2025)
    fresh = ev["data_freshness"]
    assert fresh["game_logs_updated_at"] is not None
    assert fresh["season_stats_updated_at"] is not None
    assert fresh["adp_updated_at"] is not None
    assert fresh["generated_at"] is not None


def test_data_freshness_is_null_when_nothing_stored(db, player):
    fresh = build_evidence(db, player.id, 2026)["data_freshness"]
    assert fresh["game_logs_updated_at"] is None
    assert fresh["season_stats_updated_at"] is None


def test_adp_freshness_is_null_for_a_season_with_no_adp_row(db, player, stats):
    """ADP is a per-season value, not a running "most recent" figure.

    The `stats` fixture only carries ADP through 2025. Asking about 2026 must
    report None even though the player has ADP rows for earlier seasons --
    a stale, full-season-old ADP timestamp reported as "fresh" is exactly the
    misleading signal this block exists to prevent.
    """
    ev = build_evidence(db, player.id, 2026)
    assert ev["data_freshness"]["adp_updated_at"] is None
    # Confirm this isn't just "no ADP anywhere" -- the player does have ADP,
    # just not for the requested year.
    assert any(row["adp"] is not None for row in ev["season_stats"])


@pytest.fixture
def schedule(db):
    db.add(
        DBNFLGame(
            year=2026,
            week=1,
            home_team="ATL",
            away_team="NO",
            home_score=24,
            away_score=17,
            roof="dome",
        )
    )
    db.add(
        DBNFLGame(
            year=2026,
            week=2,
            home_team="TB",
            away_team="ATL",
            roof="outdoors",
        )
    )
    db.commit()


def test_schedule_names_opponent_and_home_away(db, player, schedule):
    ev = build_evidence(db, player.id, 2026)
    games = ev["schedule"]
    assert games[0] == {
        "week": 1,
        "opponent": "NO",
        "home": True,
        "played": True,
        "roof": "dome",
    }
    assert games[1]["opponent"] == "TB"
    assert games[1]["home"] is False
    assert games[1]["played"] is False


def test_weekly_scope_shows_only_that_week_onward(db, player, schedule):
    ev = build_evidence(db, player.id, 2026, week=2)
    assert [g["week"] for g in ev["schedule"]] == [2]


def test_sportsbook_is_none_when_no_props_stored(db, player):
    ev = build_evidence(db, player.id, 2026)
    assert ev["sportsbook"] is None


def test_as_of_excludes_the_cutoff_week_and_later(db, player, stats):
    ev = build_evidence(db, player.id, 2025, week=3, as_of_week=3)
    weeks = [g["week"] for g in ev["game_logs"] if g["year"] == 2025]
    assert weeks == [1, 2]


def test_as_of_keeps_prior_seasons_intact(db, player, stats):
    for wk in (1, 2, 3):
        db.add(
            DBPlayerGameLog(
                player_id=player.id,
                year=2024,
                week=wk,
                fantasy_points=10.0,
            )
        )
    db.commit()

    ev = build_evidence(db, player.id, 2025, week=2, as_of_week=2)
    prior = [g["week"] for g in ev["game_logs"] if g["year"] == 2024]
    assert prior == [1, 2, 3]


def test_as_of_hides_results_of_games_at_or_after_cutoff(db, player, schedule):
    ev = build_evidence(db, player.id, 2026, week=1, as_of_week=1)
    assert ev["schedule"][0]["week"] == 1
    assert ev["schedule"][0]["played"] is False


def test_as_of_omits_criteria_with_a_stated_reason(db, player, stats):
    ev = build_evidence(db, player.id, 2025, week=3, as_of_week=3)
    assert ev["criteria"] is None
    assert "cutoff" in ev["criteria_omitted_reason"]


def test_criteria_reason_absent_when_not_backtesting(db, player, stats):
    ev = build_evidence(db, player.id, 2026, week=3)
    assert ev["criteria"] is not None
    assert ev["criteria_omitted_reason"] is None


def test_as_of_drops_the_target_season_aggregate(db, player, stats):
    """A season total includes the very weeks the cutoff is hiding.

    fantasy_points_avg over the full season is a near-optimal estimator for
    the week being predicted, so emitting it defeats the backtest that the
    game-log truncation exists to make possible.
    """
    ev = build_evidence(db, player.id, 2025, week=3, as_of_week=3)
    assert [row["year"] for row in ev["season_stats"]] == [2024, 2023]
    assert "cutoff" in ev["season_stats_omitted_reason"]


def test_season_stats_reason_absent_when_not_backtesting(db, player, stats):
    ev = build_evidence(db, player.id, 2025, week=3)
    assert [row["year"] for row in ev["season_stats"]] == [2025, 2024, 2023]
    assert ev["season_stats_omitted_reason"] is None


def test_schedule_cutoff_keeps_earlier_results_visible(db, player, schedule):
    """Pins the boundary comparison, not just the presence of a cutoff.

    Without this, an implementation that blanks `played` for every game
    whenever as_of_week is set passes the whole suite.
    """
    ev = build_evidence(db, player.id, 2026, week=1, as_of_week=2)
    assert ev["schedule"][0]["week"] == 1
    assert ev["schedule"][0]["played"] is True
    assert ev["schedule"][1]["week"] == 2
    assert ev["schedule"][1]["played"] is False


@pytest.fixture
def props(db, player):
    """Sportsbook lines the projection service will actually resolve.

    ``_fetch_props`` filters on ``market IN MARKET_TO_SCORING`` and
    ``description ILIKE '%<player_name>%'`` -- the name path, not the FK --
    so ``description`` must contain "Test Back" verbatim. ``_group_lines_by_
    market`` then drops any row with a NULL ``point`` and skips
    ``outcome_name == "under"`` (case-insensitive) so the Over/Under pair
    for one bookmaker doesn't double count. Two markets, two bookmakers each,
    gives the median() call in ``project_player`` more than one point to
    resolve and keeps ``categories`` non-trivial.
    """
    event_id = "evt_test_back_wk1"
    commence = datetime(2026, 9, 10, tzinfo=timezone.utc)
    rows = [
        # player_rush_yds: two bookmakers, Over + Under each.
        ("draftkings", "player_rush_yds", "Over", 64.5),
        ("draftkings", "player_rush_yds", "Under", 64.5),
        ("fanduel", "player_rush_yds", "Over", 61.5),
        ("fanduel", "player_rush_yds", "Under", 61.5),
        # player_receptions: two bookmakers, Over only.
        ("draftkings", "player_receptions", "Over", 3.5),
        ("fanduel", "player_receptions", "Over", 4.5),
    ]
    for bookmaker, market, outcome, point in rows:
        db.add(
            DBSportsbookOdds(
                event_id=event_id,
                sport_key="americanfootball_nfl",
                sport_title="NFL",
                commence_time=commence,
                home_team="ATL",
                away_team="NO",
                bookmaker=bookmaker,
                market=market,
                outcome_name=outcome,
                price=-110,
                point=point,
                description="Test Back",
            )
        )
    db.commit()


def test_sportsbook_present_without_a_cutoff(db, player, props):
    ev = build_evidence(db, player.id, 2026, week=1)
    assert ev["sportsbook"] is not None
    assert ev["sportsbook"]["categories"]
    assert ev["sportsbook_omitted_reason"] is None


def test_as_of_suppresses_sportsbook(db, player, props):
    """Books price upcoming games; there is no historical line to serve.

    The probe that motivated this task pulled September 2026 props into a
    2025 week-3 backtest.
    """
    ev = build_evidence(db, player.id, 2026, week=1, as_of_week=1)
    assert ev["sportsbook"] is None
    assert "cutoff" in ev["sportsbook_omitted_reason"]


def test_hash_is_stable_across_identical_calls(db, player, stats):
    from pigskin_mastermind.services.agent_evidence import evidence_hash

    first = build_evidence(db, player.id, 2026)
    second = build_evidence(db, player.id, 2026)
    assert first["evidence_hash"] == second["evidence_hash"]
    assert len(first["evidence_hash"]) == 64


def test_hash_changes_when_underlying_data_changes(db, player, stats):
    before = build_evidence(db, player.id, 2026)["evidence_hash"]
    db.add(
        DBPlayerGameLog(
            player_id=player.id,
            year=2025,
            week=6,
            fantasy_points=31.0,
        )
    )
    db.commit()
    after = build_evidence(db, player.id, 2026)["evidence_hash"]
    assert before != after


def test_hash_ignores_the_generated_at_timestamp(db, player, stats):
    from pigskin_mastermind.services.agent_evidence import evidence_hash

    ev = build_evidence(db, player.id, 2026)
    mutated = dict(ev)
    mutated["data_freshness"] = dict(ev["data_freshness"])
    mutated["data_freshness"]["generated_at"] = "1999-01-01T00:00:00+00:00"
    assert evidence_hash(mutated) == evidence_hash(ev)
