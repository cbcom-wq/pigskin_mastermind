"""Mock draft service for fantasy football draft simulation."""

import random
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional


class DraftStrategy(str, Enum):
    """Draft strategies for automated AI teams."""
    BEST_AVAILABLE = "best_available"
    RB_HEAVY = "rb_heavy"
    WR_HEAVY = "wr_heavy"
    QB_EARLY = "qb_early"
    TE_EARLY = "te_early"
    HERO_RB = "hero_rb"
    POSITION_BY_ROUND = "position_by_round"

    @classmethod
    def descriptions(cls) -> Dict[str, str]:
        return {
            cls.BEST_AVAILABLE: "Always draft the highest-projected available player",
            cls.RB_HEAVY: "Prioritize RBs in rounds 1–4, then best available",
            cls.WR_HEAVY: "Prioritize WRs in rounds 1–4, then best available",
            cls.QB_EARLY: "Draft a QB in rounds 1–2, then best available",
            cls.TE_EARLY: "Draft a TE in rounds 1–2, then best available",
            cls.HERO_RB: "Take one elite RB early, then load up on WRs",
            cls.POSITION_BY_ROUND: "Draft specific positions based on a custom round map",
        }


# Default lineup requirements for a standard 9-starter roster
DEFAULT_LINEUP_SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}
FLEX_ELIGIBLE = {"RB", "WR", "TE"}
DRAFT_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def _default_player_pool() -> List[Dict[str, Any]]:
    """Generate a default pool of mock players when no DB players are available."""
    pool: List[Dict[str, Any]] = []

    # (name_prefix, position, nfl_team, base_proj)
    templates = [
        # QBs
        ("Elite QB", "QB", "KC", 28.0),
        ("QB Alpha", "QB", "BUF", 25.0),
        ("QB Beta", "QB", "SF", 23.0),
        ("QB Gamma", "QB", "PHI", 21.0),
        ("QB Delta", "QB", "MIA", 19.0),
        ("QB Epsilon", "QB", "LAR", 17.0),
        ("QB Zeta", "QB", "CIN", 16.0),
        ("QB Eta", "QB", "DAL", 15.0),
        ("QB Theta", "QB", "GB", 14.0),
        ("QB Iota", "QB", "NYJ", 13.0),
        ("QB Kappa", "QB", "LV", 12.0),
        ("QB Lambda", "QB", "NE", 11.0),
        # RBs
        ("RB1 Alpha", "RB", "SF", 22.0),
        ("RB2 Beta", "RB", "DAL", 20.5),
        ("RB3 Gamma", "RB", "BUF", 19.0),
        ("RB4 Delta", "RB", "PHI", 17.5),
        ("RB5 Epsilon", "RB", "MIA", 16.0),
        ("RB6 Zeta", "RB", "KC", 15.0),
        ("RB7 Eta", "RB", "GB", 14.0),
        ("RB8 Theta", "RB", "LAR", 13.5),
        ("RB9 Iota", "RB", "CIN", 12.5),
        ("RB10 Kappa", "RB", "SEA", 12.0),
        ("RB11 Lambda", "RB", "ATL", 11.5),
        ("RB12 Mu", "RB", "DEN", 11.0),
        ("RB13 Nu", "RB", "NYG", 10.5),
        ("RB14 Xi", "RB", "TEN", 10.0),
        ("RB15 Omicron", "RB", "JAX", 9.5),
        ("RB16 Pi", "RB", "CAR", 9.0),
        ("RB17 Rho", "RB", "ARI", 8.5),
        ("RB18 Sigma", "RB", "WAS", 8.0),
        ("RB19 Tau", "RB", "IND", 7.5),
        ("RB20 Upsilon", "RB", "HOU", 7.0),
        # WRs
        ("WR1 Alpha", "WR", "CIN", 21.0),
        ("WR2 Beta", "WR", "KC", 20.0),
        ("WR3 Gamma", "WR", "BUF", 18.5),
        ("WR4 Delta", "WR", "LAR", 18.0),
        ("WR5 Epsilon", "WR", "PHI", 17.5),
        ("WR6 Zeta", "WR", "MIA", 16.5),
        ("WR7 Eta", "WR", "SF", 16.0),
        ("WR8 Theta", "WR", "GB", 15.5),
        ("WR9 Iota", "WR", "DAL", 15.0),
        ("WR10 Kappa", "WR", "SEA", 14.5),
        ("WR11 Lambda", "WR", "NYJ", 14.0),
        ("WR12 Mu", "WR", "TB", 13.5),
        ("WR13 Nu", "WR", "MIN", 13.0),
        ("WR14 Xi", "WR", "DEN", 12.5),
        ("WR15 Omicron", "WR", "ATL", 12.0),
        ("WR16 Pi", "WR", "CLE", 11.5),
        ("WR17 Rho", "WR", "CAR", 11.0),
        ("WR18 Sigma", "WR", "HOU", 10.5),
        ("WR19 Tau", "WR", "IND", 10.0),
        ("WR20 Upsilon", "WR", "ARI", 9.5),
        # TEs
        ("TE1 Alpha", "TE", "KC", 18.0),
        ("TE2 Beta", "TE", "BUF", 14.0),
        ("TE3 Gamma", "TE", "SF", 12.0),
        ("TE4 Delta", "TE", "PHI", 10.5),
        ("TE5 Epsilon", "TE", "LAR", 9.5),
        ("TE6 Zeta", "TE", "CIN", 8.5),
        ("TE7 Eta", "TE", "SEA", 8.0),
        ("TE8 Theta", "TE", "GB", 7.5),
        ("TE9 Iota", "TE", "DAL", 7.0),
        ("TE10 Kappa", "TE", "MIA", 6.5),
        # Ks
        ("K1 Alpha", "K", "KC", 9.0),
        ("K2 Beta", "K", "BUF", 8.5),
        ("K3 Gamma", "K", "BAL", 8.0),
        ("K4 Delta", "K", "LAR", 7.5),
        ("K5 Epsilon", "K", "SF", 7.0),
        ("K6 Zeta", "K", "MIA", 6.5),
        # DEFs
        ("DEF1 Alpha", "DEF", "SF", 10.0),
        ("DEF2 Beta", "DEF", "BAL", 9.5),
        ("DEF3 Gamma", "DEF", "BUF", 9.0),
        ("DEF4 Delta", "DEF", "DAL", 8.5),
        ("DEF5 Epsilon", "DEF", "PIT", 8.0),
        ("DEF6 Zeta", "DEF", "MIA", 7.5),
    ]

    for i, (name, pos, team, base) in enumerate(templates):
        # Small random jitter so players aren't perfectly deterministic
        proj = round(base + random.uniform(-0.5, 0.5), 1)
        pool.append({
            "id": f"mock_{i}",
            "name": name,
            "position": pos,
            "nfl_team": team,
            "projected_points": proj,
        })

    return pool


class MockDraftEngine:
    """
    Engine for running mock fantasy football drafts.

    Supports two modes:
    - **Interactive**: The user manually selects picks for their team while
      AI auto-picks for all other teams.
    - **Simulation**: All teams use automated strategies; multiple iterations
      can be run to compare results.
    """

    # Maximum number of simulation runs allowed per call
    MAX_SIMULATIONS = 20

    def __init__(self) -> None:
        self._drafts: Dict[str, Dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_draft(
        self,
        num_teams: int = 10,
        num_rounds: int = 15,
        user_pick_position: int = 1,
        ai_strategies: Optional[Dict[str, str]] = None,
        player_pool: Optional[List[Dict[str, Any]]] = None,
        position_by_round: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        """
        Create and return a new draft state.

        Args:
            num_teams: Total number of teams in the draft (2–20).
            num_rounds: Number of draft rounds (1–20).
            user_pick_position: The pick slot (1-indexed) assigned to the human user.
            ai_strategies: Mapping of pick-slot string → strategy name for AI teams.
                           Defaults to BEST_AVAILABLE for all AI teams.
            player_pool: Optional list of player dicts with keys
                         ``id, name, position, nfl_team, projected_points``.
                         Defaults to the built-in mock player pool.
            position_by_round: Mapping of round (1-indexed) → position for the
                                POSITION_BY_ROUND strategy (applied to the user team
                                when that strategy is selected).

        Returns:
            Draft state dictionary suitable for JSON serialisation.
        """
        if not (2 <= num_teams <= 20):
            raise ValueError("num_teams must be between 2 and 20")
        if not (1 <= num_rounds <= 20):
            raise ValueError("num_rounds must be between 1 and 20")
        if not (1 <= user_pick_position <= num_teams):
            raise ValueError("user_pick_position must be between 1 and num_teams")

        draft_id = str(uuid.uuid4())

        # Build pick order (snake draft)
        pick_order = self._build_snake_order(num_teams, num_rounds)

        # Assign strategies
        strategies: Dict[str, str] = {}
        for slot in range(1, num_teams + 1):
            if slot == user_pick_position:
                strategies[str(slot)] = "user"
            else:
                strategies[str(slot)] = (
                    (ai_strategies or {}).get(str(slot), DraftStrategy.BEST_AVAILABLE)
                )

        # Initialise team rosters
        rosters: Dict[str, List[Dict[str, Any]]] = {
            str(slot): [] for slot in range(1, num_teams + 1)
        }

        pool = player_pool if player_pool is not None else _default_player_pool()
        # Sort pool descending by projected_points so display is consistent
        pool = sorted(pool, key=lambda p: p["projected_points"], reverse=True)

        state: Dict[str, Any] = {
            "draft_id": draft_id,
            "num_teams": num_teams,
            "num_rounds": num_rounds,
            "user_pick_position": user_pick_position,
            "strategies": strategies,
            "pick_order": pick_order,            # flat list of (round, slot) tuples → stored as lists
            "current_pick_index": 0,
            "available_players": pool,
            "rosters": rosters,
            "picks_log": [],                     # [{"round": r, "slot": s, "player": {...}}]
            "status": "in_progress",             # in_progress | complete
            "position_by_round": position_by_round or {},
        }

        self._drafts[draft_id] = state
        return self._public_state(state)

    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Return the public state of an existing draft, or None."""
        state = self._drafts.get(draft_id)
        return self._public_state(state) if state else None

    def make_user_pick(
        self, draft_id: str, player_id: str
    ) -> Dict[str, Any]:
        """
        Register a manual pick by the user and then auto-advance AI picks
        until it is the user's turn again (or the draft ends).

        Args:
            draft_id: Draft identifier.
            player_id: ``id`` field of the chosen player.

        Returns:
            Updated draft state.

        Raises:
            ValueError: If the draft is not found, complete, or it is not
                        currently the user's turn.
        """
        state = self._get_state(draft_id)

        current_slot = self._current_slot(state)
        user_slot = str(state["user_pick_position"])
        if str(current_slot) != user_slot:
            raise ValueError("It is not the user's turn to pick")

        self._apply_pick(state, player_id)
        # Advance AI picks until user's turn or draft over
        self._advance_ai_picks(state)
        return self._public_state(state)

    def run_simulations(
        self,
        num_teams: int = 10,
        num_rounds: int = 15,
        strategies: Optional[Dict[str, str]] = None,
        num_simulations: int = 5,
        player_pool: Optional[List[Dict[str, Any]]] = None,
        position_by_round: Optional[Dict[int, str]] = None,
    ) -> Dict[str, Any]:
        """
        Run fully automated draft simulations and return aggregated results.

        Args:
            num_teams: Number of teams.
            num_rounds: Number of rounds.
            strategies: Mapping slot (str) → strategy name.
                        Defaults to BEST_AVAILABLE for all teams.
            num_simulations: How many simulations to run (1–20).
            player_pool: Optional player pool override.
            position_by_round: Round→position map for POSITION_BY_ROUND strategy.

        Returns:
            Dictionary with ``simulations`` (list of draft results) and
            ``summary`` (aggregated stats per strategy).
        """
        num_simulations = max(1, min(num_simulations, self.MAX_SIMULATIONS))

        results = []
        strategy_map: Dict[str, str] = strategies or {}

        for sim_num in range(1, num_simulations + 1):
            draft_id = str(uuid.uuid4())
            pick_order = self._build_snake_order(num_teams, num_rounds)

            resolved: Dict[str, str] = {}
            for slot in range(1, num_teams + 1):
                resolved[str(slot)] = strategy_map.get(
                    str(slot), DraftStrategy.BEST_AVAILABLE
                )

            rosters: Dict[str, List[Dict[str, Any]]] = {
                str(slot): [] for slot in range(1, num_teams + 1)
            }

            pool = player_pool if player_pool is not None else _default_player_pool()
            pool = sorted(pool, key=lambda p: p["projected_points"], reverse=True)

            state: Dict[str, Any] = {
                "draft_id": draft_id,
                "num_teams": num_teams,
                "num_rounds": num_rounds,
                "user_pick_position": None,
                "strategies": resolved,
                "pick_order": pick_order,
                "current_pick_index": 0,
                "available_players": pool,
                "rosters": rosters,
                "picks_log": [],
                "status": "in_progress",
                "position_by_round": position_by_round or {},
            }

            # Run all picks automatically
            while state["status"] == "in_progress":
                current_slot = self._current_slot(state)
                if current_slot is None:
                    break
                slot_str = str(current_slot)
                strat = state["strategies"].get(slot_str, DraftStrategy.BEST_AVAILABLE)
                current_round = self._current_round(state)
                player_id = self._ai_choose_player(
                    state["available_players"],
                    state["rosters"][slot_str],
                    strat,
                    current_round,
                    state.get("position_by_round", {}),
                )
                if player_id:
                    self._apply_pick(state, player_id)

            results.append({
                "simulation": sim_num,
                "rosters": {
                    slot: {
                        "strategy": resolved[slot],
                        "players": list(rosters[slot]),
                        "projected_total": round(
                            sum(p["projected_points"] for p in rosters[slot]), 2
                        ),
                        "position_counts": self._count_positions(rosters[slot]),
                    }
                    for slot in resolved
                },
            })

        summary = self._build_summary(results, num_teams)

        return {"simulations": results, "summary": summary}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_state(self, draft_id: str) -> Dict[str, Any]:
        state = self._drafts.get(draft_id)
        if not state:
            raise ValueError(f"Draft {draft_id} not found")
        if state["status"] == "complete":
            raise ValueError("Draft is already complete")
        return state

    @staticmethod
    def _build_snake_order(num_teams: int, num_rounds: int) -> List[List[int]]:
        """Return a flat list of [round, slot] pairs in snake order."""
        order = []
        for rnd in range(1, num_rounds + 1):
            slots = range(1, num_teams + 1) if rnd % 2 == 1 else range(num_teams, 0, -1)
            for slot in slots:
                order.append([rnd, slot])
        return order

    def _current_slot(self, state: Dict[str, Any]) -> Optional[int]:
        idx = state["current_pick_index"]
        if idx >= len(state["pick_order"]):
            return None
        return state["pick_order"][idx][1]

    def _current_round(self, state: Dict[str, Any]) -> int:
        idx = state["current_pick_index"]
        if idx >= len(state["pick_order"]):
            return state["num_rounds"]
        return state["pick_order"][idx][0]

    def _apply_pick(self, state: Dict[str, Any], player_id: str) -> None:
        """Remove player from pool, add to roster, advance pick index."""
        pool = state["available_players"]
        player = next((p for p in pool if p["id"] == player_id), None)
        if not player:
            raise ValueError(f"Player {player_id} not found in available pool")

        slot = self._current_slot(state)
        if slot is None:
            raise ValueError("Draft is already complete")

        state["available_players"] = [p for p in pool if p["id"] != player_id]
        state["rosters"][str(slot)].append(player)
        state["picks_log"].append({
            "round": self._current_round(state),
            "slot": slot,
            "pick_number": state["current_pick_index"] + 1,
            "player": player,
        })
        state["current_pick_index"] += 1

        if state["current_pick_index"] >= len(state["pick_order"]):
            state["status"] = "complete"

    def _advance_ai_picks(self, state: Dict[str, Any]) -> None:
        """Auto-pick for every AI team until it is the user's turn or draft ends."""
        user_slot = str(state.get("user_pick_position", ""))
        while state["status"] == "in_progress":
            current_slot = self._current_slot(state)
            if current_slot is None:
                break
            if str(current_slot) == user_slot:
                break
            slot_str = str(current_slot)
            strat = state["strategies"].get(slot_str, DraftStrategy.BEST_AVAILABLE)
            current_round = self._current_round(state)
            player_id = self._ai_choose_player(
                state["available_players"],
                state["rosters"][slot_str],
                strat,
                current_round,
                state.get("position_by_round", {}),
            )
            if player_id:
                self._apply_pick(state, player_id)
            else:
                break

    @staticmethod
    def _ai_choose_player(
        available: List[Dict[str, Any]],
        roster: List[Dict[str, Any]],
        strategy: str,
        current_round: int,
        position_by_round: Dict[int, str],
    ) -> Optional[str]:
        """
        Choose the best available player for an AI team based on strategy.

        Returns the player ``id`` string, or None if pool is empty.
        """
        if not available:
            return None

        owned_positions = [p["position"] for p in roster]

        def best_at(pos: str) -> Optional[Dict[str, Any]]:
            candidates = [p for p in available if p["position"] == pos]
            return max(candidates, key=lambda p: p["projected_points"]) if candidates else None

        def best_overall() -> Dict[str, Any]:
            return max(available, key=lambda p: p["projected_points"])

        def needs_position(pos: str, limit: int) -> bool:
            return owned_positions.count(pos) < limit

        strat = strategy

        if strat == DraftStrategy.POSITION_BY_ROUND:
            target_pos = position_by_round.get(current_round) or position_by_round.get(str(current_round))
            if target_pos:
                pick = best_at(target_pos)
                return pick["id"] if pick else best_overall()["id"]
            return best_overall()["id"]

        if strat == DraftStrategy.QB_EARLY:
            if current_round <= 2 and needs_position("QB", 1):
                pick = best_at("QB")
                if pick:
                    return pick["id"]
            return best_overall()["id"]

        if strat == DraftStrategy.TE_EARLY:
            if current_round <= 2 and needs_position("TE", 1):
                pick = best_at("TE")
                if pick:
                    return pick["id"]
            return best_overall()["id"]

        if strat == DraftStrategy.RB_HEAVY:
            rb_count = owned_positions.count("RB")
            if current_round <= 4 and rb_count < 4:
                pick = best_at("RB")
                if pick:
                    return pick["id"]
            return best_overall()["id"]

        if strat == DraftStrategy.WR_HEAVY:
            wr_count = owned_positions.count("WR")
            if current_round <= 4 and wr_count < 4:
                pick = best_at("WR")
                if pick:
                    return pick["id"]
            return best_overall()["id"]

        if strat == DraftStrategy.HERO_RB:
            rb_count = owned_positions.count("RB")
            wr_count = owned_positions.count("WR")
            if current_round == 1 and rb_count == 0:
                pick = best_at("RB")
                if pick:
                    return pick["id"]
            if current_round in (2, 3, 4) and wr_count < (current_round - 1):
                pick = best_at("WR")
                if pick:
                    return pick["id"]
            return best_overall()["id"]

        # Default: BEST_AVAILABLE
        return best_overall()["id"]

    @staticmethod
    def _count_positions(players: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for p in players:
            pos = p["position"]
            counts[pos] = counts.get(pos, 0) + 1
        return counts

    @staticmethod
    def _build_summary(
        results: List[Dict[str, Any]], num_teams: int
    ) -> Dict[str, Any]:
        """Aggregate simulation results by strategy."""
        strategy_stats: Dict[str, Dict[str, Any]] = {}

        for result in results:
            for slot, data in result["rosters"].items():
                strat = data["strategy"]
                if strat not in strategy_stats:
                    strategy_stats[strat] = {
                        "strategy": strat,
                        "runs": 0,
                        "total_projected": 0.0,
                        "position_totals": {},
                    }
                entry = strategy_stats[strat]
                entry["runs"] += 1
                entry["total_projected"] += data["projected_total"]
                for pos, cnt in data["position_counts"].items():
                    entry["position_totals"][pos] = (
                        entry["position_totals"].get(pos, 0) + cnt
                    )

        summary = []
        for strat, entry in strategy_stats.items():
            runs = entry["runs"]
            avg_projected = round(entry["total_projected"] / runs, 2) if runs else 0.0
            avg_positions = {
                pos: round(cnt / runs, 1)
                for pos, cnt in entry["position_totals"].items()
            }
            summary.append({
                "strategy": strat,
                "runs": runs,
                "avg_projected_points": avg_projected,
                "avg_position_counts": avg_positions,
            })

        summary.sort(key=lambda s: s["avg_projected_points"], reverse=True)
        return {"by_strategy": summary}

    @staticmethod
    def _public_state(state: Dict[str, Any]) -> Dict[str, Any]:
        """Return a JSON-serialisable view of the draft state."""
        if state is None:
            return {}
        idx = state["current_pick_index"]
        pick_order = state["pick_order"]
        current_round = pick_order[idx][0] if idx < len(pick_order) else state["num_rounds"]
        current_slot = pick_order[idx][1] if idx < len(pick_order) else None

        return {
            "draft_id": state["draft_id"],
            "num_teams": state["num_teams"],
            "num_rounds": state["num_rounds"],
            "user_pick_position": state["user_pick_position"],
            "strategies": state["strategies"],
            "current_pick_index": idx,
            "current_round": current_round,
            "current_slot": current_slot,
            "total_picks": len(pick_order),
            "available_players": state["available_players"],
            "rosters": state["rosters"],
            "picks_log": state["picks_log"],
            "status": state["status"],
            "position_by_round": state.get("position_by_round", {}),
        }


# Module-level singleton used by API routes
draft_engine = MockDraftEngine()
