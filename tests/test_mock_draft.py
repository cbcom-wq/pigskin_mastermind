"""Tests for the mock draft service and API routes."""

import json
import pytest
from unittest.mock import patch, MagicMock
from pigskin_mastermind.services.mock_draft import (
    AIProfile,
    DraftStrategy,
    MockDraftEngine,
    fetch_espn_adp,
    randomize_ai_profiles,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _make_engine() -> MockDraftEngine:
    return MockDraftEngine()


def _small_pool():
    """A tiny deterministic player pool for fast tests."""
    players = []
    templates = [
        # (name, position, team, projected_points, adp_rank)
        ("QB1", "QB", "KC", 28.0, 5.0),
        ("QB2", "QB", "BUF", 22.0, 12.0),
        ("RB1", "RB", "SF", 22.0, 1.0),
        ("RB2", "RB", "DAL", 18.0, 4.0),
        ("RB3", "RB", "PHI", 14.0, 8.0),
        ("RB4", "RB", "MIA", 10.0, 11.0),
        ("WR1", "WR", "CIN", 21.0, 2.0),
        ("WR2", "WR", "KC", 18.0, 3.0),
        ("WR3", "WR", "BUF", 15.0, 7.0),
        ("WR4", "WR", "LAR", 12.0, 10.0),
        ("TE1", "TE", "KC", 18.0, 6.0),
        ("TE2", "TE", "SF", 10.0, 9.0),
        ("K1", "K", "KC", 9.0, 13.0),
        ("K2", "K", "BUF", 8.0, 14.0),
        ("DEF1", "DEF", "SF", 10.0, 15.0),
        ("DEF2", "DEF", "BAL", 9.0, 16.0),
    ]
    for i, (name, pos, team, pts, adp) in enumerate(templates):
        players.append(
            {"id": f"p{i}", "name": name, "position": pos, "nfl_team": team,
             "projected_points": pts, "adp_rank": adp}
        )
    return players


# Reusable player pool for API integration tests (avoids needing a real ADP source)
_API_TEST_POOL = _small_pool()


# ---------------------------------------------------------------------------
# DraftStrategy tests
# ---------------------------------------------------------------------------


def test_strategy_values():
    values = {s.value for s in DraftStrategy}
    assert "best_available" in values
    assert "rb_heavy" in values
    assert "wb_heavy" not in values  # typo guard


def test_strategy_descriptions_covers_all():
    descs = DraftStrategy.descriptions()
    for strategy in DraftStrategy:
        assert strategy in descs


# ---------------------------------------------------------------------------
# MockDraftEngine.create_draft tests
# ---------------------------------------------------------------------------


def test_create_draft_defaults():
    engine = _make_engine()
    state = engine.create_draft(num_teams=4, num_rounds=3, user_pick_position=1, player_pool=_small_pool())
    assert state["num_teams"] == 4
    assert state["num_rounds"] == 3
    assert state["user_pick_position"] == 1
    assert state["status"] == "in_progress"
    assert len(state["available_players"]) == len(_small_pool())
    assert state["current_pick_index"] == 0
    # Snake order: 4 teams × 3 rounds = 12 picks
    assert state["total_picks"] == 12


def test_create_draft_invalid_teams():
    engine = _make_engine()
    with pytest.raises(ValueError, match="num_teams"):
        engine.create_draft(num_teams=1, player_pool=_small_pool())


def test_create_draft_invalid_rounds():
    engine = _make_engine()
    with pytest.raises(ValueError, match="num_rounds"):
        engine.create_draft(num_teams=4, num_rounds=0, player_pool=_small_pool())


def test_create_draft_invalid_user_position():
    engine = _make_engine()
    with pytest.raises(ValueError, match="user_pick_position"):
        engine.create_draft(num_teams=4, num_rounds=3, user_pick_position=5, player_pool=_small_pool())


def test_create_draft_random_user_position_when_none():
    engine = _make_engine()
    with patch("pigskin_mastermind.services.mock_draft.random.randint", return_value=3):
        state = engine.create_draft(
            num_teams=4,
            num_rounds=3,
            user_pick_position=None,
            player_pool=_small_pool(),
        )
    assert state["user_pick_position"] == 3
    assert state["strategies"]["3"] == "user"


def test_create_draft_assigns_user_strategy():
    engine = _make_engine()
    state = engine.create_draft(num_teams=4, num_rounds=2, user_pick_position=2, player_pool=_small_pool())
    assert state["strategies"]["2"] == "user"
    # Other slots should be AI strategies (best_available by default)
    for slot in ("1", "3", "4"):
        assert state["strategies"][slot] != "user"


# ---------------------------------------------------------------------------
# Snake order tests
# ---------------------------------------------------------------------------


def test_snake_order_round_1_forward():
    order = MockDraftEngine._build_snake_order(4, 2)
    # Round 1: slots 1,2,3,4
    round1 = [o[1] for o in order if o[0] == 1]
    assert round1 == [1, 2, 3, 4]


def test_snake_order_round_2_reverse():
    order = MockDraftEngine._build_snake_order(4, 2)
    # Round 2: slots 4,3,2,1
    round2 = [o[1] for o in order if o[0] == 2]
    assert round2 == [4, 3, 2, 1]


def test_snake_order_total_picks():
    order = MockDraftEngine._build_snake_order(10, 15)
    assert len(order) == 150


# ---------------------------------------------------------------------------
# AI pick strategy tests
# ---------------------------------------------------------------------------


def _avail(pool, *exclude_names):
    return [p for p in pool if p["name"] not in exclude_names]


# Profile with zero variance for deterministic tests
_DETERMINISTIC = AIProfile(aggressiveness=0.3, variance=0.0, roster_balance=0.6)


def test_ai_best_available():
    pool = _small_pool()
    # At overall pick 1 with ADP, RB1 (ADP 1) should be best available
    pid = MockDraftEngine._ai_choose_player(pool, [], DraftStrategy.BEST_AVAILABLE, 1, {}, profile=_DETERMINISTIC, overall_pick=1)
    chosen = next(p for p in pool if p["id"] == pid)
    assert chosen["adp_rank"] == 1.0  # should pick the player with the best ADP


def test_ai_qb_early_round1_takes_qb():
    pool = _small_pool()
    pid = MockDraftEngine._ai_choose_player(pool, [], DraftStrategy.QB_EARLY, 1, {}, profile=_DETERMINISTIC, overall_pick=1)
    chosen = next(p for p in pool if p["id"] == pid)
    assert chosen["position"] == "QB"


def test_ai_qb_early_round3_best_available():
    pool = _small_pool()
    roster = [p for p in pool if p["position"] == "QB"][:1]  # already have QB
    pid = MockDraftEngine._ai_choose_player(pool, roster, DraftStrategy.QB_EARLY, 3, {}, profile=_DETERMINISTIC, overall_pick=5)
    chosen = next(p for p in pool if p["id"] == pid)
    # With QB filled & no strategy bonus, should pick a high-value non-K/DEF player
    assert chosen["position"] in ("QB", "RB", "WR", "TE")


def test_ai_rb_heavy_early_rounds():
    pool = _small_pool()
    for rnd in (1, 2, 3, 4):
        pid = MockDraftEngine._ai_choose_player(pool, [], DraftStrategy.RB_HEAVY, rnd, {}, profile=_DETERMINISTIC, overall_pick=rnd)
        chosen = next(p for p in pool if p["id"] == pid)
        assert chosen["position"] == "RB"


def test_ai_rb_heavy_late_rounds_best_available():
    pool = _small_pool()
    # 4 RBs already on roster — RB_HEAVY should fall back to best available
    roster = [p for p in pool if p["position"] == "RB"][:4]
    pid = MockDraftEngine._ai_choose_player(pool, roster, DraftStrategy.RB_HEAVY, 1, {}, profile=_DETERMINISTIC, overall_pick=1)
    chosen = next(p for p in pool if p["id"] == pid)
    # With 4 RBs, strategy bonus doesn't apply; should be high-value player
    assert chosen["projected_points"] > 15  # top-tier pick


def test_ai_wr_heavy_early_rounds():
    pool = _small_pool()
    for rnd in (1, 2, 3, 4):
        pid = MockDraftEngine._ai_choose_player(pool, [], DraftStrategy.WR_HEAVY, rnd, {}, profile=_DETERMINISTIC, overall_pick=rnd)
        chosen = next(p for p in pool if p["id"] == pid)
        assert chosen["position"] == "WR"


def test_ai_te_early_round1_takes_te():
    pool = _small_pool()
    pid = MockDraftEngine._ai_choose_player(pool, [], DraftStrategy.TE_EARLY, 1, {}, profile=_DETERMINISTIC, overall_pick=1)
    chosen = next(p for p in pool if p["id"] == pid)
    assert chosen["position"] == "TE"


def test_ai_hero_rb_round1_takes_rb():
    pool = _small_pool()
    pid = MockDraftEngine._ai_choose_player(pool, [], DraftStrategy.HERO_RB, 1, {}, profile=_DETERMINISTIC, overall_pick=1)
    chosen = next(p for p in pool if p["id"] == pid)
    assert chosen["position"] == "RB"


def test_ai_position_by_round():
    pool = _small_pool()
    pbr = {1: "TE", 2: "QB"}
    pid = MockDraftEngine._ai_choose_player(pool, [], DraftStrategy.POSITION_BY_ROUND, 1, pbr, profile=_DETERMINISTIC, overall_pick=1)
    chosen = next(p for p in pool if p["id"] == pid)
    assert chosen["position"] == "TE"

    pid2 = MockDraftEngine._ai_choose_player(pool, [], DraftStrategy.POSITION_BY_ROUND, 2, pbr, profile=_DETERMINISTIC, overall_pick=5)
    chosen2 = next(p for p in pool if p["id"] == pid2)
    assert chosen2["position"] == "QB"


def test_ai_position_by_round_fallback():
    pool = _small_pool()
    # round 5 not in pbr → best available based on ADP
    pid = MockDraftEngine._ai_choose_player(pool, [], DraftStrategy.POSITION_BY_ROUND, 5, {1: "QB"}, profile=_DETERMINISTIC, overall_pick=1)
    chosen = next(p for p in pool if p["id"] == pid)
    assert chosen["adp_rank"] == 1.0  # best ADP player


def test_ai_empty_pool_returns_none():
    pid = MockDraftEngine._ai_choose_player([], [], DraftStrategy.BEST_AVAILABLE, 1, {}, profile=_DETERMINISTIC, overall_pick=1)
    assert pid is None


def test_ai_follows_adp_order():
    """Without strategy bias, AI should draft roughly in ADP order."""
    pool = _small_pool()
    profile = AIProfile(aggressiveness=0.0, variance=0.0, roster_balance=0.0)
    drafted = []
    remaining = list(pool)
    for pick_num in range(1, 9):
        pid = MockDraftEngine._ai_choose_player(
            remaining, [], DraftStrategy.BEST_AVAILABLE, 1, {},
            profile=profile, overall_pick=pick_num,
        )
        chosen = next(p for p in remaining if p["id"] == pid)
        drafted.append(chosen)
        remaining = [p for p in remaining if p["id"] != pid]
    # First 8 picks should be players with ADP 1-8 (K/DEF excluded by penalty)
    adp_ranks = [p["adp_rank"] for p in drafted]
    # All early picks should have low ADP ranks (good players)
    assert all(adp <= 10 for adp in adp_ranks), f"Expected top ADP picks but got {adp_ranks}"


# ---------------------------------------------------------------------------
# Interactive draft flow
# ---------------------------------------------------------------------------


def test_interactive_draft_user_pick():
    engine = _make_engine()
    pool = _small_pool()
    state = engine.create_draft(
        num_teams=2, num_rounds=2, user_pick_position=1, player_pool=pool
    )
    draft_id = state["draft_id"]

    # After create, AI for slot-2 hasn't gone yet; slot 1 is first (user)
    assert state["current_slot"] == 1

    # User picks the best QB
    qb = next(p for p in pool if p["position"] == "QB")
    new_state = engine.make_user_pick(draft_id, qb["id"])

    # Player removed from available
    avail_ids = {p["id"] for p in new_state["available_players"]}
    assert qb["id"] not in avail_ids

    # Player added to user roster
    user_roster = new_state["rosters"]["1"]
    assert any(p["id"] == qb["id"] for p in user_roster)


def test_interactive_draft_not_user_turn_raises():
    engine = _make_engine()
    pool = _small_pool()
    state = engine.create_draft(
        num_teams=2, num_rounds=2, user_pick_position=2, player_pool=pool
    )
    draft_id = state["draft_id"]
    # Slot 1 is AI; user is slot 2, so it is NOT the user's turn first
    player = pool[0]
    with pytest.raises(ValueError, match="not the user"):
        engine.make_user_pick(draft_id, player["id"])


def test_interactive_draft_completes():
    engine = _make_engine()
    pool = _small_pool()
    # 2 teams × 2 rounds = 4 picks; user at slot 1 so picks 1 and 4 (snake)
    state = engine.create_draft(
        num_teams=2, num_rounds=2, user_pick_position=1, player_pool=pool
    )
    draft_id = state["draft_id"]

    # Pick 1 (user, slot 1)
    p1 = state["available_players"][0]["id"]
    state = engine.make_user_pick(draft_id, p1)

    # After pick 1 + AI pick, it should be user's turn again (round 2, slot 1 in snake)
    # AI picks slot 2 in round 1, then snake flips: round 2 slot 2, slot 1 → user last
    # Actually with 2 teams snake: R1: 1,2  R2: 2,1  → picks 3 = slot2 AI, pick 4 = slot1 user
    if state["status"] == "in_progress":
        p2 = state["available_players"][0]["id"]
        state = engine.make_user_pick(draft_id, p2)

    assert state["status"] == "complete"


def test_draft_not_found():
    engine = _make_engine()
    result = engine.get_draft("nonexistent-id")
    assert result is None


def test_draft_already_complete_raises():
    engine = _make_engine()
    pool = _small_pool()
    # 2 teams × 1 round = 2 picks; user at slot 1 picks first and it's done after AI
    state = engine.create_draft(
        num_teams=2, num_rounds=1, user_pick_position=1, player_pool=pool
    )
    draft_id = state["draft_id"]
    p1 = state["available_players"][0]["id"]
    state = engine.make_user_pick(draft_id, p1)
    assert state["status"] == "complete"

    with pytest.raises(ValueError, match="complete"):
        engine.make_user_pick(draft_id, pool[1]["id"])


# ---------------------------------------------------------------------------
# Simulation tests
# ---------------------------------------------------------------------------


def test_run_simulations_returns_correct_structure():
    engine = _make_engine()
    result = engine.run_simulations(
        num_teams=4,
        num_rounds=3,
        strategies={"1": "best_available", "2": "rb_heavy", "3": "wr_heavy", "4": "qb_early"},
        num_simulations=2,
        player_pool=_small_pool(),
    )
    assert "simulations" in result
    assert "summary" in result
    assert len(result["simulations"]) == 2
    assert "by_strategy" in result["summary"]


def test_run_simulations_all_players_drafted():
    engine = _make_engine()
    pool = _small_pool()
    # 2 teams × len(pool)//2 rounds; should draft all players
    num_rounds = len(pool) // 2
    result = engine.run_simulations(
        num_teams=2,
        num_rounds=num_rounds,
        num_simulations=1,
        player_pool=pool,
    )
    sim = result["simulations"][0]
    total_drafted = sum(len(r["players"]) for r in sim["rosters"].values())
    assert total_drafted == num_rounds * 2


def test_run_simulations_max_capped():
    engine = _make_engine()
    result = engine.run_simulations(
        num_teams=2,
        num_rounds=2,
        num_simulations=100,  # should be capped at MAX_SIMULATIONS (20)
        player_pool=_small_pool(),
    )
    assert len(result["simulations"]) == MockDraftEngine.MAX_SIMULATIONS


def test_simulation_summary_aggregates_by_strategy():
    engine = _make_engine()
    result = engine.run_simulations(
        num_teams=2,
        num_rounds=3,
        strategies={"1": "rb_heavy", "2": "wr_heavy"},
        num_simulations=3,
        player_pool=_small_pool(),
    )
    strategies_in_summary = {s["strategy"] for s in result["summary"]["by_strategy"]}
    assert "rb_heavy" in strategies_in_summary
    assert "wr_heavy" in strategies_in_summary


def test_simulation_projected_points_positive():
    engine = _make_engine()
    result = engine.run_simulations(
        num_teams=2,
        num_rounds=4,
        num_simulations=1,
        player_pool=_small_pool(),
    )
    for sim in result["simulations"]:
        for roster in sim["rosters"].values():
            assert roster["projected_total"] > 0


# ---------------------------------------------------------------------------
# AIProfile & randomize_ai_profiles tests
# ---------------------------------------------------------------------------


def test_ai_profile_defaults():
    p = AIProfile()
    assert 0 <= p.aggressiveness <= 1
    assert 0 <= p.variance <= 1
    assert 0 <= p.roster_balance <= 1


def test_ai_profile_to_from_dict():
    p = AIProfile(aggressiveness=0.7, variance=0.2, roster_balance=0.9)
    d = p.to_dict()
    assert d["aggressiveness"] == 0.7
    p2 = AIProfile.from_dict(d)
    assert p2.aggressiveness == p.aggressiveness
    assert p2.variance == p.variance
    assert p2.roster_balance == p.roster_balance


def test_randomize_ai_profiles_returns_all_non_user_slots():
    result = randomize_ai_profiles(num_teams=6, user_pick_position=3, overall_aggressiveness=0.5)
    assert "3" not in result  # user slot excluded
    assert set(result.keys()) == {"1", "2", "4", "5", "6"}
    for slot, info in result.items():
        assert "strategy" in info
        assert "profile" in info
        assert info["strategy"] in [s.value for s in DraftStrategy]
        profile = info["profile"]
        assert 0 <= profile["aggressiveness"] <= 1
        assert 0 <= profile["variance"] <= 1
        assert 0 <= profile["roster_balance"] <= 1


def test_randomize_ai_profiles_high_aggressiveness():
    results = [
        randomize_ai_profiles(num_teams=4, user_pick_position=1, overall_aggressiveness=0.9)
        for _ in range(20)
    ]
    all_aggr = [v["profile"]["aggressiveness"] for r in results for v in r.values()]
    avg_aggr = sum(all_aggr) / len(all_aggr)
    assert avg_aggr > 0.5  # should trend higher with high knob


def test_randomize_never_returns_position_by_round():
    """Randomize should never assign POSITION_BY_ROUND since it needs extra config."""
    for _ in range(30):
        result = randomize_ai_profiles(num_teams=8, user_pick_position=1)
        for info in result.values():
            assert info["strategy"] != DraftStrategy.POSITION_BY_ROUND.value


# ---------------------------------------------------------------------------
# Roster-aware AI pick tests
# ---------------------------------------------------------------------------


def test_ai_avoids_kdef_early():
    """AI should not pick K or DEF in the first few rounds."""
    pool = _small_pool()
    profile = AIProfile(aggressiveness=0.3, variance=0.0, roster_balance=0.6)
    for rnd in (1, 2, 3):
        pid = MockDraftEngine._ai_choose_player(
            pool, [], DraftStrategy.BEST_AVAILABLE, rnd, {},
            num_rounds=15, profile=profile, overall_pick=rnd,
        )
        chosen = next(p for p in pool if p["id"] == pid)
        assert chosen["position"] not in ("K", "DEF"), f"Picked {chosen['position']} in round {rnd}"


def test_ai_fills_kicker_late():
    """After round 10 with no K, the AI should eventually pick a kicker."""
    pool = _small_pool()
    # Roster with starters filled except K and DEF
    roster = [
        {"position": "QB"}, {"position": "RB"}, {"position": "RB"},
        {"position": "WR"}, {"position": "WR"}, {"position": "TE"},
    ]
    profile = AIProfile(aggressiveness=0.3, variance=0.0, roster_balance=0.8)
    pid = MockDraftEngine._ai_choose_player(
        pool, roster, DraftStrategy.BEST_AVAILABLE, 13, {},
        num_rounds=15, profile=profile, overall_pick=100,
    )
    chosen = next(p for p in pool if p["id"] == pid)
    # With high roster_balance and late round, should pick K or DEF
    assert chosen["position"] in ("K", "DEF")


def test_ai_does_not_overstock_position():
    """AI should avoid a 5th RB when depth cap is 5 and better options exist."""
    pool = _small_pool()
    # Already have 5 RBs
    roster = [{"position": "RB"}] * 5
    profile = AIProfile(aggressiveness=0.0, variance=0.0, roster_balance=0.6)
    pid = MockDraftEngine._ai_choose_player(
        pool, roster, DraftStrategy.BEST_AVAILABLE, 5, {},
        num_rounds=15, profile=profile, overall_pick=30,
    )
    chosen = next(p for p in pool if p["id"] == pid)
    # Should prefer non-RB since RBs are at depth cap
    assert chosen["position"] != "RB" or chosen["projected_points"] > 20


def test_variance_creates_different_picks():
    """With variance > 0, repeated calls should sometimes produce different picks."""
    pool = _small_pool()
    high_var = AIProfile(aggressiveness=0.3, variance=1.0, roster_balance=0.3)
    picks = set()
    for _ in range(30):
        pid = MockDraftEngine._ai_choose_player(
            pool, [], DraftStrategy.BEST_AVAILABLE, 1, {},
            num_rounds=15, profile=high_var, overall_pick=1,
        )
        picks.add(pid)
    # High variance should produce at least 2 different top picks
    assert len(picks) >= 2, f"Expected varied picks but got {picks}"


def test_ai_prefers_fallen_player_over_on_schedule():
    """AI should pick a player who fell well past their ADP over one drafted
    right at their ADP — the steal bonus must outweigh the baseline."""
    # Construct a pool where one player has fallen far past ADP and another is
    # right at their ADP for the current pick.
    pool = [
        {"id": "steal", "name": "Fallen Star", "position": "WR",
         "nfl_team": "KC", "projected_points": 20.0, "adp_rank": 5.0},
        {"id": "ontime", "name": "On Schedule", "position": "WR",
         "nfl_team": "BUF", "projected_points": 12.0, "adp_rank": 30.0},
    ]
    profile = AIProfile(aggressiveness=0.0, variance=0.0, roster_balance=0.0)
    pid = MockDraftEngine._ai_choose_player(
        pool, [], DraftStrategy.BEST_AVAILABLE, 5, {},
        num_rounds=15, profile=profile, overall_pick=30,
    )
    assert pid == "steal", "AI should prefer a player who fell 25 picks past ADP"


def test_ai_steal_bonus_increases_with_fall():
    """The ADP score for a fallen player should be *above* 1.0 (baseline)."""
    # Access the private _adp_score helper indirectly by checking pick order:
    # two players at the same position/projection — the one with a bigger fall
    # should be chosen.
    pool = [
        {"id": "big_fall", "name": "Big Fall", "position": "RB",
         "nfl_team": "KC", "projected_points": 15.0, "adp_rank": 10.0},
        {"id": "small_fall", "name": "Small Fall", "position": "RB",
         "nfl_team": "BUF", "projected_points": 15.0, "adp_rank": 25.0},
    ]
    profile = AIProfile(aggressiveness=0.0, variance=0.0, roster_balance=0.0)
    pid = MockDraftEngine._ai_choose_player(
        pool, [], DraftStrategy.BEST_AVAILABLE, 5, {},
        num_rounds=15, profile=profile, overall_pick=30,
    )
    # big_fall fell 20 picks; small_fall fell 5 picks — bigger steal wins
    assert pid == "big_fall"


def test_create_draft_stores_ai_profiles():
    engine = _make_engine()
    profiles = {
        "2": AIProfile(aggressiveness=0.8, variance=0.1, roster_balance=0.5).to_dict(),
    }
    state = engine.create_draft(
        num_teams=2, num_rounds=2, user_pick_position=1,
        player_pool=_small_pool(), ai_profiles=profiles,
    )
    assert "ai_profiles" in state
    assert state["ai_profiles"]["2"]["aggressiveness"] == 0.8


def test_simulation_with_profiles():
    engine = _make_engine()
    profiles = {
        "1": AIProfile(aggressiveness=0.9, variance=0.0, roster_balance=0.8).to_dict(),
        "2": AIProfile(aggressiveness=0.1, variance=0.0, roster_balance=0.8).to_dict(),
    }
    result = engine.run_simulations(
        num_teams=2, num_rounds=4, num_simulations=1,
        player_pool=_small_pool(), ai_profiles=profiles,
    )
    assert len(result["simulations"]) == 1
    for sim in result["simulations"]:
        for roster in sim["rosters"].values():
            assert roster["projected_total"] > 0


# ---------------------------------------------------------------------------
# fetch_espn_adp tests (mocked network calls)
# ---------------------------------------------------------------------------


def _make_espn_api_response(players):
    """Build a minimal ESPN API response body for mocking."""
    return json.dumps({"players": players}).encode()


def _make_espn_player_entry(pid, name, pos_id, team_id, adp, rating=10.0):
    return {
        "id": pid,
        "onTeamId": 0,
        "player": {
            "id": pid,
            "fullName": name,
            "defaultPositionId": pos_id,
            "proTeamId": team_id,
            "ownership": {
                "averageDraftPositionPPR": adp,
                "averageDraftPosition": adp,
            },
        },
        "ratings": {
            "0": {"totalRating": rating},
        },
    }


def test_fetch_espn_adp_parses_response():
    """fetch_espn_adp should parse valid ESPN API responses correctly."""
    fake_data = [
        _make_espn_player_entry(1, "Top QB", 1, 12, 1.0, 25.0),
        _make_espn_player_entry(2, "Top RB", 2, 25, 2.5, 22.0),
        _make_espn_player_entry(3, "Top WR", 3, 4, 3.3, 20.0),
        _make_espn_player_entry(4, "Top TE", 4, 12, 10.0, 15.0),
        _make_espn_player_entry(5, "Top K", 5, 12, 100.0, 8.0),
        _make_espn_player_entry(6, "Top DEF", 16, 25, 110.0, 9.0),
    ]

    mock_response = MagicMock()
    mock_response.read.return_value = _make_espn_api_response(fake_data)
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pigskin_mastermind.services.mock_draft.urlopen", return_value=mock_response):
        result = fetch_espn_adp(year=2025, limit=50)

    assert result is not None
    assert len(result) == 6
    assert result[0]["name"] == "Top QB"
    assert result[0]["position"] == "QB"
    assert result[0]["adp_rank"] == 1.0
    assert result[1]["name"] == "Top RB"
    assert result[1]["position"] == "RB"
    assert result[5]["position"] == "DEF"


def test_fetch_espn_adp_filters_unknown_positions():
    """Players with unknown ESPN position IDs should be excluded."""
    fake_data = [
        _make_espn_player_entry(1, "Known QB", 1, 12, 1.0),
        _make_espn_player_entry(2, "Unknown Pos", 99, 12, 2.0),  # unknown position
    ]
    mock_response = MagicMock()
    mock_response.read.return_value = _make_espn_api_response(fake_data)
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pigskin_mastermind.services.mock_draft.urlopen", return_value=mock_response):
        result = fetch_espn_adp(year=2025)

    assert result is not None
    assert len(result) == 1
    assert result[0]["name"] == "Known QB"


def test_fetch_espn_adp_returns_none_on_error():
    """fetch_espn_adp should return None when the request fails."""
    from urllib.error import URLError
    with patch("pigskin_mastermind.services.mock_draft.urlopen", side_effect=URLError("timeout")):
        result = fetch_espn_adp(year=2025)
    assert result is None


def test_fetch_espn_adp_returns_none_on_empty_response():
    """fetch_espn_adp should return None when ESPN returns no players."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({"players": []}).encode()
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pigskin_mastermind.services.mock_draft.urlopen", return_value=mock_response):
        result = fetch_espn_adp(year=2025)

    assert result is None


def test_fetch_espn_adp_player_dict_fields():
    """Each parsed player must have all required draft pool fields."""
    fake_data = [_make_espn_player_entry(1, "Patrick Mahomes", 1, 12, 15.0, 28.5)]
    mock_response = MagicMock()
    mock_response.read.return_value = _make_espn_api_response(fake_data)
    mock_response.__enter__ = lambda s: s
    mock_response.__exit__ = MagicMock(return_value=False)

    with patch("pigskin_mastermind.services.mock_draft.urlopen", return_value=mock_response):
        result = fetch_espn_adp(year=2025)

    assert result is not None
    player = result[0]
    for field in ("id", "name", "position", "nfl_team", "projected_points", "adp_rank"):
        assert field in player, f"Missing field: {field}"
    assert player["id"] == "espn_1"
    assert player["nfl_team"] == "KC"  # team_id 12 = KC
    assert player["adp_rank"] == 15.0
    assert player["projected_points"] == 28.5


def test_draft_sorts_by_adp_when_espn_pool_provided():
    """When a pool with adp_rank is provided, create_draft should sort by ADP ascending."""
    engine = _make_engine()
    pool = [
        {"id": "p1", "name": "P1", "position": "QB", "nfl_team": "KC",
         "projected_points": 5.0, "adp_rank": 50.0},
        {"id": "p2", "name": "P2", "position": "RB", "nfl_team": "SF",
         "projected_points": 20.0, "adp_rank": 1.0},   # lower ADP = drafted earlier
        {"id": "p3", "name": "P3", "position": "WR", "nfl_team": "BUF",
         "projected_points": 15.0, "adp_rank": 10.0},
    ]
    state = engine.create_draft(
        num_teams=2, num_rounds=1, user_pick_position=1, player_pool=pool
    )
    adp_ranks = [p["adp_rank"] for p in state["available_players"]]
    assert adp_ranks == sorted(adp_ranks), "Players should be sorted by ADP ascending"
    assert state["available_players"][0]["id"] == "p2"  # ADP 1.0 comes first


# ---------------------------------------------------------------------------
# API route integration tests
# ---------------------------------------------------------------------------


from fastapi.testclient import TestClient
from pigskin_mastermind.api.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_draft_home_page(client):
    resp = client.get("/draft")
    assert resp.status_code == 200
    assert b"Mock Draft" in resp.content


def test_draft_simulate_page(client):
    resp = client.get("/draft/simulate")
    assert resp.status_code == 200
    assert b"Simulation" in resp.content


def test_start_draft_api(client):
    resp = client.post(
        "/draft/start",
        json={"num_teams": 4, "num_rounds": 3, "user_pick_position": 1, "player_pool": _API_TEST_POOL},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["num_teams"] == 4
    assert data["status"] in ("in_progress", "complete")
    assert "available_players" in data


def test_start_draft_api_randomized_user_slot(client):
    with patch("pigskin_mastermind.api.routes.draft.random.randint", return_value=4):
        resp = client.post(
            "/draft/start",
            json={
                "num_teams": 6,
                "num_rounds": 3,
                "randomize_user_pick_position": True,
                "user_pick_position": None,
                "player_pool": _API_TEST_POOL,
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_pick_position"] == 4
    assert data["strategies"]["4"] == "user"


def test_start_draft_invalid_params(client):
    resp = client.post(
        "/draft/start",
        json={"num_teams": 1, "num_rounds": 3, "user_pick_position": 1},
    )
    # Pydantic ge=2 constraint returns 422; our handler would return 400
    assert resp.status_code in (400, 422)


def test_get_draft_state_api(client):
    # Start a draft first
    start = client.post(
        "/draft/start",
        json={"num_teams": 2, "num_rounds": 2, "user_pick_position": 1, "player_pool": _API_TEST_POOL},
    )
    draft_id = start.json()["draft_id"]

    resp = client.get(f"/draft/state/{draft_id}")
    assert resp.status_code == 200
    assert resp.json()["draft_id"] == draft_id


def test_get_draft_state_not_found(client):
    resp = client.get("/draft/state/nonexistent-draft-id")
    assert resp.status_code == 404


def test_draft_board_page(client):
    start = client.post(
        "/draft/start",
        json={"num_teams": 2, "num_rounds": 2, "user_pick_position": 1, "player_pool": _API_TEST_POOL},
    )
    draft_id = start.json()["draft_id"]
    resp = client.get(f"/draft/board/{draft_id}")
    assert resp.status_code == 200
    assert b"Draft Board" in resp.content


def test_make_pick_api(client):
    start = client.post(
        "/draft/start",
        json={"num_teams": 2, "num_rounds": 2, "user_pick_position": 1, "player_pool": _API_TEST_POOL},
    )
    state = start.json()
    draft_id = state["draft_id"]

    # User slot is 1; after start the current slot should be 1
    if state["status"] == "in_progress" and str(state["current_slot"]) == "1":
        player_id = state["available_players"][0]["id"]
        resp = client.post(
            "/draft/pick",
            json={"draft_id": draft_id, "player_id": player_id},
        )
        assert resp.status_code == 200
        new_state = resp.json()
        avail_ids = {p["id"] for p in new_state["available_players"]}
        assert player_id not in avail_ids


def test_run_simulation_api(client):
    resp = client.post(
        "/draft/run-simulation",
        json={
            "num_teams": 4,
            "num_rounds": 3,
            "strategies": {"1": "best_available", "2": "rb_heavy", "3": "wr_heavy", "4": "qb_early"},
            "num_simulations": 2,
            "player_pool": _API_TEST_POOL,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["simulations"]) == 2
    assert "by_strategy" in data["summary"]


def test_get_adp_endpoint_failure(client):
    """GET /draft/adp?source=espn should return 503 when ESPN is unreachable."""
    from urllib.error import URLError
    with patch("pigskin_mastermind.api.routes.draft.fetch_espn_adp", return_value=None):
        resp = client.get("/draft/adp?year=2025&source=espn")
    assert resp.status_code == 503


def test_get_adp_endpoint_success(client):
    """GET /draft/adp?source=espn should return 200 with player list when ESPN responds."""
    fake_players = [
        {"id": "espn_1", "name": "Top QB", "position": "QB", "nfl_team": "KC",
         "projected_points": 25.0, "adp_rank": 1.0},
        {"id": "espn_2", "name": "Top RB", "position": "RB", "nfl_team": "SF",
         "projected_points": 22.0, "adp_rank": 2.0},
    ]
    with patch("pigskin_mastermind.api.routes.draft.fetch_espn_adp", return_value=fake_players):
        resp = client.get("/draft/adp?year=2025&limit=50&source=espn")
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "espn"
    assert data["year"] == 2025
    assert data["count"] == 2
    assert len(data["players"]) == 2
    assert data["players"][0]["adp_rank"] == 1.0


def test_get_adp_endpoint_ffc_auto_import_success(client):
    """GET /draft/adp?source=ffc should auto-import when local ADP is empty."""
    fake_ffc_players = [
        {"id": "nfl_1", "name": "Top QB", "position": "QB", "nfl_team": "KC",
         "projected_points": 25.0, "adp_rank": 1.0},
        {"id": "nfl_2", "name": "Top RB", "position": "RB", "nfl_team": "SF",
         "projected_points": 22.0, "adp_rank": 2.0},
    ]

    with patch("pigskin_mastermind.api.routes.draft.ADPService") as mock_svc_cls:
        mock_svc = mock_svc_cls.return_value
        mock_svc.get_adp_for_draft_pool.side_effect = [[], fake_ffc_players]
        mock_svc.import_from_ffc.return_value = {"imported": 2, "created": 0, "skipped": 0, "total": 2}
        mock_svc.get_adp_metadata.return_value = {
            "year": 2025, "last_updated": "2025-08-01T12:00:00", "count": 2,
        }

        resp = client.get("/draft/adp?year=2025&source=ffc")

    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "fantasyfootballcalculator"
    assert data["count"] == 2
    assert data["last_updated"] == "2025-08-01T12:00:00"
    assert data["total_in_db"] == 2
    mock_svc.import_from_ffc.assert_called_once_with(year=2025, scoring="half-ppr")


def test_get_adp_endpoint_ffc_refresh_forces_import(client):
    """GET /draft/adp?refresh=true should re-import even when local data exists."""
    fake_ffc_players = [
        {"id": "nfl_1", "name": "Top QB", "position": "QB", "nfl_team": "KC",
         "projected_points": 25.0, "adp_rank": 1.0},
    ]

    with patch("pigskin_mastermind.api.routes.draft.ADPService") as mock_svc_cls:
        mock_svc = mock_svc_cls.return_value
        mock_svc.get_adp_for_draft_pool.return_value = fake_ffc_players
        mock_svc.import_from_ffc.return_value = {
            "imported": 1, "created": 1, "skipped": 0, "total": 1,
            "last_updated": "2025-08-02T09:00:00",
        }
        mock_svc.get_adp_metadata.return_value = {
            "year": 2025, "last_updated": "2025-08-02T09:00:00", "count": 1,
        }

        resp = client.get("/draft/adp?year=2025&source=ffc&refresh=true")

    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 1
    assert data["created"] == 1
    assert data["last_updated"] == "2025-08-02T09:00:00"
    mock_svc.import_from_ffc.assert_called_once_with(year=2025, scoring="half-ppr")


def test_get_adp_endpoint_defaults_to_current_season(client):
    """GET /draft/adp without a year should use the current fantasy season."""
    fake_ffc_players = [
        {"id": "nfl_1", "name": "Top QB", "position": "QB", "nfl_team": "KC",
         "projected_points": 25.0, "adp_rank": 1.0},
    ]

    with patch("pigskin_mastermind.api.routes.draft.ADPService") as mock_svc_cls, \
         patch("pigskin_mastermind.api.routes.draft.current_fantasy_season", return_value=2031):
        mock_svc = mock_svc_cls.return_value
        mock_svc.get_adp_for_draft_pool.return_value = fake_ffc_players
        mock_svc.get_adp_metadata.return_value = {
            "year": 2031, "last_updated": "2031-08-01T12:00:00", "count": 1,
        }

        resp = client.get("/draft/adp?source=ffc")

    assert resp.status_code == 200
    assert resp.json()["year"] == 2031
    mock_svc.get_adp_for_draft_pool.assert_called_once_with(year=2031)


def test_get_adp_endpoint_ffc_auto_import_failure(client):
    """GET /draft/adp?source=ffc should return 503 when auto-import fails."""
    with patch("pigskin_mastermind.api.routes.draft.ADPService") as mock_svc_cls:
        mock_svc = mock_svc_cls.return_value
        mock_svc.get_adp_for_draft_pool.return_value = []
        mock_svc.import_from_ffc.return_value = {"error": "Failed to fetch data from Fantasy Football Calculator"}

        resp = client.get("/draft/adp?year=2025&source=ffc")

    assert resp.status_code == 503
    assert "automatic import failed" in resp.json()["detail"]


def test_start_draft_with_espn_adp(client):
    """POST /draft/start with use_espn_adp=True should use the ESPN player pool."""
    fake_players = [
        {"id": f"espn_{i}", "name": f"Player {i}", "position": pos, "nfl_team": "KC",
         "projected_points": float(20 - i), "adp_rank": float(i + 1)}
        for i, pos in enumerate(["QB", "RB", "WR", "TE", "K", "DEF",
                                   "RB", "WR", "RB", "WR", "WR", "RB",
                                   "TE", "K", "DEF", "QB", "WR", "RB",
                                   "WR", "TE"])
    ]
    with patch("pigskin_mastermind.api.routes.draft.fetch_espn_adp", return_value=fake_players):
        resp = client.post(
            "/draft/start",
            json={
                "num_teams": 2,
                "num_rounds": 3,
                "user_pick_position": 1,
                "use_espn_adp": True,
                "espn_adp_year": 2025,
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("in_progress", "complete")
    # Players should be from the ESPN pool (ids start with espn_)
    for p in data["available_players"]:
        assert p["id"].startswith("espn_")


def test_start_draft_with_espn_adp_failure(client):
    """POST /draft/start with use_espn_adp=True should return 503 when ESPN is unavailable."""
    with patch("pigskin_mastermind.api.routes.draft.fetch_espn_adp", return_value=None):
        resp = client.post(
            "/draft/start",
            json={
                "num_teams": 2,
                "num_rounds": 2,
                "user_pick_position": 1,
                "use_espn_adp": True,
            },
        )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# ADP year-fallback tests
# ---------------------------------------------------------------------------


def _uniform_adp_players(adp_value: float = 170.0):
    """Return a player list where every entry has the same ADP (placeholder data)."""
    return [
        {"id": f"espn_{i}", "name": f"Player {i}", "position": pos,
         "nfl_team": "KC", "projected_points": 0.0, "adp_rank": adp_value}
        for i, pos in enumerate(["QB", "RB", "WR", "TE", "K"])
    ]


def _varied_adp_players():
    """Return a player list with distinct ADP values (meaningful data)."""
    return [
        {"id": "espn_1", "name": "Star QB", "position": "QB", "nfl_team": "KC",
         "projected_points": 25.0, "adp_rank": 1.0},
        {"id": "espn_2", "name": "Star RB", "position": "RB", "nfl_team": "SF",
         "projected_points": 22.0, "adp_rank": 2.5},
    ]


def test_adp_endpoint_falls_back_to_previous_year(client):
    """GET /draft/adp should fall back to year-1 when current year has uniform ADP."""
    def _side_effect(year=2025, limit=300):
        if year == 2025:
            return _uniform_adp_players()
        if year == 2024:
            return _varied_adp_players()
        return None

    with patch("pigskin_mastermind.api.routes.draft.fetch_espn_adp", side_effect=_side_effect):
        resp = client.get("/draft/adp?year=2025&limit=50&source=espn")

    assert resp.status_code == 200
    data = resp.json()
    assert data["year"] == 2024
    assert data["fallback"] is True
    assert data["requested_year"] == 2025
    assert data["players"][0]["adp_rank"] == 1.0


def test_adp_endpoint_no_fallback_when_data_is_good(client):
    """GET /draft/adp?source=espn should NOT fall back when the requested year has varied ADP."""
    with patch("pigskin_mastermind.api.routes.draft.fetch_espn_adp", return_value=_varied_adp_players()):
        resp = client.get("/draft/adp?year=2025&source=espn")

    assert resp.status_code == 200
    data = resp.json()
    assert data["year"] == 2025
    assert "fallback" not in data


def test_adp_endpoint_uniform_both_years_still_returns_data(client):
    """If both years have uniform ADP, return the original year's data anyway."""
    with patch("pigskin_mastermind.api.routes.draft.fetch_espn_adp", return_value=_uniform_adp_players()):
        resp = client.get("/draft/adp?year=2025&source=espn")

    assert resp.status_code == 200
    data = resp.json()
    # Falls through — original year used because fallback wasn't better
    assert data["year"] == 2025
    assert "fallback" not in data


def test_start_draft_falls_back_to_previous_year(client):
    """POST /draft/start should fall back to year-1 ADP when current year is uniform."""
    uniform = _uniform_adp_players()
    varied = [
        {"id": f"espn_{i}", "name": f"Player {i}", "position": pos, "nfl_team": "KC",
         "projected_points": float(20 - i), "adp_rank": float(i + 1)}
        for i, pos in enumerate(["QB", "RB", "WR", "TE", "K", "DEF",
                                   "RB", "WR", "RB", "WR", "WR", "RB",
                                   "TE", "K", "DEF", "QB", "WR", "RB",
                                   "WR", "TE"])
    ]

    def _side_effect(year=2025, limit=300):
        if year == 2025:
            return uniform
        if year == 2024:
            return varied
        return None

    with patch("pigskin_mastermind.api.routes.draft.fetch_espn_adp", side_effect=_side_effect):
        resp = client.post(
            "/draft/start",
            json={
                "num_teams": 2,
                "num_rounds": 3,
                "user_pick_position": 1,
                "use_espn_adp": True,
                "espn_adp_year": 2025,
            },
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("in_progress", "complete")


# ---------------------------------------------------------------------------
# advance_one_ai_pick / grade_draft / generate_commentary service tests
# ---------------------------------------------------------------------------


def test_advance_one_ai_pick():
    engine = _make_engine()
    # User picks at position 2 so AI (slot 1) goes first
    state = engine.create_draft(
        num_teams=2, num_rounds=2, user_pick_position=2, player_pool=_small_pool()
    )
    draft_id = state["draft_id"]
    assert str(state["current_slot"]) == "1"  # AI's turn

    new_state = engine.advance_one_ai_pick(draft_id)
    assert new_state is not None
    assert len(new_state["picks_log"]) == 1
    assert str(new_state["current_slot"]) == "2"  # now user's turn


def test_advance_one_ai_pick_returns_none_on_user_turn():
    engine = _make_engine()
    state = engine.create_draft(
        num_teams=2, num_rounds=2, user_pick_position=1, player_pool=_small_pool()
    )
    # User picks first — advance should return None
    assert engine.advance_one_ai_pick(state["draft_id"]) is None


def test_advance_one_ai_pick_returns_none_when_complete():
    engine = _make_engine()
    state = engine.create_draft(
        num_teams=2, num_rounds=1, user_pick_position=1, player_pool=_small_pool()
    )
    draft_id = state["draft_id"]
    # User pick
    engine.make_user_pick(draft_id, state["available_players"][0]["id"])
    # AI pick — completes the draft
    engine.advance_one_ai_pick(draft_id)
    # Now draft is complete
    assert engine.advance_one_ai_pick(draft_id) is None


def test_grade_draft_returns_none_when_incomplete():
    engine = _make_engine()
    state = engine.create_draft(
        num_teams=2, num_rounds=2, user_pick_position=1, player_pool=_small_pool()
    )
    assert engine.grade_draft(state["draft_id"]) is None


def test_grade_draft_returns_grade():
    engine = _make_engine()
    pool = _small_pool()
    for i, p in enumerate(pool):
        p["adp_rank"] = float(i + 1)
    state = engine.create_draft(
        num_teams=2, num_rounds=2, user_pick_position=1, player_pool=pool
    )
    draft_id = state["draft_id"]
    # Complete draft
    internal = engine._drafts[draft_id]
    while internal["status"] == "in_progress":
        slot = engine._current_slot(internal)
        if slot is None:
            break
        if str(slot) == str(internal["user_pick_position"]):
            pid = internal["available_players"][0]["id"]
            engine.make_user_pick(draft_id, pid)
        else:
            engine.advance_one_ai_pick(draft_id)

    grade = engine.grade_draft(draft_id)
    assert grade is not None
    assert "grade" in grade
    assert grade["grade"][0] in "ABCDF"
    assert "pick_analysis" in grade
    assert len(grade["pick_analysis"]) > 0
    assert "strengths" in grade
    assert "weaknesses" in grade
    assert "best_ai" in grade
    assert "composite_score" in grade


def test_generate_commentary_steal():
    pick = {
        "round": 5, "slot": 1, "pick_number": 50,
        "player": {"id": "p1", "name": "TestPlayer", "position": "RB",
                    "nfl_team": "KC", "projected_points": 15.0, "adp_rank": 20},
    }
    items = MockDraftEngine.generate_commentary([], [], pick)
    assert any(i["type"] == "steal" for i in items)


def test_generate_commentary_reach():
    pick = {
        "round": 1, "slot": 1, "pick_number": 50,
        "player": {"id": "p1", "name": "Reacher", "position": "QB",
                    "nfl_team": "KC", "projected_points": 10.0, "adp_rank": 80},
    }
    items = MockDraftEngine.generate_commentary([], [], pick)
    assert any(i["type"] == "reach" for i in items)


def test_generate_commentary_position_run():
    prior_picks = [
        {"round": 1, "slot": i, "pick_number": i,
         "player": {"id": f"p{i}", "name": f"RB{i}", "position": "RB",
                     "nfl_team": "KC", "projected_points": 15.0, "adp_rank": float(i)}}
        for i in range(1, 5)
    ]
    new_pick = {
        "round": 1, "slot": 5, "pick_number": 5,
        "player": {"id": "p5", "name": "RB5", "position": "RB",
                    "nfl_team": "KC", "projected_points": 14.0, "adp_rank": 5.0},
    }
    items = MockDraftEngine.generate_commentary(prior_picks + [new_pick], [], new_pick)
    assert any("run" in i["text"].lower() for i in items)


# ---------------------------------------------------------------------------
# API endpoint tests for /advance and /grade
# ---------------------------------------------------------------------------


def test_advance_endpoint(client):
    """POST /draft/advance should advance one AI pick."""
    start = client.post(
        "/draft/start",
        json={"num_teams": 3, "num_rounds": 2, "user_pick_position": 2, "player_pool": _API_TEST_POOL},
    )
    state = start.json()
    draft_id = state["draft_id"]
    assert str(state["current_slot"]) == "1"

    resp = client.post("/draft/advance", json={"draft_id": draft_id})
    assert resp.status_code == 200
    new_state = resp.json()
    assert len(new_state["picks_log"]) == 1
    assert str(new_state["current_slot"]) == "2"


def test_advance_endpoint_returns_400_on_user_turn(client):
    """POST /draft/advance should return 400 when it's the user's turn."""
    start = client.post(
        "/draft/start",
        json={"num_teams": 2, "num_rounds": 2, "user_pick_position": 1, "player_pool": _API_TEST_POOL},
    )
    state = start.json()
    resp = client.post("/draft/advance", json={"draft_id": state["draft_id"]})
    assert resp.status_code == 400


def test_grade_endpoint_incomplete_draft(client):
    """GET /draft/grade should return 400 for incomplete draft."""
    start = client.post(
        "/draft/start",
        json={"num_teams": 2, "num_rounds": 2, "user_pick_position": 1, "player_pool": _API_TEST_POOL},
    )
    state = start.json()
    resp = client.get(f"/draft/grade/{state['draft_id']}")
    assert resp.status_code == 400


def test_grade_endpoint_complete_draft(client):
    """GET /draft/grade should return grade data for a completed draft."""
    start = client.post(
        "/draft/start",
        json={"num_teams": 2, "num_rounds": 1, "user_pick_position": 1, "player_pool": _API_TEST_POOL},
    )
    state = start.json()
    draft_id = state["draft_id"]

    # User picks
    player_id = state["available_players"][0]["id"]
    client.post("/draft/pick", json={"draft_id": draft_id, "player_id": player_id})
    # Advance AI pick to complete
    client.post("/draft/advance", json={"draft_id": draft_id})

    resp = client.get(f"/draft/grade/{draft_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "grade" in data
    assert "pick_analysis" in data
    assert "strengths" in data
    assert "weaknesses" in data


def test_pick_endpoint_includes_commentary(client):
    """POST /draft/pick should include commentary in the response."""
    start = client.post(
        "/draft/start",
        json={"num_teams": 2, "num_rounds": 2, "user_pick_position": 1, "player_pool": _API_TEST_POOL},
    )
    state = start.json()
    draft_id = state["draft_id"]
    player_id = state["available_players"][0]["id"]

    resp = client.post(
        "/draft/pick",
        json={"draft_id": draft_id, "player_id": player_id},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "commentary" in data


# ---------------------------------------------------------------------------
# Randomize strategies API tests
# ---------------------------------------------------------------------------


def test_randomize_strategies_api(client):
    """POST /draft/randomize-strategies should return strategies and profiles."""
    resp = client.post(
        "/draft/randomize-strategies",
        json={"num_teams": 6, "user_pick_position": 2, "overall_aggressiveness": 0.7},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "2" not in data  # user slot excluded
    for slot, info in data.items():
        assert "strategy" in info
        assert "profile" in info


def test_start_draft_with_ai_profiles(client):
    """POST /draft/start with ai_profiles should store them in state."""
    resp = client.post(
        "/draft/start",
        json={
            "num_teams": 4,
            "num_rounds": 3,
            "user_pick_position": 1,
            "ai_profiles": {
                "2": {"aggressiveness": 0.9, "variance": 0.1, "roster_balance": 0.5},
            },
            "player_pool": _API_TEST_POOL,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "ai_profiles" in data
    assert data["ai_profiles"]["2"]["aggressiveness"] == 0.9


def test_start_draft_with_aggressiveness(client):
    """POST /draft/start with ai_aggressiveness should generate profiles."""
    resp = client.post(
        "/draft/start",
        json={
            "num_teams": 4,
            "num_rounds": 3,
            "user_pick_position": 1,
            "ai_aggressiveness": 0.8,
            "player_pool": _API_TEST_POOL,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "ai_profiles" in data
    # All AI slots should have profiles
    for slot in ("2", "3", "4"):
        assert slot in data["ai_profiles"]
