"""Tests for ProjectionTunerService — parameterized formula and backtesting."""

import pytest
import math
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBTeam, DBPlayerSeasonStats,
    DBPlayerGameLog, DBNFLTeamStats,
)
from pigskin_mastermind.services.projection_tuner import (
    ProjectionTunerService,
    get_default_coefficients,
    get_coefficient_metadata,
    get_criteria_docs,
    COEFFICIENT_DEFS,
    CRITERIA_DOCS,
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


@pytest.fixture
def sample_data(db):
    """Create a QB with season stats and game logs for testing."""
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.flush()

    player = DBPlayer(
        player_id="p1", name="Test QB", position="QB",
        nfl_team="KC", team_id=team.id,
        stats={"age": 28, "injuryStatus": ""},
    )
    db.add(player)
    db.flush()

    # Season stats
    season = DBPlayerSeasonStats(
        player_id=player.id, year=2024,
        games_played=16,
        pass_att=500, pass_cmp=350, pass_yd=4000, pass_td=30, pass_int=10,
        rush_att=40, rush_yd=200, rush_td=3,
        targets=0, rec=0, rec_yd=0, rec_td=0,
        fantasy_points_total=320.0,
        fantasy_points_avg=20.0,
        fantasy_points_per_touch=0.59,
        snap_pct=0.95,
    )
    db.add(season)

    # Game logs (weeks 1-4)
    for w in range(1, 5):
        log = DBPlayerGameLog(
            player_id=player.id, year=2024, week=w,
            opponent="LV",
            pass_att=32, pass_cmp=22, pass_yd=260, pass_td=2, pass_int=0,
            rush_att=3, rush_yd=15, rush_td=0,
            fantasy_points=18.0 + w,  # 19, 20, 21, 22
        )
        db.add(log)

    # Team stats
    team_stats = DBNFLTeamStats(
        nfl_team="KC", year=2024, week=None,
        total_yards=5500, points_scored=420,
        def_rank_vs_qb=10, def_rank_vs_rb=15,
        def_rank_vs_wr=20, def_rank_vs_te=8,
    )
    db.add(team_stats)
    opp_stats = DBNFLTeamStats(
        nfl_team="LV", year=2024, week=None,
        total_yards=4800, points_scored=320,
        def_rank_vs_qb=25, def_rank_vs_rb=18,
        def_rank_vs_wr=12, def_rank_vs_te=22,
    )
    db.add(opp_stats)

    db.commit()
    return player


# ── Metadata tests ────────────────────────────────────────────────────────

def test_default_coefficients_has_all_keys():
    """Default coefficients dict should have an entry for every COEFFICIENT_DEFS entry."""
    defaults = get_default_coefficients()
    for cdef in COEFFICIENT_DEFS:
        assert cdef["key"] in defaults
        assert defaults[cdef["key"]] == cdef["default"]


def test_coefficient_metadata_structure():
    """Each coefficient metadata entry should have required fields."""
    meta = get_coefficient_metadata()
    required_keys = {"key", "name", "default", "min", "max", "step", "group", "description", "formula"}
    for entry in meta:
        assert required_keys.issubset(entry.keys()), f"Missing keys in {entry['key']}"
        assert entry["group"] in ("base", "weekly", "yearly")


def test_criteria_docs_structure():
    """Each criteria doc entry should have required fields."""
    docs = get_criteria_docs()
    required_keys = {"field", "name", "group", "range", "description", "data_source", "role"}
    for entry in docs:
        assert required_keys.issubset(entry.keys()), f"Missing keys in {entry['field']}"


# ── Projection breakdown tests ────────────────────────────────────────────

def test_weekly_breakdown_has_steps(db, sample_data):
    """Weekly projection should return a breakdown with steps."""
    service = ProjectionTunerService(db)
    result = service.project_weekly(sample_data.id, week=1, year=2024)

    assert "steps" in result
    assert "total" in result
    assert len(result["steps"]) > 0

    # Should have base steps + weekly steps
    # Base: 8 steps (hist avg, skill, offense, defense, touch, trend, efficiency, injury)
    # Weekly: 3 steps (def rank, momentum, weather)
    assert len(result["steps"]) == 11


def test_yearly_breakdown_has_steps(db, sample_data):
    """Yearly projection should return breakdown with base + yearly steps."""
    service = ProjectionTunerService(db)
    result = service.project_yearly(sample_data.id, year=2024)

    assert "steps" in result
    assert "total" in result
    # Base: 8 + Yearly: 2 (age, coaching) = 10
    assert len(result["steps"]) == 10


def test_breakdown_sums_match_total(db, sample_data):
    """Sum of step values should match the total (before floor clamping)."""
    service = ProjectionTunerService(db)
    result = service.project_weekly(sample_data.id, week=1, year=2024)

    step_sum = sum(s["value"] for s in result["steps"])
    # The total is max(0, step_sum), so for positive totals they should match
    if step_sum >= 0:
        assert abs(result["total"] - step_sum) < 0.01
    else:
        assert result["total"] == 0


def test_step_has_required_fields(db, sample_data):
    """Each breakdown step should contain the expected fields."""
    service = ProjectionTunerService(db)
    result = service.project_weekly(sample_data.id, week=1, year=2024)

    required = {"label", "criteria_field", "criteria_value", "coefficient_key",
                "coefficient_value", "formula", "value"}
    for step in result["steps"]:
        assert required.issubset(step.keys()), f"Missing keys in step: {step['label']}"


# ── Custom coefficients change results ────────────────────────────────────

def test_custom_coefficients_change_projection(db, sample_data):
    """Using different coefficients should produce a different projection."""
    default_service = ProjectionTunerService(db)
    custom_service = ProjectionTunerService(db, coefficients={
        "skill_multiplier": 0.3,
        "offense_multiplier": 0.2,
    })

    default_result = default_service.project_weekly(sample_data.id, week=1, year=2024)
    custom_result = custom_service.project_weekly(sample_data.id, week=1, year=2024)

    assert default_result["total"] != custom_result["total"]


def test_zero_coefficients_only_baseline(db, sample_data):
    """Setting all multipliers to 0 should produce just the baseline."""
    zero_coeffs = {c["key"]: 0.0 for c in COEFFICIENT_DEFS}
    service = ProjectionTunerService(db, coefficients=zero_coeffs)
    result = service.project_weekly(sample_data.id, week=1, year=2024)

    # Only the baseline (historical_average_points) step should be non-zero
    non_zero_steps = [s for s in result["steps"] if s["value"] != 0.0]
    # The baseline step has no coefficient_key (it's always added)
    baseline_steps = [s for s in non_zero_steps if s["coefficient_key"] is None]
    adjustment_steps = [s for s in non_zero_steps if s["coefficient_key"] is not None]

    assert len(adjustment_steps) == 0, "All adjustment steps should be zero"


# ── Actual points retrieval ───────────────────────────────────────────────

def test_actual_points_returned(db, sample_data):
    """Projection should include actual points from game log."""
    service = ProjectionTunerService(db)
    result = service.project_weekly(sample_data.id, week=1, year=2024)

    assert result["actual_points"] is not None
    assert result["actual_points"] == 19.0  # 18.0 + 1


def test_error_calculated(db, sample_data):
    """Error should be projected - actual."""
    service = ProjectionTunerService(db)
    result = service.project_weekly(sample_data.id, week=1, year=2024)

    expected_error = round(result["total"] - result["actual_points"], 2)
    assert result["error"] == expected_error


# ── Backtest tests ────────────────────────────────────────────────────────

def test_backtest_weekly_returns_stats(db, sample_data):
    """Weekly backtest should return summary statistics."""
    service = ProjectionTunerService(db)
    result = service.backtest_weekly("QB", year=2024)

    assert "mae" in result
    assert "rmse" in result
    assert "player_count" in result
    assert "sample_count" in result
    assert "results" in result
    assert "top_movers" in result

    assert result["player_count"] >= 1
    assert result["sample_count"] >= 1
    assert result["mae"] is not None
    assert result["mae"] >= 0


def test_backtest_single_week(db, sample_data):
    """Backtest for a specific week should only include that week."""
    service = ProjectionTunerService(db)
    result = service.backtest_weekly("QB", year=2024, week=2)

    assert result["sample_count"] >= 1
    for r in result["results"]:
        assert r["week"] == 2


def test_backtest_empty_position(db, sample_data):
    """Backtest for position with no players should return empty."""
    service = ProjectionTunerService(db)
    result = service.backtest_weekly("TE", year=2024)

    assert result["player_count"] == 0
    assert result["mae"] is None


def test_backtest_yearly(db, sample_data):
    """Yearly backtest should run and return stats."""
    service = ProjectionTunerService(db)
    result = service.backtest_yearly("QB", year=2024)

    assert "mae" in result
    assert "results" in result


# ── Criteria dict in result ───────────────────────────────────────────────

def test_criteria_dict_in_result(db, sample_data):
    """Result should include the criteria values used."""
    service = ProjectionTunerService(db)
    result = service.project_weekly(sample_data.id, week=1, year=2024)

    assert "criteria" in result
    assert "historical_average_points" in result["criteria"]
    assert "player_skill_level" in result["criteria"]
    assert "opposing_defense_vs_position_rank" in result["criteria"]
