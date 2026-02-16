import sys
import os
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime

# Add ESPN API to path
ESPN_API_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "lib", "espn-api"
)
if ESPN_API_PATH not in sys.path:
    sys.path.insert(0, ESPN_API_PATH)

from espn_api.football import League

from pigskin_mastermind.models.database import DBTeam, DBPlayer, DBLeague


class ESPNSyncService:
    """Service for syncing data with ESPN Fantasy API"""

    def __init__(self, db: Session):
        self.db = db

    def import_team(
        self,
        league_id: str,
        team_id: int,
        espn_s2: str,
        swid: str,
        year: int = 2024
    ) -> DBTeam:
        """Import a team from ESPN Fantasy"""
        league = League(
            league_id=int(league_id),
            year=year,
            espn_s2=espn_s2,
            swid=swid
        )

        espn_team = None
        for team in league.teams:
            if team.team_id == team_id:
                espn_team = team
                break

        if not espn_team:
            raise ValueError(f"Team {team_id} not found in league {league_id}")

        # Create or update team
        db_team = self.db.query(DBTeam).filter_by(espn_team_id=str(team_id)).first()
        if not db_team:
            db_team = DBTeam(
                team_id=f"espn_{league_id}_{team_id}",
                espn_team_id=str(team_id),
                league_id=league_id
            )
            self.db.add(db_team)

        db_team.name = espn_team.team_name
        db_team.owner = espn_team.owner
        db_team.wins = espn_team.wins
        db_team.losses = espn_team.losses
        db_team.ties = getattr(espn_team, 'ties', 0)
        db_team.total_points = espn_team.points_for
        db_team.last_synced_at = datetime.utcnow()

        self.db.commit()

        # Import players
        for espn_player in espn_team.roster:
            self._import_player(espn_player, db_team.id)

        self.db.commit()
        self.db.refresh(db_team)

        return db_team

    def _import_player(self, espn_player: Any, team_db_id: int) -> DBPlayer:
        """Import a single player"""
        player_id = f"espn_{espn_player.playerId}"

        db_player = self.db.query(DBPlayer).filter_by(player_id=player_id).first()
        if not db_player:
            db_player = DBPlayer(player_id=player_id)
            self.db.add(db_player)

        db_player.name = espn_player.name
        db_player.position = espn_player.position
        db_player.nfl_team = espn_player.proTeam
        db_player.projected_points = getattr(espn_player, 'projected_points', 0.0)
        db_player.actual_points = getattr(espn_player, 'points', 0.0)
        db_player.stats = getattr(espn_player, 'stats', {})
        db_player.team_id = team_db_id

        return db_player

    def sync_team(self, team_db_id: int) -> DBTeam:
        """Sync an existing team from ESPN"""
        db_team = self.db.query(DBTeam).filter_by(id=team_db_id).first()
        if not db_team:
            raise ValueError(f"Team {team_db_id} not found")

        if not db_team.espn_team_id:
            raise ValueError(f"Team {team_db_id} is not linked to ESPN")

        db_league = self.db.query(DBLeague).filter_by(league_id=db_team.league_id).first()
        if not db_league:
            raise ValueError(f"League credentials not found for {db_team.league_id}")

        return self.import_team(
            league_id=db_team.league_id,
            team_id=int(db_team.espn_team_id),
            espn_s2=db_league.espn_s2,
            swid=db_league.swid,
            year=db_league.year
        )
