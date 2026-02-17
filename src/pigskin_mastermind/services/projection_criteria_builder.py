"""Auto-derive projection criteria from stored stats with manual override support."""

from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc

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

        # Historical average points
        historical_avg = season.fantasy_points_avg if season else 0.0

        # Recent trend score: compare last 4 weeks vs season avg
        recent_trend = self._compute_trend_score(player_id, year, num_weeks=4)

        # Fantasy points per touch
        fpts_per_touch = season.fantasy_points_per_touch if season else 0.0

        # Player skill level: percentile ranking among same position
        skill_level = self._compute_skill_percentile(player_id, player.position, year)

        # Injury risk from ESPN status
        injury_risk = self._compute_injury_risk(player)

        # Snap percentage as touch share proxy
        touch_pct = 0.0
        if season and season.snap_pct:
            touch_pct = min(season.snap_pct * 100, 100)

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

        criteria_kwargs = {
            'historical_average_points': historical_avg,
            'recent_trend_score': recent_trend,
            'fantasy_points_per_touch': fpts_per_touch,
            'player_skill_level': skill_level,
            'injury_risk_score': injury_risk,
            'positional_touch_percentage': touch_pct,
            'team_offense_level': team_offense,
            'opponent_defense_level': 50.0,  # neutral default
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

        historical_avg = season.fantasy_points_avg if season else 0.0
        fpts_per_touch = season.fantasy_points_per_touch if season else 0.0
        skill_level = self._compute_skill_percentile(
            player_id, player.position, prev_year
        )

        touch_pct = 0.0
        if season and season.snap_pct:
            touch_pct = min(season.snap_pct * 100, 100)

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
            'injury_risk_score': self._compute_injury_risk(player),
            'positional_touch_percentage': touch_pct,
            'team_offense_level': team_offense,
            'opponent_defense_level': 50.0,
            'age_deviation_from_optimum': age_dev,
            'coaching_stability_score': 50.0,  # manual override only
        }

        if overrides:
            criteria_kwargs.update(overrides)

        return YearlyProjectionCriteria(**criteria_kwargs)

    def _compute_trend_score(
        self, player_id: int, year: int, num_weeks: int = 4
    ) -> float:
        """Compare recent weeks avg to season avg, scaled to -100..100."""
        recent_logs = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id, year=year)
            .order_by(desc(DBPlayerGameLog.week))
            .limit(num_weeks)
            .all()
        )
        if not recent_logs:
            return 0.0

        recent_avg = sum(g.fantasy_points for g in recent_logs) / len(recent_logs)

        season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )
        season_avg = season.fantasy_points_avg if season else recent_avg

        if season_avg == 0:
            return 0.0

        # Percentage deviation scaled to -100..100
        deviation = ((recent_avg - season_avg) / season_avg) * 100
        return max(-100, min(100, deviation))

    def _compute_skill_percentile(
        self, player_id: int, position: str, year: int
    ) -> float:
        """Rank player among same position, return 0-100 percentile."""
        all_seasons = (
            self.db.query(DBPlayerSeasonStats)
            .join(DBPlayer)
            .filter(DBPlayer.position == position, DBPlayerSeasonStats.year == year)
            .order_by(desc(DBPlayerSeasonStats.fantasy_points_total))
            .all()
        )

        if not all_seasons:
            return 50.0

        total = len(all_seasons)
        rank = None
        for i, s in enumerate(all_seasons):
            if s.player_id == player_id:
                rank = i
                break

        if rank is None:
            return 50.0

        # Convert rank to percentile (higher = better)
        percentile = ((total - rank) / total) * 100
        return max(0, min(100, percentile))

    def _compute_injury_risk(self, player: DBPlayer) -> float:
        """Derive injury risk from player metadata."""
        stats = player.stats or {}
        injury_status = stats.get('injuryStatus', '')
        if injury_status in ('OUT', 'IR'):
            return 90.0
        elif injury_status == 'DOUBTFUL':
            return 70.0
        elif injury_status == 'QUESTIONABLE':
            return 40.0
        elif injury_status == 'PROBABLE':
            return 15.0
        return 5.0

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
        """Compute team offense strength as 0-100 score."""
        team_stat = (
            self.db.query(DBNFLTeamStats)
            .filter_by(nfl_team=nfl_team, year=year, week=None)
            .first()
        )
        if not team_stat:
            return 50.0

        # Get all teams for ranking
        all_teams = (
            self.db.query(DBNFLTeamStats)
            .filter_by(year=year, week=None)
            .order_by(desc(DBNFLTeamStats.total_yards))
            .all()
        )
        if not all_teams:
            return 50.0

        total = len(all_teams)
        rank = None
        for i, t in enumerate(all_teams):
            if t.nfl_team == nfl_team:
                rank = i
                break

        if rank is None:
            return 50.0

        return max(0, min(100, ((total - rank) / total) * 100))

    def _compute_momentum(
        self, nfl_team: str, year: int, num_weeks: int = 4
    ) -> float:
        """Compute offensive momentum from recent team scoring trend.

        Returns:
            Score from -100 to 100.
        """
        # Use team's game logs to compute recent scoring trend
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

        recent_avg = sum(t.points_scored for t in recent) / len(recent)

        # Compare to all weeks
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

        season_avg = sum(t.points_scored for t in all_weeks) / len(all_weeks)
        if season_avg == 0:
            return 0.0

        deviation = ((recent_avg - season_avg) / season_avg) * 100
        return max(-100, min(100, deviation))

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
