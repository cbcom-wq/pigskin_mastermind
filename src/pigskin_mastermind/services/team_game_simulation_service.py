"""Build team-wide game simulation by merging per-player simulations."""

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayer, DBTeam, DBWeeklyPlayerStats, DBWeeklyTeamStats,
    DEFAULT_SCORING_SETTINGS,
)
from pigskin_mastermind.services.player_game_simulation_service import (
    PlayerGameSimulationService,
)

# Slots that are NOT active starters
_BENCH_SLOTS = {"BE", "IR"}

# Position colors for field visualization (CSS class suffixes)
_POSITION_COLORS = {
    "QB": "#ef4444",   # red
    "RB": "#22c55e",   # green
    "WR": "#3b82f6",   # blue
    "TE": "#f59e0b",   # amber
    "K": "#8b5cf6",    # purple
    "DEF": "#64748b",  # slate
    "DST": "#64748b",
}


def _sort_key_for_event(event: Dict[str, Any]) -> tuple:
    """Sort key: (quarter ASC, seconds_remaining DESC) for chronological order."""
    qtr = event.get("quarter") or 0
    # Parse seconds from time_label like "Q1 10:25" → 625
    time_label = event.get("time_label", "")
    seconds = 900  # default to start of quarter
    if time_label and ":" in time_label:
        try:
            time_part = time_label.split(" ", 1)[1] if " " in time_label else time_label
            mm, ss = time_part.split(":")
            seconds = int(mm) * 60 + int(ss)
        except (ValueError, IndexError):
            pass
    # Lower quarter first, then higher seconds first (earlier in clock = higher seconds)
    return (qtr, -seconds, event.get("_original_index", 0))


class TeamGameSimulationService:
    """Merges per-player simulations into a unified team game timeline."""

    def __init__(
        self,
        db: Session,
        player_sim_service: Optional[PlayerGameSimulationService] = None,
    ):
        self.db = db
        self.player_sim_service = player_sim_service or PlayerGameSimulationService(db)

    def build_team_simulation(
        self,
        team_db_id: int,
        year: int,
        week: int,
        scoring_settings: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """Build a unified team simulation for one week.

        Returns a payload with merged timeline events from all active players,
        each tagged with player identity.
        """
        scoring = scoring_settings or DEFAULT_SCORING_SETTINGS
        team = self.db.query(DBTeam).filter_by(id=team_db_id).first()
        if not team:
            raise ValueError(f"Team with id={team_db_id} not found")

        # Get active roster for this week
        active_players = self._get_active_players(team_db_id, week)

        if not active_players:
            # Fall back: use all rostered players
            active_players = (
                self.db.query(DBPlayer)
                .filter(DBPlayer.team_id == team_db_id)
                .all()
            )

        # Build per-player simulations
        player_sims: List[Dict[str, Any]] = []
        players_meta: List[Dict[str, Any]] = []

        for player in active_players:
            meta = {
                "player_id": player.id,
                "player_name": player.name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "color": _POSITION_COLORS.get(player.position, "#94a3b8"),
                "headshot_url": getattr(player, "headshot_url", None) or "",
                "total_events": 0,
                "stats": {},
            }

            try:
                sim = self.player_sim_service.build_simulation(
                    player_db_id=player.id,
                    year=year,
                    week=week,
                )
                meta["total_events"] = sim.get("total_events", 0)
                meta["stats"] = sim.get("player_stats", {})
                player_sims.append(sim)
            except (ValueError, Exception):
                # Player may not have PBP data — still include in roster
                player_sims.append({"events": [], "player_stats": {}})

            players_meta.append(meta)

        # Merge all events into unified timeline
        merged_events = self._merge_events(player_sims, active_players, scoring)

        # Compute team totals from final snapshots
        team_stats = self._compute_team_stats(merged_events, active_players, scoring)

        return {
            "team_id": team_db_id,
            "team_name": team.name,
            "year": year,
            "week": week,
            "players": players_meta,
            "total_events": len(merged_events),
            "events": merged_events,
            "team_stats": team_stats,
            "scoring_settings": scoring,
        }

    def _get_active_players(
        self, team_db_id: int, week: int
    ) -> List[DBPlayer]:
        """Get non-bench players for a team in a given week."""
        weekly_team = (
            self.db.query(DBWeeklyTeamStats)
            .filter_by(team_id=team_db_id, week=week)
            .first()
        )
        if not weekly_team:
            return []

        rows = (
            self.db.query(DBWeeklyPlayerStats, DBPlayer)
            .join(DBPlayer, DBWeeklyPlayerStats.player_id == DBPlayer.id)
            .filter(DBWeeklyPlayerStats.weekly_team_stats_id == weekly_team.id)
            .all()
        )

        return [
            player
            for wp, player in rows
            if wp.slot_position not in _BENCH_SLOTS
        ]

    def _merge_events(
        self,
        player_sims: List[Dict[str, Any]],
        players: List[DBPlayer],
        scoring: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        """Merge events from all players into chronological order."""
        all_events: List[Dict[str, Any]] = []

        for sim, player in zip(player_sims, players):
            for orig_idx, event in enumerate(sim.get("events", [])):
                # Tag event with player identity
                event["player_id"] = player.id
                event["player_name"] = player.name
                event["player_position"] = player.position
                event["player_color"] = _POSITION_COLORS.get(
                    player.position, "#94a3b8"
                )
                event["player_headshot_url"] = getattr(player, "headshot_url", None) or ""
                event["_original_index"] = orig_idx
                all_events.append(event)

        # Sort chronologically by game clock
        all_events.sort(key=_sort_key_for_event)

        # Re-index after merge and build team running stats
        team_running = {}
        for new_idx, event in enumerate(all_events):
            event["index"] = new_idx
            event.pop("_original_index", None)

            # Build team running fantasy points
            pid = event["player_id"]
            snapshot = event.get("stats_snapshot", {})
            fpts = self._estimate_fantasy_points(snapshot, scoring)
            team_running[pid] = fpts
            event["team_fantasy_points"] = round(
                sum(team_running.values()), 1
            )

        return all_events

    def _compute_team_stats(
        self,
        merged_events: List[Dict[str, Any]],
        players: List[DBPlayer],
        scoring: Dict[str, float],
    ) -> Dict[str, Any]:
        """Compute team-level summary stats from final event snapshots."""
        # Collect the last snapshot per player
        last_snapshots: Dict[int, Dict[str, Any]] = {}
        for event in merged_events:
            pid = event.get("player_id")
            snapshot = event.get("stats_snapshot")
            if pid and snapshot:
                last_snapshots[pid] = snapshot

        # Aggregate
        totals = {
            "total_plays": 0,
            "total_pass_yards": 0,
            "total_rush_yards": 0,
            "total_rec_yards": 0,
            "total_tds": 0,
            "total_first_downs": 0,
            "total_epa": 0.0,
            "total_fantasy_points": 0.0,
        }

        for pid, snap in last_snapshots.items():
            totals["total_plays"] += snap.get("total_plays", 0)
            totals["total_pass_yards"] += snap.get("pass_yards", 0)
            totals["total_rush_yards"] += snap.get("rush_yards", 0)
            totals["total_rec_yards"] += snap.get("rec_yards", 0)
            totals["total_tds"] += snap.get("total_tds", 0)
            totals["total_first_downs"] += snap.get("first_downs", 0)
            totals["total_epa"] += snap.get("total_epa", 0.0)
            totals["total_fantasy_points"] += self._estimate_fantasy_points(snap, scoring)

        totals["total_epa"] = round(totals["total_epa"], 2)
        totals["total_fantasy_points"] = round(totals["total_fantasy_points"], 1)

        return totals

    @staticmethod
    def _estimate_fantasy_points(
        snapshot: Dict[str, Any],
        scoring: Optional[Dict[str, float]] = None,
    ) -> float:
        """Estimate fantasy points from a stats snapshot using scoring settings."""
        s = scoring or DEFAULT_SCORING_SETTINGS
        pts = 0.0
        pts += (snapshot.get("pass_yards", 0) or 0) * s.get("pass_yd", 0.04)
        pts += (snapshot.get("pass_tds", 0) or 0) * s.get("pass_td", 4)
        pts += (snapshot.get("pass_interceptions", 0) or 0) * s.get("pass_int", -2)
        pts += (snapshot.get("rush_yards", 0) or 0) * s.get("rush_yd", 0.1)
        pts += (snapshot.get("rush_tds", 0) or 0) * s.get("rush_td", 6)
        pts += (snapshot.get("receptions", 0) or 0) * s.get("rec", 0.5)
        pts += (snapshot.get("rec_yards", 0) or 0) * s.get("rec_yd", 0.1)
        pts += (snapshot.get("rec_tds", 0) or 0) * s.get("rec_td", 6)
        pts += (snapshot.get("fumbles_lost", 0) or 0) * s.get("fumbles_lost", -2)
        pts += (snapshot.get("two_pt_conversions", 0) or 0) * s.get("two_pt", 2)
        return round(pts, 1)
