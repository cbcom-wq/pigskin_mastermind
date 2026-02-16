"""Entertainment features for fantasy football."""

import random
from typing import List, Dict, Any
from pigskin_mastermind.models.team import Team


class TeamNameGenerator:
    """Generate fun team names for fantasy football."""
    
    def __init__(self):
        """Initialize the name generator."""
        self.prefixes = [
            "The", "Mighty", "Thunder", "Lightning", "Golden", "Silver",
            "Raging", "Flying", "Unstoppable", "Legendary", "Elite"
        ]
        self.nouns = [
            "Warriors", "Titans", "Gladiators", "Champions", "Destroyers",
            "Dominators", "Bulldozers", "Crushers", "Hurricanes", "Avalanche",
            "Thunderbolts", "Hurricanes", "Blitz", "Bombers", "Dynasty"
        ]
        self.player_puns = [
            "Game of Throws", "Victorious Secret", "The Interceptors",
            "Multiple Scoregasms", "The Comebacks", "Touchdown There",
            "Gridiron Gang", "End Zone Soldiers", "Red Zone Rockets"
        ]
    
    def generate_random_name(self) -> str:
        """
        Generate a random team name.
        
        Returns:
            Generated team name
        """
        if random.random() < 0.5:
            return f"{random.choice(self.prefixes)} {random.choice(self.nouns)}"
        else:
            return random.choice(self.player_puns)
    
    def generate_player_based_name(self, player_name: str) -> List[str]:
        """
        Generate team names based on a player's name.
        
        Args:
            player_name: Player's name
            
        Returns:
            List of generated names
        """
        names = []
        last_name = player_name.split()[-1] if ' ' in player_name else player_name
        
        # Simple pun variations
        names.append(f"{last_name}'s Army")
        names.append(f"The {last_name} Express")
        names.append(f"{last_name} and the Boys")
        names.append(f"Team {last_name}")
        names.append(f"{last_name}'s Warriors")
        
        return names
    
    def suggest_names(self, count: int = 5) -> List[str]:
        """
        Generate multiple team name suggestions.
        
        Args:
            count: Number of names to generate
            
        Returns:
            List of team names
        """
        names = set()
        while len(names) < count:
            names.add(self.generate_random_name())
        return list(names)


class MatchupPredictor:
    """Predict outcomes of fantasy matchups."""
    
    def predict_matchup(self, team1: Team, team2: Team) -> Dict[str, Any]:
        """
        Predict the outcome of a matchup between two teams.
        
        Args:
            team1: First team
            team2: Second team
            
        Returns:
            Dictionary with prediction
        """
        team1_projected = sum(p.projected_points for p in team1.players)
        team2_projected = sum(p.projected_points for p in team2.players)
        
        point_diff = abs(team1_projected - team2_projected)
        
        # Calculate win probability (simplified)
        if team1_projected > team2_projected:
            winner = team1
            win_probability = min(0.95, 0.5 + (point_diff / 200))
        elif team2_projected > team1_projected:
            winner = team2
            win_probability = min(0.95, 0.5 + (point_diff / 200))
        else:
            winner = None
            win_probability = 0.5
        
        return {
            'team1': {
                'name': team1.name,
                'projected_points': team1_projected,
            },
            'team2': {
                'name': team2.name,
                'projected_points': team2_projected,
            },
            'predicted_winner': winner.name if winner else 'Toss-up',
            'win_probability': win_probability,
            'projected_margin': point_diff,
            'confidence': 'High' if point_diff > 15 else 'Medium' if point_diff > 5 else 'Low',
        }


class LeagueEntertainment:
    """Entertainment features for fantasy leagues."""
    
    def generate_power_rankings(self, teams: List[Team]) -> List[Dict[str, Any]]:
        """
        Generate power rankings for teams in a league.
        
        Args:
            teams: List of teams
            
        Returns:
            List of teams ranked by performance
        """
        # Calculate a composite score for each team
        ranked_teams = []
        for team in teams:
            wins = team.record.get('wins', 0)
            losses = team.record.get('losses', 0)
            win_pct = wins / (wins + losses) if (wins + losses) > 0 else 0
            
            # Composite score: 60% win percentage, 40% points
            max_points = max(t.total_points for t in teams) if teams else 1
            points_pct = team.total_points / max_points if max_points > 0 else 0
            
            power_score = (win_pct * 0.6) + (points_pct * 0.4)
            
            ranked_teams.append({
                'rank': 0,  # Will be filled after sorting
                'team_name': team.name,
                'owner': team.owner,
                'record': f"{wins}-{losses}",
                'total_points': team.total_points,
                'power_score': power_score,
            })
        
        # Sort by power score
        ranked_teams.sort(key=lambda t: t['power_score'], reverse=True)
        
        # Assign ranks
        for i, team in enumerate(ranked_teams):
            team['rank'] = i + 1
        
        return ranked_teams
    
    def generate_weekly_awards(self, teams: List[Team]) -> Dict[str, Any]:
        """
        Generate weekly awards for league teams.
        
        Args:
            teams: List of teams
            
        Returns:
            Dictionary with award winners
        """
        if not teams:
            return {}
        
        # Find teams with highest and lowest scores
        highest_scorer = max(teams, key=lambda t: t.total_points)
        lowest_scorer = min(teams, key=lambda t: t.total_points)
        
        # Find team with best/worst record
        best_record = max(teams, key=lambda t: t.record.get('wins', 0))
        
        # Find luckiest team (best record with low points)
        teams_with_games = [t for t in teams if sum(t.record.values()) > 0]
        if teams_with_games:
            luckiest = max(
                teams_with_games,
                key=lambda t: t.record.get('wins', 0) / max(sum(t.record.values()), 1) - 
                             (t.total_points / max(max(team.total_points for team in teams), 1))
            )
        else:
            luckiest = None
        
        return {
            'highest_scorer': {
                'team': highest_scorer.name,
                'owner': highest_scorer.owner,
                'points': highest_scorer.total_points,
            },
            'lowest_scorer': {
                'team': lowest_scorer.name,
                'owner': lowest_scorer.owner,
                'points': lowest_scorer.total_points,
            },
            'best_record': {
                'team': best_record.name,
                'owner': best_record.owner,
                'record': best_record.record,
            },
            'luckiest_team': {
                'team': luckiest.name if luckiest else 'N/A',
                'owner': luckiest.owner if luckiest else 'N/A',
            } if luckiest else None,
        }
    
    def generate_trash_talk(self, winning_team: Team, losing_team: Team) -> List[str]:
        """
        Generate friendly trash talk messages.
        
        Args:
            winning_team: The winning team
            losing_team: The losing team
            
        Returns:
            List of trash talk messages
        """
        messages = [
            f"{winning_team.name} just rolled over {losing_team.name}!",
            f"{losing_team.owner}, better luck next week!",
            f"{winning_team.owner} is on fire! {losing_team.owner} got burned!",
            f"{losing_team.name} needs to hit the waiver wire!",
            f"{winning_team.name} looking unstoppable!",
        ]
        return messages


class SeasonVisualization:
    """
    Entertainment feature for season visualization.
    
    This class provides access to year-to-date graphic visualizations
    showing player points accumulation and team results over the season.
    """
    
    @staticmethod
    def get_visualization_url(team_id: int) -> str:
        """
        Get the URL for the season animation visualization.
        
        Args:
            team_id: Team ID
            
        Returns:
            URL to the visualization page
        """
        return f"/visualizations/season-animation/{team_id}"
    
    @staticmethod
    def get_api_data_url(team_id: int) -> str:
        """
        Get the API URL for season data.
        
        Args:
            team_id: Team ID
            
        Returns:
            URL to the API endpoint
        """
        return f"/visualizations/api/season-data/{team_id}"
