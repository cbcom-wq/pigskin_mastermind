"""
Example usage of Pigskin Mastermind API.

This script demonstrates the core features of the fantasy football management application.
"""

from pigskin_mastermind.models.team import Team
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.services.team_manager import TeamManager
from pigskin_mastermind.services.decision_tools import LineupOptimizer, TradeAnalyzer
from pigskin_mastermind.entertainment import TeamNameGenerator, LeagueEntertainment, MatchupPredictor


def main():
    """Demonstrate Pigskin Mastermind features."""
    
    print("=" * 60)
    print("Pigskin Mastermind - Fantasy Football Manager")
    print("=" * 60)
    
    # Create team manager
    manager = TeamManager()
    
    # Create two teams
    print("\n1. Creating Teams...")
    team1 = manager.create_team("t1", "The Gridiron Gang", "Alice")
    team2 = manager.create_team("t2", "Thunder Bolts", "Bob")
    print(f"   Created: {team1.name} (Owner: {team1.owner})")
    print(f"   Created: {team2.name} (Owner: {team2.owner})")
    
    # Add players to team 1
    print("\n2. Adding Players to Teams...")
    players_team1 = [
        Player("p1", "Patrick Mahomes", "QB", "KC", projected_points=25.5),
        Player("p2", "Christian McCaffrey", "RB", "SF", projected_points=22.3),
        Player("p3", "Derrick Henry", "RB", "TEN", projected_points=18.7),
        Player("p4", "Tyreek Hill", "WR", "MIA", projected_points=20.1),
        Player("p5", "Stefon Diggs", "WR", "BUF", projected_points=18.4),
        Player("p6", "Travis Kelce", "TE", "KC", projected_points=17.2),
        Player("p7", "Justin Tucker", "K", "BAL", projected_points=10.5),
        Player("p8", "San Francisco", "DEF", "SF", projected_points=12.3),
    ]
    
    for player in players_team1:
        team1.add_player(player)
        print(f"   Added {player.name} ({player.position}) to {team1.name}")
    
    # Add players to team 2
    players_team2 = [
        Player("p9", "Josh Allen", "QB", "BUF", projected_points=24.8),
        Player("p10", "Saquon Barkley", "RB", "NYG", projected_points=20.1),
        Player("p11", "Austin Ekeler", "RB", "LAC", projected_points=19.5),
        Player("p12", "Justin Jefferson", "WR", "MIN", projected_points=21.3),
        Player("p13", "Davante Adams", "WR", "LV", projected_points=19.2),
        Player("p14", "Mark Andrews", "TE", "BAL", projected_points=15.7),
        Player("p15", "Harrison Butker", "K", "KC", projected_points=9.8),
        Player("p16", "Buffalo", "DEF", "BUF", projected_points=11.5),
    ]
    
    for player in players_team2:
        team2.add_player(player)
    
    # Analyze teams
    print("\n3. Analyzing Teams...")
    analysis1 = manager.analyze_team("t1")
    print(f"\n   {analysis1['team_name']}")
    print(f"   Owner: {analysis1['owner']}")
    print(f"   Players: {analysis1['player_count']}")
    print(f"   Position breakdown:")
    for pos, data in analysis1['position_breakdown'].items():
        print(f"     {pos}: {data['count']} players, {data['projected_points']:.1f} projected pts")
    
    # Optimize lineup
    print("\n4. Optimizing Lineup for Team 1...")
    optimizer = LineupOptimizer()
    result = optimizer.optimize_lineup(team1)
    print(f"   Total projected points: {result['total_projected_points']:.2f}")
    print(f"   Starters:")
    for position, players in result['lineup'].items():
        for player in players:
            print(f"     {position}: {player.name} - {player.projected_points:.1f} pts")
    
    # Predict matchup
    print("\n5. Predicting Matchup...")
    predictor = MatchupPredictor()
    prediction = predictor.predict_matchup(team1, team2)
    print(f"   {team1.name} vs {team2.name}")
    print(f"   Predicted winner: {prediction['predicted_winner']}")
    print(f"   {team1.name}: {prediction['team1']['projected_points']:.1f} pts")
    print(f"   {team2.name}: {prediction['team2']['projected_points']:.1f} pts")
    print(f"   Confidence: {prediction['confidence']}")
    
    # Generate team names
    print("\n6. Generating Team Name Suggestions...")
    generator = TeamNameGenerator()
    names = generator.suggest_names(3)
    print("   Suggested names:")
    for i, name in enumerate(names, 1):
        print(f"     {i}. {name}")
    
    # Player-based names
    print("\n   Player-based names for 'Patrick Mahomes':")
    player_names = generator.generate_player_based_name("Patrick Mahomes")
    for i, name in enumerate(player_names[:3], 1):
        print(f"     {i}. {name}")
    
    # Trade analysis
    print("\n7. Analyzing Trade...")
    analyzer = TradeAnalyzer()
    trade = analyzer.analyze_trade(
        team1, [players_team1[1]], [players_team2[2]],  # CMC for Ekeler
        team2, [players_team2[2]], [players_team1[1]]
    )
    print(f"   Trade: {players_team1[1].name} for {players_team2[2].name}")
    print(f"   {team1.name} net gain: {trade['team1']['net_gain']:.1f} pts")
    print(f"   {team2.name} net gain: {trade['team2']['net_gain']:.1f} pts")
    print(f"   Winner: {trade['winner']}")
    print(f"   Fair trade: {trade['is_fair']}")
    
    # Power rankings
    print("\n8. Generating Power Rankings...")
    team1.record = {'wins': 7, 'losses': 2, 'ties': 0}
    team1.total_points = 950.5
    team2.record = {'wins': 6, 'losses': 3, 'ties': 0}
    team2.total_points = 920.3
    
    entertainment = LeagueEntertainment()
    rankings = entertainment.generate_power_rankings([team1, team2])
    print("   League Power Rankings:")
    for team in rankings:
        print(f"     {team['rank']}. {team['team_name']} ({team['record']}) - {team['total_points']:.1f} pts")
    
    print("\n" + "=" * 60)
    print("Demo Complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
