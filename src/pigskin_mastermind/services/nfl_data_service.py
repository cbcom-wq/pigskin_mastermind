"""NFL data service wrapping nfl_data_py for league-wide stats import."""

import csv
import io
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
import pandas as pd
from sqlalchemy.orm import Session

try:
    import nfl_data_py as nfl
except ImportError:
    nfl = None  # type: ignore[assignment]

from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats, DBNFLTeamStats, DBNFLGame,
    DBWeeklyPlayerStats, DBWeeklyTeamStats, DBTeam, DBLeague,
)
from pigskin_mastermind.services.player_identity import (
    PlayerIdentityService, is_placeholder_name,
)
from pigskin_mastermind.utils.positions import normalize_position
from pigskin_mastermind.utils.nfl_teams import normalize_team

# Columns to pull from the PBP dataset — keeps payload small
_PBP_COLUMNS = [
    'play_id', 'game_id', 'week', 'season',
    'quarter_seconds_remaining', 'game_seconds_remaining',
    'qtr', 'down', 'ydstogo', 'yardline_100',
    'posteam', 'defteam',
    'desc', 'play_type',
    'yards_gained', 'air_yards', 'yards_after_catch',
    'epa',
    'passer_player_id', 'passer_player_name',
    'rusher_player_id', 'rusher_player_name',
    'receiver_player_id', 'receiver_player_name',
    'complete_pass', 'sack', 'touchdown', 'interception',
    'first_down_rush', 'first_down_pass',
    'pass_location', 'pass_length',
    'run_location', 'run_gap',
    'shotgun', 'qb_dropback', 'qb_scramble',
    'penalty', 'penalty_team', 'penalty_yards',
    'total_home_score', 'total_away_score',
    'home_team', 'away_team',
]


class NFLDataService:
    """Service for importing NFL-wide stats from nfl_data_py."""

    def __init__(self, db: Session):
        self.db = db
        self.identity = PlayerIdentityService(db)

    def _resolve_or_create(
        self,
        *,
        gsis_id: Optional[str] = None,
        pfr_id: Optional[str] = None,
        name: Optional[str] = None,
        position: Optional[str] = None,
        nfl_team: Optional[str] = None,
        create: bool = True,
    ) -> Optional[DBPlayer]:
        """Find the DBPlayer for an nflverse row, creating one only if useful.

        Older code created a row for every gsis id it saw, using ``'Unknown'``
        whenever the dataframe had no name column — ``import_seasonal_data()``
        has neither ``player_display_name`` nor ``position``, so that produced
        hundreds of nameless rows. Now the cross-ID map supplies the missing
        name and position, and a row is created only when we end up with a real
        one.
        """
        player = self.identity.resolve(
            gsis_id=gsis_id, pfr_id=pfr_id, name=name, position=position
        )
        if player is not None:
            self.identity.stamp_ids(player, gsis_id=gsis_id, pfr_id=pfr_id)
            return player

        if not create:
            return None

        # Fill the gaps from nflverse's cross-ID table before giving up.
        entry = self.identity.lookup_ids(gsis_id=gsis_id, pfr_id=pfr_id) or {}
        resolved_name = name if not is_placeholder_name(name) else None
        resolved_name = resolved_name or entry.get("name")
        resolved_position = normalize_position(position) or entry.get("position")

        if not resolved_name or not resolved_position:
            # A row with no name and no position helps nobody and pollutes
            # search; skip it and let a later import with better data create it.
            return None

        player = DBPlayer(
            player_id=f"nfl_{gsis_id or pfr_id}",
            name=resolved_name,
            position=resolved_position,
            nfl_team=nfl_team or entry.get("team") or "FA",
        )
        self.db.add(player)
        self.db.flush()
        self.identity.stamp_ids(player, gsis_id=gsis_id, pfr_id=pfr_id)
        return player

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

            db_player = self._resolve_or_create(
                gsis_id=player_gsis_id,
                name=row.get('player_display_name') or row.get('player_name'),
                position=row.get('position'),
                nfl_team=row.get('recent_team'),
            )
            if db_player is None:
                continue

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

            # import_seasonal_data() carries no name or position column, so
            # the cross-ID map inside _resolve_or_create supplies both.
            db_player = self._resolve_or_create(
                gsis_id=player_gsis_id,
                name=row.get('player_display_name') or row.get('player_name'),
                position=row.get('position'),
                nfl_team=row.get('recent_team'),
            )
            if db_player is None:
                continue

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
            # Prefer targets (opportunities) over rec (completions) for the
            # receiving component so efficiency reflects true opportunity rate.
            recv_touches = (
                season.targets
                if season.targets and season.targets > 0
                else (season.rec or 0)
            )
            total_touches = (season.pass_att or 0) + (season.rush_att or 0) + recv_touches
            season.fantasy_points_per_touch = (
                season.fantasy_points_total / total_touches
                if total_touches > 0 else 0.0
            )
            season.air_yards = _safe_float(row.get('air_yards_share'))
            season.yac = _safe_float(row.get('receiving_yards_after_catch'))
            # The seasonal frame ships wopr split as wopr_x / wopr_y by an
            # upstream merge; a plain 'wopr' column has not existed for a while,
            # so this silently stored 0.0 for everyone.
            season.wopr = _safe_float(
                row.get('wopr_x') if row.get('wopr_x') is not None else row.get('wopr')
            )
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

            # Snap-count data is keyed by PFR id ("MahoPa00"), while players are
            # stored under their gsis id. Resolving through the cross-ID map is
            # what makes this match at all — the old `nfl_<pfr_id>` lookup could
            # never hit a row, which is why snap_pct was NULL for everyone.
            db_player = self._resolve_or_create(pfr_id=pfr_id, create=False)
            if not db_player:
                continue

            season = self.db.query(DBPlayerSeasonStats).filter_by(
                player_id=db_player.id, year=year
            ).first()
            if not season:
                continue

            season.snap_count = _safe_int(row.get('offense_snaps'))
            # nflverse reports offense_pct as a 0–1 fraction; every consumer
            # (player page, projection criteria) treats snap_pct as 0–100.
            season.snap_pct = _to_percentage(row.get('offense_pct'))
            season.updated_at = datetime.utcnow()
            count += 1

        self.db.commit()
        return count

    def _promote_weekly_stats_to_game_logs(self, year: int) -> int:
        """Convert DBWeeklyPlayerStats rows into DBPlayerGameLog rows.

        ESPN's standard sync path writes to weekly_player_stats but not
        player_game_logs.  This method fills that gap by iterating every
        DBWeeklyPlayerStats row whose parent team belongs to a league for
        the requested year, parsing the stored breakdown JSON, and upserting
        a DBPlayerGameLog row.  Existing game log rows are updated in place.

        Returns:
            Number of game log rows created/updated.
        """
        from pigskin_mastermind.services.espn_stats_mapper import map_espn_breakdown_to_stats

        # Join weekly_player_stats → weekly_team_stats → teams → leagues
        # to find the right year without a year column on weekly_team_stats.
        rows = (
            self.db.query(DBWeeklyPlayerStats, DBWeeklyTeamStats)
            .join(DBWeeklyTeamStats,
                  DBWeeklyPlayerStats.weekly_team_stats_id == DBWeeklyTeamStats.id)
            .join(DBTeam, DBWeeklyTeamStats.team_id == DBTeam.id)
            .join(DBLeague, DBTeam.league_id == DBLeague.league_id)
            .filter(DBLeague.year == year)
            .all()
        )

        count = 0
        for wps, wts in rows:
            breakdown = {}
            if wps.stats and isinstance(wps.stats, dict):
                breakdown = wps.stats.get('breakdown', {})

            parsed = map_espn_breakdown_to_stats(breakdown) if breakdown else {}

            game_log = (
                self.db.query(DBPlayerGameLog)
                .filter_by(player_id=wps.player_id, year=year, week=wts.week)
                .first()
            )
            if not game_log:
                game_log = DBPlayerGameLog(
                    player_id=wps.player_id,
                    year=year,
                    week=wts.week,
                )
                self.db.add(game_log)

            game_log.pass_att  = parsed.get('pass_att',  0)
            game_log.pass_cmp  = parsed.get('pass_cmp',  0)
            game_log.pass_yd   = parsed.get('pass_yd',   0)
            game_log.pass_td   = parsed.get('pass_td',   0)
            game_log.pass_int  = parsed.get('pass_int',  0)
            game_log.rush_att  = parsed.get('rush_att',  0)
            game_log.rush_yd   = parsed.get('rush_yd',   0)
            game_log.rush_td   = parsed.get('rush_td',   0)
            game_log.targets   = parsed.get('targets',   0)
            game_log.rec       = parsed.get('rec',       0)
            game_log.rec_yd    = parsed.get('rec_yd',    0)
            game_log.rec_td    = parsed.get('rec_td',    0)
            game_log.fumbles_lost = parsed.get('fumbles_lost', 0)
            game_log.fantasy_points = wps.actual_points or 0.0
            game_log.source    = 'espn_weekly'
            game_log.updated_at = datetime.utcnow()
            count += 1

        self.db.commit()
        return count

    def compute_season_stats_from_game_logs(self, year: int) -> int:
        """Aggregate existing DBPlayerGameLog rows into DBPlayerSeasonStats.

        First promotes any DBWeeklyPlayerStats for the year to game logs
        (for players synced via ESPN's standard box-score path), then
        aggregates all game logs into season totals.  No network access
        required — works entirely from data already in the database.

        Returns:
            Number of season-stats rows created/updated.
        """
        # Step 1: ensure all ESPN weekly stats are represented as game logs
        self._promote_weekly_stats_to_game_logs(year)

        # Step 2: find every player that has at least one game log for this year
        player_ids = (
            self.db.query(DBPlayerGameLog.player_id)
            .filter_by(year=year)
            .distinct()
            .all()
        )
        count = 0

        for (player_id,) in player_ids:
            logs = (
                self.db.query(DBPlayerGameLog)
                .filter_by(player_id=player_id, year=year)
                .all()
            )
            if not logs:
                continue

            season = (
                self.db.query(DBPlayerSeasonStats)
                .filter_by(player_id=player_id, year=year)
                .first()
            )
            if not season:
                season = DBPlayerSeasonStats(player_id=player_id, year=year)
                self.db.add(season)

            season.games_played = len(logs)
            season.pass_att   = sum((g.pass_att  or 0) for g in logs)
            season.pass_cmp   = sum((g.pass_cmp  or 0) for g in logs)
            season.pass_yd    = sum((g.pass_yd   or 0) for g in logs)
            season.pass_td    = sum((g.pass_td   or 0) for g in logs)
            season.pass_int   = sum((g.pass_int  or 0) for g in logs)
            season.rush_att   = sum((g.rush_att  or 0) for g in logs)
            season.rush_yd    = sum((g.rush_yd   or 0) for g in logs)
            season.rush_td    = sum((g.rush_td   or 0) for g in logs)
            season.rush_fumbles = sum((g.fumbles_lost or 0) for g in logs)
            season.targets    = sum((g.targets   or 0) for g in logs)
            season.rec        = sum((g.rec       or 0) for g in logs)
            season.rec_yd     = sum((g.rec_yd    or 0) for g in logs)
            season.rec_td     = sum((g.rec_td    or 0) for g in logs)
            season.fantasy_points_total = sum((g.fantasy_points or 0.0) for g in logs)
            season.fantasy_points_avg = (
                season.fantasy_points_total / season.games_played
                if season.games_played > 0 else 0.0
            )

            # Prefer targets over rec for per-touch efficiency denominator
            recv_touches = (
                season.targets
                if season.targets and season.targets > 0
                else (season.rec or 0)
            )
            total_touches = (season.pass_att or 0) + (season.rush_att or 0) + recv_touches
            season.fantasy_points_per_touch = (
                season.fantasy_points_total / total_touches
                if total_touches > 0 else 0.0
            )

            # Passer rating (simplified NFL formula)
            if season.pass_att > 0:
                comp_pct = season.pass_cmp / season.pass_att
                td_pct   = season.pass_td  / season.pass_att
                int_pct  = season.pass_int / season.pass_att
                ypa      = season.pass_yd  / season.pass_att
                a = max(0, min(2.375, (comp_pct - 0.3) * 5))
                b = max(0, min(2.375, (ypa - 3) * 0.25))
                c = max(0, min(2.375, td_pct * 20))
                d = max(0, min(2.375, 2.375 - (int_pct * 25)))
                season.pass_rating = ((a + b + c + d) / 6) * 100
            else:
                season.pass_rating = 0.0

            season.source = 'game_log_aggregation'
            season.updated_at = datetime.utcnow()
            count += 1

        self.db.commit()
        return count

    def _get_or_create_team_stat(
        self, nfl_team: str, year: int, week: Optional[int] = None,
    ) -> DBNFLTeamStats:
        """Fetch (or create) a team-stat row, flushing so repeat calls see it.

        The session runs with ``autoflush=False``, so a row that was only
        ``add()``-ed is invisible to the next query. Without the flush, every
        pass that touches the same (team, year, week) appends another row —
        which is how the 2024 season data ended up with five rows per team.
        """
        stat = (
            self.db.query(DBNFLTeamStats)
            .filter_by(nfl_team=nfl_team, year=year, week=week)
            .first()
        )
        if not stat:
            stat = DBNFLTeamStats(nfl_team=nfl_team, year=year, week=week)
            self.db.add(stat)
            self.db.flush()
        return stat

    def import_schedules(self, years: List[int]) -> int:
        """Import the NFL schedule (and final scores) into ``DBNFLGame``.

        This is what lets a projection for an *upcoming* week know who the
        opponent is; game logs only cover games already played.

        Returns:
            Number of game rows created/updated.
        """
        if nfl is None:
            raise ImportError("nfl_data_py is not installed. Run: pip install nfl_data_py")

        sched = nfl.import_schedules(years)
        if sched.empty:
            return 0

        count = 0
        for _, row in sched.iterrows():
            home = normalize_team(self._sv(row.get('home_team')))
            away = normalize_team(self._sv(row.get('away_team')))
            week = self._sv(row.get('week'))
            season = self._sv(row.get('season'))
            if not home or not away or week is None or season is None:
                continue

            game = (
                self.db.query(DBNFLGame)
                .filter_by(
                    year=int(season), week=int(week),
                    home_team=home, away_team=away,
                )
                .first()
            )
            if not game:
                game = DBNFLGame(
                    year=int(season), week=int(week),
                    home_team=home, away_team=away,
                )
                self.db.add(game)
                self.db.flush()

            game.game_id = self._sv(row.get('game_id'))
            game.game_type = self._sv(row.get('game_type'))
            home_score = self._sv(row.get('home_score'))
            away_score = self._sv(row.get('away_score'))
            # Left NULL for unplayed games — that is how "upcoming" is detected.
            game.home_score = int(home_score) if home_score is not None else None
            game.away_score = int(away_score) if away_score is not None else None
            game.kickoff_at = _parse_kickoff(
                self._sv(row.get('gameday')), self._sv(row.get('gametime')),
            )
            game.roof = self._sv(row.get('roof'))
            game.surface = self._sv(row.get('surface'))
            game.source = 'nfl_data_py'
            game.updated_at = datetime.utcnow()
            count += 1

        self.db.flush()
        for year in years:
            self.sync_team_scores_from_schedule(year)

        self.db.commit()
        return count

    def sync_team_scores_from_schedule(self, year: int) -> int:
        """Backfill points scored/allowed on ``DBNFLTeamStats`` from real scores.

        Both the nfl_data_py path and the game-log aggregation path used to
        leave ``points_scored`` at 0 or at a touchdowns × 7 estimate, which
        made ``_compute_team_offense_level`` discard the season row entirely.
        Runs automatically after a schedule import.

        Returns:
            Number of team-stat rows updated.
        """
        games = (
            self.db.query(DBNFLGame)
            .filter(
                DBNFLGame.year == year,
                DBNFLGame.home_score.isnot(None),
                DBNFLGame.away_score.isnot(None),
            )
            .all()
        )
        if not games:
            return 0

        weekly: Dict[tuple, List[int]] = {}
        season: Dict[str, List[int]] = {}
        for g in games:
            for team, scored, allowed in (
                (g.home_team, g.home_score, g.away_score),
                (g.away_team, g.away_score, g.home_score),
            ):
                weekly[(team, g.week)] = [scored, allowed]
                totals = season.setdefault(team, [0, 0])
                totals[0] += scored
                totals[1] += allowed

        updated = 0
        for (team, week), (scored, allowed) in weekly.items():
            row = self._get_or_create_team_stat(team, year, week)
            row.points_scored = scored
            row.points_allowed = allowed
            row.updated_at = datetime.utcnow()
            updated += 1

        for team, (scored, allowed) in season.items():
            row = self._get_or_create_team_stat(team, year)
            row.points_scored = scored
            row.points_allowed = allowed
            row.updated_at = datetime.utcnow()
            updated += 1

        self.db.flush()
        return updated

    def import_team_defense_rankings(self, years: List[int]) -> int:
        """Import team offense/defense stats and compute positional rankings.

        Points scored and allowed come from ``DBNFLGame`` (real final scores)
        when the schedule has been imported. Without them ``points_scored``
        stays 0, and ``_compute_team_offense_level`` treats the whole season
        row as unusable and falls through to a much weaker fallback.

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
            scores = self._season_scores_from_schedule(year)

            # Get unique teams
            teams = year_df['recent_team'].dropna().unique()

            for team in teams:
                nfl_team = normalize_team(team)
                if not nfl_team:
                    continue

                # Season totals for team offense (we invert for opponent defense)
                team_offense = year_df[year_df['recent_team'] == team]

                team_stat = self._get_or_create_team_stat(nfl_team, year)
                team_stat.pass_yards = _safe_int(team_offense['passing_yards'].sum())
                team_stat.rush_yards = _safe_int(team_offense['rushing_yards'].sum())
                team_stat.total_yards = team_stat.pass_yards + team_stat.rush_yards

                scored, allowed = scores.get(nfl_team, (0, 0))
                if scored or allowed:
                    team_stat.points_scored = scored
                    team_stat.points_allowed = allowed

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
                    nfl_team = normalize_team(opp_team)
                    if not nfl_team:
                        continue
                    team_stat = self._get_or_create_team_stat(nfl_team, year)
                    rank_field = f'def_rank_vs_{position.lower()}'
                    setattr(team_stat, rank_field, rank)
                    team_stat.updated_at = datetime.utcnow()

        self.db.commit()
        return count

    def _season_scores_from_schedule(
        self, year: int,
    ) -> Dict[str, tuple]:
        """Return ``{team: (points_scored, points_allowed)}`` for *year*.

        Sourced from played games in ``DBNFLGame``. Empty when the schedule
        has not been imported, in which case callers leave the existing
        values alone rather than writing zeros.
        """
        games = (
            self.db.query(DBNFLGame)
            .filter(
                DBNFLGame.year == year,
                DBNFLGame.home_score.isnot(None),
                DBNFLGame.away_score.isnot(None),
            )
            .all()
        )
        totals: Dict[str, List[int]] = {}
        for g in games:
            totals.setdefault(g.home_team, [0, 0])
            totals.setdefault(g.away_team, [0, 0])
            totals[g.home_team][0] += g.home_score
            totals[g.home_team][1] += g.away_score
            totals[g.away_team][0] += g.away_score
            totals[g.away_team][1] += g.home_score
        return {team: (v[0], v[1]) for team, v in totals.items()}

    def import_roster_metadata(self, years: List[int]) -> int:
        """Import player bio metadata (height, weight, age, college, experience).

        Writes to real ``DBPlayer`` columns rather than the ``stats`` JSON
        field: ``stats`` holds ESPN's raw scoring-period payload and is replaced
        wholesale on every team sync, so bio stored there did not survive.

        Returns:
            Number of players updated.
        """
        if nfl is None:
            raise ImportError("nfl_data_py is not installed. Run: pip install nfl_data_py")
        df = _import_rosters(years)
        now = datetime.utcnow()
        count = 0

        for _, row in df.iterrows():
            gsis_id = row.get('player_id') or row.get('gsis_id')
            if not gsis_id:
                continue

            db_player = self._resolve_or_create(
                gsis_id=gsis_id,
                pfr_id=_safe_str(row.get('pfr_id')),
                name=row.get('player_name') or row.get('full_name'),
                position=row.get('position'),
                nfl_team=row.get('team'),
                create=False,
            )
            if not db_player:
                continue

            # The roster feed carries espn/pfr ids directly — cheaper and more
            # reliable than going back through the cross-ID table.
            self.identity.stamp_ids(
                db_player,
                gsis_id=gsis_id,
                espn_id=_safe_str(row.get('espn_id')),
                pfr_id=_safe_str(row.get('pfr_id')),
            )

            db_player.height = _format_height(row.get('height'))
            db_player.weight = _safe_int(row.get('weight')) or None
            db_player.age = _safe_int(row.get('age')) or None
            db_player.years_exp = _safe_int(row.get('years_exp'))
            db_player.draft_number = _safe_int(row.get('draft_number')) or None
            db_player.college = _safe_str(row.get('college'))
            # Normalize whatever is already stored (repairs a "1.0" written by
            # an earlier run) before falling back to the roster feed.
            db_player.jersey = (
                _format_jersey(db_player.jersey)
                or _format_jersey(row.get('jersey_number'))
            )
            if db_player.years_exp is None and row.get('rookie_year'):
                rookie_year = _safe_int(row.get('rookie_year'))
                season = _safe_int(row.get('season'))
                if rookie_year and season:
                    db_player.years_exp = max(season - rookie_year, 0)

            # Import headshot URL from roster data
            headshot = _safe_str(row.get('headshot_url') or row.get('headshot'))
            if headshot:
                db_player.headshot_url = headshot

            db_player.profile_updated_at = now
            db_player.updated_at = now
            count += 1

        self.db.commit()
        return count

    def get_play_by_play(
        self,
        player_db_id: int,
        year: int,
        week: int,
    ) -> Dict[str, Any]:
        """Return play-by-play entries involving *player_db_id* for a given week.

        Queries ``nfl_data_py.import_pbp_data`` using a limited column set for
        performance.  A player is considered *involved* in a play when their
        GSIS id appears as passer, rusher, or receiver.

        Live / in-progress games are supported: ``nfl_data_py`` pulls from the
        NFL endpoint so data is as current as the upstream source.

        Args:
            player_db_id: The integer primary key of the ``DBPlayer`` record.
            year: NFL season year (e.g. 2024).
            week: Regular-season week number (1-18).

        Returns:
            List of dicts, one per play, sorted by ``game_seconds_remaining``
            descending (earliest plays first).

        Raises:
            ImportError: If ``nfl_data_py`` is not installed.
            ValueError: If the player is not found or has no GSIS id.
        """
        try:
            import nfl_data_py as nfl
        except ImportError:
            raise ImportError(
                "nfl_data_py is not installed. Run: pip install nfl_data_py"
            )

        db_player = self.db.query(DBPlayer).filter_by(id=player_db_id).first()
        if not db_player:
            raise ValueError(f"Player with id={player_db_id} not found")

        # Resolve the stored player_id to a GSIS id for PBP matching.
        # IDs may be stored as "espn_<id>", "nfl_<gsis_id>", or bare GSIS ids.
        player_id_str = db_player.player_id
        if player_id_str.startswith("nfl_"):
            gsis_id = player_id_str[4:]
        elif player_id_str.startswith("espn_"):
            espn_id = int(player_id_str[5:])
            id_map = nfl.import_ids()
            match = id_map[id_map['espn_id'] == espn_id]
            if match.empty:
                raise ValueError(
                    f"Could not resolve ESPN id {espn_id} to a GSIS id"
                )
            gsis_id = str(match.iloc[0]['gsis_id'])
        else:
            gsis_id = player_id_str

        # Request only the columns we need; fall back to all if library raises
        try:
            df = nfl.import_pbp_data([year], columns=_PBP_COLUMNS, downcast=False)
        except Exception:
            df = nfl.import_pbp_data([year], downcast=False)

        # Filter to the requested week
        if 'week' in df.columns:
            df = df[df['week'] == week]

        if df.empty:
            return {'plays': [], 'game_summary': {}, 'player_stats': {}}

        # Filter to plays where this player is passer, rusher, or receiver
        mask = (
            (df.get('passer_player_id', '') == gsis_id)
            | (df.get('rusher_player_id', '') == gsis_id)
            | (df.get('receiver_player_id', '') == gsis_id)
        )
        player_df = df[mask]

        if player_df.empty:
            return {'plays': [], 'game_summary': {}, 'player_stats': {}}

        # Sort: earliest plays first (highest game_seconds_remaining first)
        if 'game_seconds_remaining' in player_df.columns:
            player_df = player_df.sort_values(
                'game_seconds_remaining', ascending=False
            )

        plays: List[Dict[str, Any]] = []
        for _, row in player_df.iterrows():
            play: Dict[str, Any] = {}
            for col in player_df.columns:
                val = row.get(col)
                # Convert numpy/pandas scalar types to plain Python
                if hasattr(val, 'item'):
                    try:
                        val = val.item()
                    except (ValueError, AttributeError):
                        val = None
                # Replace NaN with None
                try:
                    if pd.isna(val):
                        val = None
                except (TypeError, ValueError):
                    pass
                play[col] = val
            if row.get('passer_player_id') == gsis_id:
                play['player_role'] = 'pass'
            elif row.get('rusher_player_id') == gsis_id:
                play['player_role'] = 'rush'
            elif row.get('receiver_player_id') == gsis_id:
                play['player_role'] = 'receive'
            else:
                play['player_role'] = 'unknown'
            plays.append(play)

        # ── Game summary ─────────────────────────────────────────────────────────
        def _sv(v: Any) -> Any:
            """Convert numpy scalar / NaN to a plain Python value."""
            if v is None:
                return None
            if hasattr(v, 'item'):
                try:
                    v = v.item()
                except Exception:
                    return None
            try:
                if pd.isna(v):
                    return None
            except Exception:
                pass
            return v

        game_summary: Dict[str, Any] = {}
        if 'game_id' in player_df.columns:
            game_id_val = player_df['game_id'].iloc[0]
            game_df = df[df['game_id'] == game_id_val] if 'game_id' in df.columns else player_df
            game_summary = {
                'game_id': _sv(game_id_val),
                'home_team': _sv(game_df['home_team'].iloc[0]) if 'home_team' in game_df.columns else None,
                'away_team': _sv(game_df['away_team'].iloc[0]) if 'away_team' in game_df.columns else None,
                'home_score': _sv(game_df['total_home_score'].max()) if 'total_home_score' in game_df.columns else None,
                'away_score': _sv(game_df['total_away_score'].max()) if 'total_away_score' in game_df.columns else None,
                'player_team': _sv(player_df['posteam'].mode().iloc[0]) if 'posteam' in player_df.columns and not player_df['posteam'].dropna().empty else None,
            }

        # ── Player stat summary ───────────────────────────────────────────────
        def _psum(col: str, src_df: Any) -> float:
            try:
                return float(src_df[col].fillna(0).sum()) if col in src_df.columns else 0.0
            except Exception:
                return 0.0

        rush_mask = player_df.get('rusher_player_id', pd.Series(dtype=object)) == gsis_id
        rec_mask  = player_df.get('receiver_player_id', pd.Series(dtype=object)) == gsis_id
        pass_mask = player_df.get('passer_player_id', pd.Series(dtype=object)) == gsis_id

        rush_df = player_df[rush_mask]
        rec_df  = player_df[rec_mask]
        pass_df = player_df[pass_mask]

        player_stats: Dict[str, Any] = {
            'rush_attempts':      len(rush_df),
            'rush_yards':         int(_psum('yards_gained', rush_df)),
            'rush_tds':           int(_psum('touchdown', rush_df)),
            'rush_first_downs':   int(_psum('first_down_rush', rush_df)),
            'targets':            len(rec_df),
            'receptions':         int(_psum('complete_pass', rec_df)),
            'rec_yards':          int(_psum('yards_gained', rec_df)),
            'rec_tds':            int(_psum('touchdown', rec_df)),
            'air_yards':          int(_psum('air_yards', rec_df)),
            'yac':                int(_psum('yards_after_catch', rec_df)),
            'pass_attempts':      len(pass_df),
            'pass_completions':   int(_psum('complete_pass', pass_df)),
            'pass_yards':         int(_psum('yards_gained', pass_df)),
            'pass_tds':           int(_psum('touchdown', pass_df)),
            'pass_interceptions': int(_psum('interception', pass_df)),
            'total_epa':          round(_psum('epa', player_df), 2),
        }

        return {'plays': plays, 'game_summary': game_summary, 'player_stats': player_stats}

    @staticmethod
    def _sv(v):
        """Convert numpy scalar / NaN to a plain Python value."""
        if v is None:
            return None
        if hasattr(v, 'item'):
            try:
                v = v.item()
            except Exception:
                return None
        try:
            import pandas as _pd
            if _pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        return v

    def get_week_scoreboard(self, year: int, week: int) -> List[Dict[str, Any]]:
        """Return a list of games for a given week with scores.

        Each entry contains game_id, home_team, away_team, home_score,
        away_score, and a status indicator.
        """
        try:
            import nfl_data_py as nfl
        except ImportError:
            raise ImportError(
                "nfl_data_py is not installed. Run: pip install nfl_data_py"
            )

        try:
            sched = nfl.import_schedules([year])
        except Exception:
            return []

        if sched.empty:
            return []

        week_df = sched[sched['week'] == week] if 'week' in sched.columns else sched

        if week_df.empty:
            return []

        games: List[Dict[str, Any]] = []
        for _, row in week_df.iterrows():
            game_id = self._sv(row.get('game_id'))
            home_team = self._sv(row.get('home_team'))
            away_team = self._sv(row.get('away_team'))
            home_score = self._sv(row.get('home_score'))
            away_score = self._sv(row.get('away_score'))

            # Determine game status
            game_type = self._sv(row.get('game_type'))
            result = self._sv(row.get('result'))
            if home_score is not None and away_score is not None:
                status = 'final'
            else:
                status = 'scheduled'

            gameday = self._sv(row.get('gameday'))
            gametime = self._sv(row.get('gametime'))
            stadium = self._sv(row.get('stadium'))

            games.append({
                'game_id': game_id,
                'home_team': home_team,
                'away_team': away_team,
                'home_score': int(home_score) if home_score is not None else None,
                'away_score': int(away_score) if away_score is not None else None,
                'status': status,
                'game_type': game_type,
                'gameday': str(gameday) if gameday else None,
                'gametime': str(gametime) if gametime else None,
                'stadium': stadium,
            })

        return games

    def get_game_play_by_play(self, game_id: str, year: int, week: int) -> Dict[str, Any]:
        """Return all play-by-play data for an entire game.

        Unlike get_play_by_play which filters to a single player, this returns
        every play in the game for full-game simulation.

        Args:
            game_id: NFL game ID string (e.g. '2024_01_KC_BAL').
            year: NFL season year.
            week: Week number.

        Returns:
            Dict with 'plays', 'game_summary', and 'team_stats' keys.
        """
        try:
            import nfl_data_py as nfl
        except ImportError:
            raise ImportError(
                "nfl_data_py is not installed. Run: pip install nfl_data_py"
            )

        try:
            df = nfl.import_pbp_data([year], columns=_PBP_COLUMNS, downcast=False)
        except Exception:
            df = nfl.import_pbp_data([year], downcast=False)

        # Filter to the requested week
        if 'week' in df.columns:
            df = df[df['week'] == week]

        if df.empty:
            return {'plays': [], 'game_summary': {}, 'team_stats': {}}

        # Filter to the specific game
        if 'game_id' in df.columns:
            game_df = df[df['game_id'] == game_id]
        else:
            return {'plays': [], 'game_summary': {}, 'team_stats': {}}

        if game_df.empty:
            return {'plays': [], 'game_summary': {}, 'team_stats': {}}

        # Sort: earliest plays first (highest game_seconds_remaining first)
        if 'game_seconds_remaining' in game_df.columns:
            game_df = game_df.sort_values('game_seconds_remaining', ascending=False)

        plays: List[Dict[str, Any]] = []
        for _, row in game_df.iterrows():
            play: Dict[str, Any] = {}
            for col in game_df.columns:
                val = row.get(col)
                if hasattr(val, 'item'):
                    try:
                        val = val.item()
                    except (ValueError, AttributeError):
                        val = None
                try:
                    if pd.isna(val):
                        val = None
                except (TypeError, ValueError):
                    pass
                play[col] = val

            # Determine primary role/player for the play
            if play.get('passer_player_id'):
                play['primary_player'] = play.get('passer_player_name')
                play['primary_role'] = 'pass'
            elif play.get('rusher_player_id'):
                play['primary_player'] = play.get('rusher_player_name')
                play['primary_role'] = 'rush'
            elif play.get('receiver_player_id'):
                play['primary_player'] = play.get('receiver_player_name')
                play['primary_role'] = 'receive'
            else:
                play['primary_player'] = None
                play['primary_role'] = play.get('play_type') or 'other'

            plays.append(play)

        # Build game summary
        raw_home_score = self._sv(game_df['total_home_score'].max()) if 'total_home_score' in game_df.columns else None
        raw_away_score = self._sv(game_df['total_away_score'].max()) if 'total_away_score' in game_df.columns else None
        game_summary: Dict[str, Any] = {
            'game_id': game_id,
            'home_team': self._sv(game_df['home_team'].iloc[0]) if 'home_team' in game_df.columns else None,
            'away_team': self._sv(game_df['away_team'].iloc[0]) if 'away_team' in game_df.columns else None,
            'home_score': int(raw_home_score) if raw_home_score is not None else None,
            'away_score': int(raw_away_score) if raw_away_score is not None else None,
        }

        return {'plays': plays, 'game_summary': game_summary}

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


def _to_percentage(val) -> Optional[float]:
    """Scale a 0–1 fraction to 0–100, passing through values already in percent."""
    value = _safe_float(val)
    if val is None or (isinstance(val, float) and val != val):
        return None
    return round(value * 100, 1) if value <= 1.0 else round(value, 1)


def _format_height(val) -> Optional[str]:
    """Render nflverse's height (inches, as a float) as feet-inches.

    Returns strings already in ``6-1`` form untouched.
    """
    if val is None:
        return None
    text = str(val).strip()
    if not text or text.lower() in {'nan', 'none'}:
        return None
    if '-' in text or "'" in text:
        return text
    try:
        inches = int(float(text))
    except (TypeError, ValueError):
        return text
    if inches <= 0:
        return None
    return f"{inches // 12}-{inches % 12}"


def _format_jersey(val) -> Optional[str]:
    """Render a jersey number without a stray ``.0`` from float storage."""
    if val is None:
        return None
    text = str(val).strip()
    if not text or text.lower() in {'nan', 'none'}:
        return None
    try:
        return str(int(float(text)))
    except (TypeError, ValueError):
        return text


def _parse_kickoff(gameday, gametime) -> Optional[datetime]:
    """Combine nflverse's ``gameday`` + ``gametime`` into a datetime.

    Both are strings and either can be missing on future games; returns None
    rather than raising so a schedule import never fails on one odd row.
    """
    if not gameday:
        return None
    text = str(gameday).strip()
    if not text:
        return None
    time_text = str(gametime).strip() if gametime else ''
    for fmt, value in (
        ('%Y-%m-%d %H:%M', f'{text} {time_text}' if time_text else None),
        ('%Y-%m-%d', text),
    ):
        if value is None:
            continue
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _import_rosters(years: List[int]):
    """Fetch season rosters, tolerating nfl_data_py's renamed API.

    Older releases exposed ``import_rosters``; current ones split it into
    ``import_seasonal_rosters`` / ``import_weekly_rosters``. Calling the wrong
    one raises AttributeError, which is how this stayed broken while it had no
    callers.
    """
    for name in ('import_seasonal_rosters', 'import_rosters'):
        fn = getattr(nfl, name, None)
        if fn is not None:
            return fn(years)
    raise ImportError(
        "nfl_data_py exposes no roster import function "
        "(looked for import_seasonal_rosters, import_rosters)"
    )


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
