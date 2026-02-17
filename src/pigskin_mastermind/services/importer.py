"""Fantasy service importer for importing teams from various fantasy platforms."""

from typing import Dict, Any, Optional
from abc import ABC, abstractmethod
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.models.player import Player

try:
    from espn_api.football import League
except ImportError:
    League = None


class FantasyServiceImporter(ABC):
    """
    Abstract base class for fantasy service importers.
    
    Subclasses should implement platform-specific import logic.
    """
    
    @abstractmethod
    def authenticate(self, credentials: Dict[str, str]) -> bool:
        """
        Authenticate with the fantasy service.
        
        Args:
            credentials: Dictionary containing authentication credentials
            
        Returns:
            True if authentication successful, False otherwise
        """
        pass
    
    @abstractmethod
    def import_team(self, team_id: str) -> Team:
        """
        Import a team from the fantasy service.
        
        Args:
            team_id: Team identifier in the fantasy service
            
        Returns:
            Team instance with imported data
        """
        pass
    
    @abstractmethod
    def get_player_data(self, player_id: str) -> Player:
        """
        Get player data from the fantasy service.
        
        Args:
            player_id: Player identifier
            
        Returns:
            Player instance with data
        """
        pass


class ESPNImporter(FantasyServiceImporter):
    """Importer for ESPN Fantasy Football."""
    
    def __init__(self):
        """Initialize ESPN importer."""
        self.authenticated = False
        self.league_id = None
        self.year = None
        self.espn_s2 = None
        self.swid = None
        self.league = None
    
    def authenticate(self, credentials: Dict[str, str]) -> bool:
        """
        Authenticate with ESPN Fantasy.
        
        Args:
            credentials: Dictionary with 'swid', 'espn_s2', 'league_id', and optional 'year' 
            
        Returns:
            True if authentication successful
        """
        if League is None:
            raise RuntimeError("espn-api package is not installed. Install it with: pip install espn-api")
        
        if 'swid' not in credentials or 'espn_s2' not in credentials or 'league_id' not in credentials:
            return False
        
        try:
            self.league_id = int(credentials['league_id'])
            self.year = int(credentials.get('year', 2024))
            self.espn_s2 = credentials['espn_s2']
            self.swid = credentials['swid']
            
            # Try to connect to the league to validate credentials
            self.league = League(
                league_id=self.league_id,
                year=self.year,
                espn_s2=self.espn_s2,
                swid=self.swid
            )
            
            # If we can access the teams list, authentication was successful
            _ = self.league.teams
            self.authenticated = True
            return True
        except Exception as e:
            # If any error occurs, authentication failed
            self.authenticated = False
            self.league = None
            return False
    
    def import_team(self, team_id: str) -> Team:
        """
        Import a team from ESPN Fantasy.
        
        Args:
            team_id: ESPN team ID
            
        Returns:
            Team instance
        """
        if not self.authenticated or self.league is None:
            raise RuntimeError("Not authenticated with ESPN")
        
        # Find the team in the league
        team_id_int = int(team_id)
        espn_team = None
        for team in self.league.teams:
            if team.team_id == team_id_int:
                espn_team = team
                break
        
        if espn_team is None:
            raise ValueError(f"Team {team_id} not found in league {self.league_id}")
        
        # Extract owner information
        owner_name = 'Unknown'
        if hasattr(espn_team, 'owners') and espn_team.owners:
            owner = espn_team.owners[0]
            if isinstance(owner, dict):
                owner_name = owner.get('displayName') or f"{owner.get('firstName', '')} {owner.get('lastName', '')}".strip() or 'Unknown'
        
        # Create Team instance
        team = Team(
            team_id=f"espn_{self.league_id}_{team_id}",
            name=espn_team.team_name,
            owner=owner_name,
            league_id=str(self.league_id),
            record={
                'wins': espn_team.wins,
                'losses': espn_team.losses,
                'ties': getattr(espn_team, 'ties', 0)
            },
            total_points=espn_team.points_for
        )
        
        # Import all players on the roster
        for espn_player in espn_team.roster:
            player = self._convert_espn_player(espn_player)
            team.add_player(player)
        
        return team
    
    def get_player_data(self, player_id: str) -> Player:
        """
        Get player data from ESPN.
        
        Args:
            player_id: ESPN player ID
            
        Returns:
            Player instance
        """
        if not self.authenticated or self.league is None:
            raise RuntimeError("Not authenticated with ESPN")
        
        # Search all teams for the player
        player_id_int = int(player_id)
        for team in self.league.teams:
            for espn_player in team.roster:
                if espn_player.playerId == player_id_int:
                    return self._convert_espn_player(espn_player)
        
        raise ValueError(f"Player {player_id} not found in league {self.league_id}")
    
    def _convert_espn_player(self, espn_player) -> Player:
        """
        Convert an ESPN player object to a Player instance.
        
        Args:
            espn_player: ESPN player object
            
        Returns:
            Player instance
        """
        return Player(
            player_id=f"espn_{espn_player.playerId}",
            name=espn_player.name,
            position=espn_player.position,
            team=espn_player.proTeam,
            projected_points=getattr(espn_player, 'projected_points', 0.0),
            actual_points=getattr(espn_player, 'points', 0.0),
            stats=getattr(espn_player, 'stats', {})
        )


class YahooImporter(FantasyServiceImporter):
    """Importer for Yahoo Fantasy Football."""
    
    def __init__(self):
        """Initialize Yahoo importer."""
        self.authenticated = False
        self.access_token = None
    
    def authenticate(self, credentials: Dict[str, str]) -> bool:
        """
        Authenticate with Yahoo Fantasy.
        
        Args:
            credentials: Dictionary with OAuth credentials
            
        Returns:
            True if authentication successful
        """
        # In a real implementation, this would use OAuth with Yahoo API
        if 'access_token' in credentials:
            self.authenticated = True
            self.access_token = credentials['access_token']
            return True
        return False
    
    def import_team(self, team_id: str) -> Team:
        """
        Import a team from Yahoo Fantasy.
        
        Args:
            team_id: Yahoo team ID
            
        Returns:
            Team instance
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Yahoo")
        
        # In a real implementation, this would fetch data from Yahoo API
        team = Team(
            team_id=team_id,
            name="Sample Yahoo Team",
            owner="Yahoo User",
        )
        return team
    
    def get_player_data(self, player_id: str) -> Player:
        """
        Get player data from Yahoo.
        
        Args:
            player_id: Yahoo player ID
            
        Returns:
            Player instance
        """
        if not self.authenticated:
            raise RuntimeError("Not authenticated with Yahoo")
        
        # In a real implementation, this would fetch data from Yahoo API
        return Player(
            player_id=player_id,
            name="Sample Player",
            position="WR",
            team="TBD",
        )


class ImporterFactory:
    """Factory for creating fantasy service importers."""
    
    @staticmethod
    def create_importer(service: str) -> FantasyServiceImporter:
        """
        Create an importer for the specified service.
        
        Args:
            service: Service name ('espn', 'yahoo', etc.)
            
        Returns:
            FantasyServiceImporter instance
            
        Raises:
            ValueError: If service is not supported
        """
        service = service.lower()
        if service == 'espn':
            return ESPNImporter()
        elif service == 'yahoo':
            return YahooImporter()
        else:
            raise ValueError(f"Unsupported fantasy service: {service}")
