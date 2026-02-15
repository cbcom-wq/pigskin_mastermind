"""Tests for decision tools."""

import pytest
from pigskin_mastermind.services.decision_tools import LineupOptimizer, TradeAnalyzer
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.models.player import Player


def test_lineup_optimizer_optimize():
    """Test optimizing a lineup."""
    team = Team(team_id="t1", name="Test Team", owner="Test Owner")
    
    # Add players with different projected points
    qb = Player(player_id="p1", name="QB1", position="QB", team="KC", projected_points=20.0)
    rb1 = Player(player_id="p2", name="RB1", position="RB", team="KC", projected_points=15.0)
    rb2 = Player(player_id="p3", name="RB2", position="RB", team="BUF", projected_points=12.0)
    rb3 = Player(player_id="p4", name="RB3", position="RB", team="SF", projected_points=8.0)
    wr1 = Player(player_id="p5", name="WR1", position="WR", team="KC", projected_points=18.0)
    wr2 = Player(player_id="p6", name="WR2", position="WR", team="BUF", projected_points=14.0)
    
    team.add_player(qb)
    team.add_player(rb1)
    team.add_player(rb2)
    team.add_player(rb3)
    team.add_player(wr1)
    team.add_player(wr2)
    
    optimizer = LineupOptimizer()
    result = optimizer.optimize_lineup(team)
    
    # Check that best players are in lineup
    assert len(result['lineup']['QB']) == 1
    assert result['lineup']['QB'][0].name == "QB1"
    assert len(result['lineup']['RB']) == 2
    assert rb1 in result['lineup']['RB']
    assert rb2 in result['lineup']['RB']
    assert rb3 not in result['lineup']['RB']  # RB3 should be on bench


def test_trade_analyzer():
    """Test analyzing a trade."""
    team1 = Team(team_id="t1", name="Team 1", owner="Owner 1")
    team2 = Team(team_id="t2", name="Team 2", owner="Owner 2")
    
    # Team 1 gives high-value player
    p1 = Player(player_id="p1", name="Star RB", position="RB", team="KC", projected_points=20.0)
    # Team 1 receives low-value player
    p2 = Player(player_id="p2", name="Backup WR", position="WR", team="BUF", projected_points=8.0)
    
    analyzer = TradeAnalyzer()
    result = analyzer.analyze_trade(
        team1, [p1], [p2],
        team2, [p2], [p1]
    )
    
    assert result['team1']['gives_value'] == 20.0
    assert result['team1']['receives_value'] == 8.0
    assert result['team1']['net_gain'] == -12.0
    assert result['team2']['net_gain'] == 12.0
    assert result['winner'] == "Team 2"


def test_trade_analyzer_evaluate_for_team():
    """Test evaluating a trade for one team."""
    team = Team(team_id="t1", name="Test Team", owner="Test Owner")
    
    gives = [Player(player_id="p1", name="Player1", position="RB", team="KC", projected_points=10.0)]
    receives = [Player(player_id="p2", name="Player2", position="WR", team="BUF", projected_points=15.0)]
    
    analyzer = TradeAnalyzer()
    result = analyzer.evaluate_trade_for_team(team, gives, receives)
    
    assert result['net_gain'] == 5.0
    assert result['recommendation'] == "Accept"
