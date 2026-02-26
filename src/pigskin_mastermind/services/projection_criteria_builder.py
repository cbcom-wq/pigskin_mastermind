"""Auto-derive projection criteria from stored stats with manual override support."""

import math
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, func

from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats, DBNFLTeamStats,
    DBWeeklyTeamStats
)
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
        team_offense = self._compute_team_offense_level(player.nfl_team, year)

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
        """
        logs = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id)
            .all()
        )
        if not logs:
            return 0.0
        total = sum(g.fantasy_points for g in logs)
        return total / len(logs)

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
            return 0.0

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

        if season_avg == 0:
            return 0.0

        # Apply confidence multiplier to dampen small-sample swings
        confidence = len(recent_logs) / num_weeks
        raw_deviation = ((recent_avg - season_avg) / season_avg) * 100
        deviation = max(-100, min(100, raw_deviation)) * confidence
        return max(-100, min(100, deviation))

    def _compute_skill_composite(
        self, player_id: int, position: str, year: int
    ) -> float:
        """Multi-factor skill score (0-100) blending points, efficiency, consistency, volume."""
        all_seasons = (
            self.db.query(DBPlayerSeasonStats)
            .join(DBPlayer)
            .filter(DBPlayer.position == position, DBPlayerSeasonStats.year == year)
            .all()
        )

        if not all_seasons:
            return 50.0

        # Find this player's season
        player_season = None
        for s in all_seasons:
            if s.player_id == player_id:
                player_season = s
                break
        if player_season is None:
            return 50.0

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
        """Get the opponent team for a player in a given week."""
        game_log = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year, week=week)
            .first()
        )
        return game_log.opponent if game_log else None

    def _compute_team_offense_level(self, nfl_team: str, year: int) -> float:
        """Compute team offense strength as 0-100 blending points + yards ranks."""
        team_stat = (
            self.db.query(DBNFLTeamStats)
            .filter_by(nfl_team=nfl_team, year=year, week=None)
            .first()
        )
        if not team_stat:
            return 50.0

        all_teams = (
            self.db.query(DBNFLTeamStats)
            .filter_by(year=year, week=None)
            .all()
        )
        if not all_teams:
            return 50.0

        total = len(all_teams)

        def _rank_pct(values, team_val):
            rank = sum(1 for v in values if v < team_val)
            return (rank / total) * 100 if total else 50.0

        yards_list = [t.total_yards for t in all_teams]
        points_list = [t.points_scored for t in all_teams]

        yards_pct = _rank_pct(yards_list, team_stat.total_yards)
        points_pct = _rank_pct(points_list, team_stat.points_scored)

        return max(0, min(100, points_pct * 0.5 + yards_pct * 0.5))

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

    # ── New helpers for improved criteria derivation ──────────────────────

    def _compute_touch_share(
        self, player_id: int, position: str, nfl_team: str, year: int
    ) -> float:
        """Compute actual touch/target share relative to team totals (0-100).

        Falls back to snap_pct * 100 if team data is unavailable.
        """
        player_season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )
        if not player_season:
            return 0.0

        # Get all same-team players for this year
        team_seasons = (
            self.db.query(DBPlayerSeasonStats)
            .join(DBPlayer)
            .filter(DBPlayer.nfl_team == nfl_team, DBPlayerSeasonStats.year == year)
            .all()
        )

        if not team_seasons:
            # Fall back to snap_pct
            if player_season.snap_pct:
                return min(player_season.snap_pct * 100, 100)
            return 0.0

        pos_upper = position.upper()
        if pos_upper == 'QB':
            team_total = sum(s.pass_att or 0 for s in team_seasons)
            player_val = player_season.pass_att or 0
        elif pos_upper == 'RB':
            team_total = sum(
                (s.rush_att or 0) + (s.targets or 0) for s in team_seasons
            )
            player_val = (player_season.rush_att or 0) + (player_season.targets or 0)
        elif pos_upper in ('WR', 'TE'):
            team_total = sum(s.targets or 0 for s in team_seasons)
            player_val = player_season.targets or 0
        else:
            # K/DEF — fall back to snap_pct
            if player_season.snap_pct:
                return min(player_season.snap_pct * 100, 100)
            return 0.0

        if team_total == 0:
            if player_season.snap_pct:
                return min(player_season.snap_pct * 100, 100)
            return 0.0

        return max(0, min(100, (player_val / team_total) * 100))

    def _compute_position_efficiency(
        self, player_id: int, position: str, year: int
    ) -> float:
        """Position-aware fantasy points per touch/opportunity."""
        season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )
        if not season or not season.fantasy_points_total:
            return 0.0

        pos_upper = position.upper()
        if pos_upper == 'QB':
            denom = (season.pass_att or 0) + (season.rush_att or 0)
        elif pos_upper == 'RB':
            denom = (season.rush_att or 0) + (season.targets or 0)
        elif pos_upper in ('WR', 'TE'):
            denom = season.targets or 0
        else:
            # K/DEF — use generic
            denom = (
                (season.pass_att or 0)
                + (season.rush_att or 0)
                + (season.rec or 0)
            )

        if denom == 0:
            return 0.0
        return season.fantasy_points_total / denom

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
