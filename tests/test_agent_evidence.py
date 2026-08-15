"""Assembly of the agent evidence pack."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
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
