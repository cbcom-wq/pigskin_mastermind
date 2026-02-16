"""Team model for fantasy football teams."""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from pigskin_mastermind.models.player import Player


@dataclass
class Team:
    """
    Represents a fantasy football team.
    
    Attributes:
        team_id: Unique identifier for the team
        name: Team name
        owner: Team owner's name
        players: List of players on the team
        record: Team record (wins, losses, ties)
        total_points: Total points scored by the team
        league_id: League identifier
    """
    team_id: str
    name: str
    owner: str
    players: List[Player] = field(default_factory=list)
    record: Dict[str, int] = field(default_factory=lambda: {'wins': 0, 'losses': 0, 'ties': 0})
    total_points: float = 0.0
    league_id: Optional[str] = None
    is_user_team: bool = False
    
    def add_player(self, player: Player) -> None:
        """
        Add a player to the team.
        
        Args:
            player: Player instance to add
        """
        if player not in self.players:
            self.players.append(player)
    
    def remove_player(self, player_id: str) -> bool:
        """
        Remove a player from the team.
        
        Args:
            player_id: ID of the player to remove
            
        Returns:
            True if player was removed, False if not found
        """
        for i, player in enumerate(self.players):
            if player.player_id == player_id:
                self.players.pop(i)
                return True
        return False
    
    def get_player(self, player_id: str) -> Optional[Player]:
        """
        Get a player by ID.
        
        Args:
            player_id: ID of the player to retrieve
            
        Returns:
            Player instance if found, None otherwise
        """
        for player in self.players:
            if player.player_id == player_id:
                return player
        return None
    
    def get_players_by_position(self, position: str) -> List[Player]:
        """
        Get all players at a specific position.
        
        Args:
            position: Position to filter by
            
        Returns:
            List of players at the specified position
        """
        return [p for p in self.players if p.position == position]
    
    def calculate_total_points(self) -> float:
        """
        Calculate total points for all players on the team.
        
        Returns:
            Total points
        """
        self.total_points = sum(player.actual_points for player in self.players)
        return self.total_points
    
    def get_starting_lineup(self, lineup_rules: Optional[Dict[str, int]] = None) -> List[Player]:
        """
        Get the optimal starting lineup based on projected points.
        
        Args:
            lineup_rules: Dictionary defining lineup slots (e.g., {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1})
            
        Returns:
            List of players in the starting lineup
        """
        if lineup_rules is None:
            lineup_rules = {'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'K': 1, 'DEF': 1}
        
        lineup = []
        
        # Add players by position based on rules
        for position, count in lineup_rules.items():
            if position == 'FLEX':
                continue  # Handle FLEX separately
            
            position_players = sorted(
                self.get_players_by_position(position),
                key=lambda p: p.projected_points,
                reverse=True
            )
            lineup.extend(position_players[:count])
        
        return lineup
    
    def update_record(self, result: str) -> None:
        """
        Update team record with a game result.
        
        Args:
            result: 'win', 'loss', or 'tie'
        """
        if result.lower() == 'win':
            self.record['wins'] += 1
        elif result.lower() == 'loss':
            self.record['losses'] += 1
        elif result.lower() == 'tie':
            self.record['ties'] += 1
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert team to dictionary representation."""
        return {
            'team_id': self.team_id,
            'name': self.name,
            'owner': self.owner,
            'players': [p.to_dict() for p in self.players],
            'record': self.record,
            'total_points': self.total_points,
            'league_id': self.league_id,
        }
