"""Build entertainment simulation payloads from player play-by-play data."""

import hashlib
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer
from pigskin_mastermind.services.nfl_data_service import NFLDataService


# Lateral % by location string.  50 = field centre.
_LATERAL_MAP = {
    "left": 25.0,
    "middle": 50.0,
    "right": 75.0,
}

# Run-gap offsets from the base lateral (gives subtle directional variety)
_RUN_GAP_OFFSET = {
    "end": 8.0,
    "tackle": 4.0,
    "guard": 0.0,
}


def _deterministic_jitter(seed_value: Any, amplitude: float = 8.0) -> float:
    """Return a deterministic jitter in [-amplitude, +amplitude] seeded by *seed_value*.

    Uses a simple hash so the same play always renders in the same spot.
    """
    h = hashlib.md5(str(seed_value).encode()).hexdigest()
    # Map first 8 hex chars to [0, 1)
    frac = int(h[:8], 16) / 0xFFFFFFFF
    return (frac * 2 - 1) * amplitude


class PlayerGameSimulationService:
    """Transforms player play-by-play into timeline events for animation UIs."""

    def __init__(
        self,
        db: Session,
        nfl_data_service: Optional[NFLDataService] = None,
    ):
        self.db = db
        self.nfl_data_service = nfl_data_service or NFLDataService(db)

    def build_simulation(
        self,
        player_db_id: int,
        year: int,
        week: int,
    ) -> Dict[str, Any]:
        """Return a simulation payload for one player's game."""
        player = self.db.query(DBPlayer).filter_by(id=player_db_id).first()
        if not player:
            raise ValueError(f"Player with id={player_db_id} not found")

        pbp = self.nfl_data_service.get_play_by_play(
            player_db_id=player_db_id,
            year=year,
            week=week,
        )
        plays = pbp.get("plays", [])

        events: List[Dict[str, Any]] = []
        running = self._empty_running_stats()
        last_x = 25.0

        for idx, play in enumerate(plays):
            start_x = self._to_field_x(play.get("yardline_100"))
            if start_x is None:
                start_x = last_x
            yards = self._to_float(play.get("yards_gained"), default=0.0)
            end_x = self._clamp(start_x + yards, 0.0, 100.0)

            role = str(play.get("player_role") or "unknown")
            running = self._apply_running_stats(running, role, play)

            first_down = bool(self._to_int(play.get("first_down_pass")) or self._to_int(play.get("first_down_rush")))
            touchdown = bool(self._to_int(play.get("touchdown")))
            turnover = bool(self._to_int(play.get("interception")))
            is_complete = bool(self._to_int(play.get("complete_pass")))
            is_sack = bool(self._to_int(play.get("sack")))
            epa = round(self._to_float(play.get("epa"), default=0.0), 2)

            route_path = self._build_route_path(play, role, start_x, end_x)

            events.append(
                {
                    "index": idx,
                    "play_id": play.get("play_id"),
                    "role": role,
                    "play_type": play.get("play_type"),
                    "description": play.get("desc"),
                    "quarter": self._to_int(play.get("qtr")),
                    "time_label": self._format_time_label(play),
                    "down_distance": self._format_down_distance(play),
                    "yards_gained": int(round(yards)),
                    "epa": epa,
                    "start_x": round(start_x, 2),
                    "end_x": round(end_x, 2),
                    "lane_pct": self._lane_pct(role),
                    "route_path": route_path,
                    "is_complete": is_complete,
                    "is_sack": is_sack,
                    "home_score": self._nullable_int(play.get("total_home_score")),
                    "away_score": self._nullable_int(play.get("total_away_score")),
                    "passer_name": play.get("passer_player_name"),
                    "passer_gsis_id": play.get("passer_player_id"),
                    "receiver_name": play.get("receiver_player_name"),
                    "receiver_gsis_id": play.get("receiver_player_id"),
                    "badges": {
                        "touchdown": touchdown,
                        "first_down": first_down,
                        "turnover": turnover,
                        "big_play": abs(yards) >= 20,
                    },
                    "stats_snapshot": dict(running),
                }
            )
            last_x = end_x

        return {
            "player_id": player_db_id,
            "player_name": player.name,
            "player_position": player.position,
            "year": year,
            "week": week,
            "total_events": len(events),
            "game_summary": pbp.get("game_summary", {}),
            "player_stats": pbp.get("player_stats", {}),
            "events": events,
        }

    # ------------------------------------------------------------------
    # Route-path builder
    # ------------------------------------------------------------------

    def _build_route_path(
        self,
        play: Dict[str, Any],
        role: str,
        start_x: float,
        end_x: float,
    ) -> Dict[str, Any]:
        """Build a route-path dict for one play.

        Coordinates are expressed as percentages of the field (0-100).
        ``depth`` runs from own endzone (0) to opponent endzone (100) — same
        axis as ``start_x`` / ``end_x``.
        ``lateral`` runs from left sideline (0) to right sideline (100) when
        viewed from behind the offence.

        The returned dict contains:
        * ``segments`` – ordered list of ``{depth, lateral, type}`` waypoints.
          ``type`` is one of ``"los"`` (line of scrimmage), ``"drop"``,
          ``"target"``, ``"catch_end"``, ``"run_start"``, ``"run_end"``,
          ``"sack_end"``.
        * ``is_complete``, ``is_touchdown``, ``is_sack`` – booleans.
        """
        play_id = play.get("play_id") or 0
        is_complete = bool(self._to_int(play.get("complete_pass")))
        is_touchdown = bool(self._to_int(play.get("touchdown")))
        is_sack = bool(self._to_int(play.get("sack")))
        is_scramble = bool(self._to_int(play.get("qb_scramble")))
        air_yards = self._to_float(play.get("air_yards"), default=0.0)
        yac = self._to_float(play.get("yards_after_catch"), default=0.0)

        pass_location = play.get("pass_location")  # left / middle / right
        run_location = play.get("run_location")
        run_gap = play.get("run_gap")

        segments: List[Dict[str, Any]] = []

        if role == "pass":
            segments = self._pass_route(
                play_id, start_x, end_x,
                air_yards, yac,
                pass_location,
                is_complete, is_sack, is_scramble,
            )
        elif role == "receive":
            segments = self._receive_route(
                play_id, start_x, end_x,
                air_yards, yac,
                pass_location,
                is_complete,
            )
        elif role == "rush":
            segments = self._rush_route(
                play_id, start_x, end_x,
                run_location, run_gap,
            )
        else:
            # Unknown role – simple point-to-point
            lat = 50.0 + _deterministic_jitter(play_id, 15.0)
            segments = [
                {"depth": start_x, "lateral": lat, "type": "los"},
                {"depth": end_x, "lateral": lat, "type": "run_end"},
            ]

        return {
            "segments": segments,
            "is_complete": is_complete,
            "is_touchdown": is_touchdown,
            "is_sack": is_sack,
        }

    # ---- pass (QB perspective) -----------------------------------------------

    def _pass_route(
        self,
        play_id: Any,
        start_x: float,
        end_x: float,
        air_yards: float,
        yac: float,
        pass_location: Any,
        is_complete: bool,
        is_sack: bool,
        is_scramble: bool,
    ) -> List[Dict[str, Any]]:
        jitter = _deterministic_jitter(play_id, 6.0)
        los_lat = 50.0 + jitter

        if is_sack:
            sack_depth = self._clamp(start_x - abs(end_x - start_x), 0, 100)
            return [
                {"depth": start_x, "lateral": los_lat, "type": "los"},
                {"depth": self._clamp(start_x - 3, 0, 100), "lateral": los_lat, "type": "drop"},
                {"depth": sack_depth, "lateral": los_lat + jitter, "type": "sack_end"},
            ]

        # QB drop-back then throw
        drop_depth = self._clamp(start_x - 5, 0.0, 100.0)

        # Target point
        target_lat = _LATERAL_MAP.get(str(pass_location), 50.0) + jitter
        target_depth = self._clamp(start_x + air_yards, 0, 100)

        segs = [
            {"depth": start_x, "lateral": los_lat, "type": "los"},
            {"depth": drop_depth, "lateral": los_lat, "type": "drop"},
            {"depth": target_depth, "lateral": target_lat, "type": "target"},
        ]

        if is_scramble:
            scramble_lat = target_lat + _deterministic_jitter(play_id + 99, 10.0)
            segs.append({"depth": end_x, "lateral": scramble_lat, "type": "catch_end"})
        elif is_complete and abs(yac) > 0.5:
            yac_lat = target_lat + _deterministic_jitter(play_id + 7, 5.0)
            segs.append({"depth": self._clamp(target_depth + yac, 0, 100), "lateral": yac_lat, "type": "catch_end"})

        return segs

    # ---- receive (WR/TE/RB perspective) --------------------------------------

    def _receive_route(
        self,
        play_id: Any,
        start_x: float,
        end_x: float,
        air_yards: float,
        yac: float,
        pass_location: Any,
        is_complete: bool,
    ) -> List[Dict[str, Any]]:
        jitter = _deterministic_jitter(play_id, 8.0)

        # Receiver starts at LOS, with lateral based on pass_location
        base_lat = _LATERAL_MAP.get(str(pass_location), 50.0)

        # Starting position: slight offset from eventual target lateral (simulates route stem)
        stem_lat = 50.0 + jitter * 0.5
        target_lat = self._clamp(base_lat + jitter, 5, 95)
        target_depth = self._clamp(start_x + max(air_yards, 0), 0, 100)

        # Build a simple route: LOS stem → break → target
        mid_depth = self._clamp(start_x + max(air_yards * 0.5, 1), 0, 100)
        mid_lat = stem_lat + (target_lat - stem_lat) * 0.3

        segs: List[Dict[str, Any]] = [
            {"depth": start_x, "lateral": stem_lat, "type": "los"},
            {"depth": mid_depth, "lateral": mid_lat, "type": "route_break"},
            {"depth": target_depth, "lateral": target_lat, "type": "target"},
        ]

        if is_complete and abs(yac) > 0.5:
            yac_lat = target_lat + _deterministic_jitter(play_id + 13, 6.0)
            segs.append({
                "depth": self._clamp(target_depth + yac, 0, 100),
                "lateral": yac_lat,
                "type": "catch_end",
            })

        return segs

    # ---- rush ----------------------------------------------------------------

    def _rush_route(
        self,
        play_id: Any,
        start_x: float,
        end_x: float,
        run_location: Any,
        run_gap: Any,
    ) -> List[Dict[str, Any]]:
        jitter = _deterministic_jitter(play_id, 5.0)
        base_lat = _LATERAL_MAP.get(str(run_location), 50.0)
        gap_offset = _RUN_GAP_OFFSET.get(str(run_gap), 0.0)
        # If run is to the left, the gap offset pushes further left (negative)
        if str(run_location) == "left":
            gap_offset = -gap_offset
        target_lat = self._clamp(base_lat + gap_offset + jitter, 5, 95)

        los_lat = 50.0 + jitter * 0.5

        # Handoff point is just behind LOS
        handoff_depth = self._clamp(start_x - 1.5, 0, 100)

        # Hit point is at the gap
        hit_depth = self._clamp(start_x + min(max(end_x - start_x, 0) * 0.3, 3), 0, 100)

        segs: List[Dict[str, Any]] = [
            {"depth": start_x, "lateral": los_lat, "type": "los"},
            {"depth": handoff_depth, "lateral": los_lat, "type": "run_start"},
            {"depth": hit_depth, "lateral": target_lat, "type": "run_gap"},
            {"depth": end_x, "lateral": target_lat + _deterministic_jitter(play_id + 3, 4.0), "type": "run_end"},
        ]
        return segs

    @staticmethod
    def _empty_running_stats() -> Dict[str, Any]:
        return {
            "total_plays": 0,
            "pass_attempts": 0,
            "pass_completions": 0,
            "pass_yards": 0,
            "pass_tds": 0,
            "pass_interceptions": 0,
            "rush_attempts": 0,
            "rush_yards": 0,
            "rush_tds": 0,
            "targets": 0,
            "receptions": 0,
            "rec_yards": 0,
            "rec_tds": 0,
            "first_downs": 0,
            "total_tds": 0,
            "total_epa": 0.0,
        }

    def _apply_running_stats(
        self,
        current: Dict[str, Any],
        role: str,
        play: Dict[str, Any],
    ) -> Dict[str, Any]:
        stats = dict(current)
        stats["total_plays"] += 1

        yards = int(round(self._to_float(play.get("yards_gained"), default=0.0)))
        epa = self._to_float(play.get("epa"), default=0.0)
        stats["total_epa"] = round(stats["total_epa"] + epa, 2)

        is_complete = bool(self._to_int(play.get("complete_pass")))
        is_touchdown = bool(self._to_int(play.get("touchdown")))
        is_interception = bool(self._to_int(play.get("interception")))
        is_first_down = bool(self._to_int(play.get("first_down_pass")) or self._to_int(play.get("first_down_rush")))

        if role == "pass":
            stats["pass_attempts"] += 1
            stats["pass_yards"] += yards
            if is_complete:
                stats["pass_completions"] += 1
            if is_touchdown:
                stats["pass_tds"] += 1
            if is_interception:
                stats["pass_interceptions"] += 1
        elif role == "rush":
            stats["rush_attempts"] += 1
            stats["rush_yards"] += yards
            if is_touchdown:
                stats["rush_tds"] += 1
        elif role == "receive":
            stats["targets"] += 1
            if is_complete:
                stats["receptions"] += 1
            stats["rec_yards"] += yards
            if is_touchdown:
                stats["rec_tds"] += 1

        if is_first_down:
            stats["first_downs"] += 1

        stats["total_tds"] = stats["pass_tds"] + stats["rush_tds"] + stats["rec_tds"]
        return stats

    @staticmethod
    def _lane_pct(role: str) -> float:
        if role == "pass":
            return 32.0
        if role == "receive":
            return 70.0
        return 50.0

    @staticmethod
    def _format_time_label(play: Dict[str, Any]) -> str:
        qtr = PlayerGameSimulationService._to_int(play.get("qtr"))
        qtr_seconds = PlayerGameSimulationService._nullable_int(play.get("quarter_seconds_remaining"))
        if not qtr or qtr_seconds is None:
            return "—"
        mm = qtr_seconds // 60
        ss = qtr_seconds % 60
        return f"Q{qtr} {mm}:{ss:02d}"

    @staticmethod
    def _format_down_distance(play: Dict[str, Any]) -> str:
        down = PlayerGameSimulationService._nullable_int(play.get("down"))
        ydstogo = PlayerGameSimulationService._nullable_int(play.get("ydstogo"))
        if down is None or ydstogo is None:
            return "—"
        return f"{down}&{ydstogo}"

    @staticmethod
    def _to_field_x(yardline_100: Any) -> Optional[float]:
        yardline = PlayerGameSimulationService._nullable_float(yardline_100)
        if yardline is None:
            return None
        return 100.0 - PlayerGameSimulationService._clamp(yardline, 0.0, 100.0)

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_int(value: Any, default: int = 0) -> int:
        try:
            if value is None:
                return default
            return int(round(float(value)))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _nullable_int(value: Any) -> Optional[int]:
        try:
            if value is None:
                return None
            return int(round(float(value)))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _nullable_float(value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clamp(value: float, min_val: float, max_val: float) -> float:
        return max(min_val, min(max_val, value))
