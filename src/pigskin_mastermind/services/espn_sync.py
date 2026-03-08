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

# ESPN stat ID → internal scoring key mapping
ESPN_STAT_ID_TO_SCORING_KEY = {
    3: "pass_yd",
    4: "pass_td",
    20: "pass_int",
    24: "rush_yd",
    25: "rush_td",
    41: "rec",
    42: "rec_yd",
    43: "rec_td",
    72: "fumbles_lost",
    62: "two_pt",
}

# Per-position player limits used by the projection-tuner import.
# ESPN returns free_agents ordered by ownership/projected points (de-facto ADP
# proxy), so slicing to these counts gives a relevant, manageable pool.
TUNER_PLAYER_LIMITS: Dict[str, int] = {
    "QB": 36,   # ~1 starter + 1 backup per NFL team
    "RB": 72,   # 2+ starters + handcuffs + flex options
    "WR": 80,   # 2–3 per team across 32 teams + flex
    "TE": 40,   # 1–2 per team + streaming options
}

# Fetch this many times more candidates than the final cap so we can rank
# locally by actual production and drop zero-point / inactive players.
TUNER_CANDIDATE_MULTIPLIER: int = 2


class ESPNSyncService:
    """Service for syncing data with ESPN Fantasy API"""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def extract_scoring_settings(espn_league) -> dict:
        """Extract scoring settings from an ESPN League object.

        Maps ESPN scoring_format stat IDs to our internal keys.
        Returns a dict like {'pass_yd': 0.04, 'rec': 0.5, ...}.
        """
        settings = {}
        scoring_format = getattr(
            getattr(espn_league, "settings", None), "scoring_format", None
        )
        if not scoring_format:
            return settings
        for item in scoring_format:
            stat_id = item.get("id")
            if stat_id in ESPN_STAT_ID_TO_SCORING_KEY:
                key = ESPN_STAT_ID_TO_SCORING_KEY[stat_id]
                settings[key] = item.get("points", 0)
        return settings

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

        # Set headshot URL from ESPN CDN (only if not already set by nfl_data_py)
        if not db_player.headshot_url:
            espn_id = espn_player.playerId
            if espn_player.position == 'D/ST' or espn_player.position == 'DEF':
                # Use ESPN team logo for defenses
                pro_team_id = getattr(espn_player, 'proTeamId', None)
                if pro_team_id:
                    db_player.headshot_url = f"https://a.espncdn.com/i/teamlogos/nfl/500/{espn_player.proTeam.lower()}.png"
            else:
                db_player.headshot_url = f"https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"

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
            db_wp.espn_slot_position = getattr(box_player, 'slot_position', None)
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
            total_touches = season.pass_att + season.rush_att + season.rec
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

    def import_all_players(
        self,
        league_id: str,
        espn_s2: str,
        swid: str,
        year: int = 2024,
        week: int = None,
        positions: Optional[List[str]] = None,
        batch_size: int = 500
    ) -> int:
        """Import all available players from ESPN (including free agents).

        Args:
            league_id: ESPN league ID
            espn_s2: ESPN S2 authentication cookie
            swid: ESPN SWID authentication cookie
            year: Season year
            week: Week to fetch player data for (defaults to current week)
            positions: List of positions to import (e.g., ['QB', 'RB', 'WR', 'TE']).
                      If None, imports all positions.
            batch_size: Number of players to fetch per batch (max 500)

        Returns:
            Number of players imported/updated
        """
        league = League(
            league_id=int(league_id),
            year=year,
            espn_s2=espn_s2,
            swid=swid
        )

        if not week:
            week = league.current_week

        # Get all positions if not specified
        if positions is None:
            positions = ['QB', 'RB', 'WR', 'TE', 'K', 'D/ST']

        players_imported = 0

        # Fetch free agents for each position
        for position in positions:
            try:
                free_agents = league.free_agents(
                    week=week,
                    size=batch_size,
                    position=position
                )

                for espn_player in free_agents:
                    self._import_player_from_box(espn_player, team_db_id=None)
                    players_imported += 1

            except Exception as e:
                # Log error but continue with other positions
                print(f"Error importing {position} players: {e}")
                continue

        self.db.commit()
        return players_imported

    def import_all_players_full_season(
        self,
        league_id: str,
        espn_s2: str,
        swid: str,
        year: int = 2024,
        positions: Optional[List[str]] = None,
        batch_size: int = 500,
        progress_callback=None,
    ) -> Dict[str, int]:
        """Import all players with stats for every week of the season.

        ESPN's ``free_agents`` endpoint only returns breakdown stats for the
        single week requested.  This method loops through every completed week
        so that each player's JSON ``stats`` dict ends up with per-week
        breakdowns for the full season, then populates the structured
        ``player_game_logs`` and ``player_season_stats`` tables.

        Args:
            league_id: ESPN league ID
            espn_s2: ESPN S2 authentication cookie
            swid: ESPN SWID authentication cookie
            year: Season year
            positions: Positions to import.  Defaults to all.
            batch_size: Number of players per ESPN API page.
            progress_callback: Optional callable(week, total_weeks) for UI.

        Returns:
            Dict with 'players', 'game_logs', 'season_stats' counts.
        """
        league = League(
            league_id=int(league_id),
            year=year,
            espn_s2=espn_s2,
            swid=swid,
        )

        total_weeks = league.current_week
        if positions is None:
            positions = ['QB', 'RB', 'WR', 'TE', 'K', 'D/ST']

        seen_player_ids: set = set()

        for week in range(1, total_weeks + 1):
            if progress_callback:
                progress_callback(week, total_weeks)
            for position in positions:
                try:
                    free_agents = league.free_agents(
                        week=week, size=batch_size, position=position,
                    )
                    for espn_player in free_agents:
                        db_player = self._import_player_from_box(
                            espn_player, team_db_id=None,
                        )
                        seen_player_ids.add(db_player.player_id)
                except Exception as e:
                    print(f"Error importing {position} week {week}: {e}")
                    continue
            # Commit per week to avoid holding a huge transaction
            self.db.commit()

        # Now populate structured tables from the merged JSON
        result = self.populate_stats_from_player_json(year=year)
        result['players'] = len(seen_player_ids)
        return result

    @staticmethod
    def _rank_espn_candidates(
        candidates: list,
        keep: int,
    ) -> list:
        """Rank a pool of ESPN player objects by actual production and return
        the top *keep* players.

        Sorting priority (descending):
        1. ``total_points`` – season actual fantasy points (primary signal)
        2. ``projected_total_points`` – ESPN projected season total
        3. ``percent_owned`` – ownership % as a popularity tiebreaker
        4. ``posRank`` ascending – ESPN positional rank (lower = better)

        Players with an ``injuryStatus`` of ``'OUT'`` or ``'IR'`` **and** zero
        actual points are pushed to the bottom so they only fill remaining
        slots if not enough healthy producers exist.
        """
        def _sort_key(p):
            total = getattr(p, 'total_points', 0) or 0
            proj = getattr(p, 'projected_total_points', 0) or 0
            own = getattr(p, 'percent_owned', 0) or 0
            pos_rank = getattr(p, 'posRank', 9999) or 9999
            injury = getattr(p, 'injuryStatus', '') or ''
            # Demote OUT/IR players that have zero actual production
            is_demoted = 1 if (injury.upper() in ('OUT', 'IR', 'INJURY_RESERVE') and total <= 0) else 0
            # Sort: demoted last, then by total desc, proj desc, own desc, posRank asc
            return (is_demoted, -total, -proj, -own, pos_rank)

        ranked = sorted(candidates, key=_sort_key)
        return ranked[:keep]

    def import_relevant_players_for_tuner(
        self,
        league_id: str,
        espn_s2: str,
        swid: str,
        year: int = 2025,
        week: int = None,
        limits: Optional[Dict[str, int]] = None,
        preload_full_history: bool = True,
        progress_callback=None,
        log_callback=None,
    ) -> Dict[str, Any]:
        """Import a curated set of relevant skill-position players for the projection tuner.

        Uses ESPN's free_agents endpoint, which returns players ordered by
        ownership/projected points (a de-facto ADP/relevance proxy), and
        fetches the top-N players per position.  Unlike a full league sync that
        only captures rostered players, this populates a broader pool that spans
        all NFL teams so the tuner can operate beyond your own league's roster.

        After fetching, the raw ESPN JSON blob is converted to structured
        ``DBPlayerSeasonStats`` rows (via :meth:`populate_stats_from_player_json`)
        so the players appear in :func:`active_players_query`.

        Args:
            league_id: ESPN league ID (used for API authentication only).
            espn_s2: ESPN S2 authentication cookie.
            swid: ESPN SWID authentication cookie.
            year: Season year to import.
            week: Scoring week for the ESPN API call (defaults to current week).
                  The season-aggregate key ``'0'`` is always included regardless
                  of which week is requested.
            limits: Per-position player counts, e.g. ``{"QB": 36, "RB": 72}``.
                    Defaults to :data:`TUNER_PLAYER_LIMITS`.

        Returns:
            Dict with per-position import counts plus totals::

                {"QB": 36, "RB": 72, "WR": 80, "TE": 40,
                 "total": 228, "season_stats": 215}
        """
        def emit_progress(progress_pct: int, message: str) -> None:
            if progress_callback:
                progress_callback(max(0, min(100, int(progress_pct))), message)

        def emit_log(message: str) -> None:
            if log_callback:
                log_callback(message)

        if limits is None:
            limits = TUNER_PLAYER_LIMITS

        league = League(
            league_id=int(league_id),
            year=year,
            espn_s2=espn_s2,
            swid=swid,
        )

        if week is None:
            week = league.current_week

        per_position_counts: Dict[str, int] = {}
        total_players = 0
        imported_player_ids: List[int] = []
        history_players = 0
        history_game_logs = 0
        history_season_stats = 0

        emit_progress(2, f"Connecting to ESPN for {year} player pool…")
        emit_log(
            f"Starting ESPN preload for {year}: "
            + ", ".join(f"{pos}={limit}" for pos, limit in limits.items())
        )

        positions = list(limits.items())
        for idx, (position, limit) in enumerate(positions, start=1):
            count = 0
            fetch_size = limit * TUNER_CANDIDATE_MULTIPLIER
            emit_progress(
                5 + int(((idx - 1) / max(len(positions), 1)) * 15),
                f"Importing {position} player pool ({idx}/{len(positions)})…",
            )
            try:
                candidates = league.free_agents(
                    week=week,
                    size=fetch_size,
                    position=position,
                )
                # Rank locally by actual production and keep only top N
                ranked = self._rank_espn_candidates(candidates, keep=limit)
                skipped = len(candidates) - len(ranked)
                for espn_player in ranked:
                    db_player = self._import_player_from_box(espn_player, team_db_id=None)
                    self.db.flush()
                    if db_player.id is not None:
                        imported_player_ids.append(db_player.id)
                    count += 1
                emit_log(
                    f"Imported {count} {position} players "
                    f"(fetched {len(candidates)}, ranked & kept top {limit}, "
                    f"dropped {skipped} low-production/inactive)."
                )
            except Exception as exc:
                emit_log(f"{position} import failed: {exc}")
                print(f"[import_relevant_players_for_tuner] {position}: {exc}")
            per_position_counts[position] = count
            total_players += count

        self.db.commit()

        unique_player_ids = list(dict.fromkeys(imported_player_ids))

        if preload_full_history and unique_player_ids:
            emit_log(f"Preloading full player history for {len(unique_player_ids)} players…")
            for idx, player_id in enumerate(unique_player_ids, start=1):
                player = self.db.query(DBPlayer).filter_by(id=player_id).first()
                player_name = player.name if player else f"player {player_id}"
                emit_progress(
                    20 + int((idx / max(len(unique_player_ids), 1)) * 70),
                    f"Fetching full history for {player_name} ({idx}/{len(unique_player_ids)})…",
                )
                try:
                    result = self.fetch_player_full_stats(
                        db_player_id=player_id,
                        league_id=league_id,
                        espn_s2=espn_s2,
                        swid=swid,
                        year=year,
                        league=league,
                    )
                    history_players += 1
                    history_game_logs += result.get("game_logs", 0)
                    history_season_stats += result.get("season_stats", 0)
                    emit_log(
                        f"[{idx}/{len(unique_player_ids)}] {player_name}: "
                        f"{result.get('game_logs', 0)} game logs, "
                        f"{result.get('season_stats', 0)} season rows."
                    )
                except Exception as exc:
                    emit_log(
                        f"[{idx}/{len(unique_player_ids)}] {player_name}: history fetch failed ({exc})"
                    )

        # Convert the raw ESPN JSON blobs → structured DBPlayerSeasonStats rows
        # so active_players_query can surface them in the tuner.
        emit_progress(95, "Finalizing structured stats…")
        stats_result = self.populate_stats_from_player_json(year=year)
        emit_progress(100, f"Imported and preloaded {total_players} ESPN players.")
        emit_log(
            f"Completed import: {total_players} players, {history_game_logs} game logs, "
            f"{stats_result.get('season_stats', 0)} season stats rows."
        )

        return {
            **per_position_counts,
            "total": total_players,
            "preloaded_players": history_players,
            "history_game_logs": history_game_logs,
            "history_season_stats": history_season_stats,
            "season_stats": stats_result.get("season_stats", 0),
        }

    def fetch_player_full_stats(
        self,
        db_player_id: int,
        league_id: str,
        espn_s2: str,
        swid: str,
        year: int = 2025,
        league: Any = None,
    ) -> Dict[str, int]:
        """Fetch full per-week stats for a single player from ESPN.

        Uses ``league.player_info(playerId=X)`` which returns all weekly
        breakdowns in one API call via the ``kona_playercard`` view, then
        writes the results into ``player_game_logs`` and
        ``player_season_stats``.

        Args:
            db_player_id: Internal database ID of the player.
            league_id: ESPN league ID.
            espn_s2: ESPN authentication cookie.
            swid: ESPN SWID cookie.
            year: Season year.

        Returns:
            Dict with 'game_logs' and 'season_stats' counts.
        """
        db_player = self.db.query(DBPlayer).filter_by(id=db_player_id).first()
        if not db_player:
            return {"game_logs": 0, "season_stats": 0}

        # Extract ESPN integer ID from our "espn_XXXXX" player_id
        if not db_player.player_id or not db_player.player_id.startswith("espn_"):
            return {"game_logs": 0, "season_stats": 0}
        espn_id = int(db_player.player_id.replace("espn_", ""))

        if league is None:
            league = League(
                league_id=int(league_id),
                year=year,
                espn_s2=espn_s2,
                swid=swid,
            )

        espn_player = league.player_info(playerId=espn_id)
        if not espn_player:
            return {"game_logs": 0, "season_stats": 0}

        # Update the raw stats JSON on the player record (all weeks)
        raw_stats = getattr(espn_player, 'stats', {})
        if raw_stats:
            merged = db_player.stats if db_player.stats else {}
            for k, v in raw_stats.items():
                merged[str(k)] = v
            db_player.stats = merged
            self.db.flush()

        # Extract schedule for opponent info
        schedule = getattr(espn_player, 'schedule', {})

        # Now parse the updated JSON into structured tables for this player
        return self._populate_single_player_stats(db_player, year, schedule=schedule)

    def _populate_single_player_stats(
        self,
        player: DBPlayer,
        year: int,
        schedule: Optional[Dict] = None,
    ) -> Dict[str, int]:
        """Parse the raw stats JSON for a single player into game logs and
        season stats rows.

        Args:
            player: The DB player record.
            year: Season year.
            schedule: Optional ESPN schedule dict mapping week str to
                      ``{'team': 'OPP', 'date': datetime}``.

        Returns:
            Dict with 'game_logs' and 'season_stats' counts.
        """
        raw = player.stats
        if not raw or not isinstance(raw, dict):
            return {"game_logs": 0, "season_stats": 0}

        game_log_count = 0

        # ---- per-week game logs ----
        for week_key, week_data in raw.items():
            if week_key == '0':
                continue
            try:
                week_num = int(week_key)
            except (ValueError, TypeError):
                continue

            breakdown = week_data.get('breakdown', {}) if isinstance(week_data, dict) else {}
            if not breakdown:
                continue

            parsed = map_espn_breakdown_to_stats(breakdown)
            points = week_data.get('points', 0.0) if isinstance(week_data, dict) else 0.0

            game_log = self.db.query(DBPlayerGameLog).filter_by(
                player_id=player.id, year=year, week=week_num,
            ).first()
            if not game_log:
                game_log = DBPlayerGameLog(
                    player_id=player.id, year=year, week=week_num,
                )
                self.db.add(game_log)

            # Set opponent from schedule if available
            if schedule and week_key in schedule:
                game_log.opponent = schedule[week_key].get('team', '')
            elif schedule and str(week_num) in schedule:
                game_log.opponent = schedule[str(week_num)].get('team', '')

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
            game_log.fantasy_points = float(points) if points else 0.0
            game_log.source = 'espn'
            game_log.updated_at = datetime.utcnow()
            game_log_count += 1

        # ---- season aggregate from key '0' ----
        season_count = 0
        season_data = raw.get('0')
        if season_data and isinstance(season_data, dict):
            breakdown = season_data.get('breakdown', {})
            if breakdown:
                parsed = map_espn_breakdown_to_stats(breakdown)
                points_total = float(season_data.get('points', 0.0) or 0.0)

                season = self.db.query(DBPlayerSeasonStats).filter_by(
                    player_id=player.id, year=year,
                ).first()
                if not season:
                    season = DBPlayerSeasonStats(player_id=player.id, year=year)
                    self.db.add(season)

                week_keys = [k for k in raw.keys() if k != '0']
                season.games_played = len(week_keys)
                season.pass_att = parsed.get('pass_att', 0)
                season.pass_cmp = parsed.get('pass_cmp', 0)
                season.pass_yd = parsed.get('pass_yd', 0)
                season.pass_td = parsed.get('pass_td', 0)
                season.pass_int = parsed.get('pass_int', 0)
                season.rush_att = parsed.get('rush_att', 0)
                season.rush_yd = parsed.get('rush_yd', 0)
                season.rush_td = parsed.get('rush_td', 0)
                season.rush_fumbles = parsed.get('fumbles_lost', 0)
                season.targets = parsed.get('targets', 0)
                season.rec = parsed.get('rec', 0)
                season.rec_yd = parsed.get('rec_yd', 0)
                season.rec_td = parsed.get('rec_td', 0)
                season.fantasy_points_total = points_total
                season.fantasy_points_avg = (
                    points_total / season.games_played
                    if season.games_played > 0 else 0.0
                )
                # Prefer targets (opportunities) over rec (completions) for the
                # receiving component so efficiency reflects true opportunity rate.
                # Falls back to rec when ESPN does not export receivingTargets.
                recv_touches = (
                    season.targets
                    if season.targets and season.targets > 0
                    else (season.rec or 0)
                )
                total_touches = (season.pass_att or 0) + (season.rush_att or 0) + recv_touches
                season.fantasy_points_per_touch = (
                    points_total / total_touches if total_touches > 0 else 0.0
                )

                if season.pass_att and season.pass_att > 0:
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
                season_count = 1

        self.db.commit()
        return {"game_logs": game_log_count, "season_stats": season_count}

    def _import_player_from_box(self, espn_player: Any, team_db_id: Optional[int]) -> DBPlayer:
        """Import a player from a BoxPlayer object (used by free agents and rosters).

        Merges per-week stats into the existing JSON blob rather than
        overwriting, so multiple calls for different weeks accumulate data.

        Args:
            espn_player: ESPN BoxPlayer object
            team_db_id: Database team ID (None for free agents)

        Returns:
            DBPlayer instance
        """
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

        # Merge new stats into existing JSON (preserve per-week data from
        # previous imports rather than overwriting with single-week data).
        new_stats = getattr(espn_player, 'stats', {})
        if new_stats:
            existing = db_player.stats if db_player.stats else {}
            # Convert keys to str for consistent merging
            for k, v in new_stats.items():
                sk = str(k)
                # Always keep the freshest season totals ('0') and each week
                existing[sk] = v
            db_player.stats = existing

        # Update team_id only if provided (rostered player)
        if team_db_id is not None:
            db_player.team_id = team_db_id

        # Set headshot URL from ESPN CDN (only if not already set by nfl_data_py)
        if not db_player.headshot_url:
            espn_id = espn_player.playerId
            if espn_player.position == 'D/ST' or espn_player.position == 'DEF':
                db_player.headshot_url = f"https://a.espncdn.com/i/teamlogos/nfl/500/{espn_player.proTeam.lower()}.png"
            else:
                db_player.headshot_url = f"https://a.espncdn.com/i/headshots/nfl/players/full/{espn_id}.png"

        return db_player

    def sync_all_players(self, league_id: str, year: int = 2024) -> int:
        """Sync all available players for a league.

        Args:
            league_id: League ID to sync players for
            year: Season year

        Returns:
            Number of players imported/updated
        """
        db_league = self.db.query(DBLeague).filter_by(league_id=league_id).first()
        if not db_league:
            raise ValueError(f"League {league_id} not found in database")

        return self.import_all_players(
            league_id=league_id,
            espn_s2=db_league.espn_s2,
            swid=db_league.swid,
            year=year or db_league.year
        )

    def populate_stats_from_player_json(self, year: int = 2025) -> Dict[str, int]:
        """Parse the raw stats JSON on every player into structured game logs
        and season stats rows.

        The ``import_all_players`` flow stores ESPN stats as a JSON blob on
        ``DBPlayer.stats`` but never writes to the ``player_game_logs`` or
        ``player_season_stats`` tables.  This method fills that gap.

        Args:
            year: The season year these stats belong to.

        Returns:
            Dict with 'game_logs' and 'season_stats' counts.
        """
        players = (
            self.db.query(DBPlayer)
            .filter(DBPlayer.stats.isnot(None))
            .all()
        )

        game_log_count = 0
        season_count = 0

        for player in players:
            raw = player.stats
            if not raw or not isinstance(raw, dict):
                continue

            # ---------- per-week game logs ----------
            for week_key, week_data in raw.items():
                if week_key == '0':
                    # '0' is the season aggregate — handled below
                    continue
                try:
                    week_num = int(week_key)
                except (ValueError, TypeError):
                    continue

                breakdown = week_data.get('breakdown', {}) if isinstance(week_data, dict) else {}
                if not breakdown:
                    continue

                parsed = map_espn_breakdown_to_stats(breakdown)
                points = week_data.get('points', 0.0) if isinstance(week_data, dict) else 0.0

                game_log = self.db.query(DBPlayerGameLog).filter_by(
                    player_id=player.id,
                    year=year,
                    week=week_num,
                ).first()
                if not game_log:
                    game_log = DBPlayerGameLog(
                        player_id=player.id,
                        year=year,
                        week=week_num,
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
                game_log.two_pt_conversions = parsed.get('two_pt_conversions', 0)
                game_log.fantasy_points = float(points) if points else 0.0
                game_log.source = 'espn'
                game_log.updated_at = datetime.utcnow()
                game_log_count += 1

            # ---------- season aggregate from key '0' ----------
            season_data = raw.get('0')
            if not season_data or not isinstance(season_data, dict):
                continue
            breakdown = season_data.get('breakdown', {})
            if not breakdown:
                continue

            parsed = map_espn_breakdown_to_stats(breakdown)
            points_total = float(season_data.get('points', 0.0) or 0.0)

            season = self.db.query(DBPlayerSeasonStats).filter_by(
                player_id=player.id, year=year,
            ).first()
            if not season:
                season = DBPlayerSeasonStats(player_id=player.id, year=year)
                self.db.add(season)

            # Count games from the per-week keys (exclude '0')
            week_keys = [k for k in raw.keys() if k != '0']
            season.games_played = len(week_keys)

            season.pass_att = parsed.get('pass_att', 0)
            season.pass_cmp = parsed.get('pass_cmp', 0)
            season.pass_yd = parsed.get('pass_yd', 0)
            season.pass_td = parsed.get('pass_td', 0)
            season.pass_int = parsed.get('pass_int', 0)
            season.rush_att = parsed.get('rush_att', 0)
            season.rush_yd = parsed.get('rush_yd', 0)
            season.rush_td = parsed.get('rush_td', 0)
            season.rush_fumbles = parsed.get('fumbles_lost', 0)
            season.targets = parsed.get('targets', 0)
            season.rec = parsed.get('rec', 0)
            season.rec_yd = parsed.get('rec_yd', 0)
            season.rec_td = parsed.get('rec_td', 0)
            season.fantasy_points_total = points_total
            season.fantasy_points_avg = (
                points_total / season.games_played
                if season.games_played > 0 else 0.0
            )
            # Prefer targets (opportunities) over rec (completions) for the
            # receiving component so efficiency reflects true opportunity rate.
            # Falls back to rec when ESPN does not export receivingTargets.
            recv_touches = (
                season.targets
                if season.targets and season.targets > 0
                else (season.rec or 0)
            )
            total_touches = (season.pass_att or 0) + (season.rush_att or 0) + recv_touches
            season.fantasy_points_per_touch = (
                points_total / total_touches if total_touches > 0 else 0.0
            )

            # Passer rating (simplified NFL formula)
            if season.pass_att and season.pass_att > 0:
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
            season_count += 1

        self.db.commit()
        return {"game_logs": game_log_count, "season_stats": season_count}

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
