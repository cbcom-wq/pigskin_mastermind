"""Tests for the league-size-neutral draft value scale.

``delta = pick_number - adp`` compared two different scales.  ``pick_number``
grows with ``num_teams``; stored ADP is a single league-size-agnostic average
that stops tracking rank past roughly pick 100 (rank 250 sits at ADP 189).
The result was that identical drafting graded B+ in an 8-team league and A+ in
a 14-team one, and that forced end-of-draft K/DEF picks read as "Big Reach" in
small leagues and "Great Steal" in large ones.

These tests pin the fix: judge picks in *board rank* space, and scale the
verdict thresholds by the size of a round.
"""

import pytest

from pigskin_mastermind.services.draft_recap import analyze_value
from pigskin_mastermind.services.draft_value import (
    POOL_RANK_KEY,
    pick_delta,
    rank_pool,
    verdict,
)
from pigskin_mastermind.services.mock_draft import MockDraftEngine


def _p(name, position, points, **extra):
    player = {
        "id": f"ffc_{name.lower().replace(' ', '_')}",
        "db_id": abs(hash(name)) % 100000,
        "name": name,
        "position": position,
        "nfl_team": "KC",
        "projected_points": points,
        "adp_rank": 50.0,
    }
    player.update(extra)
    return player


def _pick(pick_number, slot, player, rnd=1):
    return {
        "round": rnd,
        "slot": slot,
        "pick_number": pick_number,
        "player": player,
    }


# ---------------------------------------------------------------------------
# verdict thresholds
# ---------------------------------------------------------------------------


def test_twelve_team_thresholds_match_the_original_calibration():
    """The scale was tuned at 12 teams; that calibration must not move."""
    assert verdict(10, 12)[0] == "steal"
    assert verdict(9.9, 12)[0] == "value"
    assert verdict(3, 12)[0] == "value"
    assert verdict(2.9, 12)[0] == "fair"
    assert verdict(-3.1, 12)[0] == "slight_reach"
    assert verdict(-10.1, 12)[0] == "reach"


@pytest.mark.parametrize("num_teams", [8, 10, 12, 14, 16])
def test_falling_a_full_round_is_a_steal_in_any_league_size(num_teams):
    """A round is worth ``num_teams`` picks, so the bar must scale with it."""
    assert verdict(num_teams, num_teams)[0] == "steal"


@pytest.mark.parametrize("num_teams", [8, 10, 12, 14, 16])
def test_reaching_a_full_round_early_is_a_reach_in_any_league_size(num_teams):
    assert verdict(-num_teams, num_teams)[0] == "reach"


def test_a_fixed_delta_is_judged_less_harshly_in_a_deeper_league():
    """Ten picks is nearly a round at 12 teams but barely half of one at 20."""
    assert verdict(10, 12)[0] == "steal"
    assert verdict(10, 20)[0] == "value"


# ---------------------------------------------------------------------------
# rank_pool
# ---------------------------------------------------------------------------


def test_rank_pool_stamps_board_position_not_adp_value():
    pool = [
        _p("Third", "RB", 10.0, adp_rank=189.2),
        _p("First", "WR", 20.0, adp_rank=1.5),
        _p("Second", "TE", 15.0, adp_rank=97.3),
    ]

    ranked = rank_pool(pool)

    assert [p["name"] for p in ranked] == ["First", "Second", "Third"]
    assert [p[POOL_RANK_KEY] for p in ranked] == [1, 2, 3]


def test_rank_pool_leaves_unranked_players_unjudgeable():
    """No ADP means no consensus to judge against — rank must not invent one."""
    pool = [
        _p("Ranked", "RB", 10.0, adp_rank=5.0),
        _p("Unranked", "WR", 9.0, adp_rank=None),
    ]

    ranked = rank_pool(pool)
    unranked = next(p for p in ranked if p["name"] == "Unranked")

    assert POOL_RANK_KEY not in unranked
    assert pick_delta(30, unranked) is None


def test_compressed_adp_no_longer_manufactures_steals():
    """The real bug: 250 players compressed into 189 ADP picks.

    A drafter taking the best player on the board is exactly on time, whatever
    the ADP *value* says.
    """
    # ADP that tracks rank early then compresses, like the real FFC board.
    pool = rank_pool([
        _p(f"P{i}", "RB", 10.0, adp_rank=i * 0.75 if i > 100 else float(i))
        for i in range(1, 251)
    ])

    # Best available at pick 200 is the rank-200 player: on time, not a steal.
    on_the_clock = pool[199]
    assert pick_delta(200, on_the_clock) == 0
    assert verdict(pick_delta(200, on_the_clock), 12)[0] == "fair"


# ---------------------------------------------------------------------------
# analyze_value — recap surface
# ---------------------------------------------------------------------------


def test_analyze_value_judges_the_same_pick_the_same_way_in_any_league_size():
    """Same board, same relative position — the verdict must not move."""
    pool = rank_pool(
        [_p(f"P{i}", "RB", 10.0, adp_rank=float(i)) for i in range(1, 101)]
    )
    faller = pool[19]  # board rank 20

    verdicts = set()
    for num_teams in (8, 10, 12, 14, 16):
        # Taken exactly one round after the board says — a steal at any size.
        result = analyze_value(
            [_pick(20 + num_teams, "1", faller)], "1", {}, num_teams=num_teams
        )
        verdicts.add(result["picks"][0]["verdict"])

    assert verdicts == {"steal"}


def test_analyze_value_uses_board_rank_rather_than_the_raw_adp_number():
    pool = rank_pool([
        _p("Early", "RB", 10.0, adp_rank=1.5),
        _p("Compressed", "WR", 10.0, adp_rank=100.0),
    ])

    result = analyze_value([_pick(2, "1", pool[1])], "1", {}, num_teams=12)

    # Board rank 2 taken at pick 2 is fair, despite an ADP *value* of 100.
    assert result["picks"][0]["delta"] == 0
    assert result["picks"][0]["verdict"] == "fair"


# ---------------------------------------------------------------------------
# grade_draft — mock draft surface
# ---------------------------------------------------------------------------


def _pool_with_compressed_adp(size=340):
    """A board shaped like the real one.

    Two properties that matter, both measured off the live 2026 board:

    * Rank and ADP diverge past pick 100 — rank 100 sits at ADP 97, rank 250 at
      ADP 189 — because ADP is an average over drafts and compresses at the tail.
    * K and D/ST are scarce and live *late* (the first D/ST is board rank 98,
      the first K rank 132), which is what forces the end-of-draft picks the
      old formula mispriced.
    """
    skill = ["RB", "WR", "QB", "TE", "WR", "RB", "WR", "TE", "RB", "WR"]
    pool = []
    for i in range(1, size + 1):
        adp = float(i) if i <= 100 else 100 + (i - 100) * 0.6
        if i > 0.4 * size and i % 5 == 0:
            position = "DEF" if (i // 5) % 2 else "K"
        else:
            position = skill[i % len(skill)]
        pool.append({
            "id": f"p{i}",
            "db_id": i,
            "name": f"Player {i}",
            "position": position,
            "nfl_team": "KC",
            "projected_points": float(size - i),
            "adp_rank": adp,
        })
    return pool


#: AI that takes strict best-available with no noise, so the board is consumed
#: in exact rank order and the user's delta is a clean measurement.
_STRICT_BPA = {"aggressiveness": 0.0, "variance": 0.0, "roster_balance": 0.0}


def _draft_bpa(num_teams, num_rounds=15, pool=None, strict_ai=False):
    """Run a draft where the user always takes the best player on the board."""
    engine = MockDraftEngine()
    state = engine.create_draft(
        num_teams=num_teams,
        num_rounds=num_rounds,
        user_pick_position=1,
        player_pool=pool or _pool_with_compressed_adp(),
        ai_profiles=(
            {str(s): dict(_STRICT_BPA) for s in range(1, num_teams + 1)}
            if strict_ai
            else None
        ),
    )
    draft_id = state["draft_id"]
    internal = engine._drafts[draft_id]
    # Best available among positions the roster still needs — what a competent
    # drafter does, and it fills K/DEF so the balance score stays constant
    # across league sizes and only the value scale is under test.
    need = {"QB": 2, "RB": 5, "WR": 5, "TE": 2, "K": 1, "DEF": 1}
    while internal["status"] == "in_progress":
        slot = engine._current_slot(internal)
        if slot is None:
            break
        if str(slot) == "1":
            have: dict = {}
            for p in internal["rosters"]["1"]:
                have[p["position"]] = have.get(p["position"], 0) + 1
            choice = next(
                (p for p in internal["available_players"]
                 if have.get(p["position"], 0) < need.get(p["position"], 0)),
                internal["available_players"][0],
            )
            engine.make_user_pick(draft_id, choice["id"])
        else:
            engine.advance_one_ai_pick(draft_id)
    return engine.grade_draft(draft_id)


@pytest.mark.parametrize("num_teams", [8, 10, 12, 14])
def test_sound_drafting_is_never_called_a_big_reach(num_teams):
    """Filling a need off the top of the board can slip a few picks — that is a
    real (slight) reach.  Being called a *big* reach was the artifact: forced
    end-of-draft picks scored -44 because the board did not reach that far.
    """
    grade = _draft_bpa(num_teams)

    big = [p for p in grade["pick_analysis"] if p["verdict"] == "reach"]
    assert big == [], f"{[(p['position'], p['delta']) for p in big]}"


@pytest.mark.parametrize("num_teams", [8, 10, 12, 14, 16])
def test_taking_the_top_of_the_board_scores_exactly_on_time(num_teams):
    """The core invariant, isolated from any AI behaviour.

    At overall pick *P* exactly *P-1* players are gone in any league of any
    size, so best-available scores zero everywhere.  Under the old ADP-space
    formula these same picks drifted to +34 by round 15 at 14 teams.
    """
    board = rank_pool(_pool_with_compressed_adp())

    deltas = set()
    for rnd in range(1, 16):
        # Snake draft from slot 1: picks 1, 2n, 2n+1, 4n, 4n+1, ...
        pick_number = (rnd - 1) * num_teams + (1 if rnd % 2 else num_teams)
        deltas.add(pick_delta(pick_number, board[pick_number - 1]))

    assert deltas == {0}


def test_league_size_no_longer_decides_the_grade():
    """The headline regression: identical drafting graded B+ at 8 and A+ at 14."""
    results = {n: _draft_bpa(n, strict_ai=True) for n in (8, 10, 12, 14)}
    letters = {n: r["grade"] for n, r in results.items()}
    scores = {n: r["composite_score"] for n, r in results.items()}

    assert len(set(letters.values())) == 1, f"grade swings with league size: {letters}"
    # Grade bands are 0.10 wide, so staying inside one is the real bar.
    assert max(scores.values()) - min(scores.values()) < 0.10, scores


def test_late_round_pick_is_not_a_steal_just_because_the_league_is_deep():
    """Round 15 of a 14-team draft used to score +34 for taking the obvious guy."""
    grade = _draft_bpa(14, num_rounds=15, strict_ai=True)

    last = grade["pick_analysis"][-1]
    assert last["verdict"] != "steal"
    assert last["delta"] is None or abs(last["delta"]) < 14


def test_grade_draft_does_not_judge_a_synthetic_tail_adp():
    """Parity with ``analyze_value``: espn_tail ADP is a sort key, not a position.

    The two surfaces disagreed — the recap suppressed these picks while the
    grade page called every one of them a steal, uniformly +20.8 because the
    tail's constant offset leaked straight into the delta.
    """
    pool = _pool_with_compressed_adp(size=60)
    tail_names = {p["name"] for p in pool[10:]}
    for p in pool[10:]:
        p["adp_source"] = "espn_tail"

    grade = _draft_bpa(2, num_rounds=15, pool=pool)

    tail = [p for p in grade["pick_analysis"] if p["player"] in tail_names]
    assert tail, "expected the draft to reach the synthetic tail"
    assert all(p["verdict"] is None for p in tail)
    assert all(p["delta"] is None for p in tail)


def test_a_player_the_board_never_reaches_gets_no_verdict():
    """Roster rules force picks the board cannot price.

    The 8th-best D/ST sits at board rank ~157, but an 8-team 15-round draft is
    only 120 picks long — every team is forced to take it "early", so the
    comparison is undefined rather than unfavourable.
    """
    deep = _p("Forced DEF", "DEF", 6.0, adp_rank=143.7)
    deep[POOL_RANK_KEY] = 157

    assert pick_delta(113, deep, board_depth=120) is None
    # Same player, same pick, in a format long enough to price him.
    assert pick_delta(113, deep, board_depth=210) == pytest.approx(-44.0)


def test_forced_end_of_draft_defense_is_not_called_a_big_reach():
    """The 8-team artifact: a mandatory last-round DEF scored -44, "Big Reach"."""
    pool = _pool_with_compressed_adp()
    grade = _draft_bpa(8, num_rounds=15, pool=pool)

    assert all(p["verdict"] != "reach" for p in grade["pick_analysis"])
