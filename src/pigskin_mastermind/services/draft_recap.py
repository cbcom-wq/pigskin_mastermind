"""Post-draft recap: enrich a finished draft with database stats.

The mock draft engine (:mod:`services.mock_draft`) is deliberately database
free — it holds only what the ADP pool handed it.  This module is where the
finished roster meets everything else the app knows: prior-season production,
weekly game logs, ADP spread, bye weeks and headshots.

Kept separate from the engine so the engine's tests never need a database.
"""

import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
)
from pigskin_mastermind.utils.season import current_fantasy_season

# Slots that accept several positions.  Everything else is filled by an exact
# position match.  FLEX is RB/WR/TE only — never QB, K or DEF.
MULTI_POSITION_SLOTS: Dict[str, Tuple[str, ...]] = {
    "FLEX": ("RB", "WR", "TE"),
    "SUPERFLEX": ("QB", "RB", "WR", "TE"),
}

# Holds no starter; exists only to size the roster.
NON_STARTING_SLOTS = frozenset({"BENCH", "IR"})

# Fill order.  Exact-position slots go first so a stud RB lands at RB rather
# than being burned in FLEX while the RB slot sits empty.
_SLOT_FILL_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF", "FLEX", "SUPERFLEX"]


def fill_lineup(
    roster: List[Dict[str, Any]],
    lineup_slots: Dict[str, int],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split a roster into an optimal starting lineup and a bench.

    Required slots are filled first with the best available player by projected
    points, then the flex slots from whoever remains.

    Args:
        roster: Player dicts as carried in the draft state.
        lineup_slots: Slot name → count, e.g. ``{"QB": 1, "FLEX": 1, "BENCH": 6}``.

    Returns:
        A ``(starters, bench)`` tuple.  ``starters`` is a list of
        ``{"slot": name, "player": dict | None}`` in fill order; ``player`` is
        ``None`` where the roster could not fill the slot.  ``bench`` holds the
        leftover players, best first.
    """
    remaining = sorted(
        roster,
        key=lambda p: p.get("projected_points") or 0.0,
        reverse=True,
    )
    starters: List[Dict[str, Any]] = []

    ordered_slots = sorted(
        (s for s in lineup_slots if s not in NON_STARTING_SLOTS),
        key=lambda s: _SLOT_FILL_ORDER.index(s) if s in _SLOT_FILL_ORDER else len(_SLOT_FILL_ORDER),
    )

    for slot in ordered_slots:
        eligible = MULTI_POSITION_SLOTS.get(slot, (slot,))
        for _ in range(lineup_slots.get(slot, 0)):
            pick = next((p for p in remaining if p.get("position") in eligible), None)
            if pick is not None:
                remaining.remove(pick)
            starters.append({"slot": slot, "player": pick})

    return starters, remaining


# Boom / bust thresholds in fantasy points, by position.  The engine's own
# 25/8 pair is a flex-player rule: applied to a kicker it calls almost every
# week a bust, and applied to a quarterback it calls an ordinary week a boom.
BOOM_BUST_THRESHOLDS: Dict[str, Tuple[float, float]] = {
    "QB": (25.0, 14.0),
    "RB": (20.0, 8.0),
    "WR": (20.0, 8.0),
    "TE": (20.0, 8.0),
    "K": (12.0, 5.0),
    "DEF": (12.0, 3.0),
}
_DEFAULT_THRESHOLDS = (20.0, 8.0)


def weekly_distribution(
    points: List[float],
    position: str,
) -> Optional[Dict[str, Any]]:
    """Summarise a player's weekly fantasy scores.

    The percentiles are the observed spread of games actually played, not a
    model's forecast — the ``player_projections`` table is empty locally, and
    last season's real scores are the honest stand-in.

    Args:
        points: One fantasy-point total per game played.
        position: Used to pick boom/bust thresholds.

    Returns:
        Floor (p10), median, ceiling (p90), standard deviation, boom and bust
        rates, best and worst game, and the game count.  ``None`` when the
        player has no games — rookies and most kickers and defenses — so the
        caller can show "no data" instead of a row of zeros.
    """
    if not points:
        return None

    boom_at, bust_at = BOOM_BUST_THRESHOLDS.get(position, _DEFAULT_THRESHOLDS)
    games = len(points)

    return {
        "games": games,
        "floor": float(np.percentile(points, 10)),
        "median": float(np.percentile(points, 50)),
        "ceiling": float(np.percentile(points, 90)),
        "std_dev": float(statistics.pstdev(points)) if games > 1 else 0.0,
        "best": float(max(points)),
        "worst": float(min(points)),
        "boom_rate": sum(1 for p in points if p > boom_at) / games,
        "bust_rate": sum(1 for p in points if p < bust_at) / games,
        "boom_at": boom_at,
        "bust_at": bust_at,
    }


def _group_points(roster: List[Dict[str, Any]], position: str, count: int) -> float:
    """Sum of a roster's ``count`` best players at ``position``."""
    scores = sorted(
        (p.get("projected_points") or 0.0 for p in roster if p.get("position") == position),
        reverse=True,
    )
    return float(sum(scores[:count]))


def position_group_ranks(
    rosters: Dict[str, List[Dict[str, Any]]],
    user_slot: str,
    lineup_slots: Dict[str, int],
) -> Dict[str, Dict[str, Any]]:
    """Rank the user's position groups against every other team in the draft.

    Ranking is per base position rather than per lineup slot: FLEX and
    SUPERFLEX accept several positions, so a flex player is already counted in
    their base group and ranking the slot separately would double-count them.

    Each team's group score is the sum of its best *N* players at the position,
    where *N* is that position's starter requirement.

    Returns:
        Position → ``{your_points, rank, of, margin, field_average}``.  ``rank``
        is 1-based with 1 best; ``margin`` is the user's points less the average
        of the other teams.
    """
    ranks: Dict[str, Dict[str, Any]] = {}

    for position, count in lineup_slots.items():
        if position in MULTI_POSITION_SLOTS or position in NON_STARTING_SLOTS:
            continue
        if not count:
            continue

        totals = {
            slot: _group_points(roster, position, count)
            for slot, roster in rosters.items()
        }
        mine = totals.get(user_slot, 0.0)
        others = [v for slot, v in totals.items() if slot != user_slot]
        field_average = sum(others) / len(others) if others else 0.0

        # Much of the draft pool carries projected_points of 0 — the ADP
        # importers do not all supply projections.  When nobody in the league
        # has a number at this position, every team ties at zero and the
        # resulting order is an artefact of dict iteration, not a ranking.
        ranked = any(value > 0 for value in totals.values())

        ranks[position] = {
            "your_points": round(mine, 1),
            "rank": (sorted(totals.values(), reverse=True).index(mine) + 1) if ranked else None,
            "of": len(totals),
            "margin": round(mine - field_average, 1) if ranked else None,
            "field_average": round(field_average, 1),
            "ranked": ranked,
            # Zero against a field that has numbers is a gap in the data, not a
            # weak group.  Showing it as a deficit blames the user for a
            # projection the app never had.
            "your_points_missing": ranked and mine == 0,
        }

    return ranks


# How much better the passed-over player's ADP must be before it is worth
# mentioning.  Something is always taken between your turns in a snake draft,
# so without a floor this reports every single round and says nothing.
MIN_REGRET_GAP = 12.0


def passed_on(
    picks_log: List[Dict[str, Any]],
    final_available: List[Dict[str, Any]],
    user_slot: str,
    min_gap: float = MIN_REGRET_GAP,
) -> List[Dict[str, Any]]:
    """Find the best player the user left on the board at each of their turns.

    "Best" means lowest ADP, and only counts when the player was gone by the
    user's *next* turn — passing on someone still there later cost nothing.

    The draft state keeps no per-pick snapshot of the board, but one is not
    needed: the players available at pick *k* are the ones never drafted plus
    everyone drafted at *k* or later.

    Args:
        picks_log: Every pick in order, as stored on the draft state.
        final_available: Players left undrafted when the draft ended.
        user_slot: The user's slot, as a string key.
        min_gap: How many ADP places better the passed player must be than the
            one taken.  The default keeps this to genuine misses; pass ``0`` to
            see every turn.

    Returns:
        One entry per turn worth mentioning, worst first:
        ``{round, pick_number, took, passed, adp_gap}``.
    """
    user_picks = [pk for pk in picks_log if str(pk["slot"]) == str(user_slot)]
    results: List[Dict[str, Any]] = []

    for position, pick in enumerate(user_picks):
        # No next turn means nothing was forgone.
        if position + 1 >= len(user_picks):
            break

        this_pick = pick["pick_number"]
        next_pick = user_picks[position + 1]["pick_number"]

        taken_later = [pk["player"] for pk in picks_log if pk["pick_number"] > this_pick]
        board = final_available + taken_later
        candidates = [p for p in board if p.get("adp_rank") is not None]
        if not candidates:
            continue

        best = min(candidates, key=lambda p: p["adp_rank"])

        # Only a regret if someone else took them before the user picked again.
        gone_by_next = any(
            pk["player"].get("id") == best.get("id")
            and this_pick < pk["pick_number"] < next_pick
            for pk in picks_log
        )
        if not gone_by_next:
            continue

        took = pick["player"]
        gap = (took.get("adp_rank") or 0) - best["adp_rank"]
        if gap < min_gap:
            continue

        results.append({
            "round": pick["round"],
            "pick_number": this_pick,
            "took": took,
            "passed": best,
            "adp_gap": round(gap, 1),
        })

    return sorted(results, key=lambda r: r["adp_gap"], reverse=True)


# NFL bye weeks have run 5 through 14 in recent seasons.
BYE_WEEK_RANGE = range(5, 15)


@dataclass
class ByeGrid:
    """Bye-week damage, plus the roster gaps that are not the byes' fault."""

    weeks: List[Dict[str, Any]] = field(default_factory=list)
    unfilled_slots: List[str] = field(default_factory=list)

    def __iter__(self):
        return iter(self.weeks)

    def __len__(self):
        return len(self.weeks)


def _missing_slots(starters: List[Dict[str, Any]]) -> List[str]:
    return [s["slot"] for s in starters if s["player"] is None]


def bye_grid(
    roster: List[Dict[str, Any]],
    lineup_slots: Dict[str, int],
) -> ByeGrid:
    """Report the bye-week damage to a roster, week by week.

    Losing starters to a bye only hurts when the bench cannot cover, so each
    week is re-optimised from whoever is actually available — the same fill
    rules the lineup itself uses.

    A slot the roster can *never* fill (nobody drafted a kicker) is short every
    week of the season.  Counting that as bye damage would flag all ten weeks
    and bury the weeks a bye genuinely breaks, so those slots are reported once
    on ``unfilled_slots`` instead.

    Players with an unknown bye week are treated as available all season; the
    caller surfaces the gap rather than guessing.

    Returns:
        A :class:`ByeGrid`.  Iterating it yields one entry per bye week:
        ``{week, starters_out, players, unfillable}``, where ``unfillable``
        holds only the slots this week's byes broke.
    """
    baseline, _ = fill_lineup(roster, lineup_slots)
    baseline_starters = [s["player"] for s in baseline if s["player"] is not None]
    always_missing = _missing_slots(baseline)

    weeks: List[Dict[str, Any]] = []
    for week in BYE_WEEK_RANGE:
        out = [p for p in baseline_starters if p.get("bye_week") == week]
        available = [p for p in roster if p.get("bye_week") != week]
        covered, _ = fill_lineup(available, lineup_slots)

        # Discount the slots that were already unfillable before any bye.
        still_owed = list(always_missing)
        broken_by_bye: List[str] = []
        for slot in _missing_slots(covered):
            if slot in still_owed:
                still_owed.remove(slot)
            else:
                broken_by_bye.append(slot)

        weeks.append({
            "week": week,
            "starters_out": len(out),
            "players": out,
            "unfillable": broken_by_bye,
        })

    return ByeGrid(weeks=weeks, unfilled_slots=always_missing)


# ADP sources whose values are real consensus draft positions.  Anything else
# (notably ``espn_tail``, which synthesises ``max_ffc_adp + rank``) is a sort
# key and cannot support a value verdict.
REAL_ADP_SOURCES = frozenset({"fantasyfootballcalculator", "ffc", "espn"})

# Picks later than ADP are value; earlier is a reach.  Thresholds in picks.
_VALUE_TIERS = [
    (10, "steal", "Great Steal"),
    (3, "value", "Good Value"),
    (-3, "fair", "Fair"),
    (-10, "slight_reach", "Slight Reach"),
]
_WORST_TIER = ("reach", "Big Reach")


def _verdict(delta: float) -> Tuple[str, str]:
    for threshold, verdict, label in _VALUE_TIERS:
        if delta >= threshold:
            return verdict, label
    return _WORST_TIER


def analyze_value(
    picks_log: List[Dict[str, Any]],
    user_slot: str,
    adp_sources: Dict[int, str],
) -> Dict[str, Any]:
    """Judge each of the user's picks against where the player normally goes.

    A pick made later than a player's ADP is value; earlier is a reach.  This
    only holds when the ADP is a real consensus number — see
    :data:`REAL_ADP_SOURCES`.  Players whose ADP came from the ESPN tail get no
    verdict and are kept out of every count and callout, because their "ADP"
    is a sort key and would manufacture enormous fake steals.

    Args:
        picks_log: Every pick in order, as stored on the draft state.
        user_slot: The user's slot, as a string key.
        adp_sources: ``db_id`` → ``adp_source``, from the current season's
            stat rows.  A missing entry is treated as a real source, since the
            tail is the only synthetic one and it is always labelled.

    Returns:
        ``{picks, steals, reaches, best_value, biggest_reach, average_delta}``.
        Each pick carries ``verdict`` of ``None`` when it cannot be judged.
    """
    picks: List[Dict[str, Any]] = []

    for pick in picks_log:
        if str(pick["slot"]) != str(user_slot):
            continue

        player = pick["player"]
        adp = player.get("adp_rank")
        source = adp_sources.get(player.get("db_id"))
        judgeable = adp is not None and (source is None or source in REAL_ADP_SOURCES)

        delta = (pick["pick_number"] - adp) if judgeable else None
        verdict, label = _verdict(delta) if judgeable else (None, None)

        picks.append({
            "round": pick["round"],
            "pick_number": pick["pick_number"],
            "player": player,
            "adp": adp,
            "adp_source": source,
            "delta": round(delta, 1) if delta is not None else None,
            "verdict": verdict,
            "label": label,
        })

    judged = [p for p in picks if p["verdict"] is not None]
    deltas = [p["delta"] for p in judged]

    return {
        "picks": picks,
        "judged_count": len(judged),
        "steals": sum(1 for p in judged if p["verdict"] == "steal"),
        "reaches": sum(1 for p in judged if p["verdict"] in ("reach", "slight_reach")),
        "average_delta": round(sum(deltas) / len(deltas), 1) if deltas else None,
        "best_value": max(judged, key=lambda p: p["delta"]) if judged else None,
        "biggest_reach": min(judged, key=lambda p: p["delta"]) if judged else None,
    }


# Counting stats worth showing per position.  Keyed to what the position
# actually does, so a running back's card is not padded with passing zeros.
STAT_LINE_FIELDS: Dict[str, Tuple[Tuple[str, str], ...]] = {
    "QB": (("pass_yd", "Pass Yds"), ("pass_td", "Pass TD"), ("pass_int", "INT"),
           ("rush_yd", "Rush Yds"), ("rush_td", "Rush TD")),
    "RB": (("rush_att", "Carries"), ("rush_yd", "Rush Yds"), ("rush_td", "Rush TD"),
           ("rec", "Rec"), ("rec_yd", "Rec Yds")),
    "WR": (("targets", "Targets"), ("rec", "Rec"), ("rec_yd", "Rec Yds"),
           ("rec_td", "Rec TD")),
    "TE": (("targets", "Targets"), ("rec", "Rec"), ("rec_yd", "Rec Yds"),
           ("rec_td", "Rec TD")),
    # Kickers and defenses have no carries or targets to show, but volume and
    # scoring rate still say something — without these their cards are blank.
    "K": (("games_played", "Games"), ("fantasy_points_total", "Points"),
          ("fantasy_points_avg", "Pts/Gm")),
    "DEF": (("games_played", "Games"), ("fantasy_points_total", "Points"),
            ("fantasy_points_avg", "Pts/Gm")),
}

# Below this many logged games the spread of weekly scores is noise: a single
# game has zero variance, so its floor, median and ceiling all land on the same
# number and read as certainty rather than one afternoon.
MIN_SPREAD_GAMES = 4

_SEASON_COPY_FIELDS = (
    "games_played", "pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int",
    "rush_att", "rush_yd", "rush_td", "targets", "rec", "rec_yd", "rec_td",
    "fantasy_points_total", "fantasy_points_avg",
)

_PROFILE_FIELDS = (
    "age", "height", "weight", "college", "years_exp", "jersey",
    "draft_number", "pos_rank", "percent_owned",
)

# Sparkline geometry, in the viewBox the template declares.
SPARKLINE_WIDTH = 100.0
SPARKLINE_HEIGHT = 30.0


def sparkline_points(points: List[float]) -> str:
    """Render weekly scores as an SVG ``polyline`` points string.

    Computed here rather than in the template because normalising a series into
    viewBox coordinates is unreadable in Jinja.

    Returns an empty string below two games — a single point is not a line.
    """
    if len(points) < 2:
        return ""

    low, high = min(points), max(points)
    span = high - low
    step = SPARKLINE_WIDTH / (len(points) - 1)

    coords = []
    for index, value in enumerate(points):
        # A flat series has no span to normalise against; draw it down the middle.
        ratio = 0.5 if span == 0 else (value - low) / span
        y = SPARKLINE_HEIGHT - (ratio * SPARKLINE_HEIGHT)
        coords.append(f"{index * step:.1f},{y:.1f}")

    return " ".join(coords)


_LOGO_BASE = "https://a.espncdn.com/i/teamlogos/nfl/500"

# ESPN's logo CDN spells Washington ``wsh``; ``utils.nfl_teams.normalize_team``
# produces ``WAS``.  Every other abbreviation matches once lowercased.
_LOGO_ABBR_OVERRIDES = {"WAS": "wsh"}

# Values ``normalize_team`` uses for "no NFL team", which have no logo.
_TEAMLESS = frozenset({"FA", "NONE", ""})


def team_logo_url(nfl_team: Optional[str]) -> str:
    """Return the ESPN logo URL for an NFL team abbreviation.

    Returns an empty string for free agents and unknown teams so the template
    can fall back rather than requesting a 404 image.
    """
    if not nfl_team or nfl_team.strip().upper() in _TEAMLESS:
        return ""
    abbr = nfl_team.strip().upper()
    return f"{_LOGO_BASE}/{_LOGO_ABBR_OVERRIDES.get(abbr, abbr.lower())}.png"


@dataclass
class DraftRecap:
    """Everything the recap page renders, assembled once."""

    draft_id: str
    user_slot: str
    num_teams: int
    num_rounds: int
    season: int
    prior_season: int

    players: List[Dict[str, Any]] = field(default_factory=list)
    starters: List[Dict[str, Any]] = field(default_factory=list)
    bench: List[Dict[str, Any]] = field(default_factory=list)
    position_ranks: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    value: Dict[str, Any] = field(default_factory=dict)
    regrets: List[Dict[str, Any]] = field(default_factory=list)
    byes: List[Dict[str, Any]] = field(default_factory=list)
    outlook: Dict[str, Any] = field(default_factory=dict)
    consistency: List[Dict[str, Any]] = field(default_factory=list)
    totals: Dict[str, Any] = field(default_factory=dict)
    grade: Optional[Dict[str, Any]] = None
    headline: str = ""


class DraftRecapService:
    """Turn a finished draft into the recap page's payload.

    All database access happens in :meth:`build`, in three bulk queries keyed
    on the ``db_id`` each roster player already carries.  Roster size does not
    change the query count.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def build(
        self,
        state: Dict[str, Any],
        *,
        grade: Optional[Dict[str, Any]] = None,
        year: Optional[int] = None,
    ) -> DraftRecap:
        """Assemble the recap for the user's team in ``state``.

        Args:
            state: A completed draft state from the mock draft engine.
            grade: Optional output of ``MockDraftEngine.grade_draft``.
            year: Season to treat as current.  Defaults to the live fantasy
                season; the prior season supplies the stat lines and game logs.
        """
        season = year or current_fantasy_season()
        prior = season - 1

        user_slot = str(state.get("user_pick_position", ""))
        roster = list(state["rosters"].get(user_slot, []))
        lineup_slots = state.get("lineup_slots") or {}

        db_ids = [p["db_id"] for p in roster if p.get("db_id")]
        rows, season_rows, prior_rows, logs = self._load(db_ids, season, prior)

        players = [
            self._enrich(p, rows, season_rows, prior_rows, logs) for p in roster
        ]
        # Enrichment can supply a bye week the pool did not carry, so the
        # lineup and bye grid must run on the enriched copies.
        starters, bench = fill_lineup(players, lineup_slots)

        rosters = dict(state["rosters"])
        rosters[user_slot] = players

        recap = DraftRecap(
            draft_id=state["draft_id"],
            user_slot=user_slot,
            num_teams=state["num_teams"],
            num_rounds=state["num_rounds"],
            season=season,
            prior_season=prior,
            players=players,
            starters=starters,
            bench=bench,
            position_ranks=position_group_ranks(rosters, user_slot, lineup_slots),
            value=analyze_value(
                state["picks_log"],
                user_slot,
                {pid: r.adp_source for pid, r in season_rows.items()},
            ),
            regrets=passed_on(
                state["picks_log"], state.get("available_players", []), user_slot
            ),
            byes=bye_grid(players, lineup_slots),
            grade=grade,
        )
        recap.outlook = self._outlook(starters)
        recap.consistency = self._consistency(players)
        recap.totals = self._totals(state, user_slot, starters)
        recap.headline = self._headline(recap)
        return recap

    # -- database ---------------------------------------------------------

    def _load(self, db_ids, season, prior):
        """Three bulk queries: player rows, both season rows, prior game logs."""
        if not db_ids:
            return {}, {}, {}, {}

        rows = {
            p.id: p
            for p in self.db.query(DBPlayer).filter(DBPlayer.id.in_(db_ids)).all()
        }

        season_rows: Dict[int, Any] = {}
        prior_rows: Dict[int, Any] = {}
        for row in (
            self.db.query(DBPlayerSeasonStats)
            .filter(
                DBPlayerSeasonStats.player_id.in_(db_ids),
                DBPlayerSeasonStats.year.in_([season, prior]),
            )
            .all()
        ):
            target = season_rows if row.year == season else prior_rows
            target[row.player_id] = row

        logs: Dict[int, List[Any]] = {}
        for log in (
            self.db.query(DBPlayerGameLog)
            .filter(
                DBPlayerGameLog.player_id.in_(db_ids),
                DBPlayerGameLog.year == prior,
            )
            .order_by(DBPlayerGameLog.week.asc())
            .all()
        ):
            logs.setdefault(log.player_id, []).append(log)

        return rows, season_rows, prior_rows, logs

    def _enrich(self, player, rows, season_rows, prior_rows, logs):
        """Merge one roster player with everything the database knows."""
        enriched = dict(player)
        db_id = player.get("db_id")
        row = rows.get(db_id)
        prior_row = prior_rows.get(db_id)
        season_row = season_rows.get(db_id)
        game_logs = logs.get(db_id, [])

        if row is not None:
            # The ESPN-ADP pool path name-matches and carries neither of these.
            enriched["headshot_url"] = enriched.get("headshot_url") or row.headshot_url or ""
            if enriched.get("bye_week") is None:
                enriched["bye_week"] = row.bye_week
            enriched["profile"] = {f: getattr(row, f) for f in _PROFILE_FIELDS}
        else:
            enriched.setdefault("headshot_url", "")
            enriched.setdefault("bye_week", None)
            enriched["profile"] = None

        enriched["season"] = (
            {f: getattr(prior_row, f) for f in _SEASON_COPY_FIELDS}
            if prior_row is not None
            else None
        )
        enriched["stat_line"] = self._stat_line(enriched["season"], player.get("position"))
        enriched["adp_spread"] = (
            {
                "stdev": season_row.adp_stdev,
                "high": season_row.adp_high,
                "low": season_row.adp_low,
                "times_drafted": season_row.adp_times_drafted,
                "source": season_row.adp_source,
            }
            if season_row is not None
            else None
        )

        weekly = [
            {"week": g.week, "points": round(g.fantasy_points or 0.0, 1),
             "opponent": g.opponent}
            for g in game_logs
        ]
        enriched["weekly"] = weekly
        enriched["distribution"] = weekly_distribution(
            [w["points"] for w in weekly], player.get("position", "")
        )
        enriched["has_history"] = enriched["distribution"] is not None
        enriched["spread_reliable"] = (
            enriched["distribution"] is not None
            and enriched["distribution"]["games"] >= MIN_SPREAD_GAMES
        )
        enriched["logo_url"] = team_logo_url(enriched.get("nfl_team"))
        enriched["sparkline"] = sparkline_points([w["points"] for w in weekly])
        enriched["initials"] = "".join(
            part[0] for part in str(player.get("name", "")).split()[:2] if part
        ).upper()
        return enriched

    @staticmethod
    def _stat_line(season: Optional[Dict[str, Any]], position: Optional[str]):
        """Position-appropriate counting stats, ready to render as label/value."""
        if not season or position not in STAT_LINE_FIELDS:
            return []
        return [
            {"label": label, "value": season.get(key) or 0}
            for key, label in STAT_LINE_FIELDS[position]
        ]

    # -- derived sections -------------------------------------------------

    @staticmethod
    def _outlook(starters):
        """Range of weekly outcomes for the starting lineup.

        Summing per-player percentiles overstates the spread of the true team
        distribution — every starter would have to hit their p90 in the same
        week.  Presented as a range of outcomes, never as a probability.
        """
        filled = [s["player"] for s in starters if s["player"] is not None]
        with_history = [p for p in filled if p.get("distribution")]

        return {
            "floor": round(sum(p["distribution"]["floor"] for p in with_history), 1),
            "median": round(sum(p["distribution"]["median"] for p in with_history), 1),
            "ceiling": round(sum(p["distribution"]["ceiling"] for p in with_history), 1),
            "covered": len(with_history),
            "total": len(filled),
        }

    @staticmethod
    def _consistency(players):
        """Roster ordered by how much their weekly scores moved, steadiest first.

        Players with only a game or two are left out entirely rather than shown
        at zero variance, which would rank them as the most reliable on the
        roster on the strength of one afternoon.
        """
        rated = [
            p for p in players
            if p.get("distribution")
            and p["distribution"]["games"] >= MIN_SPREAD_GAMES
        ]
        return sorted(rated, key=lambda p: p["distribution"]["std_dev"])

    @staticmethod
    def _totals(state, user_slot, starters):
        """Projected points, plus where the user's total lands in the draft."""
        team_totals = {
            slot: sum(p.get("projected_points") or 0.0 for p in roster)
            for slot, roster in state["rosters"].items()
        }
        mine = team_totals.get(user_slot, 0.0)

        return {
            "total_projected": round(mine, 1),
            "starters_projected": round(
                sum(
                    (s["player"].get("projected_points") or 0.0)
                    for s in starters
                    if s["player"] is not None
                ),
                1,
            ),
            "league_rank": sorted(team_totals.values(), reverse=True).index(mine) + 1,
            "of": len(team_totals),
        }

    @staticmethod
    def _headline(recap: DraftRecap) -> str:
        """One sentence pulling out whatever is most notable about the draft."""
        parts: List[str] = []

        steals = recap.value.get("steals", 0)
        if steals:
            parts.append(f"{steals} steal{'s' if steals != 1 else ''}")

        best_group = min(
            (r for r in recap.position_ranks.values() if r["rank"] == 1),
            key=lambda r: -r["margin"],
            default=None,
        )
        if best_group is not None:
            position = next(
                p for p, r in recap.position_ranks.items() if r is best_group
            )
            parts.append(f"the top {position} group in the league")

        rank = recap.totals.get("league_rank")
        if not parts and rank:
            parts.append(f"the #{rank} roster by projected points")

        if not parts:
            return "Your team is on the board."
        return "You landed " + " and ".join(parts) + "."
