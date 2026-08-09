"""Tests for the post-draft recap service.

The recap enriches a finished in-memory draft with database stats.  Every
helper here is exercised against hand-built state dicts rather than a real
draft, so the tests stay fast and do not depend on the ADP importers.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
)
from pigskin_mastermind.services.draft_recap import (
    DraftRecapService,
    analyze_value,
    bye_grid,
    fill_lineup,
    passed_on,
    position_group_ranks,
    sparkline_points,
    team_logo_url,
    weekly_distribution,
)


def _p(name, position, points, **extra):
    """Build a roster player dict shaped like the draft engine's."""
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


# ---------------------------------------------------------------------------
# fill_lineup
# ---------------------------------------------------------------------------


def test_fill_lineup_puts_best_player_in_each_required_slot():
    roster = [
        _p("Backup QB", "QB", 10.0),
        _p("Star QB", "QB", 25.0),
    ]

    starters, bench = fill_lineup(roster, {"QB": 1, "BENCH": 1})

    assert [s["player"]["name"] for s in starters] == ["Star QB"]
    assert [b["name"] for b in bench] == ["Backup QB"]


def test_fill_lineup_never_puts_a_qb_in_flex():
    """FLEX is RB/WR/TE only.  A high-scoring spare QB must stay on the bench."""
    roster = [
        _p("Star QB", "QB", 25.0),
        _p("Spare QB", "QB", 24.0),
        _p("Some RB", "RB", 8.0),
    ]

    starters, bench = fill_lineup(roster, {"QB": 1, "FLEX": 1, "BENCH": 1})

    flex = [s for s in starters if s["slot"] == "FLEX"]
    assert [f["player"]["name"] for f in flex] == ["Some RB"]
    assert "Spare QB" in [b["name"] for b in bench]


def test_fill_lineup_never_puts_a_kicker_or_defense_in_flex():
    roster = [
        _p("Big Leg", "K", 20.0),
        _p("Great D", "DEF", 19.0),
        _p("Weak WR", "WR", 3.0),
    ]

    starters, _ = fill_lineup(roster, {"FLEX": 1, "BENCH": 2})

    assert [s["player"]["name"] for s in starters] == ["Weak WR"]


def test_fill_lineup_allows_a_qb_in_superflex():
    roster = [
        _p("Star QB", "QB", 25.0),
        _p("Spare QB", "QB", 24.0),
        _p("Some RB", "RB", 8.0),
    ]

    starters, _ = fill_lineup(roster, {"QB": 1, "SUPERFLEX": 1, "BENCH": 1})

    superflex = [s for s in starters if s["slot"] == "SUPERFLEX"]
    assert [s["player"]["name"] for s in superflex] == ["Spare QB"]


def test_fill_lineup_fills_required_slots_before_flex():
    """The best RB belongs at RB, not burned in FLEX leaving RB empty."""
    roster = [
        _p("Stud RB", "RB", 22.0),
        _p("Ok WR", "WR", 12.0),
    ]

    starters, _ = fill_lineup(roster, {"RB": 1, "FLEX": 1, "BENCH": 0})

    by_slot = {s["slot"]: s["player"]["name"] for s in starters}
    assert by_slot["RB"] == "Stud RB"
    assert by_slot["FLEX"] == "Ok WR"


def test_fill_lineup_bench_is_not_a_starting_slot():
    roster = [_p("Only Guy", "RB", 10.0)]

    starters, bench = fill_lineup(roster, {"RB": 1, "BENCH": 5})

    assert len(starters) == 1
    assert bench == []


def test_fill_lineup_marks_unfilled_slots_as_empty():
    """A roster short at a position still returns the slot, so the UI can flag it."""
    roster = [_p("Only Guy", "RB", 10.0)]

    starters, _ = fill_lineup(roster, {"RB": 2, "BENCH": 0})

    assert [s["player"] for s in starters] == [roster[0], None]


# ---------------------------------------------------------------------------
# weekly_distribution
# ---------------------------------------------------------------------------


def test_weekly_distribution_percentiles_for_a_known_series():
    points = [float(n) for n in range(11)]  # 0.0 .. 10.0

    dist = weekly_distribution(points, "RB")

    assert dist["floor"] == pytest.approx(1.0)
    assert dist["median"] == pytest.approx(5.0)
    assert dist["ceiling"] == pytest.approx(9.0)
    assert dist["games"] == 11


def test_weekly_distribution_reports_best_and_worst_game():
    dist = weekly_distribution([12.0, 31.5, 4.0], "WR")

    assert dist["best"] == pytest.approx(31.5)
    assert dist["worst"] == pytest.approx(4.0)


def test_weekly_distribution_boom_and_bust_use_flex_thresholds_for_a_running_back():
    """RB booms above 20 and busts below 8."""
    dist = weekly_distribution([25.0, 22.0, 10.0, 5.0, 3.0], "RB")

    assert dist["boom_rate"] == pytest.approx(0.4)
    assert dist["bust_rate"] == pytest.approx(0.4)


def test_weekly_distribution_boom_and_bust_thresholds_are_higher_for_a_quarterback():
    """The same series scores differently for a QB, which booms above 25."""
    dist = weekly_distribution([25.0, 22.0, 10.0, 5.0, 3.0], "QB")

    assert dist["boom_rate"] == pytest.approx(0.0)
    assert dist["bust_rate"] == pytest.approx(0.6)


def test_weekly_distribution_boom_and_bust_thresholds_are_lower_for_a_kicker():
    """A kicker scoring 13 is a boom, not the bust that flex thresholds imply."""
    dist = weekly_distribution([13.0, 4.0], "K")

    assert dist["boom_rate"] == pytest.approx(0.5)
    assert dist["bust_rate"] == pytest.approx(0.5)


def test_weekly_distribution_returns_none_without_games():
    """A rookie has no prior-season log; the caller shows 'no data' rather than zeros."""
    assert weekly_distribution([], "RB") is None


def test_weekly_distribution_consistency_is_the_spread_of_weekly_scores():
    steady = weekly_distribution([10.0, 10.0, 10.0], "WR")
    erratic = weekly_distribution([0.0, 10.0, 20.0], "WR")

    assert steady["std_dev"] == pytest.approx(0.0)
    assert erratic["std_dev"] > steady["std_dev"]


# ---------------------------------------------------------------------------
# position_group_ranks
# ---------------------------------------------------------------------------


THREE_TEAM_ROSTERS = {
    "1": [_p("My RB1", "RB", 20.0), _p("My RB2", "RB", 15.0),
          _p("My RB3", "RB", 5.0), _p("My QB", "QB", 25.0)],
    "2": [_p("B RB1", "RB", 18.0), _p("B RB2", "RB", 12.0), _p("B QB", "QB", 20.0)],
    "3": [_p("C RB1", "RB", 10.0), _p("C RB2", "RB", 5.0), _p("C QB", "QB", 30.0)],
}
THREE_TEAM_SLOTS = {"QB": 1, "RB": 2, "BENCH": 3}


def test_position_group_ranks_counts_only_the_required_number_of_starters():
    """The user's third RB does not inflate the group — only the top 2 start."""
    ranks = position_group_ranks(THREE_TEAM_ROSTERS, "1", THREE_TEAM_SLOTS)

    assert ranks["RB"]["your_points"] == pytest.approx(35.0)


def test_position_group_ranks_places_the_user_against_the_other_teams():
    ranks = position_group_ranks(THREE_TEAM_ROSTERS, "1", THREE_TEAM_SLOTS)

    assert ranks["RB"]["rank"] == 1
    assert ranks["RB"]["of"] == 3
    assert ranks["QB"]["rank"] == 2


def test_position_group_ranks_margin_is_measured_against_the_rest_of_the_field():
    """35 against a field averaging (30 + 15) / 2 = 22.5."""
    ranks = position_group_ranks(THREE_TEAM_ROSTERS, "1", THREE_TEAM_SLOTS)

    assert ranks["RB"]["margin"] == pytest.approx(12.5)


def test_position_group_ranks_skips_flex_slots():
    """A flex player is already counted in their base position."""
    ranks = position_group_ranks(
        THREE_TEAM_ROSTERS, "1", {"QB": 1, "RB": 2, "FLEX": 1, "SUPERFLEX": 1}
    )

    assert "FLEX" not in ranks
    assert "SUPERFLEX" not in ranks


def test_position_group_ranks_skips_positions_nobody_starts():
    ranks = position_group_ranks(THREE_TEAM_ROSTERS, "1", THREE_TEAM_SLOTS)

    assert "TE" not in ranks
    assert "K" not in ranks


def test_position_group_ranks_marks_a_group_with_no_projections_as_unranked():
    """Most of the draft pool has projected_points of 0.

    Ranking teams on all-zero data produces an ordering with no meaning, so the
    group says it has no data instead of claiming a rank.
    """
    rosters = {
        "1": [_p("A K", "K", 0.0)],
        "2": [_p("B K", "K", 0.0)],
        "3": [_p("C K", "K", 0.0)],
    }

    ranks = position_group_ranks(rosters, "1", {"K": 1})

    assert ranks["K"]["ranked"] is False
    assert ranks["K"]["rank"] is None


def test_position_group_ranks_flags_when_only_the_user_lacks_projections():
    """Scoring zero against a field that has data is a missing projection.

    Reporting it as a deficit tells the user they drafted badly when the truth
    is the app has no number for their player.
    """
    rosters = {
        "1": [_p("Mine", "QB", 0.0)],
        "2": [_p("Theirs", "QB", 20.0)],
    }

    ranks = position_group_ranks(rosters, "1", {"QB": 1})

    assert ranks["QB"]["your_points_missing"] is True


def test_position_group_ranks_does_not_flag_a_group_with_real_points():
    rosters = {
        "1": [_p("Mine", "QB", 18.0)],
        "2": [_p("Theirs", "QB", 20.0)],
    }

    ranks = position_group_ranks(rosters, "1", {"QB": 1})

    assert ranks["QB"]["your_points_missing"] is False


def test_position_group_ranks_stays_ranked_when_any_team_has_projections():
    rosters = {
        "1": [_p("A K", "K", 0.0)],
        "2": [_p("B K", "K", 9.0)],
    }

    ranks = position_group_ranks(rosters, "1", {"K": 1})

    assert ranks["K"]["ranked"] is True
    assert ranks["K"]["rank"] == 2


# ---------------------------------------------------------------------------
# passed_on
# ---------------------------------------------------------------------------


def _pick(pick_number, slot, player):
    return {
        "round": (pick_number + 1) // 2,
        "slot": slot,
        "pick_number": pick_number,
        "player": player,
    }


# Two-team snake: user is slot "1" and picks at 1 and 4.
PLAYER_A = _p("Player A", "RB", 12.0, adp_rank=5.0)
PLAYER_B = _p("Player B", "WR", 18.0, adp_rank=1.0)
PLAYER_C = _p("Player C", "WR", 14.0, adp_rank=3.0)
PLAYER_D = _p("Player D", "TE", 9.0, adp_rank=10.0)
PLAYER_E = _p("Player E", "QB", 8.0, adp_rank=20.0)

TWO_TEAM_LOG = [
    _pick(1, "1", PLAYER_A),
    _pick(2, "2", PLAYER_B),
    _pick(3, "2", PLAYER_C),
    _pick(4, "1", PLAYER_D),
]


def test_passed_on_names_the_best_player_taken_before_the_users_next_turn():
    """Player B (ADP 1) was on the board at pick 1 and gone by pick 4."""
    result = passed_on(TWO_TEAM_LOG, [PLAYER_E], "1", min_gap=0)

    assert len(result) == 1
    assert result[0]["pick_number"] == 1
    assert result[0]["took"]["name"] == "Player A"
    assert result[0]["passed"]["name"] == "Player B"


def test_passed_on_ignores_a_player_barely_better_than_the_one_taken():
    """In a snake draft the best available always goes before your next turn.

    Listing every round is arithmetic, not insight — only a real gap counts.
    """
    result = passed_on(TWO_TEAM_LOG, [PLAYER_E], "1")

    assert result == []


def test_passed_on_reports_a_pick_that_skipped_a_much_better_player():
    reached_for = _p("Reach", "TE", 5.0, adp_rank=60.0)
    log = [
        _pick(1, "1", reached_for),
        _pick(2, "2", PLAYER_B),
        _pick(3, "2", PLAYER_C),
        _pick(4, "1", PLAYER_D),
    ]

    result = passed_on(log, [PLAYER_E], "1")

    assert len(result) == 1
    assert result[0]["passed"]["name"] == "Player B"
    assert result[0]["adp_gap"] == pytest.approx(59.0)


def test_passed_on_ignores_players_still_available_at_the_next_turn():
    """Player E was never drafted, so passing on him cost nothing."""
    result = passed_on(TWO_TEAM_LOG, [PLAYER_E], "1")

    assert all(entry["passed"]["name"] != "Player E" for entry in result)


def test_passed_on_skips_the_final_pick():
    """There is no 'next turn' after the last pick, so nothing was forgone."""
    result = passed_on(TWO_TEAM_LOG, [PLAYER_E], "1")

    assert all(entry["pick_number"] != 4 for entry in result)


def test_passed_on_ignores_players_with_no_adp():
    """An unranked player cannot be called the best available."""
    unranked = _p("No ADP", "WR", 30.0, adp_rank=None)
    log = [
        _pick(1, "1", PLAYER_A),
        _pick(2, "2", unranked),
        _pick(3, "2", PLAYER_C),
        _pick(4, "1", PLAYER_D),
    ]

    result = passed_on(log, [PLAYER_E], "1", min_gap=0)

    assert result[0]["passed"]["name"] == "Player C"


# ---------------------------------------------------------------------------
# bye_grid
# ---------------------------------------------------------------------------


BYE_ROSTER = [
    _p("My QB", "QB", 25.0, bye_week=5),
    _p("Starting RB", "RB", 20.0, bye_week=5),
    _p("Other RB", "RB", 15.0, bye_week=9),
    _p("Bench RB", "RB", 5.0, bye_week=9),
    _p("Spare RB", "RB", 4.0, bye_week=12),
]
BYE_SLOTS = {"QB": 1, "RB": 2, "BENCH": 2}


def _week(grid, number):
    return next(w for w in grid if w["week"] == number)


def test_bye_grid_counts_starters_idle_each_week():
    grid = bye_grid(BYE_ROSTER, BYE_SLOTS)

    assert _week(grid, 5)["starters_out"] == 2
    assert _week(grid, 6)["starters_out"] == 0


def test_bye_grid_does_not_count_bench_players():
    """Bench RB shares week 9 with a starter, but only the starter matters."""
    grid = bye_grid(BYE_ROSTER, BYE_SLOTS)

    assert _week(grid, 9)["starters_out"] == 1


def test_bye_grid_flags_slots_that_cannot_be_covered():
    """Week 5 loses the only QB, and nobody on the bench can fill in."""
    grid = bye_grid(BYE_ROSTER, BYE_SLOTS)

    assert _week(grid, 5)["unfillable"] == ["QB"]


def test_bye_grid_leaves_a_covered_week_unflagged():
    """Week 9 loses an RB, but the bench RB slots straight in."""
    grid = bye_grid(BYE_ROSTER, BYE_SLOTS)

    assert _week(grid, 9)["unfillable"] == []


def test_bye_grid_covers_every_bye_week_of_the_season():
    grid = bye_grid(BYE_ROSTER, BYE_SLOTS)

    assert [w["week"] for w in grid] == list(range(5, 15))


def test_bye_grid_ignores_players_with_an_unknown_bye_week():
    roster = [_p("Mystery", "RB", 10.0, bye_week=None)]

    grid = bye_grid(roster, {"RB": 1})

    assert all(w["starters_out"] == 0 for w in grid)


def test_bye_grid_does_not_blame_byes_for_a_position_never_drafted():
    """A roster with no kicker is short at K every week of the season.

    Reporting that as bye damage buries the weeks a bye actually breaks.
    """
    roster = [_p("Just an RB", "RB", 10.0, bye_week=9)]

    grid = bye_grid(roster, {"RB": 1, "K": 1})

    assert all("K" not in week["unfillable"] for week in grid)


def test_bye_grid_still_flags_a_slot_a_bye_actually_breaks():
    roster = [_p("Lonely K", "K", 8.0, bye_week=9)]

    grid = bye_grid(roster, {"K": 1})

    assert _week(grid, 9)["unfillable"] == ["K"]
    assert _week(grid, 8)["unfillable"] == []


def test_bye_grid_reports_slots_left_permanently_unfilled_separately():
    """The roster gap is still worth surfacing — just not as a bye problem."""
    roster = [_p("Just an RB", "RB", 10.0, bye_week=9)]

    grid = bye_grid(roster, {"RB": 1, "K": 1, "DEF": 1})

    assert grid.unfilled_slots == ["K", "DEF"]


# ---------------------------------------------------------------------------
# analyze_value
# ---------------------------------------------------------------------------


def test_analyze_value_calls_a_player_who_fell_a_steal():
    log = [_pick(30, "1", _p("Faller", "RB", 15.0, adp_rank=12.0))]

    result = analyze_value(log, "1", adp_sources={})

    assert result["picks"][0]["verdict"] == "steal"
    assert result["picks"][0]["delta"] == pytest.approx(18.0)
    assert result["steals"] == 1


def test_analyze_value_calls_a_player_taken_early_a_reach():
    log = [_pick(5, "1", _p("Early", "WR", 15.0, adp_rank=40.0))]

    result = analyze_value(log, "1", adp_sources={})

    assert result["picks"][0]["verdict"] == "reach"
    assert result["reaches"] == 1


def test_analyze_value_calls_a_pick_near_its_adp_fair():
    log = [_pick(20, "1", _p("On Time", "TE", 15.0, adp_rank=21.0))]

    result = analyze_value(log, "1", adp_sources={})

    assert result["picks"][0]["verdict"] == "fair"
    assert result["steals"] == 0
    assert result["reaches"] == 0


def test_analyze_value_ignores_other_teams_picks():
    log = [
        _pick(1, "2", _p("Not Mine", "RB", 15.0, adp_rank=90.0)),
        _pick(2, "1", _p("Mine", "RB", 15.0, adp_rank=2.0)),
    ]

    result = analyze_value(log, "1", adp_sources={})

    assert [p["player"]["name"] for p in result["picks"]] == ["Mine"]


def test_analyze_value_gives_no_verdict_to_a_synthetic_tail_adp():
    """espn_tail ADP is max_ffc_adp + rank — a sort key, not a draft position.

    Judging value against it would invent steals out of arithmetic.
    """
    player = _p("Deep Guy", "WR", 3.0, adp_rank=400.0, db_id=77)
    log = [_pick(180, "1", player)]

    result = analyze_value(log, "1", adp_sources={77: "espn_tail"})

    assert result["picks"][0]["verdict"] is None
    assert result["steals"] == 0


def test_analyze_value_excludes_tail_players_from_the_headline_callouts():
    real = _p("Real Steal", "RB", 15.0, adp_rank=20.0, db_id=1)
    tail = _p("Tail Guy", "WR", 3.0, adp_rank=400.0, db_id=2)
    log = [_pick(30, "1", real), _pick(180, "1", tail)]

    result = analyze_value(log, "1", adp_sources={2: "espn_tail"})

    assert result["best_value"]["player"]["name"] == "Real Steal"


def test_analyze_value_reports_the_best_and_worst_picks():
    log = [
        _pick(30, "1", _p("Bargain", "RB", 15.0, adp_rank=10.0)),
        _pick(31, "1", _p("Fine", "WR", 15.0, adp_rank=31.0)),
        _pick(32, "1", _p("Overpay", "TE", 15.0, adp_rank=95.0)),
    ]

    result = analyze_value(log, "1", adp_sources={})

    assert result["best_value"]["player"]["name"] == "Bargain"
    assert result["biggest_reach"]["player"]["name"] == "Overpay"


def test_analyze_value_handles_a_player_with_no_adp_at_all():
    log = [_pick(30, "1", _p("Unranked", "RB", 15.0, adp_rank=None))]

    result = analyze_value(log, "1", adp_sources={})

    assert result["picks"][0]["verdict"] is None
    assert result["best_value"] is None


# ---------------------------------------------------------------------------
# sparkline_points
# ---------------------------------------------------------------------------


def _pairs(points):
    return [tuple(float(n) for n in pair.split(",")) for pair in points.split()]


def test_sparkline_points_spans_the_full_width():
    pairs = _pairs(sparkline_points([5.0, 10.0, 2.0]))

    assert pairs[0][0] == pytest.approx(0.0)
    assert pairs[-1][0] == pytest.approx(100.0)


def test_sparkline_points_puts_the_best_game_at_the_top():
    """SVG y grows downward, so the highest score gets the smallest y."""
    pairs = _pairs(sparkline_points([5.0, 20.0, 2.0]))
    ys = [y for _, y in pairs]

    assert ys[1] == min(ys)
    assert ys[2] == max(ys)


def test_sparkline_points_draws_a_flat_line_for_identical_scores():
    """A player who scored the same every week must not divide by zero."""
    pairs = _pairs(sparkline_points([9.0, 9.0, 9.0]))
    ys = {y for _, y in pairs}

    assert len(ys) == 1


def test_sparkline_points_is_empty_below_two_games():
    assert sparkline_points([7.0]) == ""
    assert sparkline_points([]) == ""


# ---------------------------------------------------------------------------
# team_logo_url
# ---------------------------------------------------------------------------


def test_team_logo_url_lowercases_the_abbreviation():
    assert team_logo_url("KC").endswith("/kc.png")


def test_team_logo_url_translates_washington():
    """normalize_team gives WAS; ESPN's logo CDN files it under wsh."""
    assert team_logo_url("WAS").endswith("/wsh.png")


def test_team_logo_url_is_empty_for_a_player_without_a_team():
    assert team_logo_url("FA") == ""
    assert team_logo_url(None) == ""


# ---------------------------------------------------------------------------
# DraftRecapService — database enrichment
# ---------------------------------------------------------------------------

SEASON = 2026
PRIOR = 2025


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _add_player(db, name, position, *, weekly=(), adp_source="fantasyfootballcalculator",
                prior_stats=None, **profile):
    player = DBPlayer(
        player_id=f"ffc_{name.lower().replace(' ', '_')}",
        name=name,
        position=position,
        nfl_team=profile.pop("nfl_team", "KC"),
        bye_week=profile.pop("bye_week", 9),
        **profile,
    )
    db.add(player)
    db.flush()

    db.add(DBPlayerSeasonStats(
        player_id=player.id, year=SEASON, adp=10.0, adp_source=adp_source,
        adp_stdev=1.5, adp_high=8.0, adp_low=14.0, adp_times_drafted=200,
    ))
    if prior_stats is not None:
        db.add(DBPlayerSeasonStats(player_id=player.id, year=PRIOR, **prior_stats))
    for week, points in enumerate(weekly, start=1):
        db.add(DBPlayerGameLog(
            player_id=player.id, year=PRIOR, week=week,
            fantasy_points=points, opponent="DEN",
        ))
    db.commit()
    return player


def _state(roster_players, lineup_slots=None):
    """Minimal completed one-team draft state."""
    lineup_slots = lineup_slots or {"RB": 1, "BENCH": 5}
    picks = [_pick(i + 1, "1", p) for i, p in enumerate(roster_players)]
    return {
        "draft_id": "test-draft",
        "num_teams": 1,
        "num_rounds": len(roster_players),
        "user_pick_position": 1,
        "status": "complete",
        "rosters": {"1": list(roster_players)},
        "picks_log": picks,
        "available_players": [],
        "lineup_slots": lineup_slots,
        "strategies": {"1": "user"},
    }


def test_build_attaches_the_prior_season_stat_line(db):
    row = _add_player(
        db, "Stat Guy", "RB",
        prior_stats={"games_played": 17, "rush_yd": 1200, "rush_td": 11,
                     "fantasy_points_avg": 15.5},
    )
    state = _state([_p("Stat Guy", "RB", 15.0, db_id=row.id)])

    recap = DraftRecapService(db).build(state, year=SEASON)

    season = recap.players[0]["season"]
    assert season["rush_yd"] == 1200
    assert season["fantasy_points_avg"] == pytest.approx(15.5)


def test_build_reads_adp_spread_from_the_current_season_row(db):
    """The stat line comes from last season; the ADP spread from this one."""
    row = _add_player(db, "Spread Guy", "RB", prior_stats={"games_played": 17})
    state = _state([_p("Spread Guy", "RB", 15.0, db_id=row.id)])

    recap = DraftRecapService(db).build(state, year=SEASON)

    spread = recap.players[0]["adp_spread"]
    assert spread["high"] == pytest.approx(8.0)
    assert spread["low"] == pytest.approx(14.0)
    assert spread["times_drafted"] == 200


def test_build_computes_a_distribution_from_prior_season_game_logs(db):
    row = _add_player(db, "Logged", "RB", weekly=[float(n) for n in range(11)])
    state = _state([_p("Logged", "RB", 15.0, db_id=row.id)])

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.players[0]["distribution"]["median"] == pytest.approx(5.0)
    assert len(recap.players[0]["weekly"]) == 11


def test_build_marks_a_player_with_no_game_logs_as_having_no_data(db):
    row = _add_player(db, "Rookie", "RB")
    state = _state([_p("Rookie", "RB", 15.0, db_id=row.id)])

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.players[0]["distribution"] is None
    assert recap.players[0]["has_history"] is False


def test_build_counts_outlook_coverage_over_starters_with_history(db):
    """One of two starters has no prior season, so the roll-up says so."""
    logged = _add_player(db, "Logged", "RB", weekly=[10.0, 12.0, 14.0])
    rookie = _add_player(db, "Rookie", "RB")
    state = _state(
        [_p("Logged", "RB", 20.0, db_id=logged.id),
         _p("Rookie", "RB", 15.0, db_id=rookie.id)],
        lineup_slots={"RB": 2, "BENCH": 0},
    )

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.outlook["covered"] == 1
    assert recap.outlook["total"] == 2


def test_build_outlook_sums_only_starters(db):
    """A bench player's ceiling does not count — he does not score."""
    starter = _add_player(db, "Starter", "RB", weekly=[10.0, 10.0, 10.0])
    benched = _add_player(db, "Benched", "RB", weekly=[99.0, 99.0, 99.0])
    state = _state(
        [_p("Starter", "RB", 20.0, db_id=starter.id),
         _p("Benched", "RB", 1.0, db_id=benched.id)],
        lineup_slots={"RB": 1, "BENCH": 1},
    )

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.outlook["ceiling"] == pytest.approx(10.0)


def test_build_survives_a_player_with_no_database_id(db):
    """The ESPN-ADP pool matches on name and can leave db_id unset."""
    player = _p("Ghost", "RB", 15.0)
    player.pop("db_id")
    state = _state([player])

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.players[0]["has_history"] is False
    assert recap.players[0]["season"] is None


def test_build_gives_no_value_verdict_to_a_tail_sourced_player(db):
    row = _add_player(db, "Tail Guy", "WR", adp_source="espn_tail")
    state = _state([_p("Tail Guy", "WR", 3.0, db_id=row.id, adp_rank=400.0)])

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.value["picks"][0]["verdict"] is None


def test_consistency_excludes_players_with_too_few_games(db):
    """One logged game has zero variance, which would rank it steadiest of all.

    A kicker with a single game is not more reliable than a back with sixteen.
    """
    steady = _add_player(db, "Steady", "RB", weekly=[10.0, 11.0, 9.0, 10.0, 10.0])
    one_game = _add_player(db, "One Game", "K", weekly=[7.0])
    state = _state(
        [_p("Steady", "RB", 20.0, db_id=steady.id),
         _p("One Game", "K", 8.0, db_id=one_game.id)],
        lineup_slots={"RB": 1, "K": 1},
    )

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert [p["name"] for p in recap.consistency] == ["Steady"]


def test_a_single_game_does_not_count_as_a_reliable_spread(db):
    """Floor, typical and ceiling all equal from one game reads as certainty."""
    row = _add_player(db, "One Game", "K", weekly=[5.0])
    state = _state([_p("One Game", "K", 8.0, db_id=row.id)])

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.players[0]["spread_reliable"] is False


def test_a_full_season_counts_as_a_reliable_spread(db):
    row = _add_player(db, "Full Year", "RB", weekly=[8.0, 12.0, 15.0, 9.0, 20.0])
    state = _state([_p("Full Year", "RB", 15.0, db_id=row.id)])

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.players[0]["spread_reliable"] is True


def test_a_kicker_still_gets_a_stat_line(db):
    """K and DEF have no carries or targets, but games and average still say something."""
    row = _add_player(db, "Big Leg", "K",
                      prior_stats={"games_played": 17, "fantasy_points_total": 150.0,
                                   "fantasy_points_avg": 8.8})
    state = _state([_p("Big Leg", "K", 8.0, db_id=row.id)])

    recap = DraftRecapService(db).build(state, year=SEASON)

    labels = [stat["label"] for stat in recap.players[0]["stat_line"]]
    assert "Games" in labels
    assert "Pts/Gm" in labels


def test_build_attaches_profile_fields_from_the_player_row(db):
    row = _add_player(db, "Profiled", "RB", age=26, college="Georgia", years_exp=4)
    state = _state([_p("Profiled", "RB", 15.0, db_id=row.id)])

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.players[0]["profile"]["college"] == "Georgia"
    assert recap.players[0]["profile"]["age"] == 26


def test_build_prefers_the_database_headshot_and_bye_week(db):
    """The ESPN-ADP pool path carries neither."""
    row = _add_player(db, "Imaged", "RB", bye_week=7,
                      headshot_url="https://example.test/x.png")
    player = _p("Imaged", "RB", 15.0, db_id=row.id)
    player.pop("bye_week", None)
    state = _state([player])

    recap = DraftRecapService(db).build(state, year=SEASON)

    assert recap.players[0]["headshot_url"] == "https://example.test/x.png"
    assert recap.players[0]["bye_week"] == 7


def test_build_runs_a_fixed_number_of_queries_regardless_of_roster_size(db):
    """Enrichment is bulk-loaded; a bigger roster must not mean more queries."""
    from sqlalchemy import event

    def count_for(prefix, roster_size):
        players = [
            _add_player(db, f"{prefix}{n}", "RB", weekly=[10.0, 12.0])
            for n in range(roster_size)
        ]
        state = _state([
            _p(f"{prefix}{n}", "RB", 15.0, db_id=p.id) for n, p in enumerate(players)
        ])

        queries = []

        def record(*args, **kwargs):
            queries.append(1)

        event.listen(db.bind, "before_cursor_execute", record)
        try:
            DraftRecapService(db).build(state, year=SEASON)
        finally:
            event.remove(db.bind, "before_cursor_execute", record)
        return len(queries)

    assert count_for("small", 2) == count_for("big", 8)
