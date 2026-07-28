"""Statistical realism tests for the AI mock-draft engine.

The unit tests in test_mock_draft.py use a ~16-player pool, which cannot
detect pool-size-proportional defects (a reach penalty diluted by pool
size, or noise amplified by argmax over hundreds of players). These
tests run full 12-team drafts over a realistic ~250-player board and
assert distribution-level properties: picks track ADP tightly early,
loosen late, elite fallers get scooped up, and the aggressiveness knob
actually widens deviation.
"""

import random

import pytest

from pigskin_mastermind.services.mock_draft import (
    AIProfile,
    DraftStrategy,
    MockDraftEngine,
    _derive_depth_caps,
    _derive_starter_needs,
    DEFAULT_LINEUP_SLOTS,
)


def _large_pool():
    """A deterministic ~254-player pool with plausible interleaved ADPs."""
    players = []

    def add(prefix, pos, count, adp_start, adp_step, proj_start, proj_step):
        for i in range(count):
            players.append({
                "id": f"{prefix}{i}",
                "name": f"{prefix}{i}",
                "position": pos,
                "nfl_team": "FA",
                "projected_points": round(max(proj_start - i * proj_step, 1.0), 2),
                "adp_rank": round(adp_start + i * adp_step, 1),
            })

    add("rb", "RB", 80, 1.0, 2.6, 20.0, 0.2)
    add("wr", "WR", 90, 2.0, 2.4, 19.0, 0.18)
    add("te", "TE", 30, 12.0, 7.5, 15.0, 0.4)
    add("qb", "QB", 30, 24.0, 6.5, 26.0, 0.5)
    add("def", "DEF", 12, 125.0, 6.0, 9.0, 0.2)
    add("k", "K", 12, 135.0, 6.0, 9.5, 0.2)

    # A few FFC-style gaps: players with no local projection data.
    for p in players:
        if p["adp_rank"] in (7.2, 26.0, 49.5, 82.0):
            p["projected_points"] = 0.0
    return players


def _run_full_ai_draft(pool, *, num_teams=12, num_rounds=15,
                       strategies=None, profiles=None):
    """Drive a complete all-AI draft and return its picks_log.

    Mirrors the run_simulations inner loop (which does not expose
    picks_log) so tests can inspect pick-by-pick ADP deviations.
    """
    engine = MockDraftEngine()
    starter_needs = _derive_starter_needs(DEFAULT_LINEUP_SLOTS)
    depth_caps = _derive_depth_caps(starter_needs)
    pool = sorted(pool, key=lambda p: p["adp_rank"])
    state = {
        "num_teams": num_teams,
        "num_rounds": num_rounds,
        "pick_order": MockDraftEngine._build_snake_order(num_teams, num_rounds),
        "current_pick_index": 0,
        "available_players": list(pool),
        "rosters": {str(s): [] for s in range(1, num_teams + 1)},
        "picks_log": [],
        "status": "in_progress",
    }
    strategies = strategies or {}
    profiles = profiles or {}
    while state["status"] == "in_progress":
        slot = str(state["pick_order"][state["current_pick_index"]][1])
        current_round = state["pick_order"][state["current_pick_index"]][0]
        player_id = MockDraftEngine._ai_choose_player(
            state["available_players"],
            state["rosters"][slot],
            strategies.get(slot, DraftStrategy.BEST_AVAILABLE),
            current_round,
            {},
            num_rounds=num_rounds,
            profile=profiles.get(slot) or AIProfile(),
            overall_pick=state["current_pick_index"] + 1,
            starter_needs=starter_needs,
            depth_caps=depth_caps,
        )
        assert player_id is not None
        engine._apply_pick(state, player_id)
    return state["picks_log"]


def _deviations(picks, lo=None, hi=None):
    """|pick_number - adp| for picks in the [lo, hi] pick range."""
    out = []
    for pk in picks:
        n = pk["pick_number"]
        if (lo is None or n >= lo) and (hi is None or n <= hi):
            out.append(abs(n - pk["player"]["adp_rank"]))
    return out


def _median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def test_elite_players_never_fall():
    random.seed(42)
    picks = _run_full_ai_draft(_large_pool())
    pick_by_id = {pk["player"]["id"]: pk["pick_number"] for pk in picks}
    board = sorted(_large_pool(), key=lambda p: p["adp_rank"])
    for player in board[:10]:
        assert pick_by_id[player["id"]] <= 20, (
            f"ADP top-10 player {player['id']} (adp {player['adp_rank']}) "
            f"fell to pick {pick_by_id[player['id']]}"
        )
    for player in board[:24]:
        assert pick_by_id[player["id"]] <= 40


def test_picks_track_adp_early():
    random.seed(42)
    picks = _run_full_ai_draft(_large_pool())
    early = _deviations(picks, 1, 60)
    assert _median(early) <= 8, f"median early deviation {_median(early)}"
    # Reaches (taken before ADP) in rounds 1-3 stay bounded.
    reaches = [
        pk["player"]["adp_rank"] - pk["pick_number"]
        for pk in picks
        if pk["pick_number"] <= 36 and pk["player"]["adp_rank"] > pk["pick_number"]
    ]
    assert max(reaches, default=0) <= 14, f"max early reach {max(reaches)}"


def test_late_rounds_looser_than_early():
    random.seed(42)
    picks = _run_full_ai_draft(_large_pool())
    early = _median(_deviations(picks, 1, 36))
    late = _median(_deviations(picks, 109, 180))
    assert late > early, f"late median {late} should exceed early median {early}"


def test_zero_projection_players_still_drafted_near_adp():
    random.seed(42)
    picks = _run_full_ai_draft(_large_pool())
    pick_by_id = {pk["player"]["id"]: pk["pick_number"] for pk in picks}
    plants = [p for p in _large_pool()
              if p["projected_points"] == 0.0 and p["adp_rank"] < 100]
    assert plants, "pool must contain zero-projection plants"
    for plant in plants:
        assert plant["id"] in pick_by_id, f"{plant['id']} went undrafted"
        deviation = abs(pick_by_id[plant["id"]] - plant["adp_rank"])
        assert deviation <= 15, (
            f"zero-projection {plant['id']} (adp {plant['adp_rank']}) "
            f"deviated {deviation}"
        )


def test_aggressiveness_widens_deviation():
    def mean_dev(aggressiveness):
        random.seed(42)
        profiles = {
            str(s): AIProfile(aggressiveness=aggressiveness, variance=0.15)
            for s in range(1, 13)
        }
        picks = _run_full_ai_draft(_large_pool(), profiles=profiles)
        devs = _deviations(picks)
        return sum(devs) / len(devs)

    assert mean_dev(0.9) > mean_dev(0.1)


def test_variance_widens_deviation():
    def mean_dev(variance):
        random.seed(42)
        profiles = {
            str(s): AIProfile(aggressiveness=0.3, variance=variance)
            for s in range(1, 13)
        }
        picks = _run_full_ai_draft(_large_pool(), profiles=profiles)
        devs = _deviations(picks)
        return sum(devs) / len(devs)

    assert mean_dev(0.9) > mean_dev(0.0)


@pytest.mark.parametrize("fmt_name,slots", [
    ("standard", {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1}),
    ("2qb_2flex", {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "K": 1, "DEF": 1}),
    ("superflex", {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1,
                   "K": 1, "DEF": 1}),
    ("3wr_2te", {"QB": 1, "RB": 2, "WR": 3, "TE": 2, "FLEX": 1, "K": 1, "DEF": 1}),
])
def test_every_team_can_field_the_imported_lineup(fmt_name, slots):
    """AI teams must satisfy the league's positional starting slots.

    Regression: in a 2-QB league a team finished with zero QBs, because a
    deeply fallen value pick out-scored the unmet-starter urgency.
    """
    for seed in range(4):
        random.seed(seed)
        result = MockDraftEngine().run_simulations(
            num_teams=10, num_rounds=16, num_simulations=1,
            player_pool=_large_pool(), lineup_slots=slots,
        )
        for team_slot, roster in result["simulations"][0]["rosters"].items():
            counts = roster["position_counts"]
            for pos, need in slots.items():
                if pos in ("FLEX", "SUPERFLEX"):
                    continue  # any of several positions can fill these
                assert counts.get(pos, 0) >= need, (
                    f"{fmt_name} seed {seed} team {team_slot}: "
                    f"{counts.get(pos, 0)} {pos} but format needs {need}"
                )


def test_flex_slots_do_not_force_extra_rbs_over_starters():
    """FLEX inflation must not outrank filling a real positional slot."""
    slots = {"QB": 2, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "K": 1, "DEF": 1}
    random.seed(3)
    result = MockDraftEngine().run_simulations(
        num_teams=10, num_rounds=16, num_simulations=1,
        player_pool=_large_pool(), lineup_slots=slots,
    )
    for roster in result["simulations"][0]["rosters"].values():
        counts = roster["position_counts"]
        # Enough bodies for the two flex slots on top of the hard starters.
        flex_eligible = sum(counts.get(p, 0) for p in ("RB", "WR", "TE"))
        assert flex_eligible >= 2 + 2 + 1 + 2


def test_strategy_biases_stay_bounded():
    """A positionally biased drafter still cannot blow past the window."""
    random.seed(42)
    strategies = {str(s): strat for s, strat in zip(
        range(1, 13),
        [DraftStrategy.RB_HEAVY, DraftStrategy.WR_HEAVY, DraftStrategy.QB_EARLY,
         DraftStrategy.TE_EARLY, DraftStrategy.HERO_RB, DraftStrategy.BEST_AVAILABLE] * 2,
    )}
    picks = _run_full_ai_draft(_large_pool(), strategies=strategies)
    reaches = [
        pk["player"]["adp_rank"] - pk["pick_number"]
        for pk in picks
        if pk["pick_number"] <= 36 and pk["player"]["adp_rank"] > pk["pick_number"]
    ]
    # Strategy reaches happen, but stay within the candidate window's bounds.
    assert max(reaches, default=0) <= 20
    early = _deviations(picks, 1, 60)
    assert _median(early) <= 10
