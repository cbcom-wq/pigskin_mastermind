"""ESPN public box scores, for live in-week scoring.

Separate from espn_stats_mapper on purpose. That module maps ESPN's *fantasy*
PLAYER_STATS_MAP numeric ids, which is what the vendored espn_api client
returns. This is the public site API: a different service with a different
shape -- per-category arrays keyed by display labels -- and no authentication.

The endpoint is undocumented. Every function here degrades to empty rather
than raising, because this runs inside a background task whose death would
silently stop a league from scoring.

The label map below was verified against a recorded response (Chiefs @
Chargers, 2025 week 1, ``tests/fixtures/espn_summary_sample.json``), not
guessed from convention. Two things the recording changed from a first-pass
guess, documented here because the next person to touch this file will
otherwise "fix" them back to the wrong shape:

* Team defense stats (sacks, interceptions, defensive TDs, fumbles
  recovered) are NOT attributed to individual defenders in
  ``parse_player_stats``. ``DBPlayer`` models a defense by *team*, not by the
  linebacker who made the tackle (see ``player_identity.py``), and
  ``DEFAULT_SCORING_SETTINGS`` expects ``def_sack``/``def_int``/``def_td``/
  ``def_fumble_rec`` on the team-defense stat line. ESPN already computes a
  team total per category (the ``totals`` row alongside each category's
  ``athletes`` list), so ``parse_team_defense_stats`` reads those directly
  instead of summing individual box-score rows.
* ``def_fumble_rec`` for team X is read from team Y's fumbles ``LOST``
  total, not team X's own fumbles ``REC`` total. ESPN's per-player ``REC``
  count conflates "recovered my own team's fumble" (no turnover) with
  "recovered the opponent's fumble" (a real defensive turnover), and those
  are indistinguishable from the ``REC`` number alone. A fumble is only
  ever counted "lost" by the team that fumbled it, so the opponent's
  ``LOST`` total is the unambiguous turnover count.

Known gaps, kept empty rather than guessed:

* ``def_safety`` -- safeties do not appear in any per-player or per-team
  category in this endpoint's box score; there is no "safety" stat column
  to read. Would need scoring-play text parsing this module does not do.
* Field-goal distance is not in the ``kicking`` category at all -- it only
  carries a combined make/attempt string (``"3/3"``). Distances are parsed
  out of ``scoringPlays`` text (``"Harrison Butker 59 Yd Field Goal"``) and
  matched back to the kicker by display name; a kicker with no scoring-play
  match (e.g. a game with no ``scoringPlays`` block) yields no distance
  buckets, only ``fg_miss`` (which needs no distance and comes straight
  from the kicking category's own make/attempt split).
"""

import logging
import re
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

SCOREBOARD_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
)
SUMMARY_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
)

#: category name -> {ESPN stat label: our internal key}. Verified against the
#: recorded fixture; ESPN can rename a label, which is why a miss is skipped
#: rather than treated as zero. Individual-defender categories
#: ("defensive", "interceptions") are deliberately absent here -- see the
#: module docstring. "kicking" is handled separately (`_kicking_stats`)
#: because both of its scored labels are "made/attempted" strings, and
#: field-goal distance needs data outside this category entirely.
_CATEGORY_MAP: Dict[str, Dict[str, str]] = {
    "passing": {
        "YDS": "pass_yd", "TD": "pass_td", "INT": "pass_int",
    },
    "rushing": {
        "CAR": "rush_att", "YDS": "rush_yd", "TD": "rush_td",
    },
    "receiving": {
        "REC": "rec", "YDS": "rec_yd", "TD": "rec_td", "TGTS": "targets",
    },
    "fumbles": {
        "LOST": "fumbles_lost",
    },
}

#: "C/ATT" style labels that carry two numbers in one string.
_SPLIT_LABELS = {
    ("passing", "C/ATT"): ("pass_cmp", "pass_att"),
}

#: Team-level category totals -> our internal key. Read from each category's
#: own ``totals`` row (ESPN's own per-team sum), not summed from individual
#: athletes, so a category with zero occurrences (``totals: []``) is simply
#: absent rather than needing special-cased zero-filling.
_TEAM_DEFENSE_CATEGORY_MAP: Dict[Any, str] = {
    ("defensive", "SACKS"): "def_sack",
    ("defensive", "TD"): "def_td",
    ("interceptions", "INT"): "def_int",
    ("interceptions", "TD"): "def_td",
}

#: Matches scoring-play text like "Harrison Butker 59 Yd Field Goal".
_FG_PLAY_RE = re.compile(
    r"^(?P<name>.+?)\s+(?P<yards>\d+)\s+Yd Field Goal$", re.IGNORECASE,
)


def _to_number(value: Any) -> Optional[float]:
    """ESPN returns display strings. Anything unparseable is skipped."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    cleaned = value.replace(",", "").strip()
    if not cleaned or cleaned == "--":
        return None
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _stats_from_athlete(
    category: str, labels: List[str], values: List[Any],
) -> Dict[str, float]:
    mapping = _CATEGORY_MAP.get(category, {})
    stats: Dict[str, float] = {}

    for label, raw in zip(labels, values):
        split_key = (category, label)
        if split_key in _SPLIT_LABELS and isinstance(raw, str) and "/" in raw:
            left_key, right_key = _SPLIT_LABELS[split_key]
            left, _, right = raw.partition("/")
            for key, part in ((left_key, left), (right_key, right)):
                number = _to_number(part)
                if number is not None:
                    stats[key] = stats.get(key, 0) + number
            continue

        internal = mapping.get(label)
        if internal is None:
            continue
        number = _to_number(raw)
        if number is not None:
            stats[internal] = stats.get(internal, 0) + number

    return stats


def _fg_distance_buckets(summary: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Kicker display name -> {fg_0_39/fg_40_49/fg_50_plus: count}.

    The ``kicking`` category has no per-kick distance, only a combined
    make/attempt string, so distance comes from parsing made-field-goal
    scoring-play text instead.
    """
    buckets: Dict[str, Dict[str, int]] = {}
    plays = summary.get("scoringPlays")
    if not isinstance(plays, list):
        return buckets

    for play in plays:
        if not isinstance(play, dict):
            continue
        scoring_type = (play.get("scoringType") or {}).get("name")
        if scoring_type != "field-goal":
            continue
        text = play.get("text")
        if not isinstance(text, str):
            continue
        match = _FG_PLAY_RE.match(text.strip())
        if not match:
            continue
        yards = int(match.group("yards"))
        name = match.group("name").strip()
        if yards >= 50:
            key = "fg_50_plus"
        elif yards >= 40:
            key = "fg_40_49"
        else:
            key = "fg_0_39"
        entry = buckets.setdefault(name, {})
        entry[key] = entry.get(key, 0) + 1

    return buckets


def _kicking_stats(
    labels: List[str], values: List[Any], fg_buckets: Dict[str, int],
) -> Dict[str, float]:
    """XP/FG are "made/attempted" strings; distance comes from fg_buckets."""
    stats: Dict[str, float] = {}
    pairs = dict(zip(labels, values))

    xp_raw = pairs.get("XP")
    if isinstance(xp_raw, str) and "/" in xp_raw:
        made, _, _att = xp_raw.partition("/")
        made_number = _to_number(made)
        if made_number is not None:
            stats["xp"] = made_number

    fg_raw = pairs.get("FG")
    if isinstance(fg_raw, str) and "/" in fg_raw:
        made, _, att = fg_raw.partition("/")
        made_number = _to_number(made)
        att_number = _to_number(att)
        if made_number is not None and att_number is not None:
            miss = att_number - made_number
            if miss > 0:
                stats["fg_miss"] = miss

    for key, count in fg_buckets.items():
        stats[key] = stats.get(key, 0) + count

    return stats


def parse_player_stats(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per player appearing in the box score."""
    if not isinstance(summary, dict):
        return []
    try:
        groups = summary.get("boxscore", {}).get("players", [])
    except AttributeError:
        return []
    if not isinstance(groups, list):
        return []

    fg_buckets = _fg_distance_buckets(summary)
    by_player: Dict[str, Dict[str, Any]] = {}

    for group in groups:
        if not isinstance(group, dict):
            continue
        team = (group.get("team") or {}).get("abbreviation")
        for block in group.get("statistics") or []:
            if not isinstance(block, dict):
                continue
            category = (block.get("name") or "").lower()
            labels = block.get("labels") or []
            for athlete_row in block.get("athletes") or []:
                if not isinstance(athlete_row, dict):
                    continue
                athlete = athlete_row.get("athlete") or {}
                espn_id = str(athlete.get("id") or "")
                if not espn_id:
                    continue
                values = athlete_row.get("stats") or []
                name = athlete.get("displayName") or ""

                if category == "kicking":
                    stats = _kicking_stats(
                        labels, values, fg_buckets.get(name, {}),
                    )
                else:
                    stats = _stats_from_athlete(category, labels, values)
                if not stats:
                    continue

                entry = by_player.setdefault(espn_id, {
                    "espn_id": espn_id,
                    "name": name,
                    "team": team,
                    "stats": {},
                })
                for key, value in stats.items():
                    entry["stats"][key] = entry["stats"].get(key, 0) + value

    return list(by_player.values())


def _team_category_totals(
    boxscore_players: List[Dict[str, Any]],
) -> Dict[str, Dict[str, float]]:
    """team abbreviation -> aggregated def_* stats plus fumbles-lost.

    Read from each category's own ``totals`` row (ESPN's own per-team sum)
    rather than summed from individual athletes, so a category with zero
    occurrences (``totals: []``) is simply absent.
    """
    result: Dict[str, Dict[str, float]] = {}
    for group in boxscore_players or []:
        if not isinstance(group, dict):
            continue
        team = (group.get("team") or {}).get("abbreviation")
        if not team:
            continue
        stats = result.setdefault(team, {})
        for block in group.get("statistics") or []:
            if not isinstance(block, dict):
                continue
            category = (block.get("name") or "").lower()
            labels = block.get("labels") or []
            totals = block.get("totals") or []
            pairs = dict(zip(labels, totals))

            if category == "fumbles":
                lost = _to_number(pairs.get("LOST"))
                if lost is not None:
                    stats["_fumbles_lost"] = lost
                continue

            for label, raw in pairs.items():
                internal = _TEAM_DEFENSE_CATEGORY_MAP.get((category, label))
                if internal is None:
                    continue
                number = _to_number(raw)
                if number is not None:
                    stats[internal] = stats.get(internal, 0) + number

    return result


def parse_team_defense_stats(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One row per team defense, carrying points allowed.

    Points allowed is the opponent's final score, which comes from the
    competition header rather than the defensive stat block. Sacks,
    interceptions, defensive TDs and fumbles recovered come from the
    boxscore's per-category team totals -- see module docstring for why
    fumbles recovered is read off the *opponent's* "lost" total instead of
    the team's own "recovered" total.
    """
    if not isinstance(summary, dict):
        return []
    try:
        competitions = (summary.get("header") or {}).get("competitions") or []
        competitors = competitions[0].get("competitors") or []
    except (AttributeError, IndexError, KeyError, TypeError):
        return []

    scores: Dict[str, int] = {}
    for competitor in competitors:
        if not isinstance(competitor, dict):
            continue
        abbreviation = (competitor.get("team") or {}).get("abbreviation")
        score = _to_number(competitor.get("score"))
        if abbreviation and score is not None:
            scores[abbreviation] = int(score)

    if len(scores) != 2:
        return []

    try:
        boxscore_players = summary.get("boxscore", {}).get("players", [])
        if not isinstance(boxscore_players, list):
            boxscore_players = []
    except AttributeError:
        boxscore_players = []

    totals = _team_category_totals(boxscore_players)

    teams = list(scores)
    rows = []
    for team, opponent in ((teams[0], teams[1]), (teams[1], teams[0])):
        stats: Dict[str, float] = {"pts_allowed": scores[opponent]}

        team_totals = totals.get(team, {})
        for key in ("def_sack", "def_int", "def_td"):
            if key in team_totals:
                stats[key] = team_totals[key]

        opponent_lost = totals.get(opponent, {}).get("_fumbles_lost")
        if opponent_lost is not None:
            stats["def_fumble_rec"] = opponent_lost

        rows.append({"team": team, "stats": stats})

    return rows


def fetch_week_events(
    year: int, week: int, timeout: float = 10.0,
) -> List[Dict[str, Any]]:
    """Games in *week*, with their live status. Empty on any failure."""
    try:
        response = requests.get(
            SCOREBOARD_URL,
            params={"dates": year, "seasontype": 2, "week": week},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        logger.warning("ESPN scoreboard fetch failed for %s week %s", year, week)
        return []

    events = []
    for event in payload.get("events") or []:
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors") or []
        home = next(
            (c["team"]["abbreviation"] for c in competitors
             if c.get("homeAway") == "home" and c.get("team")), None,
        )
        away = next(
            (c["team"]["abbreviation"] for c in competitors
             if c.get("homeAway") == "away" and c.get("team")), None,
        )
        events.append({
            "event_id": str(event.get("id")),
            # pre | in | post
            "status": ((event.get("status") or {}).get("type") or {}).get(
                "state",
            ),
            "home_team": home,
            "away_team": away,
        })
    return events


def fetch_event_summary(
    event_id: str, timeout: float = 10.0,
) -> Optional[Dict[str, Any]]:
    """Full box score for one game, or None on any failure."""
    try:
        response = requests.get(
            SUMMARY_URL, params={"event": event_id}, timeout=timeout,
        )
        response.raise_for_status()
        return response.json()
    except Exception:
        logger.warning("ESPN summary fetch failed for event %s", event_id)
        return None


class BoxScoreClient:
    """Injectable seam so live_scoring is testable without a network."""

    def week_events(self, year: int, week: int) -> List[Dict[str, Any]]:
        return fetch_week_events(year, week)

    def event_summary(self, event_id: str) -> Optional[Dict[str, Any]]:
        return fetch_event_summary(event_id)
