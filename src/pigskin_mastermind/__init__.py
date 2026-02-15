"""
Pigskin Mastermind - Fantasy Football Research, Entertainment, and Management Application

This package provides tools for managing fantasy football teams, including:
- Importing teams from fantasy football services
- Viewing and analyzing player data
- Making informed decisions about lineups and trades
- Entertainment features for your team and league
"""

__version__ = "0.1.0"
__author__ = "Pigskin Mastermind Team"

from pigskin_mastermind.models.team import Team
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.services.team_manager import TeamManager

__all__ = ["Team", "Player", "TeamManager", "__version__"]
