"""NFL data service wrapping nfl_data_py for league-wide stats import."""

import csv
import io
from typing import List, Optional, Union
from datetime import datetime
from sqlalchemy.orm import Session

try:
    import nfl_data_py as nfl
except ImportError:
    nfl = None

from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats, DBNFLTeamStats
)


class NFLDataService:
    """Service for importing NFL-wide stats from nfl_data_py."""

    def __init__(self, db: Session):
        self.db = db

    def import_weekly_stats(self, years: List[int]) -> int:
        """Import weekly player stats for given years.

        Returns:
            Number of game log rows imported.
        """
        if nfl is None:
            raise ImportError("nfl_data_py is not installed. Run: pip install nfl_data_py")
        df = nfl.import_weekly_data(years)
        count = 0

        for _, row in df.iterrows():
            player_gsis_id = row.get('player_id')
            if not player_gsis_id:
                continue

            # Find or create player by gsis_id
            player_id_str = f"nfl_{player_gsis_id}"
            db_player = self.db.query(DBPlayer).filter_by(player_id=player_id_str).first()
            if not db_player:
                name = row.get('player_display_name') or row.get('player_name', 'Unknown')
                position = row.get('position', 'Unknown')
                team = row.get('recent_team', 'FA')
                db_player = DBPlayer(
                    player_id=player_id_str,
                    name=name,
                    position=position,
                    nfl_team=team or 'FA',
                )
                self.db.add(db_player)
                self.db.flush()

            year = int(row.get('season', 0))
            week = int(row.get('week', 0))
            if not year or not week:
                continue

            game_log = self.db.query(DBPlayerGameLog).filter_by(
                player_id=db_player.id, year=year, week=week
            ).first()
            if not game_log:
                game_log = DBPlayerGameLog(
                    player_id=db_player.id, year=year, week=week
                )
                self.db.add(game_log)

            game_log.opponent = _safe_str(row.get('opponent_team'))
            game_log.pass_att = _safe_int(row.get('attempts'))
            game_log.pass_cmp = _safe_int(row.get('completions'))
            game_log.pass_yd = _safe_int(row.get('passing_yards'))
            game_log.pass_td = _safe_int(row.get('passing_tds'))
            game_log.pass_int = _safe_int(row.get('interceptions'))
            game_log.rush_att = _safe_int(row.get('carries'))
            game_log.rush_yd = _safe_int(row.get('rushing_yards'))
            game_log.rush_td = _safe_int(row.get('rushing_tds'))
            game_log.targets = _safe_int(row.get('targets'))
            game_log.rec = _safe_int(row.get('receptions'))
            game_log.rec_yd = _safe_int(row.get('receiving_yards'))
            game_log.rec_td = _safe_int(row.get('receiving_tds'))
            game_log.fumbles = _safe_int(row.get('rushing_fumbles', 0)) + _safe_int(row.get('receiving_fumbles', 0))
            game_log.fumbles_lost = _safe_int(row.get('rushing_fumbles_lost', 0)) + _safe_int(row.get('receiving_fumbles_lost', 0))
            game_log.fantasy_points = _safe_float(row.get('fantasy_points_ppr'))
            game_log.source = 'nfl_data_py'
            game_log.updated_at = datetime.utcnow()
            count += 1

        self.db.commit()
        return count

    def import_seasonal_stats(self, years: List[int]) -> int:
        """Import seasonal aggregates for all players.

        Returns:
            Number of season stat rows imported.
        """
        if nfl is None:
            raise ImportError("nfl_data_py is not installed. Run: pip install nfl_data_py")
        df = nfl.import_seasonal_data(years)
        count = 0

        for _, row in df.iterrows():
            player_gsis_id = row.get('player_id')
            if not player_gsis_id:
                continue

            player_id_str = f"nfl_{player_gsis_id}"
            db_player = self.db.query(DBPlayer).filter_by(player_id=player_id_str).first()
            if not db_player:
                name = row.get('player_display_name') or row.get('player_name', 'Unknown')
                position = row.get('position', 'Unknown')
                team = row.get('recent_team', 'FA')
                db_player = DBPlayer(
                    player_id=player_id_str,
                    name=name,
                    position=position,
                    nfl_team=team or 'FA',
                )
                self.db.add(db_player)
                self.db.flush()

            year = int(row.get('season', 0))
            if not year:
                continue

            season = self.db.query(DBPlayerSeasonStats).filter_by(
                player_id=db_player.id, year=year
            ).first()
            if not season:
                season = DBPlayerSeasonStats(player_id=db_player.id, year=year)
                self.db.add(season)

            season.games_played = _safe_int(row.get('games'))
            season.pass_att = _safe_int(row.get('attempts'))
            season.pass_cmp = _safe_int(row.get('completions'))
            season.pass_yd = _safe_int(row.get('passing_yards'))
            season.pass_td = _safe_int(row.get('passing_tds'))
            season.pass_int = _safe_int(row.get('interceptions'))
            season.rush_att = _safe_int(row.get('carries'))
            season.rush_yd = _safe_int(row.get('rushing_yards'))
            season.rush_td = _safe_int(row.get('rushing_tds'))
            season.rush_fumbles = _safe_int(row.get('rushing_fumbles_lost', 0))
            season.targets = _safe_int(row.get('targets'))
            season.rec = _safe_int(row.get('receptions'))
            season.rec_yd = _safe_int(row.get('receiving_yards'))
            season.rec_td = _safe_int(row.get('receiving_tds'))
            season.fantasy_points_total = _safe_float(row.get('fantasy_points_ppr'))
            season.fantasy_points_avg = (
                season.fantasy_points_total / season.games_played
                if season.games_played > 0 else 0.0
            )
            total_touches = season.rush_att + season.rec
            season.fantasy_points_per_touch = (
                season.fantasy_points_total / total_touches
                if total_touches > 0 else 0.0
            )
            season.air_yards = _safe_float(row.get('air_yards_share'))
            season.yac = _safe_float(row.get('receiving_yards_after_catch'))
            season.wopr = _safe_float(row.get('wopr'))
            season.source = 'nfl_data_py'
            season.updated_at = datetime.utcnow()
            count += 1

        self.db.commit()
        return count

    def import_snap_counts(self, years: List[int]) -> int:
        """Import snap count data and update season stats.

        Returns:
            Number of player season stats updated with snap data.
        """
        if nfl is None:
            raise ImportError("nfl_data_py is not installed. Run: pip install nfl_data_py")
        df = nfl.import_snap_counts(years)
        count = 0

        # Aggregate snap counts by player and season
        grouped = df.groupby(['pfr_player_id', 'season']).agg({
            'offense_snaps': 'sum',
            'offense_pct': 'mean',
        }).reset_index()

        for _, row in grouped.iterrows():
            pfr_id = row.get('pfr_player_id')
            if not pfr_id:
                continue

            year = int(row.get('season', 0))
            if not year:
                continue

            # Try to find player by matching — snap count data uses PFR IDs
            # We'll update any existing season stats that match
            # Look up via game logs from same year to find the player
            player_id_str = f"nfl_{pfr_id}"
            db_player = self.db.query(DBPlayer).filter_by(player_id=player_id_str).first()
            if not db_player:
                continue

            season = self.db.query(DBPlayerSeasonStats).filter_by(
                player_id=db_player.id, year=year
            ).first()
            if not season:
                continue

            season.snap_count = _safe_int(row.get('offense_snaps'))
            season.snap_pct = _safe_float(row.get('offense_pct'))
            season.updated_at = datetime.utcnow()
            count += 1

        self.db.commit()
        return count

    def import_team_defense_rankings(self, years: List[int]) -> int:
        """Import team defense stats and compute positional rankings.

        Returns:
            Number of team stat rows created/updated.
        """
        if nfl is None:
            raise ImportError("nfl_data_py is not installed. Run: pip install nfl_data_py")
        df = nfl.import_weekly_data(years)
        count = 0

        # Compute defensive stats: points/yards allowed from opponent perspective
        for year in years:
            year_df = df[df['season'] == year]

            # Get unique teams
            teams = year_df['recent_team'].dropna().unique()

            for team in teams:
                # Season totals for team offense (we invert for opponent defense)
                team_offense = year_df[year_df['recent_team'] == team]

                team_stat = self.db.query(DBNFLTeamStats).filter_by(
                    nfl_team=team, year=year, week=None
                ).first()
                if not team_stat:
                    team_stat = DBNFLTeamStats(nfl_team=team, year=year, week=None)
                    self.db.add(team_stat)

                team_stat.pass_yards = _safe_int(team_offense['passing_yards'].sum())
                team_stat.rush_yards = _safe_int(team_offense['rushing_yards'].sum())
                team_stat.total_yards = team_stat.pass_yards + team_stat.rush_yards
                team_stat.source = 'nfl_data_py'
                team_stat.updated_at = datetime.utcnow()
                count += 1

            # Compute defense rankings by position
            # Fantasy points allowed to each position by each team
            for position in ['QB', 'RB', 'WR', 'TE']:
                pos_df = year_df[year_df['position'] == position]
                if pos_df.empty:
                    continue

                # Group by opponent team to get fantasy points allowed
                fps_allowed = (
                    pos_df.groupby('opponent_team')['fantasy_points_ppr']
                    .sum()
                    .sort_values(ascending=True)
                )

                # Rank: 1 = fewest points allowed (best defense)
                for rank, (opp_team, _) in enumerate(fps_allowed.items(), 1):
                    team_stat = self.db.query(DBNFLTeamStats).filter_by(
                        nfl_team=opp_team, year=year, week=None
                    ).first()
                    if not team_stat:
                        team_stat = DBNFLTeamStats(nfl_team=opp_team, year=year, week=None)
                        self.db.add(team_stat)

                    rank_field = f'def_rank_vs_{position.lower()}'
                    setattr(team_stat, rank_field, rank)
                    team_stat.updated_at = datetime.utcnow()

        self.db.commit()
        return count

    def import_roster_metadata(self, years: List[int]) -> None:
        """Import player metadata (updates existing DBPlayer records).

        Uses nfl_data_py roster data to supplement player info.
        """
        if nfl is None:
            raise ImportError("nfl_data_py is not installed. Run: pip install nfl_data_py")
        df = nfl.import_rosters(years)

        for _, row in df.iterrows():
            gsis_id = row.get('player_id') or row.get('gsis_id')
            if not gsis_id:
                continue

            player_id_str = f"nfl_{gsis_id}"
            db_player = self.db.query(DBPlayer).filter_by(player_id=player_id_str).first()
            if not db_player:
                continue

            # Update metadata stored in the stats JSON field
            metadata = db_player.stats or {}
            metadata['height'] = _safe_str(row.get('height'))
            metadata['weight'] = _safe_int(row.get('weight'))
            metadata['age'] = _safe_int(row.get('age'))
            metadata['years_exp'] = _safe_int(row.get('years_exp'))
            metadata['draft_number'] = _safe_int(row.get('draft_number'))
            metadata['college'] = _safe_str(row.get('college'))
            db_player.stats = metadata
            db_player.updated_at = datetime.utcnow()

        self.db.commit()

    def import_adp_from_csv(
        self,
        csv_source: Union[str, io.IOBase],
        year: int,
        adp_source: str = 'csv',
    ) -> int:
        """Import player Average Draft Position (ADP) data from a CSV source.

        The CSV must contain at least the following columns:
        - ``name``: player display name
        - ``position``: player position (QB, RB, WR, TE, K, DEF)
        - ``adp``: average draft position (float, lower = earlier pick)

        An optional ``player_id`` column (gsis-style) may be present for
        more precise player matching.  When not present, matching falls back
        to ``name`` + ``position``.

        Args:
            csv_source: File path string **or** a file-like object (must be
                opened in text mode / ``io.StringIO``).
            year: The season year the ADP data belongs to.
            adp_source: Label stored in ``adp_source`` column (e.g. 'csv',
                'espn', 'yahoo', 'fantasypros').

        Returns:
            Number of player season-stat rows updated with ADP.
        """
        if isinstance(csv_source, str):
            with open(csv_source, newline='', encoding='utf-8') as fh:
                rows = list(csv.DictReader(fh))
        else:
            # Accept any file-like (StringIO, open file handles, etc.)
            rows = list(csv.DictReader(csv_source))

        count = 0
        for row in rows:
            raw_adp = row.get('adp') or row.get('ADP')
            player_name = row.get('name') or row.get('Name') or row.get('player_name')
            position = row.get('position') or row.get('Position') or row.get('pos')
            gsis_id = row.get('player_id') or row.get('gsis_id')

            if raw_adp is None or player_name is None:
                continue

            try:
                adp_value = float(raw_adp)
            except (ValueError, TypeError):
                continue

            # Locate the DBPlayer record
            db_player = None
            if gsis_id:
                player_id_str = f"nfl_{gsis_id}"
                db_player = self.db.query(DBPlayer).filter_by(
                    player_id=player_id_str
                ).first()

            if db_player is None:
                # Fallback: match by name (case-insensitive) and optionally position
                query = self.db.query(DBPlayer).filter(
                    DBPlayer.name.ilike(player_name.strip())
                )
                if position:
                    query = query.filter(DBPlayer.position == position.upper().strip())
                db_player = query.first()

            if db_player is None:
                continue

            # Find or create the season stats row for this year
            season = self.db.query(DBPlayerSeasonStats).filter_by(
                player_id=db_player.id, year=year
            ).first()
            if not season:
                season = DBPlayerSeasonStats(player_id=db_player.id, year=year)
                self.db.add(season)

            season.adp = adp_value
            season.adp_source = adp_source
            season.updated_at = datetime.utcnow()
            count += 1

        self.db.commit()
        return count


def _safe_int(val) -> int:
    try:
        if val is None or (isinstance(val, float) and str(val) == 'nan'):
            return 0
        return int(round(float(val)))
    except (ValueError, TypeError):
        return 0


def _safe_float(val) -> float:
    try:
        if val is None or (isinstance(val, float) and str(val) == 'nan'):
            return 0.0
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _safe_str(val) -> Optional[str]:
    if val is None or (isinstance(val, float) and str(val) == 'nan'):
        return None
    return str(val)
