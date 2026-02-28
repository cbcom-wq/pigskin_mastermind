"""Build full-game simulation from NFL play-by-play data."""

import hashlib
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer
from pigskin_mastermind.services.nfl_data_service import NFLDataService


# Lateral % by pass location string.  50 = field centre.
_LATERAL_MAP = {
    "left": 25.0,
    "middle": 50.0,
    "right": 75.0,
}

_RUN_GAP_OFFSET = {
    "end": 8.0,
    "tackle": 4.0,
    "guard": 0.0,
}


def _deterministic_jitter(seed_value: Any, amplitude: float = 8.0) -> float:
    h = hashlib.md5(str(seed_value).encode()).hexdigest()
    frac = int(h[:8], 16) / 0xFFFFFFFF
    return (frac * 2 - 1) * amplitude


class NFLGameSimulationService:
    """Transforms full-game play-by-play into timeline events for animation."""

    def __init__(
        self,
        db: Session,
        nfl_data_service: Optional[NFLDataService] = None,
    ):
        self.db = db
        self.nfl_data_service = nfl_data_service or NFLDataService(db)

    def build_game_simulation(
        self,
        game_id: str,
        year: int,
        week: int,
    ) -> Dict[str, Any]:
        """Return a simulation payload for a full NFL game."""
        pbp = self.nfl_data_service.get_game_play_by_play(
            game_id=game_id,
            year=year,
            week=week,
        )
        plays = pbp.get("plays", [])
        game_summary = pbp.get("game_summary", {})

        # Resolve headshot URLs for players involved in the game
        player_headshots = self._resolve_headshots(plays)

        events: List[Dict[str, Any]] = []
        last_x = 25.0

        for idx, play in enumerate(plays):
            start_x = self._to_field_x(play.get("yardline_100"))
            if start_x is None:
                start_x = last_x
            yards = self._to_float(play.get("yards_gained"), default=0.0)
            end_x = self._clamp(start_x + yards, 0.0, 100.0)

            role = play.get("primary_role") or "other"
            play_type = play.get("play_type")

            first_down = bool(
                self._to_int(play.get("first_down_pass"))
                or self._to_int(play.get("first_down_rush"))
            )
            touchdown = bool(self._to_int(play.get("touchdown")))
            turnover = bool(self._to_int(play.get("interception")))
            is_complete = bool(self._to_int(play.get("complete_pass")))
            is_sack = bool(self._to_int(play.get("sack")))
            epa = round(self._to_float(play.get("epa"), default=0.0), 2)

            route_path = self._build_route_path(play, role, start_x, end_x)

            # Resolve player identities
            passer_id = play.get("passer_player_id")
            passer_name = play.get("passer_player_name")
            rusher_id = play.get("rusher_player_id")
            rusher_name = play.get("rusher_player_name")
            receiver_id = play.get("receiver_player_id")
            receiver_name = play.get("receiver_player_name")

            # Get headshot URLs for involved players
            passer_headshot = player_headshots.get(passer_id, "") if passer_id else ""
            receiver_headshot = player_headshots.get(receiver_id, "") if receiver_id else ""
            rusher_headshot = player_headshots.get(rusher_id, "") if rusher_id else ""

            events.append(
                {
                    "index": idx,
                    "play_id": play.get("play_id"),
                    "role": role,
                    "play_type": play_type,
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
                    "posteam": play.get("posteam"),
                    "defteam": play.get("defteam"),
                    "home_score": self._nullable_int(play.get("total_home_score")),
                    "away_score": self._nullable_int(play.get("total_away_score")),
                    "passer_name": passer_name,
                    "passer_gsis_id": passer_id,
                    "passer_headshot_url": passer_headshot,
                    "rusher_name": rusher_name,
                    "rusher_gsis_id": rusher_id,
                    "rusher_headshot_url": rusher_headshot,
                    "receiver_name": receiver_name,
                    "receiver_gsis_id": receiver_id,
                    "receiver_headshot_url": receiver_headshot,
                    "badges": {
                        "touchdown": touchdown,
                        "first_down": first_down,
                        "turnover": turnover,
                        "big_play": abs(yards) >= 20,
                    },
                }
            )
            last_x = end_x

        return {
            "game_id": game_id,
            "year": year,
            "week": week,
            "total_events": len(events),
            "game_summary": game_summary,
            "events": events,
        }

    def _resolve_headshots(self, plays: List[Dict[str, Any]]) -> Dict[str, str]:
        """Build a mapping of GSIS player IDs to headshot URLs.

        Player IDs in play-by-play data are GSIS ids (e.g. ``00-0033106``),
        but the local database stores players with ``espn_<id>`` keys.  We
        use the ``nfl_data_py`` id-mapping table to bridge GSIS → ESPN, then
        look the headshot up via the ``espn_<id>`` player_id.
        """
        gsis_ids = set()
        for play in plays:
            for key in ("passer_player_id", "rusher_player_id", "receiver_player_id"):
                pid = play.get(key)
                if pid:
                    gsis_ids.add(pid)

        if not gsis_ids:
            return {}

        # ── Build GSIS → espn_<id> mapping via nfl_data_py ──────────────
        gsis_to_player_id: Dict[str, str] = {}
        try:
            import nfl_data_py as nfl
            id_map = nfl.import_ids()
            for gsis_id in gsis_ids:
                match = id_map[id_map["gsis_id"] == gsis_id]
                if not match.empty:
                    espn_id = match.iloc[0].get("espn_id")
                    if espn_id is not None:
                        try:
                            gsis_to_player_id[gsis_id] = f"espn_{int(espn_id)}"
                        except (ValueError, TypeError):
                            pass
                # Also try the nfl_ prefix as a fallback
                if gsis_id not in gsis_to_player_id:
                    gsis_to_player_id[gsis_id] = f"nfl_{gsis_id}"
        except Exception:
            # If the id-map import fails, fall back to nfl_ prefix only
            for gsis_id in gsis_ids:
                gsis_to_player_id[gsis_id] = f"nfl_{gsis_id}"

        # ── Batch-query the database for all candidate player_ids ───────
        candidate_ids = list(set(gsis_to_player_id.values()))
        players = (
            self.db.query(DBPlayer)
            .filter(DBPlayer.player_id.in_(candidate_ids))
            .all()
        )
        pid_to_headshot = {
            p.player_id: p.headshot_url
            for p in players
            if p.headshot_url
        }

        headshots: Dict[str, str] = {}
        for gsis_id in gsis_ids:
            db_pid = gsis_to_player_id.get(gsis_id, "")
            url = pid_to_headshot.get(db_pid, "")
            if url:
                headshots[gsis_id] = url

        return headshots

    # ------------------------------------------------------------------
    # Route-path builder (adapted from PlayerGameSimulationService)
    # ------------------------------------------------------------------

    def _build_route_path(
        self, play: Dict[str, Any], role: str, start_x: float, end_x: float,
    ) -> Dict[str, Any]:
        play_id = play.get("play_id") or 0
        is_complete = bool(self._to_int(play.get("complete_pass")))
        is_touchdown = bool(self._to_int(play.get("touchdown")))
        is_sack = bool(self._to_int(play.get("sack")))
        air_yards = self._to_float(play.get("air_yards"), default=0.0)
        yac = self._to_float(play.get("yards_after_catch"), default=0.0)
        pass_location = play.get("pass_location")
        run_location = play.get("run_location")
        run_gap = play.get("run_gap")

        segments: List[Dict[str, Any]] = []

        if role == "pass":
            segments = self._pass_route(
                play_id, start_x, end_x, air_yards, yac,
                pass_location, is_complete, is_sack,
            )
        elif role in ("receive",):
            segments = self._receive_route(
                play_id, start_x, end_x, air_yards, yac,
                pass_location, is_complete,
            )
        elif role == "rush":
            segments = self._rush_route(
                play_id, start_x, end_x, run_location, run_gap,
            )
        else:
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

    def _pass_route(self, play_id, start_x, end_x, air_yards, yac,
                    pass_location, is_complete, is_sack):
        jitter = _deterministic_jitter(play_id, 6.0)
        los_lat = 50.0 + jitter
        if is_sack:
            sack_depth = self._clamp(start_x - abs(end_x - start_x), 0, 100)
            return [
                {"depth": start_x, "lateral": los_lat, "type": "los"},
                {"depth": self._clamp(start_x - 3, 0, 100), "lateral": los_lat, "type": "drop"},
                {"depth": sack_depth, "lateral": los_lat + jitter, "type": "sack_end"},
            ]
        drop_depth = self._clamp(start_x - 5, 0.0, 100.0)
        target_lat = _LATERAL_MAP.get(str(pass_location), 50.0) + jitter
        target_depth = self._clamp(start_x + air_yards, 0, 100)
        segs = [
            {"depth": start_x, "lateral": los_lat, "type": "los"},
            {"depth": drop_depth, "lateral": los_lat, "type": "drop"},
            {"depth": target_depth, "lateral": target_lat, "type": "target"},
        ]
        if is_complete and abs(yac) > 0.5:
            yac_lat = target_lat + _deterministic_jitter(play_id + 7, 5.0)
            segs.append({"depth": self._clamp(target_depth + yac, 0, 100), "lateral": yac_lat, "type": "catch_end"})
        return segs

    def _receive_route(self, play_id, start_x, end_x, air_yards, yac,
                       pass_location, is_complete):
        jitter = _deterministic_jitter(play_id, 8.0)
        base_lat = _LATERAL_MAP.get(str(pass_location), 50.0)
        stem_lat = 50.0 + jitter * 0.5
        target_lat = self._clamp(base_lat + jitter, 5, 95)
        target_depth = self._clamp(start_x + max(air_yards, 0), 0, 100)
        mid_depth = self._clamp(start_x + max(air_yards * 0.5, 1), 0, 100)
        mid_lat = stem_lat + (target_lat - stem_lat) * 0.3
        segs = [
            {"depth": start_x, "lateral": stem_lat, "type": "los"},
            {"depth": mid_depth, "lateral": mid_lat, "type": "route_break"},
            {"depth": target_depth, "lateral": target_lat, "type": "target"},
        ]
        if is_complete and abs(yac) > 0.5:
            yac_lat = target_lat + _deterministic_jitter(play_id + 13, 6.0)
            segs.append({"depth": self._clamp(target_depth + yac, 0, 100), "lateral": yac_lat, "type": "catch_end"})
        return segs

    def _rush_route(self, play_id, start_x, end_x, run_location, run_gap):
        jitter = _deterministic_jitter(play_id, 5.0)
        base_lat = _LATERAL_MAP.get(str(run_location), 50.0)
        gap_offset = _RUN_GAP_OFFSET.get(str(run_gap), 0.0)
        if str(run_location) == "left":
            gap_offset = -gap_offset
        target_lat = self._clamp(base_lat + gap_offset + jitter, 5, 95)
        los_lat = 50.0 + jitter * 0.5
        handoff_depth = self._clamp(start_x - 1.5, 0, 100)
        hit_depth = self._clamp(start_x + min(max(end_x - start_x, 0) * 0.3, 3), 0, 100)
        return [
            {"depth": start_x, "lateral": los_lat, "type": "los"},
            {"depth": handoff_depth, "lateral": los_lat, "type": "run_start"},
            {"depth": hit_depth, "lateral": target_lat, "type": "run_gap"},
            {"depth": end_x, "lateral": target_lat + _deterministic_jitter(play_id + 3, 4.0), "type": "run_end"},
        ]

    @staticmethod
    def _lane_pct(role: str) -> float:
        if role == "pass":
            return 32.0
        if role == "receive":
            return 70.0
        return 50.0

    @staticmethod
    def _format_time_label(play: Dict[str, Any]) -> str:
        qtr = NFLGameSimulationService._to_int(play.get("qtr"))
        qtr_seconds = NFLGameSimulationService._nullable_int(
            play.get("quarter_seconds_remaining")
        )
        if not qtr or qtr_seconds is None:
            return "—"
        mm = qtr_seconds // 60
        ss = qtr_seconds % 60
        return f"Q{qtr} {mm}:{ss:02d}"

    @staticmethod
    def _format_down_distance(play: Dict[str, Any]) -> str:
        down = NFLGameSimulationService._nullable_int(play.get("down"))
        ydstogo = NFLGameSimulationService._nullable_int(play.get("ydstogo"))
        if down is None or ydstogo is None:
            return "—"
        return f"{down}&{ydstogo}"

    @staticmethod
    def _to_field_x(yardline_100: Any) -> Optional[float]:
        yardline = NFLGameSimulationService._nullable_float(yardline_100)
        if yardline is None:
            return None
        return 100.0 - NFLGameSimulationService._clamp(yardline, 0.0, 100.0)

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
