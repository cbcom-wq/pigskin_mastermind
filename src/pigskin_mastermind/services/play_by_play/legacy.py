"""Adapting a normalized ``Play`` to the dict the animation already reads.

``player_game_simulation_service`` builds route paths, running stat lines and
badges from nflverse's column names.  That math is correct and well exercised;
only its *source* was the problem.  This module converts a ``Play`` into the
shape those helpers already consume, so swapping the source changes where the
data comes from without touching how it is drawn.

Fields nflverse publishes and ESPN does not (``epa``, ``air_yards``,
``yards_after_catch``) come through as ``None``/0.0 rather than invented
values.
"""

from typing import Any, Dict, List, Optional

from pigskin_mastermind.services.play_by_play.base import Play


def _quarter_seconds(clock: Optional[str]) -> Optional[int]:
    """ESPN's "12:56" as seconds remaining in the quarter."""
    if not clock or ":" not in clock:
        return None
    minutes, _, seconds = clock.partition(":")
    try:
        return int(minutes) * 60 + int(seconds)
    except ValueError:
        return None


def player_role(play: Play, player_espn_id: str) -> Optional[str]:
    """What this player did on this play, or None if they were not involved.

    Actors are stored in role order -- passer then receiver for a pass, the
    carrier alone for a rush -- so position carries the meaning.
    """
    if not player_espn_id or player_espn_id not in play.actor_ids:
        return None
    position = play.actor_ids.index(player_espn_id)
    if play.role == "rush":
        return "rush"
    if play.role == "pass":
        return "pass" if position == 0 else "receive"
    return None


def to_legacy_play(play: Play, player_espn_id: str) -> Optional[Dict[str, Any]]:
    """One ``Play`` in the dict shape the animation helpers read.

    Returns None when the player took no part in the play, which is what
    filters a whole game's feed down to one player's game.
    """
    role = player_role(play, player_espn_id)
    if role is None:
        return None

    gained = play.yards_gained
    distance = play.distance
    # nflverse publishes first-down flags; ESPN does not, so derive them.
    # A conversion is yardage at least equal to the distance to gain.
    converted = bool(distance is not None and gained >= distance)

    names = play.actor_names
    passer_name = names[0] if play.role == "pass" and names else None
    receiver_name = names[1] if play.role == "pass" and len(names) > 1 else None

    return {
        "play_id": play.play_id,
        "desc": play.description,
        "play_type": play.play_type,
        "player_role": role,
        "yardline_100": play.yardline_100,
        "yards_gained": gained,
        "qtr": play.quarter,
        "quarter_seconds_remaining": _quarter_seconds(play.clock),
        "down": play.down,
        "ydstogo": distance,
        "total_home_score": play.home_score,
        "total_away_score": play.away_score,
        "touchdown": int(play.touchdown),
        "interception": int(play.interception),
        "sack": int(play.sack),
        "complete_pass": int(play.complete_pass),
        "first_down_pass": int(converted and play.role == "pass"),
        "first_down_rush": int(converted and play.role == "rush"),
        "qb_scramble": int("scramble" in (play.description or "").lower()),
        "passer_player_name": passer_name,
        "receiver_player_name": receiver_name,
        # The front end reads neither of these.  They keep their place in the
        # payload for compatibility, but an ESPN athlete id is not a GSIS id
        # and must not be passed off as one.
        "passer_player_id": None,
        "receiver_player_id": None,
        # Not published by ESPN.  Left absent rather than zeroed -- 0.0 is a
        # real EPA value and 0 air yards is a real throw.
        "epa": None,
        "air_yards": 0.0,
        "yards_after_catch": 0.0,
    }


def legacy_plays_for_player(
    plays: List[Play], player_espn_id: str
) -> List[Dict[str, Any]]:
    """A whole game's plays, filtered and adapted to one player."""
    out = []
    for play in plays:
        adapted = to_legacy_play(play, player_espn_id)
        if adapted is not None:
            out.append(adapted)
    return out
