"""Tests for Player model."""

import pytest
from pigskin_mastermind.models.player import Player


def test_player_creation():
    """Test creating a player."""
    player = Player(
        player_id="p1",
        name="John Doe",
        position="RB",
        team="KC",
    )
    assert player.player_id == "p1"
    assert player.name == "John Doe"
    assert player.position == "RB"
    assert player.team == "KC"


def test_player_invalid_position():
    """Test that invalid position raises error."""
    with pytest.raises(ValueError):
        Player(
            player_id="p1",
            name="John Doe",
            position="INVALID",
            team="KC",
        )


def test_player_update_stats():
    """Test updating player stats."""
    player = Player(
        player_id="p1",
        name="John Doe",
        position="RB",
        team="KC",
    )
    player.update_stats({'rush_yd': 100, 'rush_td': 2})
    assert player.stats['rush_yd'] == 100
    assert player.stats['rush_td'] == 2


def test_player_calculate_points():
    """Test calculating fantasy points."""
    player = Player(
        player_id="p1",
        name="John Doe",
        position="RB",
        team="KC",
    )
    player.update_stats({'rush_yd': 100, 'rush_td': 2})
    points = player.calculate_points()
    # 100 yards * 0.1 + 2 TDs * 6 = 10 + 12 = 22
    assert points == 22.0
    assert player.actual_points == 22.0


def test_player_to_dict():
    """Test converting player to dictionary."""
    player = Player(
        player_id="p1",
        name="John Doe",
        position="RB",
        team="KC",
        projected_points=15.0,
    )
    data = player.to_dict()
    assert data['player_id'] == "p1"
    assert data['name'] == "John Doe"
    assert data['position'] == "RB"
    assert data['projected_points'] == 15.0
