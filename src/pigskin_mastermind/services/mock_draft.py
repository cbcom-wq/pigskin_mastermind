"""Mock draft service for fantasy football draft simulation."""

import json
import math
import random
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen


# ESPN pro-team ID → NFL abbreviation (covers all 32 active franchises; unknown IDs map to "FA")
_ESPN_TEAM_MAP: Dict[int, str] = {
    1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE",
    6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 10: "TEN",
    11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA",
    16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
    21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF",
    26: "SEA", 27: "TB", 28: "WAS", 29: "CAR", 30: "JAX",
    33: "BAL", 34: "HOU",
}

# ESPN defaultPositionId → standard fantasy position abbreviation
_ESPN_POSITION_MAP: Dict[int, str] = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF",
}


def fetch_espn_adp(year: int = 2025, limit: int = 300) -> Optional[List[Dict[str, Any]]]:
    """Fetch ADP-ordered player rankings from ESPN's public fantasy API.

    Uses ESPN's league-defaults endpoint which is publicly accessible and
    returns players sorted by Average Draft Position (PPR scoring).

    Args:
        year: The fantasy football season year (e.g. 2025).
        limit: Maximum number of players to return (default 300).

    Returns:
        List of player dicts (id, name, position, nfl_team, projected_points,
        adp_rank) ordered by ADP, or ``None`` if the request fails.
    """
    url = (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{year}"
        f"/segments/0/leaguedefaults/3?view=kona_player_info"
    )

    fantasy_filter = json.dumps({
        "players": {
            "filterSlotIds": {
                "value": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 23, 24],
            },
            "sortAdp": {"sortPriority": 1, "sortAsc": True},
            "limit": limit,
            "filterRanksForScoringPeriodIds": {"value": [0]},
            "filterRanksForRankTypes": {"value": ["PPR"]},
        }
    })

    req = Request(
        url,
        headers={
            "X-Fantasy-Filter": fantasy_filter,
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (compatible; PigskinMastermind/1.0)",
        },
    )

    try:
        with urlopen(req, timeout=10) as response:
            data = json.loads(response.read())
    except (URLError, OSError, ValueError):
        return None

    raw_players = data.get("players", [])
    if not raw_players:
        return None

    players: List[Dict[str, Any]] = []
    for i, entry in enumerate(raw_players):
        # ESPN API returns player data directly on each entry (no playerPoolEntry wrapper).
        player_info = entry.get("player", {})

        name = player_info.get("fullName")
        if not name:
            continue

        pos_id = player_info.get("defaultPositionId")
        position = _ESPN_POSITION_MAP.get(pos_id)
        if not position:
            continue

        # NFL team comes from the player's proTeamId field
        nfl_team = _ESPN_TEAM_MAP.get(player_info.get("proTeamId", 0), "FA")

        # ADP lives inside the player's ownership data
        ownership = player_info.get("ownership", {})
        adp = (
            ownership.get("averageDraftPositionPPR")
            or ownership.get("averageDraftPosition")
            or float(i + 1)
        )

        # Projected points come from the top-level ratings object
        ratings = entry.get("ratings", {})
        rating_entry = ratings.get("0", {})
        projected = round(float(rating_entry.get("totalRating") or 0.0), 1)

        players.append({
            "id": f"espn_{entry.get('id', i)}",
            "name": name,
            "position": position,
            "nfl_team": nfl_team,
            "projected_points": projected,
            "adp_rank": round(float(adp), 1),
        })

    return players if players else None


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

# Minimum roster targets for a well-constructed team
_STARTER_NEEDS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1}
# Comfortable depth (includes bench) — beyond this the AI deprioritises
_DEPTH_CAPS = {"QB": 2, "RB": 5, "WR": 5, "TE": 2, "K": 1, "DEF": 1}
# Positions that should only be drafted in later rounds
_LATE_ROUND_POSITIONS = {"K", "DEF"}

# ---------------------------------------------------------------------------
# Common lineup format presets
# ---------------------------------------------------------------------------
#
# Each preset is a dict of position → number of starting slots.
# FLEX = RB/WR/TE eligible slot.  SUPERFLEX = QB/RB/WR/TE eligible slot.
# ---------------------------------------------------------------------------
LINEUP_PRESETS: Dict[str, Dict[str, Any]] = {
    "standard": {
        "label": "Standard (9-man)",
        "description": "Classic 9-starter format: 1QB 2RB 2WR 1TE 1FLEX 1K 1DST",
        "slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1},
    },
    "ppr_10": {
        "label": "PPR (10-man)",
        "description": "10-starter PPR: 1QB 2RB 3WR 1TE 1FLEX 1K 1DST",
        "slots": {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1},
    },
    "superflex": {
        "label": "Superflex",
        "description": "10-starter with a QB-eligible SUPERFLEX slot; QBs have huge value",
        "slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "K": 1, "DEF": 1},
    },
    "two_qb": {
        "label": "2-QB",
        "description": "Requires two starting QBs — draft a QB early AND late",
        "slots": {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1},
    },
    "te_premium": {
        "label": "TE Premium",
        "description": "Two TE starter slots — elite TEs are must-have targets",
        "slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 2, "FLEX": 1, "K": 1, "DEF": 1},
    },
    "deep_flex": {
        "label": "Deep Flex (3 FLEX)",
        "description": "Three FLEX slots on top of 1QB 2RB 2WR 1TE — great for depth",
        "slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 3, "K": 1, "DEF": 1},
    },
    "no_k_def": {
        "label": "No K/DST",
        "description": "Skip kickers and defenses entirely — 1QB 3RB 4WR 2TE 2FLEX",
        "slots": {"QB": 1, "RB": 3, "WR": 4, "TE": 2, "FLEX": 2, "K": 0, "DEF": 0},
    },
}


def _derive_starter_needs(lineup_slots: Dict[str, int]) -> Dict[str, int]:
    """Compute minimum starter targets for each draftable position.

    FLEX slots are counted toward the most-needed flex-eligible position (RB).
    SUPERFLEX slots count toward QB needs.
    """
    needs: Dict[str, int] = {}
    for pos in DRAFT_POSITIONS:
        needs[pos] = lineup_slots.get(pos, 0)
    # Each FLEX slot bumps RB need by 1 (RB is the most common FLEX player)
    flex = lineup_slots.get("FLEX", 0)
    if flex > 0:
        needs["RB"] = needs.get("RB", 0) + flex
    # Each SUPERFLEX slot bumps QB need
    superflex = lineup_slots.get("SUPERFLEX", 0)
    if superflex > 0:
        needs["QB"] = needs.get("QB", 0) + superflex
    return needs


def _derive_hard_starter_needs(lineup_slots: Dict[str, int]) -> Dict[str, int]:
    """Positional starting slots that *only* that position can fill.

    Unlike :func:`_derive_starter_needs`, FLEX/SUPERFLEX slots are excluded:
    those accept several positions, so a team is never locked out of
    fielding a legal lineup by leaving them for last.  Used to decide when
    filling a starting slot must outrank taking the best value on the board.
    """
    return {pos: lineup_slots.get(pos, 0) for pos in DRAFT_POSITIONS}


def _derive_depth_caps(starter_needs: Dict[str, int]) -> Dict[str, int]:
    """Compute comfortable roster depth caps derived from starter requirements."""
    return {
        "QB": max(starter_needs.get("QB", 1) + 1, 2),
        "RB": max(starter_needs.get("RB", 2) + 3, 5),
        "WR": max(starter_needs.get("WR", 2) + 3, 5),
        "TE": max(starter_needs.get("TE", 1) + 1, 2),
        "K":  max(starter_needs.get("K",  1), 1),
        "DEF": max(starter_needs.get("DEF", 1), 1),
    }


@dataclass
class AIProfile:
    """Per-slot AI behaviour profile controlling how aggressively / erratically
    a team drafts.  All values are 0-1 floats.

    Attributes:
        aggressiveness: How willing the AI is to reach for a need vs following
                        strict BPA.  0 = pure BPA, 1 = very aggressive reaches.
        variance: Controls the width of random noise injected into the
                  candidate scoring function.  0 = deterministic, 1 = chaotic.
        roster_balance: How much weight the AI puts on filling starting lineup
                        holes vs taking the best raw talent.  0 = ignore roster,
                        1 = strictly prioritise starters.
    """
    aggressiveness: float = 0.3
    variance: float = 0.15
    roster_balance: float = 0.6

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AIProfile":
        return cls(
            aggressiveness=float(d.get("aggressiveness", 0.3)),
            variance=float(d.get("variance", 0.15)),
            roster_balance=float(d.get("roster_balance", 0.6)),
        )


def randomize_ai_profiles(
    num_teams: int,
    user_pick_position: int,
    overall_aggressiveness: float = 0.5,
) -> Dict[str, Dict[str, Any]]:
    """Generate randomised AI profiles and strategies for every non-user slot.

    Args:
        num_teams: Total teams in draft.
        user_pick_position: 1-indexed slot belonging to the human.
        overall_aggressiveness: 0-1 master knob.  Higher values widen the
            range of per-slot aggressiveness and variance.

    Returns:
        Dict mapping slot string → ``{"strategy": str, "profile": dict}``.
    """
    ai_strats = [s for s in DraftStrategy if s != DraftStrategy.POSITION_BY_ROUND]
    result: Dict[str, Dict[str, Any]] = {}
    for slot in range(1, num_teams + 1):
        if slot == user_pick_position:
            continue
        strat = random.choice(ai_strats)
        # Centre aggressiveness around the overall knob with per-slot jitter
        aggr = max(0.0, min(1.0, overall_aggressiveness + random.gauss(0, 0.15)))
        var = max(0.0, min(1.0, 0.10 + overall_aggressiveness * 0.2 + random.gauss(0, 0.05)))
        bal = max(0.0, min(1.0, 0.55 + random.gauss(0, 0.1)))
        result[str(slot)] = {
            "strategy": strat.value,
            "profile": AIProfile(aggressiveness=round(aggr, 2),
                                 variance=round(var, 2),
                                 roster_balance=round(bal, 2)).to_dict(),
        }
    return result


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
        user_pick_position: Optional[int] = 1,
        ai_strategies: Optional[Dict[str, str]] = None,
        player_pool: Optional[List[Dict[str, Any]]] = None,
        position_by_round: Optional[Dict[int, str]] = None,
        ai_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
        lineup_slots: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """
        Create and return a new draft state.

        Args:
            num_teams: Total number of teams in the draft (2–20).
            num_rounds: Number of draft rounds (1–20).
            user_pick_position: The pick slot (1-indexed) assigned to the human user.
                                When ``None``, a random slot is assigned.
            ai_strategies: Mapping of pick-slot string → strategy name for AI teams.
                           Defaults to BEST_AVAILABLE for all AI teams.
            player_pool: Optional list of player dicts with keys
                         ``id, name, position, nfl_team, projected_points``.
                         Defaults to the built-in mock player pool.
            position_by_round: Mapping of round (1-indexed) → position for the
                                POSITION_BY_ROUND strategy (applied to the user team
                                when that strategy is selected).
            ai_profiles: Mapping of pick-slot string → AIProfile dict.  Controls
                         aggressiveness, variance, and roster-balance per AI team.
                         Defaults to a moderate profile for all AI teams.
            lineup_slots: Mapping of position → number of starting slots that
                          defines the roster format (e.g. ``{"QB": 1, "RB": 2,
                          "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}``).
                          Defaults to ``DEFAULT_LINEUP_SLOTS``.

        Returns:
            Draft state dictionary suitable for JSON serialisation.
        """
        if not (2 <= num_teams <= 20):
            raise ValueError("num_teams must be between 2 and 20")
        if not (1 <= num_rounds <= 20):
            raise ValueError("num_rounds must be between 1 and 20")
        if user_pick_position is None:
            user_pick_position = random.randint(1, num_teams)
        if not (1 <= user_pick_position <= num_teams):
            raise ValueError("user_pick_position must be between 1 and num_teams")

        draft_id = str(uuid.uuid4())

        # Build pick order (snake draft)
        pick_order = self._build_snake_order(num_teams, num_rounds)

        # Assign strategies and profiles
        strategies: Dict[str, str] = {}
        profiles: Dict[str, Dict[str, Any]] = {}
        for slot in range(1, num_teams + 1):
            slot_s = str(slot)
            if slot == user_pick_position:
                strategies[slot_s] = "user"
            else:
                strategies[slot_s] = (
                    (ai_strategies or {}).get(slot_s, DraftStrategy.BEST_AVAILABLE)
                )
                if ai_profiles and slot_s in ai_profiles:
                    profiles[slot_s] = ai_profiles[slot_s]
                else:
                    profiles[slot_s] = AIProfile().to_dict()

        # Initialise team rosters
        rosters: Dict[str, List[Dict[str, Any]]] = {
            str(slot): [] for slot in range(1, num_teams + 1)
        }

        if player_pool is None:
            raise ValueError("player_pool is required; use use_ffc_adp or use_espn_adp to load players")
        pool = list(player_pool)
        # Sort pool: by ADP rank (ascending) when available, else by projected_points (descending)
        if pool and pool[0].get("adp_rank") is not None:
            pool = sorted(pool, key=lambda p: p.get("adp_rank") or 9999)
        else:
            pool = sorted(pool, key=lambda p: p["projected_points"], reverse=True)

        resolved_lineup = dict(lineup_slots) if lineup_slots else dict(DEFAULT_LINEUP_SLOTS)

        state: Dict[str, Any] = {
            "draft_id": draft_id,
            "num_teams": num_teams,
            "num_rounds": num_rounds,
            "user_pick_position": user_pick_position,
            "strategies": strategies,
            "ai_profiles": profiles,
            "pick_order": pick_order,            # flat list of (round, slot) tuples → stored as lists
            "current_pick_index": 0,
            "available_players": pool,
            "rosters": rosters,
            "picks_log": [],                     # [{"round": r, "slot": s, "player": {...}}]
            "status": "in_progress",             # in_progress | complete
            "position_by_round": position_by_round or {},
            "lineup_slots": resolved_lineup,
        }

        self._drafts[draft_id] = state
        return self._public_state(state)

    def get_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Return the public state of an existing draft, or None."""
        state = self._drafts.get(draft_id)
        return self._public_state(state) if state else None

    def make_user_pick(
        self, draft_id: str, player_id: str, *, advance_ai: bool = True
    ) -> Dict[str, Any]:
        """
        Register a manual pick by the user and optionally auto-advance AI picks
        until it is the user's turn again (or the draft ends).

        Args:
            draft_id: Draft identifier.
            player_id: ``id`` field of the chosen player.
            advance_ai: When *True* (default), AI picks are auto-advanced after
                the user's pick.  Set to *False* when the frontend will use the
                staggered ``/draft/advance`` endpoint instead.

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
        if advance_ai:
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
        ai_profiles: Optional[Dict[str, Dict[str, Any]]] = None,
        lineup_slots: Optional[Dict[str, int]] = None,
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
            ai_profiles: Optional mapping slot → AIProfile dict.
            lineup_slots: Roster format overriding ``DEFAULT_LINEUP_SLOTS``.

        Returns:
            Dictionary with ``simulations`` (list of draft results) and
            ``summary`` (aggregated stats per strategy).
        """
        num_simulations = max(1, min(num_simulations, self.MAX_SIMULATIONS))

        resolved_lineup = dict(lineup_slots) if lineup_slots else dict(DEFAULT_LINEUP_SLOTS)
        sim_starter_needs = _derive_starter_needs(resolved_lineup)
        sim_hard_needs = _derive_hard_starter_needs(resolved_lineup)
        sim_depth_caps = _derive_depth_caps(sim_starter_needs)

        results = []
        strategy_map: Dict[str, str] = strategies or {}
        profile_map: Dict[str, Dict[str, Any]] = ai_profiles or {}

        for sim_num in range(1, num_simulations + 1):
            draft_id = str(uuid.uuid4())
            pick_order = self._build_snake_order(num_teams, num_rounds)

            resolved: Dict[str, str] = {}
            profiles: Dict[str, Dict[str, Any]] = {}
            for slot in range(1, num_teams + 1):
                slot_s = str(slot)
                resolved[slot_s] = strategy_map.get(
                    slot_s, DraftStrategy.BEST_AVAILABLE
                )
                profiles[slot_s] = profile_map.get(slot_s, AIProfile().to_dict())

            rosters: Dict[str, List[Dict[str, Any]]] = {
                str(slot): [] for slot in range(1, num_teams + 1)
            }

            if player_pool is None:
                raise ValueError("player_pool is required; use use_ffc_adp or use_espn_adp to load players")
            pool = list(player_pool)
            if pool and pool[0].get("adp_rank") is not None:
                pool = sorted(pool, key=lambda p: p.get("adp_rank") or 9999)
            else:
                pool = sorted(pool, key=lambda p: p["projected_points"], reverse=True)

            state: Dict[str, Any] = {
                "draft_id": draft_id,
                "num_teams": num_teams,
                "num_rounds": num_rounds,
                "user_pick_position": None,
                "strategies": resolved,
                "ai_profiles": profiles,
                "pick_order": pick_order,
                "current_pick_index": 0,
                "available_players": pool,
                "rosters": rosters,
                "picks_log": [],
                "status": "in_progress",
                "position_by_round": position_by_round or {},
                "lineup_slots": resolved_lineup,
            }

            # Run all picks automatically
            while state["status"] == "in_progress":
                current_slot = self._current_slot(state)
                if current_slot is None:
                    break
                slot_str = str(current_slot)
                strat = state["strategies"].get(slot_str, DraftStrategy.BEST_AVAILABLE)
                current_round = self._current_round(state)
                overall_pick = state["current_pick_index"] + 1
                profile = AIProfile.from_dict(state["ai_profiles"].get(slot_str, {}))
                player_id = self._ai_choose_player(
                    state["available_players"],
                    state["rosters"][slot_str],
                    strat,
                    current_round,
                    state.get("position_by_round", {}),
                    num_rounds=num_rounds,
                    profile=profile,
                    overall_pick=overall_pick,
                    starter_needs=sim_starter_needs,
                    depth_caps=sim_depth_caps,
                    hard_starter_needs=sim_hard_needs,
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
        s_needs = _derive_starter_needs(state.get("lineup_slots") or DEFAULT_LINEUP_SLOTS)
        d_caps = _derive_depth_caps(s_needs)
        h_needs = _derive_hard_starter_needs(state.get("lineup_slots") or DEFAULT_LINEUP_SLOTS)
        while state["status"] == "in_progress":
            current_slot = self._current_slot(state)
            if current_slot is None:
                break
            if str(current_slot) == user_slot:
                break
            slot_str = str(current_slot)
            strat = state["strategies"].get(slot_str, DraftStrategy.BEST_AVAILABLE)
            current_round = self._current_round(state)
            overall_pick = state["current_pick_index"] + 1
            profile = AIProfile.from_dict(state.get("ai_profiles", {}).get(slot_str, {}))
            player_id = self._ai_choose_player(
                state["available_players"],
                state["rosters"][slot_str],
                strat,
                current_round,
                state.get("position_by_round", {}),
                num_rounds=state["num_rounds"],
                profile=profile,
                overall_pick=overall_pick,
                starter_needs=s_needs,
                depth_caps=d_caps,
                hard_starter_needs=h_needs,
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
        *,
        num_rounds: int = 15,
        profile: Optional["AIProfile"] = None,
        overall_pick: int = 1,
        starter_needs: Optional[Dict[str, int]] = None,
        depth_caps: Optional[Dict[str, int]] = None,
        hard_starter_needs: Optional[Dict[str, int]] = None,
    ) -> Optional[str]:
        """
        Choose the best available player for an AI team.

        Realism model: only a bounded window of players near the top of the
        ADP-sorted board is considered (tight in early rounds, wider late),
        scored on ADP alignment plus smaller projection / roster-need /
        strategy nudges, then sampled with softmax randomness so picks
        cluster around consensus with occasional bounded surprises.

        Returns the player ``id`` string, or None if pool is empty.
        """
        if not available:
            return None

        prof = profile or AIProfile()
        _eff_starter_needs = starter_needs if starter_needs is not None else _STARTER_NEEDS
        _eff_depth_caps = depth_caps if depth_caps is not None else _DEPTH_CAPS
        owned_positions = [p["position"] for p in roster]
        pos_counts: Dict[str, int] = {}
        for pos in owned_positions:
            pos_counts[pos] = pos_counts.get(pos, 0) + 1

        round_frac = current_round / max(num_rounds, 1)  # 0..1

        # ---- candidate window ----
        # The front of the ADP-sorted board holds both on-schedule players
        # and anyone who has fallen past ADP. Restricting scoring to this
        # window is what keeps picks realistic: no term below can promote a
        # player from 80 spots down the board. (Players without ADP sort to
        # the back.)
        board = sorted(
            available,
            key=lambda p: (p.get("adp_rank") is None, p.get("adp_rank") or 0.0),
        )
        window = int(6 + 2 * current_round + 8 * prof.aggressiveness + 6 * prof.variance)
        window = max(8, min(window, 30))
        candidates = board[:window]

        # Roster-legality guard. Measured against *hard* positional slots
        # (FLEX/SUPERFLEX accept several positions, so they can never lock a
        # team out), this is what makes imported formats like 2-QB actually
        # bind: slack is how many picks remain beyond the ones still needed
        # to field a legal starting lineup.
        _eff_hard_needs = (
            hard_starter_needs if hard_starter_needs is not None else _eff_starter_needs
        )
        rounds_left = max(num_rounds - current_round + 1, 1)
        unmet_positions = [
            pos for pos, need in _eff_hard_needs.items()
            if pos_counts.get(pos, 0) < need
        ]
        unmet_slots = sum(
            _eff_hard_needs[pos] - pos_counts.get(pos, 0) for pos in unmet_positions
        )
        slack = rounds_left - unmet_slots

        if slack <= 3:
            candidate_positions = {p["position"] for p in candidates}
            for pos in unmet_positions:
                if pos in candidate_positions:
                    continue
                best_at_pos = next((p for p in board if p["position"] == pos), None)
                if best_at_pos is not None:
                    candidates.append(best_at_pos)

        def _starter_lockout(p: Dict[str, Any]) -> float:
            """Force unfilled starting slots when picks are running out.

            Without this a team finishes a 2-QB league with zero QBs: a
            deeply fallen value pick out-scores the modest unmet-starter
            urgency in ``_need_score``.  Ramps in over the last few picks
            and dominates outright at zero slack.
            """
            if slack > 2:
                return 0.0
            if pos_counts.get(p["position"], 0) >= _eff_hard_needs.get(p["position"], 0):
                return 0.0
            return 1.2 * (3 - max(slack, 0)) / 3

        has_adp = any(p.get("adp_rank") is not None for p in candidates)

        # ---- helper: projection nudge, normalised within position ----
        # Cross-position raw points would hand QBs a permanent head start.
        pos_max_proj: Dict[str, float] = {}
        for p in available:
            pts = p.get("projected_points") or 0.0
            if pts > pos_max_proj.get(p["position"], 0.0):
                pos_max_proj[p["position"]] = pts

        def _proj_score(p: Dict[str, Any]) -> float:
            top = pos_max_proj.get(p["position"], 0.0)
            return (p.get("projected_points") or 0.0) / top if top else 0.0

        # ---- helper: ADP-based primary score ----
        # 1.0 = player exactly at ADP. The reach cost is on an absolute
        # per-pick scale (denominator in picks, not pool size) and loosens
        # as the draft progresses; the steal bonus stays monotone so an
        # elite faller keeps getting more attractive until someone takes him.
        reach_denom = 12.0 + 24.0 * round_frac + 10.0 * prof.aggressiveness

        def _adp_score(p: Dict[str, Any]) -> float:
            adp = p.get("adp_rank")
            if adp is None or not has_adp:
                # No consensus data — rank below on-schedule players, ordered
                # by positional projection.
                return 0.5 + 0.4 * _proj_score(p)

            delta = overall_pick - adp
            if delta >= 0:
                # Fallen past ADP — half-max bonus at 25 picks, asymptote 0.6.
                return 1.0 + 0.6 * delta / (delta + 25.0)
            return max(1.0 - abs(delta) / reach_denom, 0.05)

        # ---- helper: roster-need bonus ----
        def _need_score(p: Dict[str, Any]) -> float:
            pos = p["position"]
            have = pos_counts.get(pos, 0)
            starter_need = _eff_starter_needs.get(pos, 0)
            depth_cap = _eff_depth_caps.get(pos, 99)

            if have < starter_need:
                # Still missing a starter — bonus scales up as draft progresses
                urgency = 0.15 + 0.25 * round_frac
                return urgency
            if have >= depth_cap:
                # Over-stocked — penalty
                return -0.35
            # Bench depth — small positive
            return 0.02

        # ---- helper: late-round K/DEF logic ----
        def _kdef_penalty(p: Dict[str, Any]) -> float:
            if p["position"] not in _LATE_ROUND_POSITIONS:
                return 0.0
            if pos_counts.get(p["position"], 0) >= _eff_starter_needs.get(p["position"], 1):
                return -0.8  # already have one — hard avoid
            # Smooth ramp from "too early" to "grab your starter now" —
            # no cliff at a single round boundary.
            ramp_start, ramp_end = 0.55, 0.95
            if round_frac < ramp_start:
                return -0.6
            progress = min((round_frac - ramp_start) / (ramp_end - ramp_start), 1.0)
            return -0.6 + 1.5 * progress

        # ---- helper: strategy bias ----
        def _strategy_score(p: Dict[str, Any]) -> float:
            pos = p["position"]
            strat = strategy

            if strat == DraftStrategy.POSITION_BY_ROUND:
                target = (position_by_round.get(current_round)
                          or position_by_round.get(str(current_round)))
                if target and pos == target:
                    return 0.6
                return 0.0

            if strat == DraftStrategy.QB_EARLY:
                if current_round <= 2 and pos == "QB" and pos_counts.get("QB", 0) < 1:
                    return 0.55
                return 0.0

            if strat == DraftStrategy.TE_EARLY:
                if current_round <= 2 and pos == "TE" and pos_counts.get("TE", 0) < 1:
                    return 0.6
                return 0.0

            if strat == DraftStrategy.RB_HEAVY:
                if current_round <= 4 and pos == "RB" and pos_counts.get("RB", 0) < 4:
                    return 0.5
                return 0.0

            if strat == DraftStrategy.WR_HEAVY:
                if current_round <= 4 and pos == "WR" and pos_counts.get("WR", 0) < 4:
                    return 0.5
                return 0.0

            if strat == DraftStrategy.HERO_RB:
                rb_cnt = pos_counts.get("RB", 0)
                wr_cnt = pos_counts.get("WR", 0)
                if current_round == 1 and pos == "RB" and rb_cnt == 0:
                    return 0.55
                if current_round in (2, 3, 4) and pos == "WR" and wr_cnt < (current_round - 1):
                    return 0.5
                return 0.0

            # BEST_AVAILABLE — no extra bias
            return 0.0

        # ---- composite score ----
        # ADP dominates: the candidate window bounds how far any preference
        # can deviate from consensus, and the terms below are sized so a
        # full-strength strategy bonus buys roughly a 5-8 pick reach in
        # round 1 (more later, as the reach denominator loosens).
        W_PROJ = 0.10      # Positional projection nudge
        W_NEED = 0.4       # Roster construction
        W_STRAT = 0.9      # Strategy emphasis (flavors return 0.5-0.6)
        W_KDEF = 1.0       # K/DEF timing gate

        scored: List[tuple] = []
        for p in candidates:
            total = (
                _adp_score(p)
                + _proj_score(p) * W_PROJ
                + _need_score(p) * W_NEED * prof.roster_balance
                + _strategy_score(p) * W_STRAT * (0.5 + 0.5 * prof.aggressiveness)
                + _kdef_penalty(p) * W_KDEF
                + _starter_lockout(p)
            )
            # Real drafter disagreement (FFC stdev) makes a player slightly
            # more likely to move around his consensus slot.
            stdev = p.get("adp_stdev")
            if stdev:
                total += 0.05 * min(float(stdev), 15.0) / 15.0
            scored.append((total, p))

        if prof.variance <= 0:
            # Deterministic: highest score, ties broken by ADP order.
            return max(scored, key=lambda x: x[0])[1]["id"]

        # Softmax sampling: bounded, tunable randomness. A candidate scoring
        # `temperature` below the leader is ~e^-1 (~37%) as likely.
        temperature = 0.03 + 0.12 * prof.variance
        best = max(s for s, _ in scored)
        weights = [math.exp((s - best) / temperature) for s, _ in scored]
        chosen = random.choices([p for _, p in scored], weights=weights, k=1)[0]
        return chosen["id"]

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

    def advance_one_ai_pick(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Advance exactly one AI pick and return the updated state.

        Returns ``None`` if it is the user's turn or the draft is complete.
        """
        state = self._drafts.get(draft_id)
        if not state or state["status"] == "complete":
            return None

        user_slot = str(state.get("user_pick_position", ""))
        current_slot = self._current_slot(state)
        if current_slot is None or str(current_slot) == user_slot:
            return None

        slot_str = str(current_slot)
        strat = state["strategies"].get(slot_str, DraftStrategy.BEST_AVAILABLE)
        current_round = self._current_round(state)
        overall_pick = state["current_pick_index"] + 1
        profile = AIProfile.from_dict(state.get("ai_profiles", {}).get(slot_str, {}))
        s_needs = _derive_starter_needs(state.get("lineup_slots") or DEFAULT_LINEUP_SLOTS)
        d_caps = _derive_depth_caps(s_needs)
        h_needs = _derive_hard_starter_needs(state.get("lineup_slots") or DEFAULT_LINEUP_SLOTS)
        player_id = self._ai_choose_player(
            state["available_players"],
            state["rosters"][slot_str],
            strat,
            current_round,
            state.get("position_by_round", {}),
            num_rounds=state["num_rounds"],
            profile=profile,
            overall_pick=overall_pick,
            starter_needs=s_needs,
            depth_caps=d_caps,
            hard_starter_needs=h_needs,
        )
        if player_id:
            self._apply_pick(state, player_id)

        return self._public_state(state)

    def grade_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Grade the user's draft performance.

        Returns a dictionary with letter grade, pick-by-pick analysis,
        and team strengths/weaknesses, or ``None`` if the draft is not
        found or not yet complete.
        """
        state = self._drafts.get(draft_id)
        if not state or state["status"] != "complete":
            return None

        user_slot = str(state.get("user_pick_position", ""))
        user_roster = state["rosters"].get(user_slot, [])
        all_picks = state["picks_log"]

        # Pick-by-pick value analysis
        pick_analysis = []
        total_value = 0.0
        for pick in all_picks:
            if str(pick["slot"]) != user_slot:
                continue
            p = pick["player"]
            adp = p.get("adp_rank")
            pick_num = pick["pick_number"]
            delta = (pick_num - adp) if adp is not None else 0
            total_value += delta

            if delta >= 10:
                verdict = "steal"
                label = "🔥 Great Steal"
            elif delta >= 3:
                verdict = "value"
                label = "✅ Good Value"
            elif delta >= -3:
                verdict = "fair"
                label = "Fair"
            elif delta >= -10:
                verdict = "slight_reach"
                label = "⚠️ Slight Reach"
            else:
                verdict = "reach"
                label = "⚠️ Big Reach"

            pick_analysis.append({
                "round": pick["round"],
                "pick_number": pick_num,
                "player": p["name"],
                "position": p["position"],
                "adp": adp,
                "delta": round(delta, 1) if adp is not None else None,
                "verdict": verdict,
                "label": label,
            })

        # Positional balance score (30%)
        pos_counts = self._count_positions(user_roster)
        balance_score = 0
        # Use the draft's configured lineup slots for grading (fall back to defaults)
        _grade_lineup = state.get("lineup_slots") or DEFAULT_LINEUP_SLOTS
        required = {pos: _grade_lineup.get(pos, 0) for pos in DRAFT_POSITIONS if _grade_lineup.get(pos, 0) > 0}
        for pos, need in required.items():
            have = pos_counts.get(pos, 0)
            if have >= need:
                balance_score += 1
        balance_pct = balance_score / len(required)

        # Tier distribution score (20%)
        tier1 = sum(1 for p in user_roster if (p.get("adp_rank") or 999) <= 36)
        tier2 = sum(1 for p in user_roster if 36 < (p.get("adp_rank") or 999) <= 72)
        tier_score = min(1.0, (tier1 * 0.15 + tier2 * 0.08))

        # ADP value score (50%) — normalize to 0-1 range
        num_picks = len(pick_analysis) or 1
        avg_value = total_value / num_picks
        value_pct = min(1.0, max(0.0, (avg_value + 10) / 20))

        # Composite score → letter grade
        composite = value_pct * 0.50 + balance_pct * 0.30 + tier_score * 0.20
        if composite >= 0.85:
            letter, modifier = "A", "+"
        elif composite >= 0.75:
            letter, modifier = "A", ""
        elif composite >= 0.65:
            letter, modifier = "B", "+"
        elif composite >= 0.55:
            letter, modifier = "B", ""
        elif composite >= 0.45:
            letter, modifier = "C", "+"
        elif composite >= 0.35:
            letter, modifier = "C", ""
        elif composite >= 0.25:
            letter, modifier = "D", ""
        else:
            letter, modifier = "F", ""

        # Strengths / weaknesses
        strengths = []
        weaknesses = []
        for pos in DRAFT_POSITIONS:
            cnt = pos_counts.get(pos, 0)
            req = required.get(pos, 0)  # required already derived from lineup_slots above
            if cnt >= req + 2:
                strengths.append(f"Deep at {pos} ({cnt} players)")
            elif cnt >= req:
                strengths.append(f"Solid {pos} coverage")
            elif cnt < req:
                weaknesses.append(f"Need more {pos} depth ({cnt}/{req})")

        if tier1 >= 3:
            strengths.append(f"{tier1} elite-tier players (top 36 ADP)")
        if tier1 == 0:
            weaknesses.append("No elite-tier talent (top 36 ADP)")

        steals = sum(1 for pa in pick_analysis if pa["verdict"] == "steal")
        reaches = sum(1 for pa in pick_analysis if pa["verdict"] in ("reach", "slight_reach"))
        if steals >= 3:
            strengths.append(f"Found {steals} steals in the draft")
        if reaches >= 3:
            weaknesses.append(f"{reaches} picks were reaches")

        # Best AI team comparison
        best_ai_slot = None
        best_ai_total = 0.0
        for slot, roster in state["rosters"].items():
            if slot == user_slot:
                continue
            total = sum(p["projected_points"] for p in roster)
            if total > best_ai_total:
                best_ai_total = total
                best_ai_slot = slot

        user_total = sum(p["projected_points"] for p in user_roster)

        return {
            "grade": letter + modifier,
            "composite_score": round(composite, 3),
            "total_projected": round(user_total, 1),
            "pick_analysis": pick_analysis,
            "position_counts": pos_counts,
            "strengths": strengths[:5],
            "weaknesses": weaknesses[:5],
            "best_ai": {
                "slot": best_ai_slot,
                "projected": round(best_ai_total, 1),
                "strategy": state["strategies"].get(best_ai_slot, "unknown") if best_ai_slot else None,
            },
        }

    @staticmethod
    def generate_commentary(
        picks_log: List[Dict[str, Any]],
        available: List[Dict[str, Any]],
        pick: Dict[str, Any],
    ) -> List[Dict[str, str]]:
        """Generate commentary items for a single pick.

        Returns a list of ``{"type": ..., "text": ...}`` dicts.
        """
        items: List[Dict[str, str]] = []
        p = pick["player"]
        pick_num = pick["pick_number"]
        adp = p.get("adp_rank")

        # Value assessment
        if adp is not None:
            delta = pick_num - adp
            if delta >= 15:
                items.append({"type": "steal", "text": f"🔥 Steal! {p['name']} (ADP {adp:.0f}) falls {delta:.0f} spots past ADP"})
            elif delta >= 5:
                items.append({"type": "steal", "text": f"✅ Value pick — {p['name']} going {delta:.0f} picks later than ADP"})
            elif delta <= -15:
                items.append({"type": "reach", "text": f"⚠️ Big reach — {p['name']} drafted {abs(delta):.0f} picks above ADP {adp:.0f}"})
            elif delta <= -8:
                items.append({"type": "reach", "text": f"⚠️ Reach — {p['name']} going {abs(delta):.0f} picks early"})

        # Position run
        recent = picks_log[-5:] if len(picks_log) >= 5 else picks_log
        pos_run = sum(1 for pk in recent if pk["player"]["position"] == p["position"])
        if pos_run >= 3:
            items.append({"type": "alert", "text": f"📊 {p['position']} run! {pos_run} of last {len(recent)} picks are {p['position']}s"})

        # Scarcity
        same_pos_remaining = [pl for pl in available if pl["position"] == p["position"]]
        if p["position"] in ("QB", "TE", "K", "DEF") and 0 < len(same_pos_remaining) <= 3:
            items.append({"type": "tip", "text": f"⏳ Only {len(same_pos_remaining)} {p['position']}{'s' if len(same_pos_remaining) != 1 else ''} left"})

        return items

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
            "ai_profiles": state.get("ai_profiles", {}),
            "current_pick_index": idx,
            "current_round": current_round,
            "current_slot": current_slot,
            "total_picks": len(pick_order),
            "available_players": state["available_players"],
            "rosters": state["rosters"],
            "picks_log": state["picks_log"],
            "status": state["status"],
            "position_by_round": state.get("position_by_round", {}),
            "lineup_slots": state.get("lineup_slots", dict(DEFAULT_LINEUP_SLOTS)),
        }


# Module-level singleton used by API routes
draft_engine = MockDraftEngine()
