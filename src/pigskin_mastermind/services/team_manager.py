"""Team manager service for managing fantasy football teams."""

from typing import Dict, List, Optional, Any
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.models.player import Player
import json


class TeamManager:
    """
    Service for managing fantasy football teams.
    
    Provides functionality for:
    - Creating and managing teams
    - Analyzing team performance
    - Managing player rosters
    """
    
    def __init__(self):
        """Initialize the team manager."""
        self.teams: Dict[str, Team] = {}
    
    def create_team(self, team_id: str, name: str, owner: str, league_id: Optional[str] = None) -> Team:
        """
        Create a new fantasy team.
        
        Args:
            team_id: Unique team identifier
            name: Team name
            owner: Team owner's name
            league_id: Optional league identifier
            
        Returns:
            Created Team instance
        """
        team = Team(team_id=team_id, name=name, owner=owner, league_id=league_id)
        self.teams[team_id] = team
        return team
    
    def get_team(self, team_id: str) -> Optional[Team]:
        """
        Get a team by ID.
        
        Args:
            team_id: Team identifier
            
        Returns:
            Team instance if found, None otherwise
        """
        return self.teams.get(team_id)
    
    def list_teams(self) -> List[Team]:
        """
        List all teams.
        
        Returns:
            List of all teams
        """
        return list(self.teams.values())
    
    def delete_team(self, team_id: str) -> bool:
        """
        Delete a team.
        
        Args:
            team_id: Team identifier
            
        Returns:
            True if team was deleted, False if not found
        """
        if team_id in self.teams:
            del self.teams[team_id]
            return True
        return False
    
    def compare_teams(self, team_id1: str, team_id2: str) -> Dict[str, Any]:
        """
        Compare two teams.
        
        Args:
            team_id1: First team ID
            team_id2: Second team ID
            
        Returns:
            Dictionary with comparison data
        """
        team1 = self.get_team(team_id1)
        team2 = self.get_team(team_id2)
        
        if not team1 or not team2:
            raise ValueError("One or both teams not found")
        
        return {
            'team1': {
                'name': team1.name,
                'owner': team1.owner,
                'total_points': team1.total_points,
                'record': team1.record,
                'player_count': len(team1.players),
            },
            'team2': {
                'name': team2.name,
                'owner': team2.owner,
                'total_points': team2.total_points,
                'record': team2.record,
                'player_count': len(team2.players),
            },
            'point_differential': team1.total_points - team2.total_points,
        }
    
    def analyze_team(self, team_id: str) -> Dict[str, Any]:
        """
        Analyze a team's composition and performance.
        
        Args:
            team_id: Team identifier
            
        Returns:
            Dictionary with analysis data
        """
        team = self.get_team(team_id)
        if not team:
            raise ValueError(f"Team {team_id} not found")
        
        position_breakdown = {}
        for player in team.players:
            if player.position not in position_breakdown:
                position_breakdown[player.position] = {
                    'count': 0,
                    'total_points': 0.0,
                    'projected_points': 0.0,
                }
            position_breakdown[player.position]['count'] += 1
            position_breakdown[player.position]['total_points'] += player.actual_points
            position_breakdown[player.position]['projected_points'] += player.projected_points
        
        return {
            'team_name': team.name,
            'owner': team.owner,
            'total_points': team.total_points,
            'record': team.record,
            'player_count': len(team.players),
            'position_breakdown': position_breakdown,
            'avg_points_per_player': team.total_points / len(team.players) if team.players else 0,
        }
    
    def export_team_data(self, team_id: str) -> str:
        """
        Export team data as JSON.
        
        Args:
            team_id: Team identifier
            
        Returns:
            JSON string of team data
        """
        team = self.get_team(team_id)
        if not team:
            raise ValueError(f"Team {team_id} not found")
        
        return json.dumps(team.to_dict(), indent=2)
    
    def import_team_data(self, team_data: str) -> Team:
        """
        Import team data from JSON.
        
        Args:
            team_data: JSON string of team data
            
        Returns:
            Imported Team instance
        """
        data = json.loads(team_data)
        
        team = Team(
            team_id=data['team_id'],
            name=data['name'],
            owner=data['owner'],
            league_id=data.get('league_id'),
        )
        team.record = data.get('record', {'wins': 0, 'losses': 0, 'ties': 0})
        team.total_points = data.get('total_points', 0.0)
        
        for player_data in data.get('players', []):
            player = Player(
                player_id=player_data['player_id'],
                name=player_data['name'],
                position=player_data['position'],
                team=player_data['team'],
                stats=player_data.get('stats', {}),
                projected_points=player_data.get('projected_points', 0.0),
                actual_points=player_data.get('actual_points', 0.0),
            )
            team.add_player(player)
        
        self.teams[team.team_id] = team
        return team
