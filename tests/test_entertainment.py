"""Tests for entertainment features."""

import pytest
from pigskin_mastermind.entertainment import (
    TeamNameGenerator, LeagueEntertainment, MatchupPredictor, SeasonVisualization
)
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.models.player import Player


def test_team_name_generator():
    """Test generating team names."""
    generator = TeamNameGenerator()
    names = generator.suggest_names(5)
    
    assert len(names) == 5
    assert all(isinstance(name, str) for name in names)


def test_team_name_player_based():
    """Test generating player-based team names."""
    generator = TeamNameGenerator()
    names = generator.generate_player_based_name("Patrick Mahomes")
    
    assert len(names) > 0
    assert any("Mahomes" in name for name in names)


def test_matchup_predictor():
    """Test predicting matchup outcomes."""
    team1 = Team(team_id="t1", name="Team 1", owner="Owner 1")
    team2 = Team(team_id="t2", name="Team 2", owner="Owner 2")
    
    # Team 1 has higher projected points
    p1 = Player(player_id="p1", name="Player1", position="RB", team="KC", projected_points=20.0)
    p2 = Player(player_id="p2", name="Player2", position="RB", team="BUF", projected_points=10.0)
    
    team1.add_player(p1)
    team2.add_player(p2)
    
    predictor = MatchupPredictor()
    result = predictor.predict_matchup(team1, team2)
    
    assert result['predicted_winner'] == "Team 1"
    assert result['team1']['projected_points'] == 20.0
    assert result['team2']['projected_points'] == 10.0
    assert result['projected_margin'] == 10.0


def test_power_rankings():
    """Test generating power rankings."""
    team1 = Team(team_id="t1", name="Team 1", owner="Owner 1")
    team1.record = {'wins': 5, 'losses': 2, 'ties': 0}
    team1.total_points = 800.0
    
    team2 = Team(team_id="t2", name="Team 2", owner="Owner 2")
    team2.record = {'wins': 3, 'losses': 4, 'ties': 0}
    team2.total_points = 700.0
    
    entertainment = LeagueEntertainment()
    rankings = entertainment.generate_power_rankings([team1, team2])
    
    assert len(rankings) == 2
    assert rankings[0]['rank'] == 1
    assert rankings[0]['team_name'] == "Team 1"
    assert rankings[1]['rank'] == 2


def test_weekly_awards():
    """Test generating weekly awards."""
    team1 = Team(team_id="t1", name="Team 1", owner="Owner 1")
    team1.total_points = 120.0
    team1.record = {'wins': 1, 'losses': 0, 'ties': 0}
    
    team2 = Team(team_id="t2", name="Team 2", owner="Owner 2")
    team2.total_points = 80.0
    team2.record = {'wins': 0, 'losses': 1, 'ties': 0}
    
    entertainment = LeagueEntertainment()
    awards = entertainment.generate_weekly_awards([team1, team2])
    
    assert awards['highest_scorer']['team'] == "Team 1"
    assert awards['lowest_scorer']['team'] == "Team 2"
    assert awards['best_record']['team'] == "Team 1"


def test_season_visualization_urls():
    """Test season visualization URL generation."""
    team_id = 123
    
    viz_url = SeasonVisualization.get_visualization_url(team_id)
    api_url = SeasonVisualization.get_api_data_url(team_id)
    
    assert viz_url == "/visualizations/season-animation/123"
    assert api_url == "/visualizations/api/season-data/123"
