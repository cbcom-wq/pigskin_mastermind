"""Auto-derive projection criteria from stored stats with manual override support."""

import math
from collections import defaultdict
from datetime import datetime
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats, DBNFLTeamStats,
    DBWeeklyTeamStats, DBWeeklyPlayerStats, DBLeague,
)

import logging

logger = logging.getLogger(__name__)
from pigskin_mastermind.models.projection_criteria import (
    WeeklyProjectionCriteria,
    YearlyProjectionCriteria,
)

# Approximate peak ages by position
POSITION_PEAK_AGES = {
    'QB': 29,
    'RB': 25,
    'WR': 27,
    'TE': 27,
    'K': 32,
    'DEF': 27,
}


class ProjectionCriteriaBuilder:
    """Builds projection criteria from stored stats data."""

    def __init__(self, db: Session):
        self.db = db
        # Track players already checked this session to avoid repeated work
        self._ensured_players: set = set()
        self._ensured_team_years: set = set()

    # ------------------------------------------------------------------
    # On-demand per-player data population
    # ------------------------------------------------------------------

    # A player with fewer game logs than this is considered to have
    # incomplete data (likely only rostered weeks from a fantasy team
    # import).  fetch_player_full_stats will be called to fill the gaps.
    _MIN_GAME_LOGS_FOR_COMPLETE = 6

    def _player_has_complete_data(
        self, player_id: int, year: int,
    ) -> bool:
        """Return True if the player has adequate data for the year.

        "Adequate" means a DBPlayerSeasonStats row with real points AND
        enough game logs to suggest a full-season import (not just a few
        rostered weeks from a fantasy team sync).
        """
        season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )
        if not (season
                and season.games_played
                and season.games_played > 0
                and season.fantasy_points_total
                and season.fantasy_points_total > 0):
            return False

        # Check game log coverage — partial roster data often has only
        # 1-5 game logs even though the player played 10+ NFL games
        log_count = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year)
            .count()
        )
        return log_count >= self._MIN_GAME_LOGS_FOR_COMPLETE

    def _ensure_player_stats(
        self, player_id: int, year: int,
    ) -> None:
        """Ensure a player has structured game logs and season stats.

        Mirrors the on-demand import that the player detail page performs
        so the tuner sees the same data quality for every player, not just
        those previously viewed in the team/player pages.

        Order of operations:
          1. Skip if we already checked this player+year this session.
          2. Skip if the player has complete data (season stats with
             real points AND >= _MIN_GAME_LOGS_FOR_COMPLETE game logs).
          3. Try to parse the player's existing ``DBPlayer.stats`` JSON
             blob into game logs + season stats (offline, no network).
          4. If still incomplete, try promoting any DBWeeklyPlayerStats
             rows to game logs, then aggregate.
          5. If still incomplete and ESPN credentials are configured,
             fetch full per-week stats from the ESPN API.
        """
        cache_key = (player_id, year)
        if cache_key in self._ensured_players:
            return
        self._ensured_players.add(cache_key)

        # Check if we already have complete data
        if self._player_has_complete_data(player_id, year):
            return

        player = self.db.query(DBPlayer).filter_by(id=player_id).first()
        if not player:
            return

        # ── Step 1: parse existing JSON blob (offline) ────────────────
        if player.stats and isinstance(player.stats, dict):
            has_weekly_keys = any(
                k != '0' and k.isdigit()
                for k in player.stats.keys()
            )
            if has_weekly_keys:
                try:
                    from pigskin_mastermind.services.espn_sync import ESPNSyncService
                    sync = ESPNSyncService(self.db)
                    sync._populate_single_player_stats(player, year)
                    self.db.flush()
                    logger.debug(
                        "Populated stats from JSON blob for player %s (id=%d)",
                        player.name, player_id,
                    )
                    # Re-check — if this produced complete data, we're done
                    if self._player_has_complete_data(player_id, year):
                        return
                except Exception:
                    pass  # Non-critical — continue with other paths

        # ── Step 2: promote weekly player stats → game logs → season ──
        has_weekly = (
            self.db.query(DBWeeklyPlayerStats)
            .filter(
                DBWeeklyPlayerStats.player_id == player_id,
                DBWeeklyPlayerStats.actual_points > 0,
            )
            .first()
        )
        if has_weekly:
            try:
                from pigskin_mastermind.services.nfl_data_service import NFLDataService
                nfl_svc = NFLDataService(self.db)
                nfl_svc.compute_season_stats_from_game_logs(year)
                logger.debug(
                    "Promoted weekly stats for player %s (id=%d)",
                    player.name, player_id,
                )
                # Re-check
                if self._player_has_complete_data(player_id, year):
                    return
            except Exception:
                pass

        # ── Step 3: ESPN API fetch (requires credentials) ─────────────
        if player.player_id and player.player_id.startswith('espn_'):
            league = self.db.query(DBLeague).first()
            if league and league.espn_s2 and league.swid:
                try:
                    from pigskin_mastermind.services.espn_sync import ESPNSyncService
                    sync = ESPNSyncService(self.db)
                    sync.fetch_player_full_stats(
                        db_player_id=player_id,
                        league_id=league.league_id,
                        espn_s2=league.espn_s2,
                        swid=league.swid,
                        year=year,
                    )
                    self.db.flush()
                    logger.debug(
                        "Fetched ESPN full stats for player %s (id=%d)",
                        player.name, player_id,
                    )
                except Exception:
                    pass  # Non-critical — criteria builder will use fallbacks

    def ensure_players_stats(
        self, player_ids: List[int], year: int,
    ) -> None:
        """Batch version of _ensure_player_stats for multiple players.

        Runs the offline paths (JSON parsing, WPS promotion) first as a
        single batch, then falls back to per-player ESPN fetches only for
        players that still lack data.
        """
        # Quick filter: which players actually need work?
        needs_work = []
        for pid in player_ids:
            cache_key = (pid, year)
            if cache_key in self._ensured_players:
                continue
            if self._player_has_complete_data(pid, year):
                self._ensured_players.add(cache_key)
                continue
            needs_work.append(pid)

        if not needs_work:
            return

        # ── Batch step 1: parse JSON blobs for all players at once ────
        players_with_json = (
            self.db.query(DBPlayer)
            .filter(
                DBPlayer.id.in_(needs_work),
                DBPlayer.stats.isnot(None),
            )
            .all()
        )
        if players_with_json:
            try:
                from pigskin_mastermind.services.espn_sync import ESPNSyncService
                sync = ESPNSyncService(self.db)
                for player in players_with_json:
                    if not player.stats or not isinstance(player.stats, dict):
                        continue
                    has_weekly_keys = any(
                        k != '0' and k.isdigit()
                        for k in player.stats.keys()
                    )
                    if has_weekly_keys:
                        try:
                            sync._populate_single_player_stats(player, year)
                        except Exception:
                            pass
                self.db.flush()
            except Exception:
                pass

        # ── Batch step 2: promote all weekly stats → game logs at once ─
        try:
            from pigskin_mastermind.services.nfl_data_service import NFLDataService
            nfl_svc = NFLDataService(self.db)
            nfl_svc.compute_season_stats_from_game_logs(year)
        except Exception:
            pass

        # ── Batch step 3: ESPN fetch for any still-missing players ─────
        still_missing = []
        for pid in needs_work:
            if self._player_has_complete_data(pid, year):
                self._ensured_players.add((pid, year))
            else:
                still_missing.append(pid)

        if still_missing:
            league = self.db.query(DBLeague).first()
            if league and league.espn_s2 and league.swid:
                try:
                    from pigskin_mastermind.services.espn_sync import ESPNSyncService
                    sync = ESPNSyncService(self.db)
                    for pid in still_missing:
                        player = self.db.query(DBPlayer).filter_by(id=pid).first()
                        if (player
                                and player.player_id
                                and player.player_id.startswith('espn_')):
                            try:
                                sync.fetch_player_full_stats(
                                    db_player_id=pid,
                                    league_id=league.league_id,
                                    espn_s2=league.espn_s2,
                                    swid=league.swid,
                                    year=year,
                                )
                            except Exception:
                                pass
                    self.db.flush()
                except Exception:
                    pass

        # Mark all as checked
        for pid in needs_work:
            self._ensured_players.add((pid, year))

    def build_weekly_criteria(
        self,
        player_id: int,
        week: int,
        year: int,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> WeeklyProjectionCriteria:
        """Auto-build weekly projection criteria from stats data.

        Args:
            player_id: DB player ID.
            week: Target week number.
            year: Target year.
            overrides: Optional dict of criteria field names to override values.

        Returns:
            WeeklyProjectionCriteria populated from stats.
        """
        self._ensure_player_stats(player_id, year)
        self._ensure_team_stats(year)

        player = self.db.query(DBPlayer).filter_by(id=player_id).first()
        if not player:
            raise ValueError(f"Player {player_id} not found")

        season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )

        # Historical average points — fall back to game-log average if the season
        # record has no fantasy data (e.g. ESPN import only, NFL import not yet run)
        historical_avg = season.fantasy_points_avg if season else 0.0
        if historical_avg == 0.0:
            historical_avg = self._compute_historical_avg_from_logs(player_id)

        # Recent trend score: compare last 4 weeks vs season avg (recency-weighted)
        recent_trend = self._compute_trend_score(player_id, year, num_weeks=4)

        # Fantasy points per touch (position-aware denominator)
        fpts_per_touch = self._compute_position_efficiency(
            player_id, player.position, year
        )

        # Player skill level: multi-factor composite among same position
        skill_level = self._compute_skill_composite(player_id, player.position, year)

        # Injury risk from ESPN status + historical availability
        injury_risk = self._compute_injury_risk(player, player_id)

        # Actual touch/target share (not snap%)
        touch_pct = self._compute_touch_share(
            player_id, player.position, player.nfl_team, year
        )

        # Opposing defense ranking for this week's opponent
        def_rank = 16  # default middle
        opponent = self._get_week_opponent(player_id, week, year)
        if opponent:
            team_def = (
                self.db.query(DBNFLTeamStats)
                .filter_by(nfl_team=opponent, year=year, week=None)
                .first()
            )
            if team_def:
                pos_lower = player.position.lower()
                rank_val = getattr(team_def, f'def_rank_vs_{pos_lower}', None)
                if rank_val:
                    def_rank = max(1, min(32, rank_val))

        # Team offense level from team scoring
        team_offense = self._compute_team_offense_level(player.nfl_team, year, week=week)

        # Offensive momentum from recent team scoring
        momentum = self._compute_momentum(player.nfl_team, year, num_weeks=4)

        # Rank 1 = best defense (hardest to score against) → level near 0
        # Rank 32 = worst defense (easiest to score against) → level near 100
        opponent_def_level = ((def_rank - 1) / 31) * 100

        criteria_kwargs = {
            'historical_average_points': historical_avg,
            'recent_trend_score': recent_trend,
            'fantasy_points_per_touch': fpts_per_touch,
            'player_skill_level': skill_level,
            'injury_risk_score': injury_risk,
            'positional_touch_percentage': touch_pct,
            'team_offense_level': team_offense,
            'opponent_defense_level': opponent_def_level,
            'opposing_defense_vs_position_rank': def_rank,
            'offensive_momentum_score': momentum,
            'weather_impact_score': 0.0,
        }

        # Apply overrides
        if overrides:
            criteria_kwargs.update(overrides)

        return WeeklyProjectionCriteria(**criteria_kwargs)

    def build_yearly_criteria(
        self,
        player_id: int,
        year: int,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> YearlyProjectionCriteria:
        """Auto-build yearly projection criteria from stats data.

        Args:
            player_id: DB player ID.
            year: Target year.
            overrides: Optional dict of criteria field names to override values.

        Returns:
            YearlyProjectionCriteria populated from stats.
        """
        player = self.db.query(DBPlayer).filter_by(id=player_id).first()
        if not player:
            raise ValueError(f"Player {player_id} not found")

        # Use previous year's stats for yearly projection
        prev_year = year - 1
        self._ensure_player_stats(player_id, prev_year)
        self._ensure_team_stats(prev_year)
        season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=prev_year)
            .first()
        )
        if not season:
            # Fall back to any available season
            season = (
                self.db.query(DBPlayerSeasonStats)
                .filter_by(player_id=player_id)
                .order_by(desc(DBPlayerSeasonStats.year))
                .first()
            )

        historical_avg = self._compute_weighted_historical_avg(player_id, year)
        if historical_avg == 0.0:
            historical_avg = self._compute_historical_avg_from_logs(player_id)
        fpts_per_touch = self._compute_position_efficiency(
            player_id, player.position, prev_year
        )
        skill_level = self._compute_skill_composite(
            player_id, player.position, prev_year
        )

        touch_pct = self._compute_touch_share(
            player_id, player.position, player.nfl_team, prev_year
        )

        # Age deviation
        age = self._get_player_age(player)
        peak_age = POSITION_PEAK_AGES.get(player.position, 27)
        age_dev = (age - peak_age) if age else 0.0
        age_dev = max(-10, min(10, age_dev))

        team_offense = self._compute_team_offense_level(player.nfl_team, prev_year)

        criteria_kwargs = {
            'historical_average_points': historical_avg,
            'recent_trend_score': 0.0,
            'fantasy_points_per_touch': fpts_per_touch,
            'player_skill_level': skill_level,
            'injury_risk_score': self._compute_injury_risk(player, player_id),
            'positional_touch_percentage': touch_pct,
            'team_offense_level': team_offense,
            'opponent_defense_level': self._compute_schedule_defense_level(
                player_id, player.position, prev_year
            ),
            'age_deviation_from_optimum': age_dev,
            'coaching_stability_score': 50.0,  # manual override only
        }

        if overrides:
            criteria_kwargs.update(overrides)

        return YearlyProjectionCriteria(**criteria_kwargs)

    def _compute_historical_avg_from_logs(self, player_id: int) -> float:
        """Compute per-game fantasy average directly from game logs.

        Used as a fallback when DBPlayerSeasonStats.fantasy_points_avg is 0.0,
        which happens when only the ESPN import has run (not the NFL data import).
        Falls back further to DBWeeklyPlayerStats.actual_points if no game logs exist.
        """
        logs = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id)
            .all()
        )
        if logs:
            total = sum(g.fantasy_points for g in logs)
            return total / len(logs)

        # Tertiary fallback: use ESPN weekly player stats (actual_points from matchups)
        weekly_stats = (
            self.db.query(DBWeeklyPlayerStats)
            .filter(
                DBWeeklyPlayerStats.player_id == player_id,
                DBWeeklyPlayerStats.actual_points > 0,
            )
            .all()
        )
        if weekly_stats:
            total = sum(w.actual_points for w in weekly_stats)
            return total / len(weekly_stats)

        return 0.0

    def _compute_trend_score(
        self, player_id: int, year: int, num_weeks: int = 4
    ) -> float:
        """Compare recency-weighted recent weeks avg to season avg, scaled to -100..100."""
        recent_logs = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year)
            .order_by(desc(DBPlayerGameLog.week))
            .limit(num_weeks)
            .all()
        )
        if not recent_logs:
            # Fall back to DBWeeklyPlayerStats (ESPN matchup data)
            recent_weekly = (
                self.db.query(DBWeeklyPlayerStats)
                .filter(
                    DBWeeklyPlayerStats.player_id == player_id,
                    DBWeeklyPlayerStats.actual_points > 0,
                )
                .order_by(desc(DBWeeklyPlayerStats.week))
                .limit(num_weeks)
                .all()
            )
            if not recent_weekly:
                return 0.0

            weights = list(range(len(recent_weekly), 0, -1))
            total_weight = sum(weights)
            recent_avg = sum(
                w * s.actual_points for w, s in zip(weights, recent_weekly)
            ) / total_weight

            all_weekly = (
                self.db.query(DBWeeklyPlayerStats)
                .filter(
                    DBWeeklyPlayerStats.player_id == player_id,
                    DBWeeklyPlayerStats.actual_points > 0,
                )
                .all()
            )
            season_avg = (
                sum(s.actual_points for s in all_weekly) / len(all_weekly)
                if all_weekly else recent_avg
            )
            # Guard: need at least 2 data points and a meaningful baseline
            # to compute a stable deviation percentage
            if season_avg < 1.0 or len(all_weekly) < 2:
                return 0.0
            confidence = len(recent_weekly) / num_weeks
            raw_deviation = ((recent_avg - season_avg) / season_avg) * 100
            return max(-100, min(100, max(-100, min(100, raw_deviation)) * confidence))

        # Linear recency weights: most recent gets highest weight
        weights = list(range(len(recent_logs), 0, -1))  # e.g. [4,3,2,1]
        total_weight = sum(weights)
        recent_avg = sum(
            w * g.fantasy_points for w, g in zip(weights, recent_logs)
        ) / total_weight

        season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )
        season_avg = season.fantasy_points_avg if season else recent_avg

        # Guard: need a meaningful baseline (>= 1 point) and at least 2
        # game logs to produce a stable trend — prevents wild ±100 swings
        # when the season average is near zero
        if season_avg < 1.0:
            return 0.0

        all_logs_count = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year)
            .count()
        )
        if all_logs_count < 2:
            return 0.0

        # Apply confidence multiplier to dampen small-sample swings
        confidence = len(recent_logs) / num_weeks
        raw_deviation = ((recent_avg - season_avg) / season_avg) * 100
        deviation = max(-100, min(100, raw_deviation)) * confidence
        return max(-100, min(100, deviation))

    def _compute_skill_composite(
        self, player_id: int, position: str, year: int
    ) -> float:
        """Multi-factor skill score (0-100) blending points, efficiency, consistency, volume.

        Falls back to ESPN weekly actual_points when no DBPlayerSeasonStats
        exist for this player (or the whole position group), so ESPN-only
        players still receive a meaningful skill ranking.
        """
        all_seasons = (
            self.db.query(DBPlayerSeasonStats)
            .join(DBPlayer)
            .filter(DBPlayer.position == position, DBPlayerSeasonStats.year == year)
            .all()
        )

        if not all_seasons:
            # No season stats for any player at this position — try ESPN weekly
            return self._estimate_skill_from_weekly(player_id, position)

        # Find this player's season
        player_season = None
        for s in all_seasons:
            if s.player_id == player_id:
                player_season = s
                break
        if player_season is None:
            # Other same-position players have season stats but this one doesn't.
            # Estimate from ESPN weekly data, but cap at peer median (can't rank
            # properly without comparable stats).
            return self._estimate_skill_from_weekly(player_id, position)

        total = len(all_seasons)

        def _percentile(values: List[float], player_val: float) -> float:
            """Return 0-100 percentile of player_val in values (higher=better)."""
            sorted_vals = sorted(values)
            rank = sum(1 for v in sorted_vals if v < player_val)
            return (rank / total) * 100 if total else 50.0

        # 1. Fantasy points percentile (40%)
        fpts_list = [s.fantasy_points_total for s in all_seasons]
        fpts_pct = _percentile(fpts_list, player_season.fantasy_points_total)

        # 2. Efficiency percentile (20%) — fantasy points per touch
        eff_list = [s.fantasy_points_per_touch for s in all_seasons]
        eff_pct = _percentile(eff_list, player_season.fantasy_points_per_touch)

        # 3. Consistency score (20%) — inverse coefficient of variation from game logs
        game_logs = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year)
            .all()
        )
        if len(game_logs) >= 3:
            pts_list = [g.fantasy_points for g in game_logs]
            mean = sum(pts_list) / len(pts_list)
            if mean > 0:
                std_dev = math.sqrt(
                    sum((p - mean) ** 2 for p in pts_list) / len(pts_list)
                )
                cov = std_dev / mean
                # Lower CoV = more consistent → higher score
                # Typical CoV ranges 0.2 (very consistent) to 1.5+ (boom-bust)
                consistency = max(0, min(100, (1 - cov) * 100))
            else:
                consistency = 50.0
        else:
            consistency = 50.0

        # 4. Volume percentile (20%) — total touches
        vol_list = [
            (s.pass_att or 0) + (s.rush_att or 0) + (s.targets or 0)
            for s in all_seasons
        ]
        player_vol = (
            (player_season.pass_att or 0)
            + (player_season.rush_att or 0)
            + (player_season.targets or 0)
        )
        vol_pct = _percentile(vol_list, player_vol)

        composite = (
            fpts_pct * 0.4
            + eff_pct * 0.2
            + consistency * 0.2
            + vol_pct * 0.2
        )
        return max(0, min(100, composite))

    def _compute_injury_risk(
        self, player: DBPlayer, player_id: Optional[int] = None
    ) -> float:
        """Derive injury risk from current status (70%) + historical availability (30%)."""
        stats = player.stats or {}
        injury_status = stats.get('injuryStatus', '')
        if injury_status in ('OUT', 'IR'):
            status_score = 90.0
        elif injury_status == 'DOUBTFUL':
            status_score = 70.0
        elif injury_status == 'QUESTIONABLE':
            status_score = 40.0
        elif injury_status == 'PROBABLE':
            status_score = 15.0
        else:
            status_score = 5.0

        # Blend with historical availability if player_id provided
        if player_id is not None:
            seasons = (
                self.db.query(DBPlayerSeasonStats)
                .filter_by(player_id=player_id)
                .order_by(desc(DBPlayerSeasonStats.year))
                .limit(3)
                .all()
            )
            if seasons:
                expected_games = 17
                avg_played = sum(
                    s.games_played for s in seasons if s.games_played
                ) / len(seasons)
                # availability: 0 (never plays) to 1 (all games)
                availability = min(avg_played / expected_games, 1.0)
                # Higher missed rate → higher risk
                history_score = (1 - availability) * 100
                return max(0, min(100, status_score * 0.7 + history_score * 0.3))

        return status_score

    def _get_week_opponent(
        self, player_id: int, week: int, year: int
    ) -> Optional[str]:
        """Get the opponent team for a player in a given week.

        Data-source priority:
          1. DBPlayerGameLog.opponent (NFL data import)
          2. DBWeeklyTeamStats.opponent_name via DBWeeklyPlayerStats (ESPN sync)
        """
        game_log = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year, week=week)
            .first()
        )
        if game_log and game_log.opponent:
            return game_log.opponent

        # Fallback: ESPN weekly data links player → weekly_team_stats → opponent
        weekly = (
            self.db.query(DBWeeklyPlayerStats)
            .filter_by(player_id=player_id, week=week)
            .first()
        )
        if weekly and weekly.weekly_team_stats_id:
            team_week = (
                self.db.query(DBWeeklyTeamStats)
                .filter_by(id=weekly.weekly_team_stats_id)
                .first()
            )
            if team_week and team_week.opponent_name:
                return team_week.opponent_name

        return None

    def _compute_team_offense_level(
        self, nfl_team: str, year: int, week: Optional[int] = None
    ) -> float:
        """Compute team offense strength as 0–100 based on points scored per game.

        Year selection:
          - week > 6 (or week=None for yearly callers that already pass prev_year):
            use the supplied *year*.
          - week <= 6: use *year - 1* (not enough current-season data yet).

        Data source priority:
          1. DBNFLTeamStats season aggregates (week=None) — populated by NFL sync.
          2. Average of DBNFLTeamStats per-week rows — also from NFL sync.
          3. Sum of DBWeeklyPlayerStats.actual_points per NFL team per week (ESPN) —
             always available after ESPN sync; no year filter (week numbers repeat
             across seasons but ESPN data is typically a single season).
        """
        data_year = (year - 1) if (week is not None and week <= 6) else year

        def _pct_rank(values: list, team_val: float) -> float:
            n = len(values)
            if n == 0:
                return 50.0
            return (sum(1 for v in values if v < team_val) / n) * 100

        # ── Source 1: NFL sync season aggregate rows ─────────────────────────
        team_stat = (
            self.db.query(DBNFLTeamStats)
            .filter_by(nfl_team=nfl_team, year=data_year, week=None)
            .first()
        )
        all_season = (
            self.db.query(DBNFLTeamStats)
            .filter_by(year=data_year, week=None)
            .all()
        )
        if team_stat and all_season and team_stat.points_scored > 0:
            pts_pct = _pct_rank([t.points_scored for t in all_season], team_stat.points_scored)
            yds_pct = _pct_rank([t.total_yards for t in all_season], team_stat.total_yards)
            return max(0.0, min(100.0, pts_pct * 0.5 + yds_pct * 0.5))

        # ── Source 2: NFL sync per-week rows → compute per-team averages ─────
        all_weekly = (
            self.db.query(DBNFLTeamStats)
            .filter(
                DBNFLTeamStats.year == data_year,
                DBNFLTeamStats.week.isnot(None),
            )
            .all()
        )
        if all_weekly:
            team_pts_by_team: Dict[str, list] = defaultdict(list)
            team_yds_by_team: Dict[str, list] = defaultdict(list)
            for row in all_weekly:
                team_pts_by_team[row.nfl_team].append(row.points_scored)
                team_yds_by_team[row.nfl_team].append(row.total_yards)

            if nfl_team in team_pts_by_team:
                team_pts_avg = sum(team_pts_by_team[nfl_team]) / len(team_pts_by_team[nfl_team])
                team_yds_avg = sum(team_yds_by_team[nfl_team]) / len(team_yds_by_team[nfl_team])
                all_pts_avgs = [sum(v) / len(v) for v in team_pts_by_team.values() if v]
                all_yds_avgs = [sum(v) / len(v) for v in team_yds_by_team.values() if v]
                pts_pct = _pct_rank(all_pts_avgs, team_pts_avg)
                yds_pct = _pct_rank(all_yds_avgs, team_yds_avg)
                return max(0.0, min(100.0, pts_pct * 0.5 + yds_pct * 0.5))

        # ── Source 3: ESPN weekly player stats — sum actual_points per NFL team ─
        # Sum each skill-position player's actual_points grouped by (nfl_team, week).
        # Average across weeks to get a mean team fantasy output per game.
        espn_rows = (
            self.db.query(
                DBPlayer.nfl_team,
                DBWeeklyPlayerStats.week,
                func.sum(DBWeeklyPlayerStats.actual_points).label('team_pts'),
            )
            .join(DBPlayer, DBWeeklyPlayerStats.player_id == DBPlayer.id)
            .filter(
                DBWeeklyPlayerStats.actual_points > 0,
                DBPlayer.position.in_(["QB", "RB", "WR", "TE"]),
            )
            .group_by(DBPlayer.nfl_team, DBWeeklyPlayerStats.week)
            .all()
        )
        if not espn_rows:
            return 50.0

        weekly_by_team: Dict[str, list] = defaultdict(list)
        for row in espn_rows:
            if row.nfl_team:
                weekly_by_team[row.nfl_team].append(float(row.team_pts))

        if nfl_team not in weekly_by_team:
            return 50.0

        team_avg = sum(weekly_by_team[nfl_team]) / len(weekly_by_team[nfl_team])
        all_avgs = [sum(v) / len(v) for v in weekly_by_team.values() if v]
        return max(0.0, min(100.0, _pct_rank(all_avgs, team_avg)))

    def _compute_momentum(
        self, nfl_team: str, year: int, num_weeks: int = 4
    ) -> float:
        """Compute offensive momentum blending points (60%) and yards (40%) trends.

        Returns:
            Score from -100 to 100.
        """
        recent = (
            self.db.query(DBNFLTeamStats)
            .filter(
                DBNFLTeamStats.nfl_team == nfl_team,
                DBNFLTeamStats.year == year,
                DBNFLTeamStats.week.isnot(None),
            )
            .order_by(desc(DBNFLTeamStats.week))
            .limit(num_weeks)
            .all()
        )
        if len(recent) < 2:
            return 0.0

        all_weeks = (
            self.db.query(DBNFLTeamStats)
            .filter(
                DBNFLTeamStats.nfl_team == nfl_team,
                DBNFLTeamStats.year == year,
                DBNFLTeamStats.week.isnot(None),
            )
            .all()
        )
        if not all_weeks:
            return 0.0

        # Points deviation
        recent_pts_avg = sum(t.points_scored for t in recent) / len(recent)
        season_pts_avg = sum(t.points_scored for t in all_weeks) / len(all_weeks)
        pts_dev = (
            ((recent_pts_avg - season_pts_avg) / season_pts_avg) * 100
            if season_pts_avg else 0.0
        )

        # Yards deviation
        recent_yds_avg = sum(t.total_yards for t in recent) / len(recent)
        season_yds_avg = sum(t.total_yards for t in all_weeks) / len(all_weeks)
        yds_dev = (
            ((recent_yds_avg - season_yds_avg) / season_yds_avg) * 100
            if season_yds_avg else 0.0
        )

        blended = pts_dev * 0.6 + yds_dev * 0.4
        return max(-100, min(100, blended))

    def _get_player_age(self, player: DBPlayer) -> Optional[int]:
        """Get player age from metadata."""
        stats = player.stats or {}
        age = stats.get('age')
        if age:
            try:
                return int(age)
            except (ValueError, TypeError):
                pass
        return None

    # ── ESPN-based fallback helpers ─────────────────────────────────────

    def _estimate_touch_share_from_weekly(
        self, player_id: int, position: str, nfl_team: str,
    ) -> float:
        """Estimate touch share from ESPN weekly fantasy points among same-position teammates.

        Used when no DBPlayerSeasonStats exists for the player.  Computes the
        player's proportion of total same-position fantasy output on the team.
        """
        pos_upper = position.upper()
        if pos_upper == 'QB':
            pos_group = ['QB']
        elif pos_upper == 'RB':
            pos_group = ['RB']
        else:
            pos_group = ['WR', 'TE']

        # All weekly stats for same-position teammates on this NFL team
        team_weekly = (
            self.db.query(
                DBWeeklyPlayerStats.player_id,
                func.sum(DBWeeklyPlayerStats.actual_points).label('total_pts'),
            )
            .join(DBPlayer, DBWeeklyPlayerStats.player_id == DBPlayer.id)
            .filter(
                DBPlayer.nfl_team == nfl_team,
                DBPlayer.position.in_(pos_group),
                DBWeeklyPlayerStats.actual_points > 0,
            )
            .group_by(DBWeeklyPlayerStats.player_id)
            .all()
        )
        if not team_weekly:
            return 0.0

        team_total = sum(float(r.total_pts) for r in team_weekly)
        if team_total <= 0:
            return 0.0

        player_total = next(
            (float(r.total_pts) for r in team_weekly if r.player_id == player_id),
            0.0,
        )
        return max(0, min(100, (player_total / team_total) * 100))

    def _estimate_skill_from_weekly(
        self, player_id: int, position: str,
    ) -> float:
        """Estimate a skill composite from ESPN weekly actual_points when season stats are absent.

        Ranks this player's per-game average among all same-position players
        who have weekly data, producing a 0-100 percentile.
        """
        all_weekly = (
            self.db.query(
                DBWeeklyPlayerStats.player_id,
                func.avg(DBWeeklyPlayerStats.actual_points).label('avg_pts'),
                func.count(DBWeeklyPlayerStats.id).label('game_count'),
            )
            .join(DBPlayer, DBWeeklyPlayerStats.player_id == DBPlayer.id)
            .filter(
                DBPlayer.position == position,
                DBWeeklyPlayerStats.actual_points > 0,
            )
            .group_by(DBWeeklyPlayerStats.player_id)
            .having(func.count(DBWeeklyPlayerStats.id) >= 2)
            .all()
        )
        if not all_weekly:
            return 50.0

        player_avg = next(
            (float(r.avg_pts) for r in all_weekly if r.player_id == player_id),
            None,
        )
        if player_avg is None:
            return 50.0

        all_avgs = sorted(float(r.avg_pts) for r in all_weekly)
        total = len(all_avgs)
        rank = sum(1 for v in all_avgs if v < player_avg)
        return max(0, min(100, (rank / total) * 100))

    # ── Touch share ──────────────────────────────────────────────────────

    def _compute_touch_share(
        self, player_id: int, position: str, nfl_team: str, year: int
    ) -> float:
        """Compute touch/target share relative to SAME-POSITION team totals (0-100).

        Filters team data to the same positional group so the denominator
        reflects relevant competition (WRs+TEs compete for targets; RBs
        compete for carries+targets; QBs for pass attempts).  Falls back to
        ``snap_pct`` (already 0-100) when position-specific team data is
        unavailable,
        and ultimately to ESPN weekly fantasy-point share among same-position
        teammates when no season stats exist at all.

        For WR/TE and RB the receiving component prefers ``targets``; when
        targets are absent (ESPN does not always export them) it falls back to
        ``rec`` (receptions) so players with known production are not silently
        assigned 0%.
        """
        player_season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )
        if not player_season:
            return self._estimate_touch_share_from_weekly(
                player_id, position, nfl_team
            )

        pos_upper = position.upper()

        # K/DEF have no relevant touch-based denominator — use snap_pct directly
        if pos_upper not in ('QB', 'RB', 'WR', 'TE'):
            if player_season.snap_pct:
                return min(player_season.snap_pct, 100)
            return 0.0

        # Positional group that shares the same opportunity pool
        if pos_upper == 'QB':
            pos_group = ['QB']
        elif pos_upper == 'RB':
            pos_group = ['RB']
        else:  # WR or TE
            pos_group = ['WR', 'TE']

        # Restrict to same-position players on the same team for this year
        team_seasons = (
            self.db.query(DBPlayerSeasonStats)
            .join(DBPlayer)
            .filter(
                DBPlayer.nfl_team == nfl_team,
                DBPlayer.position.in_(pos_group),
                DBPlayerSeasonStats.year == year,
            )
            .all()
        )

        if not team_seasons:
            # Fall back to snap_pct when no position-matching teammates found
            if player_season.snap_pct:
                return min(player_season.snap_pct, 100)
            return 0.0

        if pos_upper == 'QB':
            team_total = sum(s.pass_att or 0 for s in team_seasons)
            player_val = player_season.pass_att or 0
        elif pos_upper == 'RB':
            # targets preferred; fall back to rec when targets not populated
            team_total = sum(
                (s.rush_att or 0) + (s.targets or s.rec or 0) for s in team_seasons
            )
            player_val = (
                (player_season.rush_att or 0)
                + (player_season.targets or player_season.rec or 0)
            )
        else:  # WR or TE
            # targets preferred; fall back to rec when targets not populated
            team_total = sum(s.targets or s.rec or 0 for s in team_seasons)
            player_val = player_season.targets or player_season.rec or 0

        if team_total == 0:
            if player_season.snap_pct:
                return min(player_season.snap_pct, 100)
            return 0.0

        return max(0, min(100, (player_val / team_total) * 100))

    def _compute_position_efficiency(
        self, player_id: int, position: str, year: int
    ) -> float:
        """Position-aware fantasy points per touch/opportunity.

        Falls back to ESPN weekly stats (actual_points / games) when no
        season stats exist, producing a coarse per-game efficiency proxy
        rather than returning 0.0.
        """
        season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )
        if season and season.fantasy_points_total:
            pos_upper = position.upper()
            if pos_upper == 'QB':
                denom = (season.pass_att or 0) + (season.rush_att or 0)
            elif pos_upper == 'RB':
                # targets preferred; fall back to rec when targets not populated
                denom = (season.rush_att or 0) + (season.targets or season.rec or 0)
            elif pos_upper in ('WR', 'TE'):
                # targets preferred; fall back to rec when targets not populated
                denom = season.targets or season.rec or 0
            else:
                # K/DEF — use generic
                denom = (
                    (season.pass_att or 0)
                    + (season.rush_att or 0)
                    + (season.targets or season.rec or 0)
                )

            if denom > 0:
                return season.fantasy_points_total / denom

            # Season stats exist but no volume breakdown — use pre-computed value
            if season.fantasy_points_per_touch and season.fantasy_points_per_touch > 0:
                return season.fantasy_points_per_touch

        # Fallback: ESPN weekly stats — crude per-game proxy
        weekly_stats = (
            self.db.query(DBWeeklyPlayerStats)
            .filter(
                DBWeeklyPlayerStats.player_id == player_id,
                DBWeeklyPlayerStats.actual_points > 0,
            )
            .all()
        )
        if weekly_stats:
            total_pts = sum(w.actual_points for w in weekly_stats)
            # Use count of games as a rough "touches" proxy — yields
            # per-game efficiency which is on a different scale than
            # per-touch, but far better than 0.0 for ranking purposes.
            return total_pts / len(weekly_stats)

        return 0.0

    def _compute_weighted_historical_avg(
        self, player_id: int, target_year: int
    ) -> float:
        """Weighted multi-season historical average (60%/30%/10%) for yearly projections."""
        weights = [0.6, 0.3, 0.1]
        total_weight = 0.0
        weighted_sum = 0.0

        for i, w in enumerate(weights):
            yr = target_year - 1 - i
            season = (
                self.db.query(DBPlayerSeasonStats)
                .filter_by(player_id=player_id, year=yr)
                .first()
            )
            if season and season.games_played and season.games_played >= 6:
                avg = (
                    season.fantasy_points_avg
                    if season.fantasy_points_avg
                    else (
                        season.fantasy_points_total / season.games_played
                        if season.games_played > 0
                        else 0.0
                    )
                )
                weighted_sum += w * avg
                total_weight += w

        if total_weight == 0:
            return 0.0
        return weighted_sum / total_weight

    def _compute_schedule_defense_level(
        self, player_id: int, position: str, year: int
    ) -> float:
        """Average opponent defense level from game log opponents (0-100).

        Returns 50.0 (neutral) if insufficient data.
        """
        game_logs = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year)
            .filter(DBPlayerGameLog.opponent.isnot(None))
            .all()
        )
        if not game_logs:
            return 50.0

        pos_lower = position.lower()
        levels = []
        for log in game_logs:
            team_def = (
                self.db.query(DBNFLTeamStats)
                .filter_by(nfl_team=log.opponent, year=year, week=None)
                .first()
            )
            if team_def:
                rank_val = getattr(team_def, f'def_rank_vs_{pos_lower}', None)
                if rank_val:
                    level = ((rank_val - 1) / 31) * 100
                    levels.append(level)

        if not levels:
            return 50.0

        return max(0, min(100, sum(levels) / len(levels)))

    # ── Team stats population from game logs ─────────────────────────────

    def _ensure_team_stats(self, year: int) -> None:
        """Lazily populate DBNFLTeamStats from game logs if no data exists for the year."""
        if year in self._ensured_team_years:
            return

        existing = (
            self.db.query(DBNFLTeamStats)
            .filter_by(year=year)
            .first()
        )
        if existing:
            self._ensured_team_years.add(year)
            return

        # Check we actually have game logs to compute from
        log_count = (
            self.db.query(DBPlayerGameLog)
            .filter_by(year=year)
            .count()
        )
        if log_count == 0:
            return

        self._populate_team_offense_stats(year)
        self.db.flush()
        self._populate_defense_allowed(year)
        self.db.flush()
        self._populate_defense_rankings(year)
        self.db.flush()
        self._ensured_team_years.add(year)

    def _populate_team_offense_stats(self, year: int) -> None:
        """Aggregate game logs into team offense stats (weekly + season)."""
        logs = (
            self.db.query(
                DBPlayer.nfl_team,
                DBPlayerGameLog.week,
                func.sum(DBPlayerGameLog.pass_yd).label('pass_yd'),
                func.sum(DBPlayerGameLog.rush_yd).label('rush_yd'),
                func.sum(DBPlayerGameLog.pass_td).label('pass_td'),
                func.sum(DBPlayerGameLog.rush_td).label('rush_td'),
                func.sum(DBPlayerGameLog.rec_td).label('rec_td'),
                func.sum(DBPlayerGameLog.fantasy_points).label('fpts'),
            )
            .join(DBPlayer, DBPlayerGameLog.player_id == DBPlayer.id)
            .filter(DBPlayerGameLog.year == year)
            .group_by(DBPlayer.nfl_team, DBPlayerGameLog.week)
            .all()
        )

        # Per-team season accumulators
        team_season: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {'pass_yards': 0, 'rush_yards': 0, 'points_scored': 0}
        )

        for row in logs:
            nfl_team = row.nfl_team
            if not nfl_team:
                continue

            pass_yds = int(row.pass_yd or 0)
            rush_yds = int(row.rush_yd or 0)
            total_yds = pass_yds + rush_yds
            # Approximate NFL points: unique TDs × 7 (including XP).
            # pass_td and rec_td overlap (same play), so take max.
            pass_tds = int(row.pass_td or 0)
            rec_tds = int(row.rec_td or 0)
            rush_tds = int(row.rush_td or 0)
            unique_pass_tds = max(pass_tds, rec_tds)
            points = (unique_pass_tds + rush_tds) * 7

            # Upsert weekly row
            weekly_stat = (
                self.db.query(DBNFLTeamStats)
                .filter_by(nfl_team=nfl_team, year=year, week=row.week)
                .first()
            )
            if not weekly_stat:
                weekly_stat = DBNFLTeamStats(
                    nfl_team=nfl_team, year=year, week=row.week
                )
                self.db.add(weekly_stat)

            weekly_stat.pass_yards = pass_yds
            weekly_stat.rush_yards = rush_yds
            weekly_stat.total_yards = total_yds
            weekly_stat.points_scored = points
            weekly_stat.source = 'computed_from_game_logs'
            weekly_stat.updated_at = datetime.utcnow()

            # Accumulate for season totals
            team_season[nfl_team]['pass_yards'] += pass_yds
            team_season[nfl_team]['rush_yards'] += rush_yds
            team_season[nfl_team]['points_scored'] += points

        # Create season aggregate rows (week=None)
        for nfl_team, totals in team_season.items():
            season_stat = (
                self.db.query(DBNFLTeamStats)
                .filter_by(nfl_team=nfl_team, year=year, week=None)
                .first()
            )
            if not season_stat:
                season_stat = DBNFLTeamStats(
                    nfl_team=nfl_team, year=year, week=None
                )
                self.db.add(season_stat)

            season_stat.pass_yards = totals['pass_yards']
            season_stat.rush_yards = totals['rush_yards']
            season_stat.total_yards = totals['pass_yards'] + totals['rush_yards']
            season_stat.points_scored = totals['points_scored']
            season_stat.source = 'computed_from_game_logs'
            season_stat.updated_at = datetime.utcnow()

    def _populate_defense_allowed(self, year: int) -> None:
        """Compute what each defense allowed from game logs (weekly + season)."""
        logs = (
            self.db.query(
                DBPlayerGameLog.opponent,
                DBPlayerGameLog.week,
                func.sum(DBPlayerGameLog.pass_yd).label('pass_yd'),
                func.sum(DBPlayerGameLog.rush_yd).label('rush_yd'),
                func.sum(DBPlayerGameLog.pass_td).label('pass_td'),
                func.sum(DBPlayerGameLog.rush_td).label('rush_td'),
                func.sum(DBPlayerGameLog.rec_td).label('rec_td'),
            )
            .filter(
                DBPlayerGameLog.year == year,
                DBPlayerGameLog.opponent.isnot(None),
            )
            .group_by(DBPlayerGameLog.opponent, DBPlayerGameLog.week)
            .all()
        )

        # Accumulate season defense totals
        def_season: Dict[str, Dict[str, int]] = defaultdict(
            lambda: {'pass_yards_allowed': 0, 'rush_yards_allowed': 0, 'points_allowed': 0}
        )

        for row in logs:
            opp = row.opponent
            if not opp:
                continue

            pass_yds = int(row.pass_yd or 0)
            rush_yds = int(row.rush_yd or 0)
            pass_tds = int(row.pass_td or 0)
            rec_tds = int(row.rec_td or 0)
            rush_tds = int(row.rush_td or 0)
            unique_pass_tds = max(pass_tds, rec_tds)
            points_allowed = (unique_pass_tds + rush_tds) * 7

            # Update or create weekly defense row
            weekly_stat = (
                self.db.query(DBNFLTeamStats)
                .filter_by(nfl_team=opp, year=year, week=row.week)
                .first()
            )
            if not weekly_stat:
                weekly_stat = DBNFLTeamStats(
                    nfl_team=opp, year=year, week=row.week
                )
                self.db.add(weekly_stat)

            weekly_stat.pass_yards_allowed = pass_yds
            weekly_stat.rush_yards_allowed = rush_yds
            weekly_stat.points_allowed = points_allowed
            weekly_stat.source = weekly_stat.source or 'computed_from_game_logs'
            weekly_stat.updated_at = datetime.utcnow()

            def_season[opp]['pass_yards_allowed'] += pass_yds
            def_season[opp]['rush_yards_allowed'] += rush_yds
            def_season[opp]['points_allowed'] += points_allowed

        # Update season aggregate rows
        for opp, totals in def_season.items():
            season_stat = (
                self.db.query(DBNFLTeamStats)
                .filter_by(nfl_team=opp, year=year, week=None)
                .first()
            )
            if not season_stat:
                season_stat = DBNFLTeamStats(
                    nfl_team=opp, year=year, week=None
                )
                self.db.add(season_stat)

            season_stat.pass_yards_allowed = totals['pass_yards_allowed']
            season_stat.rush_yards_allowed = totals['rush_yards_allowed']
            season_stat.points_allowed = totals['points_allowed']
            season_stat.source = season_stat.source or 'computed_from_game_logs'
            season_stat.updated_at = datetime.utcnow()

    def _populate_defense_rankings(self, year: int) -> None:
        """Compute positional defense rankings from fantasy points allowed."""
        for position in ('QB', 'RB', 'WR', 'TE'):
            pos_lower = position.lower()
            rank_field = f'def_rank_vs_{pos_lower}'

            # Sum fantasy points scored against each opponent by this position
            fpts_by_opp = (
                self.db.query(
                    DBPlayerGameLog.opponent,
                    func.sum(DBPlayerGameLog.fantasy_points).label('total_fpts'),
                )
                .join(DBPlayer, DBPlayerGameLog.player_id == DBPlayer.id)
                .filter(
                    DBPlayerGameLog.year == year,
                    DBPlayer.position == position,
                    DBPlayerGameLog.opponent.isnot(None),
                )
                .group_by(DBPlayerGameLog.opponent)
                .all()
            )

            if not fpts_by_opp:
                continue

            # Sort ascending: fewest fantasy points allowed = rank 1 (best defense)
            sorted_opps = sorted(fpts_by_opp, key=lambda x: x.total_fpts or 0)

            for rank, row in enumerate(sorted_opps, 1):
                season_stat = (
                    self.db.query(DBNFLTeamStats)
                    .filter_by(nfl_team=row.opponent, year=year, week=None)
                    .first()
                )
                if not season_stat:
                    season_stat = DBNFLTeamStats(
                        nfl_team=row.opponent, year=year, week=None
                    )
                    self.db.add(season_stat)

                setattr(season_stat, rank_field, rank)
                season_stat.source = season_stat.source or 'computed_from_game_logs'
                season_stat.updated_at = datetime.utcnow()

            self.db.flush()
