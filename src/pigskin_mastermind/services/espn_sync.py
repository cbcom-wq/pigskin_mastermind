import sys
import os
from typing import Optional, Dict, Any, List
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

from pigskin_mastermind.models.database import (
    DBTeam, DBPlayer, DBLeague, DBWeeklyTeamStats, DBWeeklyPlayerStats,
    DBPlayerGameLog, DBPlayerSeasonStats
)
from pigskin_mastermind.services.espn_stats_mapper import map_espn_breakdown_to_stats


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
        # Get owner name(s) from the owners list
        owners = getattr(espn_team, 'owners', [])
        if owners and isinstance(owners[0], dict):
            owner_info = owners[0]
            db_team.owner = owner_info.get('displayName') or f"{owner_info.get('firstName', '')} {owner_info.get('lastName', '')}".strip() or 'Unknown'
        else:
            db_team.owner = 'Unknown'
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

    def import_weekly_stats(
        self,
        league_id: str,
        team_id: int,
        espn_s2: str,
        swid: str,
        year: int = 2024
    ) -> int:
        """Import weekly stats for all completed weeks for a team.

        Uses ESPN box_scores API to get per-week player lineups and points.
        Returns the number of weeks imported.
        """
        league = League(
            league_id=int(league_id),
            year=year,
            espn_s2=espn_s2,
            swid=swid
        )

        # Find the DB team
        db_team = self.db.query(DBTeam).filter_by(
            espn_team_id=str(team_id),
            league_id=league_id
        ).first()
        if not db_team:
            raise ValueError(
                f"Team {team_id} in league {league_id} not found in database. "
                "Import the team first."
            )

        # Find the ESPN team object to get schedule/outcomes
        espn_team = None
        for t in league.teams:
            if t.team_id == team_id:
                espn_team = t
                break
        if not espn_team:
            raise ValueError(f"Team {team_id} not found in ESPN league {league_id}")

        current_week = league.current_week
        weeks_imported = 0

        for week in range(1, current_week + 1):
            try:
                box_scores = league.box_scores(week=week)
            except Exception:
                continue

            # Find the box score matching our team
            team_box = None
            is_home = False
            for box in box_scores:
                home_id = box.home_team.team_id if hasattr(box.home_team, 'team_id') else box.home_team
                away_id = box.away_team.team_id if hasattr(box.away_team, 'team_id') else box.away_team
                if home_id == team_id:
                    team_box = box
                    is_home = True
                    break
                elif away_id == team_id:
                    team_box = box
                    is_home = False
                    break

            if not team_box:
                continue

            # Extract team-level data
            if is_home:
                points_for = team_box.home_score
                points_against = team_box.away_score
                projected = team_box.home_projected
                lineup = team_box.home_lineup
                opponent = team_box.away_team
            else:
                points_for = team_box.away_score
                points_against = team_box.home_score
                projected = team_box.away_projected
                lineup = team_box.away_lineup
                opponent = team_box.home_team

            opponent_name = opponent.team_name if hasattr(opponent, 'team_name') else str(opponent)

            # Determine result from the espn_team outcomes list
            result = 'U'
            if week <= len(espn_team.outcomes):
                result = espn_team.outcomes[week - 1]

            # Upsert weekly team stats
            db_weekly = self.db.query(DBWeeklyTeamStats).filter_by(
                team_id=db_team.id, week=week
            ).first()
            if not db_weekly:
                db_weekly = DBWeeklyTeamStats(
                    team_id=db_team.id,
                    week=week
                )
                self.db.add(db_weekly)

            db_weekly.points_for = points_for
            db_weekly.points_against = points_against
            db_weekly.projected_points = projected
            db_weekly.opponent_name = opponent_name
            db_weekly.result = result
            db_weekly.updated_at = datetime.utcnow()

            self.db.flush()  # ensure db_weekly.id is available

            # Import player stats for this week
            self._import_weekly_player_stats(lineup, db_weekly)

            weeks_imported += 1

        self.db.commit()
        return weeks_imported

    def _import_weekly_player_stats(
        self,
        lineup: List[Any],
        db_weekly: DBWeeklyTeamStats
    ) -> None:
        """Import player-level stats for a single week."""
        for box_player in lineup:
            player_id = f"espn_{box_player.playerId}"

            # Find or create the base player record
            db_player = self.db.query(DBPlayer).filter_by(player_id=player_id).first()
            if not db_player:
                db_player = DBPlayer(
                    player_id=player_id,
                    name=box_player.name,
                    position=box_player.position,
                    nfl_team=box_player.proTeam,
                    team_id=db_weekly.team_id
                )
                self.db.add(db_player)
                self.db.flush()

            # Upsert the weekly player stats
            db_wp = self.db.query(DBWeeklyPlayerStats).filter_by(
                player_id=db_player.id,
                weekly_team_stats_id=db_weekly.id
            ).first()
            if not db_wp:
                db_wp = DBWeeklyPlayerStats(
                    player_id=db_player.id,
                    weekly_team_stats_id=db_weekly.id,
                    week=db_weekly.week
                )
                self.db.add(db_wp)

            db_wp.slot_position = getattr(box_player, 'slot_position', None)
            db_wp.projected_points = getattr(box_player, 'projected_points', 0.0)
            db_wp.actual_points = getattr(box_player, 'points', 0.0)
            db_wp.stats = {
                'breakdown': getattr(box_player, 'breakdown', {}),
                'points_breakdown': getattr(box_player, 'points_breakdown', {}),
            }
            db_wp.updated_at = datetime.utcnow()

    def sync_weekly_stats(self, team_db_id: int) -> int:
        """Sync weekly stats for an existing team from ESPN.

        Returns number of weeks imported.
        """
        db_team = self.db.query(DBTeam).filter_by(id=team_db_id).first()
        if not db_team:
            raise ValueError(f"Team {team_db_id} not found")

        if not db_team.espn_team_id:
            raise ValueError(f"Team {team_db_id} is not linked to ESPN")

        db_league = self.db.query(DBLeague).filter_by(league_id=db_team.league_id).first()
        if not db_league:
            raise ValueError(f"League credentials not found for {db_team.league_id}")

        return self.import_weekly_stats(
            league_id=db_team.league_id,
            team_id=int(db_team.espn_team_id),
            espn_s2=db_league.espn_s2,
            swid=db_league.swid,
            year=db_league.year
        )

    def import_weekly_stats_multi_season(
        self,
        league_id: str,
        team_id: int,
        espn_s2: str,
        swid: str,
        years: Optional[List[int]] = None,
    ) -> Dict[int, int]:
        """Import weekly stats across multiple seasons.

        Args:
            years: List of years to import. Defaults to current year only.

        Returns:
            Dict mapping year to number of weeks imported for that year.
        """
        if years is None:
            years = [2024]

        results = {}
        for year in years:
            try:
                # Ensure team exists for this year (re-import team data)
                self.import_team(
                    league_id=league_id,
                    team_id=team_id,
                    espn_s2=espn_s2,
                    swid=swid,
                    year=year,
                )
                weeks = self.import_weekly_stats(
                    league_id=league_id,
                    team_id=team_id,
                    espn_s2=espn_s2,
                    swid=swid,
                    year=year,
                )
                results[year] = weeks

                # Parse game logs from the imported weekly stats
                self._build_game_logs_from_weekly(
                    league_id=league_id,
                    team_id=team_id,
                    year=year,
                )
                # Aggregate season stats
                self._aggregate_season_stats(
                    league_id=league_id,
                    team_id=team_id,
                    year=year,
                )
            except Exception as e:
                results[year] = 0

        return results

    def _build_game_logs_from_weekly(
        self,
        league_id: str,
        team_id: int,
        year: int,
    ) -> int:
        """Parse weekly player stats breakdowns into structured game log rows.

        Returns:
            Number of game log rows created/updated.
        """
        db_team = self.db.query(DBTeam).filter_by(
            espn_team_id=str(team_id),
            league_id=league_id,
        ).first()
        if not db_team:
            return 0

        count = 0
        weekly_stats_rows = (
            self.db.query(DBWeeklyTeamStats)
            .filter_by(team_id=db_team.id)
            .all()
        )

        for weekly_team in weekly_stats_rows:
            player_stats = (
                self.db.query(DBWeeklyPlayerStats)
                .filter_by(weekly_team_stats_id=weekly_team.id)
                .all()
            )
            for wp in player_stats:
                breakdown = {}
                if wp.stats and isinstance(wp.stats, dict):
                    breakdown = wp.stats.get('breakdown', {})

                parsed = map_espn_breakdown_to_stats(breakdown)

                # Upsert game log
                game_log = self.db.query(DBPlayerGameLog).filter_by(
                    player_id=wp.player_id,
                    year=year,
                    week=weekly_team.week,
                ).first()
                if not game_log:
                    game_log = DBPlayerGameLog(
                        player_id=wp.player_id,
                        year=year,
                        week=weekly_team.week,
                    )
                    self.db.add(game_log)

                game_log.opponent = weekly_team.opponent_name
                game_log.pass_att = parsed.get('pass_att', 0)
                game_log.pass_cmp = parsed.get('pass_cmp', 0)
                game_log.pass_yd = parsed.get('pass_yd', 0)
                game_log.pass_td = parsed.get('pass_td', 0)
                game_log.pass_int = parsed.get('pass_int', 0)
                game_log.rush_att = parsed.get('rush_att', 0)
                game_log.rush_yd = parsed.get('rush_yd', 0)
                game_log.rush_td = parsed.get('rush_td', 0)
                game_log.targets = parsed.get('targets', 0)
                game_log.rec = parsed.get('rec', 0)
                game_log.rec_yd = parsed.get('rec_yd', 0)
                game_log.rec_td = parsed.get('rec_td', 0)
                game_log.fumbles = parsed.get('fumbles', 0)
                game_log.fumbles_lost = parsed.get('fumbles_lost', 0)
                game_log.two_pt_conversions = parsed.get('two_pt_conversions', 0)
                game_log.fantasy_points = wp.actual_points or 0.0
                game_log.source = 'espn'
                game_log.updated_at = datetime.utcnow()
                count += 1

        self.db.commit()
        return count

    def _aggregate_season_stats(
        self,
        league_id: str,
        team_id: int,
        year: int,
    ) -> int:
        """Compute season aggregates from game logs for all players on a team.

        Returns:
            Number of season stat rows created/updated.
        """
        db_team = self.db.query(DBTeam).filter_by(
            espn_team_id=str(team_id),
            league_id=league_id,
        ).first()
        if not db_team:
            return 0

        players = self.db.query(DBPlayer).filter_by(team_id=db_team.id).all()
        count = 0

        for player in players:
            logs = (
                self.db.query(DBPlayerGameLog)
                .filter_by(player_id=player.id, year=year)
                .all()
            )
            if not logs:
                continue

            season = self.db.query(DBPlayerSeasonStats).filter_by(
                player_id=player.id, year=year
            ).first()
            if not season:
                season = DBPlayerSeasonStats(player_id=player.id, year=year)
                self.db.add(season)

            season.games_played = len(logs)
            season.pass_att = sum(g.pass_att for g in logs)
            season.pass_cmp = sum(g.pass_cmp for g in logs)
            season.pass_yd = sum(g.pass_yd for g in logs)
            season.pass_td = sum(g.pass_td for g in logs)
            season.pass_int = sum(g.pass_int for g in logs)
            season.rush_att = sum(g.rush_att for g in logs)
            season.rush_yd = sum(g.rush_yd for g in logs)
            season.rush_td = sum(g.rush_td for g in logs)
            season.rush_fumbles = sum(g.fumbles_lost for g in logs)
            season.targets = sum(g.targets for g in logs)
            season.rec = sum(g.rec for g in logs)
            season.rec_yd = sum(g.rec_yd for g in logs)
            season.rec_td = sum(g.rec_td for g in logs)
            season.fantasy_points_total = sum(g.fantasy_points for g in logs)
            season.fantasy_points_avg = (
                season.fantasy_points_total / season.games_played
                if season.games_played > 0 else 0.0
            )
            total_touches = season.rush_att + season.rec
            season.fantasy_points_per_touch = (
                season.fantasy_points_total / total_touches
                if total_touches > 0 else 0.0
            )

            # Passer rating calculation (simplified NFL formula)
            if season.pass_att > 0:
                comp_pct = season.pass_cmp / season.pass_att
                td_pct = season.pass_td / season.pass_att
                int_pct = season.pass_int / season.pass_att
                ypa = season.pass_yd / season.pass_att
                a = max(0, min(2.375, (comp_pct - 0.3) * 5))
                b = max(0, min(2.375, (ypa - 3) * 0.25))
                c = max(0, min(2.375, td_pct * 20))
                d = max(0, min(2.375, 2.375 - (int_pct * 25)))
                season.pass_rating = ((a + b + c + d) / 6) * 100
            else:
                season.pass_rating = 0.0

            season.source = 'espn'
            season.updated_at = datetime.utcnow()
            count += 1

        self.db.commit()
        return count

    def refresh_current_week(self, team_db_id: int) -> Dict[str, Any]:
        """Re-fetch only current week's box scores for active game tracking.

        Args:
            team_db_id: Database ID of the team to refresh.

        Returns:
            Dict with week number, number of players updated, and active game count.
        """
        db_team = self.db.query(DBTeam).filter_by(id=team_db_id).first()
        if not db_team:
            raise ValueError(f"Team {team_db_id} not found")
        if not db_team.espn_team_id:
            raise ValueError(f"Team {team_db_id} is not linked to ESPN")

        db_league = self.db.query(DBLeague).filter_by(league_id=db_team.league_id).first()
        if not db_league:
            raise ValueError(f"League credentials not found for {db_team.league_id}")

        league = League(
            league_id=int(db_team.league_id),
            year=db_league.year,
            espn_s2=db_league.espn_s2,
            swid=db_league.swid,
        )

        current_week = league.current_week
        espn_team_id = int(db_team.espn_team_id)

        try:
            box_scores = league.box_scores(week=current_week)
        except Exception as e:
            raise ValueError(f"Failed to fetch box scores: {e}")

        team_box = None
        is_home = False
        for box in box_scores:
            home_id = box.home_team.team_id if hasattr(box.home_team, 'team_id') else box.home_team
            away_id = box.away_team.team_id if hasattr(box.away_team, 'team_id') else box.away_team
            if home_id == espn_team_id:
                team_box = box
                is_home = True
                break
            elif away_id == espn_team_id:
                team_box = box
                is_home = False
                break

        if not team_box:
            return {"week": current_week, "players_updated": 0, "active_games": 0}

        lineup = team_box.home_lineup if is_home else team_box.away_lineup
        players_updated = 0
        active_games = 0

        for box_player in lineup:
            player_id = f"espn_{box_player.playerId}"
            db_player = self.db.query(DBPlayer).filter_by(player_id=player_id).first()
            if not db_player:
                continue

            game_played = getattr(box_player, 'game_played', 0)
            is_active = 0 < game_played < 100

            # Update game log
            game_log = self.db.query(DBPlayerGameLog).filter_by(
                player_id=db_player.id,
                year=db_league.year,
                week=current_week,
            ).first()

            breakdown = getattr(box_player, 'breakdown', {})
            parsed = map_espn_breakdown_to_stats(breakdown)

            if not game_log:
                game_log = DBPlayerGameLog(
                    player_id=db_player.id,
                    year=db_league.year,
                    week=current_week,
                )
                self.db.add(game_log)

            game_log.pass_att = parsed.get('pass_att', 0)
            game_log.pass_cmp = parsed.get('pass_cmp', 0)
            game_log.pass_yd = parsed.get('pass_yd', 0)
            game_log.pass_td = parsed.get('pass_td', 0)
            game_log.pass_int = parsed.get('pass_int', 0)
            game_log.rush_att = parsed.get('rush_att', 0)
            game_log.rush_yd = parsed.get('rush_yd', 0)
            game_log.rush_td = parsed.get('rush_td', 0)
            game_log.targets = parsed.get('targets', 0)
            game_log.rec = parsed.get('rec', 0)
            game_log.rec_yd = parsed.get('rec_yd', 0)
            game_log.rec_td = parsed.get('rec_td', 0)
            game_log.fumbles = parsed.get('fumbles', 0)
            game_log.fumbles_lost = parsed.get('fumbles_lost', 0)
            game_log.fantasy_points = getattr(box_player, 'points', 0.0)
            game_log.is_active_game = is_active
            game_log.source = 'espn'
            game_log.updated_at = datetime.utcnow()

            players_updated += 1
            if is_active:
                active_games += 1

        self.db.commit()
        return {
            "week": current_week,
            "players_updated": players_updated,
            "active_games": active_games,
        }
