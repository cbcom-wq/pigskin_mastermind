"""Play-by-play derived from ESPN's public summary payload.

Chosen over the nflverse path because it is live (each play carries a
``wallclock``), costs one JSON GET rather than a season of parquet, needs
nothing but ``requests``, and actually has current-season data.

Known limitation: rebuilt stat lines will not reconcile to a boxscore on
compound penalty plays.  ``statYardage`` is the *net* field result, which is
the right number for moving a marker down a field and the wrong number for
crediting a rush.  See ``test_a_penalty_play_keeps_espns_net_yardage``.
"""

import logging
from typing import Any, Dict, List, Optional

from pigskin_mastermind.services.play_by_play.attribution import (
    RosterIndex,
    resolve_actors,
)
from pigskin_mastermind.services.play_by_play.base import Play


def _plays_in(summary: Dict[str, Any]) -> List[Dict[str, Any]]:
    drives = (summary.get("drives") or {}).get("previous") or []
    return [p for d in drives for p in (d.get("plays") or [])]


def _int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _classify(type_text: str, description: str) -> Dict[str, Any]:
    """Role and outcome flags for one play.

    ESPN encodes the outcome in ``type.text``, and the vocabulary does not
    factor the way you would expect: a scoring pass is "Passing Touchdown",
    which contains neither "reception" nor "pass complete".  Testing only for
    those drops every scoring pass.  A sack is its own type and belongs to the
    passing game -- filing it as a rush gives quarterbacks negative rushing
    yards.
    """
    t = type_text.lower()
    low = description.lower()

    sack = "sack" in t
    interception = "interception" in t
    passing_td = "passing touchdown" in t
    rushing_td = "rushing touchdown" in t

    if sack or interception or passing_td or "pass" in t:
        role = "pass"
    elif rushing_td or "rush" in t:
        role = "rush"
    else:
        role = "other"

    return {
        "role": role,
        "sack": sack,
        "interception": interception,
        "complete_pass": bool("reception" in t or passing_td or "pass complete" in low),
    }


def _derive(raw: Dict[str, Any], index: RosterIndex) -> Play:
    start = raw.get("start") or {}
    description = raw.get("text") or ""
    type_text = (raw.get("type") or {}).get("text") or ""
    flags = _classify(type_text, description)

    yards = raw.get("statYardage")
    scoring = bool(raw.get("scoringPlay"))
    actor_ids, actor_names = resolve_actors(description, flags["role"], index)

    return Play(
        play_id=str(raw.get("id") or ""),
        description=description,
        play_type=type_text or None,
        role=flags["role"],
        yardline_100=_int(start.get("yardsToEndzone")),
        yards_gained=float(yards) if yards is not None else 0.0,
        quarter=_int((raw.get("period") or {}).get("number")),
        clock=(raw.get("clock") or {}).get("displayValue"),
        down=_int(start.get("down")),
        distance=_int(start.get("distance")),
        home_score=_int(raw.get("homeScore")),
        away_score=_int(raw.get("awayScore")),
        touchdown=scoring and "touchdown" in description.lower(),
        interception=flags["interception"],
        sack=flags["sack"],
        complete_pass=flags["complete_pass"],
        actor_ids=actor_ids,
        actor_names=actor_names,
    )


def plays_from_summary(summary: Dict[str, Any]) -> List[Play]:
    """Normalized plays from one ESPN summary payload."""
    index = RosterIndex.from_summary(summary)
    return [_derive(raw, index) for raw in _plays_in(summary)]


def _teams_in(event: Dict[str, Any]) -> List[str]:
    """Both teams in one ``fetch_week_events`` row.

    That helper already flattens ESPN's scoreboard to
    ``{event_id, status, home_team, away_team}`` -- it is not raw ESPN JSON.
    """
    return [t for t in (event.get("home_team"), event.get("away_team")) if t]


class ESPNPlayByPlaySource:
    """Play-by-play for one team's game in one week.

    Takes a client rather than calling ``requests`` directly so the tests run
    offline -- ``espn_boxscore.BoxScoreClient`` already exists as exactly this
    seam for ``live_scoring``, and is the default.
    """

    name = "espn"

    def __init__(self, client: Optional[Any] = None) -> None:
        if client is None:
            # The shared cache, not a bare BoxScoreClient.  Every player in a
            # team simulation asks for the same week list, and players who
            # were in the same game ask for the same summary.
            from pigskin_mastermind.services.play_by_play.cache import shared_client

            client = shared_client()
        self.client = client

    def _find_event(self, year: int, week: int, team: str) -> Optional[str]:
        # normalize_team bridges ESPN's WSH against the WAS everyone else uses.
        from pigskin_mastermind.utils.nfl_teams import normalize_team

        target = normalize_team(team) or team
        for event in self.client.week_events(year, week) or []:
            names = [normalize_team(t) or t for t in _teams_in(event)]
            if target in names:
                return str(event.get("event_id") or "")
        return None

    def game_context(self, year: int, week: int, team: str) -> Dict[str, Any]:
        """Identity of *team*'s game that week, for the payload's summary."""
        from pigskin_mastermind.utils.nfl_teams import normalize_team

        target = normalize_team(team) or team
        for event in self.client.week_events(year, week) or []:
            names = [normalize_team(t) or t for t in _teams_in(event)]
            if target in names:
                return {
                    "game_id": str(event.get("event_id") or ""),
                    "home_team": event.get("home_team"),
                    "away_team": event.get("away_team"),
                }
        return {}

    def plays(self, year: int, week: int, team: str) -> List[Play]:
        """Plays from *team*'s game that week.

        Empty is a real answer -- a bye, a week that has not happened, or a
        game whose detail ESPN has not published.  None of those is an error,
        and none should raise into a page render.
        """
        event_id = self._find_event(year, week, team)
        if not event_id:
            return []

        summary = self.client.event_summary(event_id)
        if not summary:
            logging.getLogger(__name__).info(
                "ESPN had no summary for event %s (%s week %s)", event_id, year, week
            )
            return []

        return plays_from_summary(summary)
