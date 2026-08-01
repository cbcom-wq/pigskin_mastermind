import pytest
from unittest.mock import Mock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pigskin_mastermind.models.database import (
    Base, DBTeam, DBPlayer, DBPlayerSeasonStats,
)
from pigskin_mastermind.services.espn_sync import ESPNSyncService, TUNER_PLAYER_LIMITS

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


@patch('pigskin_mastermind.services.espn_sync.League')
def test_import_team_from_espn(mock_league, db):
    """Test importing a team from ESPN"""
    mock_team = Mock()
    mock_team.team_id = 1
    mock_team.team_name = "ESPN Team"
    mock_team.owner = "ESPN Owner"
    mock_team.wins = 5
    mock_team.losses = 3
    mock_team.ties = 0
    mock_team.points_for = 950.5

    mock_player = Mock()
    mock_player.playerId = 12345
    mock_player.name = "Patrick Mahomes"
    mock_player.position = "QB"
    mock_player.proTeam = "KC"
    mock_player.projected_points = 25.5
    mock_player.points = 22.3
    mock_player.stats = {}

    mock_team.roster = [mock_player]
    mock_league.return_value.teams = [mock_team]

    service = ESPNSyncService(db)
    result = service.import_team(
        league_id="123456",
        team_id=1,
        espn_s2="test_s2",
        swid="test_swid",
        year=2024
    )

    assert result is not None
    assert result.name == "ESPN Team"

    db_team = db.query(DBTeam).filter_by(espn_team_id="1").first()
    assert db_team is not None
    assert db_team.name == "ESPN Team"
    assert db_team.wins == 5

    db_player = db.query(DBPlayer).filter_by(player_id="espn_12345").first()
    assert db_player is not None
    assert db_player.name == "Patrick Mahomes"
    assert db_player.position == "QB"


@patch('pigskin_mastermind.services.espn_sync.League')
def test_import_team_not_found(mock_league, db):
    """Test importing a team that doesn't exist in the league"""
    mock_league.return_value.teams = []

    service = ESPNSyncService(db)
    with pytest.raises(ValueError, match="Team 99 not found"):
        service.import_team(
            league_id="123456",
            team_id=99,
            espn_s2="test_s2",
            swid="test_swid",
        )


# ---------------------------------------------------------------------------
# _rank_espn_candidates tests
# ---------------------------------------------------------------------------

def _make_mock_player(
    name: str,
    total_points: float = 0,
    projected_total_points: float = 0,
    percent_owned: float = 0,
    posRank: int = 999,
    injuryStatus: str = "ACTIVE",
):
    """Create a lightweight mock ESPN player for ranking tests."""
    p = Mock()
    p.name = name
    p.total_points = total_points
    p.projected_total_points = projected_total_points
    p.percent_owned = percent_owned
    p.posRank = posRank
    p.injuryStatus = injuryStatus
    return p


def test_rank_espn_candidates_prefers_high_scorers():
    """High-production players should be ranked above zero-point players."""
    players = [
        _make_mock_player("Zero Guy", total_points=0, projected_total_points=10, percent_owned=50),
        _make_mock_player("Star RB", total_points=200, projected_total_points=180, percent_owned=95),
        _make_mock_player("Decent RB", total_points=120, projected_total_points=110, percent_owned=60),
        _make_mock_player("Low Guy", total_points=15, projected_total_points=5, percent_owned=10),
    ]
    result = ESPNSyncService._rank_espn_candidates(players, keep=2)
    names = [p.name for p in result]
    assert names == ["Star RB", "Decent RB"]


def test_rank_espn_candidates_demotes_ir_zero_point():
    """OUT/IR players with zero points should be pushed to the bottom."""
    players = [
        _make_mock_player("IR Zero", total_points=0, injuryStatus="IR"),
        _make_mock_player("OUT Zero", total_points=0, injuryStatus="OUT"),
        _make_mock_player("Active Low", total_points=30, injuryStatus="ACTIVE"),
        _make_mock_player("Active Med", total_points=80, injuryStatus="ACTIVE"),
    ]
    result = ESPNSyncService._rank_espn_candidates(players, keep=3)
    names = [p.name for p in result]
    # The two demoted players should NOT appear when we only keep 3
    # (there are 2 healthy + 2 demoted, keeping 3 means top 2 healthy + 1 demoted)
    assert names[0] == "Active Med"
    assert names[1] == "Active Low"
    # Third slot goes to one of the demoted players
    assert names[2] in ("IR Zero", "OUT Zero")


def test_rank_espn_candidates_ir_with_points_not_demoted():
    """An IR player who actually scored points should NOT be demoted."""
    players = [
        _make_mock_player("IR Star", total_points=150, injuryStatus="IR"),
        _make_mock_player("Healthy Avg", total_points=80, injuryStatus="ACTIVE"),
    ]
    result = ESPNSyncService._rank_espn_candidates(players, keep=2)
    names = [p.name for p in result]
    assert names[0] == "IR Star"
    assert names[1] == "Healthy Avg"


def test_rank_espn_candidates_tiebreak_by_projection():
    """When total_points are equal, projected_total_points breaks the tie."""
    players = [
        _make_mock_player("Low Proj", total_points=100, projected_total_points=90),
        _make_mock_player("High Proj", total_points=100, projected_total_points=150),
    ]
    result = ESPNSyncService._rank_espn_candidates(players, keep=2)
    assert result[0].name == "High Proj"
    assert result[1].name == "Low Proj"


def test_rank_espn_candidates_respects_keep_limit():
    """Should return at most *keep* players."""
    players = [_make_mock_player(f"P{i}", total_points=100 - i) for i in range(10)]
    result = ESPNSyncService._rank_espn_candidates(players, keep=3)
    assert len(result) == 3


@patch('pigskin_mastermind.services.espn_sync.League')
def test_import_relevant_players_uses_ranking(mock_league, db):
    """The import method should over-fetch and only import the ranked subset."""
    mock_league_instance = mock_league.return_value
    mock_league_instance.current_week = 10

    # Create 6 mock QB candidates — limit is 2, so 4 should be fetched (2x)
    # and only the top 2 by production imported.
    candidates = [
        _make_mock_player("Zero QB1", total_points=0, projected_total_points=5),
        _make_mock_player("Zero QB2", total_points=0, projected_total_points=3),
        _make_mock_player("Star QB", total_points=300, projected_total_points=280),
        _make_mock_player("Good QB", total_points=200, projected_total_points=190),
    ]
    # Add required BoxPlayer attributes for _import_player_from_box
    for i, p in enumerate(candidates):
        p.playerId = 1000 + i
        p.position = "QB"
        p.proTeam = "KC"
        p.projected_points = 20.0
        p.points = float(p.total_points)
        p.stats = {}

    mock_league_instance.free_agents.return_value = candidates

    service = ESPNSyncService(db)
    result = service.import_relevant_players_for_tuner(
        league_id="123",
        espn_s2="s2",
        swid="swid",
        year=2025,
        limits={"QB": 2},
        preload_full_history=False,
    )

    assert result["QB"] == 2
    assert result["total"] == 2

    # Verify the imported players are the high-production ones
    imported = db.query(DBPlayer).all()
    imported_names = {p.name for p in imported}
    assert "Star QB" in imported_names
    assert "Good QB" in imported_names
    assert "Zero QB1" not in imported_names
    assert "Zero QB2" not in imported_names


# ---------------------------------------------------------------------------
# Season aggregation: games_played must reflect games actually played
# ---------------------------------------------------------------------------

# A season aggregate as ESPN returns it under scoring period '0'.  Modelled on
# the real 2025 Saquon Barkley payload: 213.8 points over 16 games played.
BARKLEY_SEASON_BREAKDOWN = {
    'rushingAttempts': 293,
    'rushingYards': 1250,
    'rushingTouchdowns': 6,
    'receivingTargets': 47,
    'receivingReceptions': 38,
    'receivingYards': 340,
    'receivingTouchdowns': 2,
    'lostFumbles': 1,
}


def _make_season_blob(points, avg_points, extra_weeks=None):
    """Build a raw ESPN stats blob: season aggregate under '0' plus weeks."""
    blob = {
        '0': {
            'points': points,
            'avg_points': avg_points,
            'breakdown': dict(BARKLEY_SEASON_BREAKDOWN),
            'projected_points': 0.0,
            'projected_breakdown': {},
        },
    }
    blob.update(extra_weeks or {})
    return blob


def _add_player(db, name="Saquon Barkley", stats=None, position="RB"):
    player = DBPlayer(
        player_id=f"espn_{abs(hash(name)) % 100000}",
        name=name,
        position=position,
        nfl_team="PHI",
        stats=stats,
    )
    db.add(player)
    db.commit()
    return player


def test_season_aggregate_does_not_count_empty_week_as_a_game(db):
    """Regression: a season total must never be divided by a placeholder week.

    ESPN's blob commonly holds only the season aggregate ('0') plus a single
    scoring-period entry for a week the player did not play — ``points`` 0.0
    and no ``breakdown``.  Counting that key as a game made
    ``fantasy_points_avg`` equal the full-season ``fantasy_points_total``.
    """
    stats = _make_season_blob(
        points=213.8,
        avg_points=13.36,
        extra_weeks={'18': {'points': 0.0, 'breakdown': {}, 'avg_points': 0.0}},
    )
    player = _add_player(db, stats=stats)

    ESPNSyncService(db).populate_stats_from_player_json(year=2025)

    season = db.query(DBPlayerSeasonStats).filter_by(
        player_id=player.id, year=2025,
    ).first()
    assert season is not None
    assert season.fantasy_points_total == pytest.approx(213.8)
    # The bug: games_played == 1 and avg == total
    assert season.games_played == 16
    assert season.fantasy_points_avg == pytest.approx(13.36, abs=0.05)
    assert season.fantasy_points_avg != pytest.approx(season.fantasy_points_total)


def test_season_aggregate_never_pairs_one_game_with_a_season_total(db):
    """A season-total-sized number must never sit against games_played == 1."""
    players = [
        _add_player(db, name="QB One", position="QB",
                    stats=_make_season_blob(349.06, 21.82,
                                            {'18': {'points': 0.0, 'breakdown': {}}})),
        _add_player(db, name="RB Two", position="RB",
                    stats=_make_season_blob(213.8, 13.36,
                                            {'17': {'points': 0.0, 'breakdown': {}}})),
        _add_player(db, name="K Three", position="K",
                    stats=_make_season_blob(108.0, 7.2,
                                            {'18': {'points': 0.0, 'breakdown': {}}})),
    ]
    ESPNSyncService(db).populate_stats_from_player_json(year=2025)

    rows = db.query(DBPlayerSeasonStats).filter_by(year=2025).all()
    assert len(rows) == len(players)
    for row in rows:
        if row.fantasy_points_total > 50:
            assert row.games_played > 1, (
                f"player {row.player_id} has a season total of "
                f"{row.fantasy_points_total} against {row.games_played} game(s)"
            )
        # The averaging invariant, whatever the game count.
        assert row.fantasy_points_avg <= row.fantasy_points_total / 2


def test_season_average_matches_total_over_games(db):
    """fantasy_points_avg must stay consistent with total / games_played."""
    player = _add_player(db, stats=_make_season_blob(
        points=213.8, avg_points=13.36,
        extra_weeks={'18': {'points': 0.0, 'breakdown': {}}},
    ))
    ESPNSyncService(db).populate_stats_from_player_json(year=2025)

    season = db.query(DBPlayerSeasonStats).filter_by(player_id=player.id).first()
    assert season.fantasy_points_avg == pytest.approx(
        season.fantasy_points_total / season.games_played, abs=0.05,
    )


def test_season_games_fall_back_to_weeks_with_real_stats(db):
    """Without a usable avg_points, only weeks carrying stats count as games."""
    week_with_stats = {
        'points': 12.4,
        'breakdown': {'rushingAttempts': 14, 'rushingYards': 74},
    }
    stats = _make_season_blob(points=24.8, avg_points=0.0, extra_weeks={
        '1': dict(week_with_stats),
        '2': dict(week_with_stats),
        '3': {'points': 0.0, 'breakdown': {}},   # did not play
    })
    player = _add_player(db, stats=stats)

    ESPNSyncService(db).populate_stats_from_player_json(year=2025)

    season = db.query(DBPlayerSeasonStats).filter_by(player_id=player.id).first()
    assert season.games_played == 2
    assert season.fantasy_points_avg == pytest.approx(12.4)


def test_single_player_stats_path_also_counts_games_correctly(db):
    """_populate_single_player_stats shares the same games_played rule."""
    player = _add_player(db, stats=_make_season_blob(
        points=213.8, avg_points=13.36,
        extra_weeks={'18': {'points': 0.0, 'breakdown': {}}},
    ))

    ESPNSyncService(db)._populate_single_player_stats(player, year=2025)

    season = db.query(DBPlayerSeasonStats).filter_by(player_id=player.id).first()
    assert season.games_played == 16
    assert season.fantasy_points_avg == pytest.approx(13.36, abs=0.05)
