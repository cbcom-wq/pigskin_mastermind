"""Tests for Team model."""

import pytest
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.models.player import Player


def test_team_creation():
    """Test creating a team."""
    team = Team(
        team_id="t1",
        name="Test Team",
        owner="Test Owner",
    )
    assert team.team_id == "t1"
    assert team.name == "Test Team"
    assert team.owner == "Test Owner"
    assert len(team.players) == 0


def test_team_add_player():
    """Test adding a player to a team."""
    team = Team(team_id="t1", name="Test Team", owner="Test Owner")
    player = Player(player_id="p1", name="John Doe", position="RB", team="KC")
    
    team.add_player(player)
    assert len(team.players) == 1
    assert team.players[0] == player


def test_team_remove_player():
    """Test removing a player from a team."""
    team = Team(team_id="t1", name="Test Team", owner="Test Owner")
    player = Player(player_id="p1", name="John Doe", position="RB", team="KC")
    
    team.add_player(player)
    result = team.remove_player("p1")
    assert result is True
    assert len(team.players) == 0


def test_team_get_player():
    """Test getting a player by ID."""
    team = Team(team_id="t1", name="Test Team", owner="Test Owner")
    player = Player(player_id="p1", name="John Doe", position="RB", team="KC")
    
    team.add_player(player)
    retrieved = team.get_player("p1")
    assert retrieved == player


def test_team_get_players_by_position():
    """Test getting players by position."""
    team = Team(team_id="t1", name="Test Team", owner="Test Owner")
    rb1 = Player(player_id="p1", name="RB1", position="RB", team="KC")
    rb2 = Player(player_id="p2", name="RB2", position="RB", team="BUF")
    wr1 = Player(player_id="p3", name="WR1", position="WR", team="KC")
    
    team.add_player(rb1)
    team.add_player(rb2)
    team.add_player(wr1)
    
    rbs = team.get_players_by_position("RB")
    assert len(rbs) == 2
    assert rb1 in rbs
    assert rb2 in rbs


def test_team_calculate_total_points():
    """Test calculating total points."""
    team = Team(team_id="t1", name="Test Team", owner="Test Owner")
    p1 = Player(player_id="p1", name="Player1", position="RB", team="KC")
    p1.actual_points = 15.0
    p2 = Player(player_id="p2", name="Player2", position="WR", team="BUF")
    p2.actual_points = 20.0
    
    team.add_player(p1)
    team.add_player(p2)
    
    total = team.calculate_total_points()
    assert total == 35.0
    assert team.total_points == 35.0


def test_team_update_record():
    """Test updating team record."""
    team = Team(team_id="t1", name="Test Team", owner="Test Owner")
    
    team.update_record("win")
    assert team.record['wins'] == 1
    
    team.update_record("loss")
    assert team.record['losses'] == 1
    
    team.update_record("tie")
    assert team.record['ties'] == 1
