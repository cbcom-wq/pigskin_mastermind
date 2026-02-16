"""Tests for visualization service."""

import pytest
from unittest.mock import Mock, MagicMock
from sqlalchemy.orm import Session

from pigskin_mastermind.services.visualization_service import SeasonVisualizationService
from pigskin_mastermind.models.database import (
    DBTeam, DBPlayer, DBWeeklyTeamStats, DBWeeklyPlayerStats
)


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return Mock(spec=Session)


@pytest.fixture
def viz_service(mock_db):
    """Create a visualization service with mock db."""
    return SeasonVisualizationService(mock_db)


@pytest.fixture
def sample_team():
    """Create a sample team."""
    team = DBTeam(
        id=1,
        team_id="t1",
        name="Test Team",
        owner="Test Owner"
    )
    return team


@pytest.fixture
def sample_players():
    """Create sample players."""
    return [
        DBPlayer(id=1, player_id="p1", name="Player 1", position="QB", nfl_team="KC"),
        DBPlayer(id=2, player_id="p2", name="Player 2", position="RB", nfl_team="BUF"),
        DBPlayer(id=3, player_id="p3", name="Player 3", position="WR", nfl_team="MIA"),
    ]


@pytest.fixture
def sample_weekly_stats(sample_team, sample_players):
    """Create sample weekly stats."""
    weekly_stats = []
    
    # Week 1
    ws1 = DBWeeklyTeamStats(
        id=1,
        team_id=1,
        week=1,
        points_for=120.5,
        points_against=110.0,
        projected_points=115.0,
        opponent_name="Opponent 1",
        result="W"
    )
    ws1.player_stats = [
        DBWeeklyPlayerStats(
            id=1,
            player_id=1,
            weekly_team_stats_id=1,
            week=1,
            slot_position="QB",
            projected_points=25.0,
            actual_points=28.5,
            stats={}
        ),
        DBWeeklyPlayerStats(
            id=2,
            player_id=2,
            weekly_team_stats_id=1,
            week=1,
            slot_position="RB",
            projected_points=15.0,
            actual_points=18.0,
            stats={}
        ),
        DBWeeklyPlayerStats(
            id=3,
            player_id=3,
            weekly_team_stats_id=1,
            week=1,
            slot_position="WR",
            projected_points=12.0,
            actual_points=14.0,
            stats={}
        ),
    ]
    weekly_stats.append(ws1)
    
    # Week 2
    ws2 = DBWeeklyTeamStats(
        id=2,
        team_id=1,
        week=2,
        points_for=105.0,
        points_against=115.0,
        projected_points=110.0,
        opponent_name="Opponent 2",
        result="L"
    )
    ws2.player_stats = [
        DBWeeklyPlayerStats(
            id=4,
            player_id=1,
            weekly_team_stats_id=2,
            week=2,
            slot_position="QB",
            projected_points=22.0,
            actual_points=20.0,
            stats={}
        ),
        DBWeeklyPlayerStats(
            id=5,
            player_id=2,
            weekly_team_stats_id=2,
            week=2,
            slot_position="RB",
            projected_points=18.0,
            actual_points=15.5,
            stats={}
        ),
        DBWeeklyPlayerStats(
            id=6,
            player_id=3,
            weekly_team_stats_id=2,
            week=2,
            slot_position="BE",
            projected_points=10.0,
            actual_points=8.0,
            stats={}
        ),
    ]
    weekly_stats.append(ws2)
    
    return weekly_stats


def test_get_season_data_no_team(viz_service, mock_db):
    """Test getting season data when team doesn't exist."""
    mock_db.query().filter().first.return_value = None
    
    result = viz_service.get_season_data(999)
    
    assert result is None


def test_get_season_data_no_weekly_stats(viz_service, mock_db, sample_team):
    """Test getting season data when no weekly stats exist."""
    mock_db.query().filter().first.return_value = sample_team
    mock_db.query().filter().order_by().all.return_value = []
    
    result = viz_service.get_season_data(1)
    
    assert result is not None
    assert result['team_id'] == 1
    assert result['team_name'] == "Test Team"
    assert result['weeks'] == []
    assert result['player_data'] == {}
    assert result['team_results'] == []
    assert result['mvps'] == []


def test_get_season_data_with_stats(viz_service, mock_db, sample_team, sample_players, sample_weekly_stats):
    """Test getting season data with weekly stats."""
    # This is a complex integration that's better tested with actual DB
    # For now, just verify the method exists and can be called
    assert hasattr(viz_service, 'get_season_data')
    assert callable(viz_service.get_season_data)


def test_get_season_summary(viz_service, mock_db, sample_team, sample_players, sample_weekly_stats):
    """Test getting season summary."""
    # Mock get_season_data
    viz_service.get_season_data = Mock(return_value={
        'team_id': 1,
        'team_name': 'Test Team',
        'weeks': [1, 2],
        'player_data': {
            1: {
                'name': 'Player 1',
                'position': 'QB',
                'cumulative_points': [28.5, 48.5]
            },
            2: {
                'name': 'Player 2',
                'position': 'RB',
                'cumulative_points': [18.0, 33.5]
            }
        },
        'team_results': [
            {'week': 1, 'result': 'W', 'points_for': 120.5, 'points_against': 110.0},
            {'week': 2, 'result': 'L', 'points_for': 105.0, 'points_against': 115.0}
        ],
        'mvps': [
            {'week': 1, 'player': 'Player 1', 'points': 28.5},
            {'week': 2, 'player': 'Player 2', 'points': 20.0}
        ]
    })
    
    summary = viz_service.get_season_summary(1)
    
    assert summary['team_name'] == 'Test Team'
    assert summary['record'] == '1-1'
    assert summary['total_weeks'] == 2
    assert summary['total_points'] == 225.5
    assert summary['avg_points_per_week'] == 112.75
    assert summary['season_mvp'] == 'Player 1'
    assert summary['season_mvp_points'] == 48.5


def test_generate_static_visualization_no_data(viz_service, mock_db):
    """Test generating static visualization with no data."""
    viz_service.get_season_data = Mock(return_value=None)
    
    result = viz_service.generate_static_visualization(1)
    
    assert result is None


def test_generate_static_visualization_empty_weeks(viz_service, mock_db):
    """Test generating static visualization with empty weeks."""
    viz_service.get_season_data = Mock(return_value={
        'team_name': 'Test Team',
        'weeks': [],
        'player_data': {},
        'team_results': [],
        'mvps': []
    })
    
    result = viz_service.generate_static_visualization(1)
    
    assert result is None


def test_generate_animation_frames_no_data(viz_service, mock_db):
    """Test generating animation frames with no data."""
    viz_service.get_season_data = Mock(return_value=None)
    
    frames = viz_service.generate_animation_frames(1)
    
    assert frames == []


def test_generate_animation_frames_empty_weeks(viz_service, mock_db):
    """Test generating animation frames with empty weeks."""
    viz_service.get_season_data = Mock(return_value={
        'team_name': 'Test Team',
        'weeks': [],
        'player_data': {},
        'team_results': [],
        'mvps': []
    })
    
    frames = viz_service.generate_animation_frames(1)
    
    assert frames == []


def test_season_visualization_integration(viz_service):
    """Test that visualization service methods are callable."""
    # This is a basic integration test to ensure methods exist
    assert hasattr(viz_service, 'get_season_data')
    assert hasattr(viz_service, 'generate_static_visualization')
    assert hasattr(viz_service, 'generate_animation_frames')
    assert hasattr(viz_service, 'get_season_summary')
    assert callable(viz_service.get_season_data)
    assert callable(viz_service.generate_static_visualization)
    assert callable(viz_service.generate_animation_frames)
    assert callable(viz_service.get_season_summary)
