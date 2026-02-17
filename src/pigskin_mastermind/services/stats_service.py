"""Central stats query and aggregation service."""

from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc

from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats, DBNFLTeamStats
)


class StatsService:
    """Service for querying and aggregating player/team statistics."""

    def __init__(self, db: Session):
        self.db = db

    def get_player_stats(
        self,
        player_id: int,
        year: Optional[int] = None,
        weeks: Optional[List[int]] = None,
    ) -> dict:
        """Get player stats, optionally filtered by year/weeks.

        Args:
            player_id: DB id of the player.
            year: Filter to specific year.
            weeks: Filter to specific weeks (requires year).

        Returns:
            Dict with season stats and optional weekly breakdown.
        """
        player = self.db.query(DBPlayer).filter_by(id=player_id).first()
        if not player:
            return {}

        result = {
            "player_id": player.id,
            "name": player.name,
            "position": player.position,
            "nfl_team": player.nfl_team,
        }

        # Season stats
        season_query = self.db.query(DBPlayerSeasonStats).filter_by(player_id=player_id)
        if year:
            season_query = season_query.filter_by(year=year)
        seasons = season_query.order_by(desc(DBPlayerSeasonStats.year)).all()

        result["seasons"] = [
            {
                "year": s.year,
                "games_played": s.games_played,
                "pass_yd": s.pass_yd,
                "pass_td": s.pass_td,
                "pass_int": s.pass_int,
                "rush_yd": s.rush_yd,
                "rush_td": s.rush_td,
                "rec": s.rec,
                "rec_yd": s.rec_yd,
                "rec_td": s.rec_td,
                "fantasy_points_total": s.fantasy_points_total,
                "fantasy_points_avg": round(s.fantasy_points_avg, 2),
                "fantasy_points_per_touch": round(s.fantasy_points_per_touch, 2),
                "snap_pct": s.snap_pct,
            }
            for s in seasons
        ]

        # Weekly breakdown if requested
        if weeks and year:
            logs = (
                self.db.query(DBPlayerGameLog)
                .filter(
                    DBPlayerGameLog.player_id == player_id,
                    DBPlayerGameLog.year == year,
                    DBPlayerGameLog.week.in_(weeks),
                )
                .order_by(DBPlayerGameLog.week)
                .all()
            )
            result["weekly"] = [self._game_log_to_dict(g) for g in logs]

        return result

    def get_player_game_logs(
        self,
        player_id: int,
        year: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> List[dict]:
        """Get chronological game logs for a player."""
        query = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id)
        )
        if year:
            query = query.filter_by(year=year)
        query = query.order_by(desc(DBPlayerGameLog.year), desc(DBPlayerGameLog.week))
        if limit:
            query = query.limit(limit)

        return [self._game_log_to_dict(g) for g in query.all()]

    def get_recent_performance(
        self,
        player_id: int,
        num_weeks: int = 4,
    ) -> dict:
        """Get recent stats for trend analysis.

        Returns:
            Dict with recent averages and comparison to season average.
        """
        recent_logs = (
            self.db.query(DBPlayerGameLog)
            .filter_by(player_id=player_id)
            .order_by(desc(DBPlayerGameLog.year), desc(DBPlayerGameLog.week))
            .limit(num_weeks)
            .all()
        )

        if not recent_logs:
            return {"num_weeks": 0, "recent_avg_points": 0.0, "trend_vs_season": 0.0}

        recent_avg = sum(g.fantasy_points for g in recent_logs) / len(recent_logs)

        # Compare to full season average
        year = recent_logs[0].year
        season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )
        season_avg = season.fantasy_points_avg if season else recent_avg

        trend = recent_avg - season_avg if season_avg else 0.0

        return {
            "num_weeks": len(recent_logs),
            "recent_avg_points": round(recent_avg, 2),
            "season_avg_points": round(season_avg, 2),
            "trend_vs_season": round(trend, 2),
            "recent_games": [self._game_log_to_dict(g) for g in recent_logs],
        }

    def get_season_averages(self, player_id: int, year: int) -> dict:
        """Get per-game averages for a season."""
        season = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )
        if not season:
            return {}

        gp = max(season.games_played, 1)
        return {
            "year": season.year,
            "games_played": season.games_played,
            "pass_yd_per_game": round(season.pass_yd / gp, 1),
            "pass_td_per_game": round(season.pass_td / gp, 2),
            "rush_yd_per_game": round(season.rush_yd / gp, 1),
            "rush_td_per_game": round(season.rush_td / gp, 2),
            "rec_per_game": round(season.rec / gp, 1),
            "rec_yd_per_game": round(season.rec_yd / gp, 1),
            "rec_td_per_game": round(season.rec_td / gp, 2),
            "fantasy_points_avg": round(season.fantasy_points_avg, 2),
            "fantasy_points_per_touch": round(season.fantasy_points_per_touch, 2),
        }

    def get_team_defense_rankings(self, nfl_team: str, year: int) -> dict:
        """Get defensive rankings by position for matchup analysis."""
        team_stat = (
            self.db.query(DBNFLTeamStats)
            .filter_by(nfl_team=nfl_team, year=year, week=None)
            .first()
        )
        if not team_stat:
            return {}

        return {
            "nfl_team": team_stat.nfl_team,
            "year": team_stat.year,
            "points_allowed": team_stat.points_allowed,
            "pass_yards_allowed": team_stat.pass_yards_allowed,
            "rush_yards_allowed": team_stat.rush_yards_allowed,
            "def_rank_vs_qb": team_stat.def_rank_vs_qb,
            "def_rank_vs_rb": team_stat.def_rank_vs_rb,
            "def_rank_vs_wr": team_stat.def_rank_vs_wr,
            "def_rank_vs_te": team_stat.def_rank_vs_te,
        }

    def compare_players(
        self,
        player_ids: List[int],
        year: Optional[int] = None,
    ) -> dict:
        """Side-by-side stat comparison for trade analysis."""
        comparisons = []
        for pid in player_ids:
            player = self.db.query(DBPlayer).filter_by(id=pid).first()
            if not player:
                continue

            season_query = (
                self.db.query(DBPlayerSeasonStats)
                .filter_by(player_id=pid)
            )
            if year:
                season_query = season_query.filter_by(year=year)
            season = season_query.order_by(desc(DBPlayerSeasonStats.year)).first()

            recent = self.get_recent_performance(pid)

            entry = {
                "player_id": pid,
                "name": player.name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "recent_avg_points": recent.get("recent_avg_points", 0.0),
                "trend_vs_season": recent.get("trend_vs_season", 0.0),
            }

            if season:
                entry.update({
                    "year": season.year,
                    "games_played": season.games_played,
                    "fantasy_points_total": season.fantasy_points_total,
                    "fantasy_points_avg": round(season.fantasy_points_avg, 2),
                    "fantasy_points_per_touch": round(season.fantasy_points_per_touch, 2),
                    "pass_yd": season.pass_yd,
                    "rush_yd": season.rush_yd,
                    "rec_yd": season.rec_yd,
                    "total_td": season.pass_td + season.rush_td + season.rec_td,
                })

            comparisons.append(entry)

        return {"players": comparisons}

    def _game_log_to_dict(self, g: DBPlayerGameLog) -> dict:
        return {
            "year": g.year,
            "week": g.week,
            "opponent": g.opponent,
            "pass_att": g.pass_att,
            "pass_cmp": g.pass_cmp,
            "pass_yd": g.pass_yd,
            "pass_td": g.pass_td,
            "pass_int": g.pass_int,
            "rush_att": g.rush_att,
            "rush_yd": g.rush_yd,
            "rush_td": g.rush_td,
            "targets": g.targets,
            "rec": g.rec,
            "rec_yd": g.rec_yd,
            "rec_td": g.rec_td,
            "fumbles": g.fumbles,
            "fantasy_points": g.fantasy_points,
            "is_active_game": g.is_active_game,
        }
