"""Tests for TeamManager service."""

import pytest
from pigskin_mastermind.services.team_manager import TeamManager
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.models.player import Player


def test_create_team():
    """Test creating a team through manager."""
    manager = TeamManager()
    team = manager.create_team("t1", "Test Team", "Test Owner")
    
    assert team.team_id == "t1"
    assert team.name == "Test Team"
    assert manager.get_team("t1") == team


def test_list_teams():
    """Test listing teams."""
    manager = TeamManager()
    team1 = manager.create_team("t1", "Team 1", "Owner 1")
    team2 = manager.create_team("t2", "Team 2", "Owner 2")
    
    teams = manager.list_teams()
    assert len(teams) == 2
    assert team1 in teams
    assert team2 in teams


def test_delete_team():
    """Test deleting a team."""
    manager = TeamManager()
    manager.create_team("t1", "Test Team", "Test Owner")
    
    result = manager.delete_team("t1")
    assert result is True
    assert manager.get_team("t1") is None


def test_compare_teams():
    """Test comparing two teams."""
    manager = TeamManager()
    team1 = manager.create_team("t1", "Team 1", "Owner 1")
    team2 = manager.create_team("t2", "Team 2", "Owner 2")
    
    team1.total_points = 100.0
    team1.record = {'wins': 5, 'losses': 2, 'ties': 0}
    team2.total_points = 90.0
    team2.record = {'wins': 4, 'losses': 3, 'ties': 0}
    
    comparison = manager.compare_teams("t1", "t2")
    assert comparison['team1']['total_points'] == 100.0
    assert comparison['team2']['total_points'] == 90.0
    assert comparison['point_differential'] == 10.0


def test_analyze_team():
    """Test analyzing a team."""
    manager = TeamManager()
    team = manager.create_team("t1", "Test Team", "Test Owner")
    
    rb = Player(player_id="p1", name="RB1", position="RB", team="KC")
    rb.actual_points = 15.0
    wr = Player(player_id="p2", name="WR1", position="WR", team="BUF")
    wr.actual_points = 20.0
    
    team.add_player(rb)
    team.add_player(wr)
    team.calculate_total_points()
    
    analysis = manager.analyze_team("t1")
    assert analysis['team_name'] == "Test Team"
    assert analysis['player_count'] == 2
    assert analysis['total_points'] == 35.0
    assert 'RB' in analysis['position_breakdown']
    assert 'WR' in analysis['position_breakdown']
