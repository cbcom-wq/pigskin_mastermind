"""Player model for fantasy football players."""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class Player:
    """
    Represents a fantasy football player.
    
    Attributes:
        player_id: Unique identifier for the player
        name: Player's full name
        position: Player's position (QB, RB, WR, TE, K, DEF)
        team: NFL team abbreviation
        stats: Dictionary of player statistics
        projected_points: Projected fantasy points
        actual_points: Actual fantasy points scored
        db_id: Primary key of the matching DBPlayer, when converted from one.
            Templates use it to link to the player profile.
        bye_week: Week this player's NFL team is off, when known
        injury_status: ESPN injury designation (QUESTIONABLE, OUT, ...)
    """
    player_id: str
    name: str
    position: str
    team: str
    stats: Dict[str, Any] = field(default_factory=dict)
    projected_points: float = 0.0
    actual_points: float = 0.0
    headshot_url: Optional[str] = None
    db_id: Optional[int] = None
    bye_week: Optional[int] = None
    injury_status: Optional[str] = None

    def __post_init__(self):
        """Validate player data after initialization."""
        valid_positions = ['QB', 'RB', 'WR', 'TE', 'K', 'DEF']
        if self.position not in valid_positions:
            raise ValueError(f"Invalid position: {self.position}. Must be one of {valid_positions}")

    @property
    def is_out(self) -> bool:
        """True when the player is ruled out (OUT, IR, or suspended)."""
        return (self.injury_status or '').upper() in {
            'OUT', 'IR', 'INJURY_RESERVE', 'SUSPENSION',
        }
    
    def update_stats(self, stats: Dict[str, Any]) -> None:
        """
        Update player statistics.
        
        Args:
            stats: Dictionary of statistics to update
        """
        self.stats.update(stats)
    
    def calculate_points(self, scoring_settings: Optional[Dict[str, float]] = None) -> float:
        """
        Calculate fantasy points based on stats and scoring settings.
        
        Args:
            scoring_settings: Dictionary of scoring rules (e.g., {'pass_td': 4, 'rush_td': 6})
            
        Returns:
            Calculated fantasy points
        """
        if scoring_settings is None:
            from pigskin_mastermind.models.database import DEFAULT_SCORING_SETTINGS
            scoring_settings = DEFAULT_SCORING_SETTINGS
        
        points = 0.0
        for stat, value in self.stats.items():
            if stat in scoring_settings:
                points += value * scoring_settings[stat]
        
        self.actual_points = points
        return points
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert player to dictionary representation."""
        return {
            'player_id': self.player_id,
            'name': self.name,
            'position': self.position,
            'team': self.team,
            'stats': self.stats,
            'projected_points': self.projected_points,
            'actual_points': self.actual_points,
            'headshot_url': self.headshot_url,
        }
