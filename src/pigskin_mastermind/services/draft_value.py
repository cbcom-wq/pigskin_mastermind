"""The single source of truth for judging a draft pick as a steal or a reach.

Both the live draft grade (:mod:`services.mock_draft`) and the post-draft recap
(:mod:`services.draft_recap`) rank picks, and they used to do it independently —
so the same draft could be told two different stories about the same pick.  The
scale lives here so they cannot disagree, in the same spirit as
``ADPService.get_adp_metadata()`` owning the staleness verdict.

**Judge in board-rank space, not ADP space.**  The obvious formula,
``pick_number - adp``, subtracts two quantities that only share a scale by
coincidence:

* ``pick_number`` runs to ``num_teams * num_rounds`` — it grows with league size.
* Stored ADP is a single league-size-agnostic curve (Fantasy Football
  Calculator returns byte-identical data for ``teams=8`` through ``teams=14``),
  and it is an *average over drafts*, so it compresses badly at the tail.  On
  the real board, consensus rank 100 sits at ADP 97 but rank 250 sits at ADP
  189 — a 61-pick gap that is pure arithmetic, not value.

Together those made late picks in deep leagues look like enormous steals and
forced end-of-draft K/DEF picks in shallow leagues look like big reaches.
Identical drafting graded B+ in an 8-team league and A+ in a 14-team one.

Board rank has neither problem: at overall pick *P* exactly *P-1* players are
off the board in any league of any size, so a drafter taking the best player
available scores a delta of 0 everywhere.  Thresholds are then expressed in
*fractions of a round* so that "he fell a full round" means the same thing at
8 teams as at 16.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Key under which :func:`rank_pool` stamps a player's position on the board.
#: Deliberately distinct from ``adp_rank``, which holds the raw ADP *value* and
#: is still what the UI displays — users expect to see "ADP 24.5".
POOL_RANK_KEY = "adp_overall_rank"

#: League size the thresholds below were originally tuned at.  Used as the
#: default so hand-built callers and older tests keep their calibration.
BASELINE_TEAMS = 12

#: ADP sources whose values are real consensus draft positions.  Anything else
#: (notably ``espn_tail``, which synthesises ``max_ffc_adp + rank``) is a sort
#: key and cannot support a value verdict.
REAL_ADP_SOURCES = frozenset({"fantasyfootballcalculator", "ffc", "espn"})

#: Verdict thresholds as a fraction of one round.  The values reproduce the
#: original ±10 / ±3 pick cutoffs exactly at :data:`BASELINE_TEAMS`
#: (10/12 and 3/12 of a round) and scale from there.
_VALUE_TIERS: Sequence[Tuple[float, str, str]] = (
    (10 / 12, "steal", "Great Steal"),
    (3 / 12, "value", "Good Value"),
    (-3 / 12, "fair", "Fair"),
    (-10 / 12, "slight_reach", "Slight Reach"),
)
_WORST_TIER = ("reach", "Big Reach")


def verdict(delta: float, num_teams: int = BASELINE_TEAMS) -> Tuple[str, str]:
    """Classify a rank-space ``delta`` into a verdict and a display label.

    Args:
        delta: Picks later than the board expected.  Positive is value.
        num_teams: League size, which sets how many picks a round is worth.

    Returns:
        ``(verdict, label)`` — e.g. ``("steal", "Great Steal")``.
    """
    round_size = max(int(num_teams or BASELINE_TEAMS), 1)
    for rounds, name, label in _VALUE_TIERS:
        if delta >= rounds * round_size:
            return name, label
    return _WORST_TIER


def is_judgeable(
    player: Dict[str, Any],
    adp_source: Optional[str] = None,
    board_depth: Optional[int] = None,
) -> bool:
    """Whether this player's board position can support a value verdict.

    Three ways a pick cannot be judged:

    * No ADP at all — there is no consensus to compare against.
    * ``espn_tail`` ADP.  Those players are ordered by ESPN's projection behind
      a synthetic ``max_ffc_adp + rank`` value.  That orders them, but it is
      not a claim about where the market drafts them.
    * A board position past the end of the draft (``board_depth``).  Formats
      with roster requirements force picks the board cannot price: the 8th-best
      D/ST sits at board rank 157, but an 8-team 15-round draft is only 120
      picks long, so *every* team is forced to take it "early" and no pick
      number in that draft could ever score it fairly.  Undefined, not bad.
    """
    source = adp_source if adp_source is not None else player.get("adp_source")
    if source is not None and source not in REAL_ADP_SOURCES:
        return False
    rank = player.get(POOL_RANK_KEY)
    if rank is None and player.get("adp_rank") is None:
        return False
    if board_depth and rank is not None and rank > board_depth:
        return False
    return True


def board_rank(player: Dict[str, Any]) -> Optional[float]:
    """This player's position on the draft board.

    Falls back to the raw ADP value for pools that never went through
    :func:`rank_pool` — hand-built states in tests, and any caller holding a
    bare list of players.
    """
    rank = player.get(POOL_RANK_KEY)
    if rank is not None:
        return float(rank)
    adp = player.get("adp_rank")
    return float(adp) if adp is not None else None


def pick_delta(
    pick_number: int,
    player: Dict[str, Any],
    adp_source: Optional[str] = None,
    board_depth: Optional[int] = None,
) -> Optional[float]:
    """How many picks past their board position the player went.

    Positive is value, negative is a reach.  ``None`` when the pick cannot be
    judged — see :func:`is_judgeable`.
    """
    if not is_judgeable(player, adp_source, board_depth):
        return None
    rank = board_rank(player)
    return None if rank is None else pick_number - rank


def rank_pool(pool: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sort a draft pool into board order and stamp each player's rank.

    Players are copied rather than mutated so a caller can reuse one pool list
    across several drafts without it accumulating state.

    Players with no ADP sort to the back and are left *unstamped*, so they stay
    unjudgeable — ranking them would invent a consensus that does not exist.
    """
    players = [dict(p) for p in pool]
    if players and any(p.get("adp_rank") is not None for p in players):
        players.sort(
            key=lambda p: p["adp_rank"] if p.get("adp_rank") is not None else 9999
        )
    else:
        players.sort(key=lambda p: p.get("projected_points") or 0.0, reverse=True)

    rank = 0
    for player in players:
        if player.get("adp_rank") is None:
            player.pop(POOL_RANK_KEY, None)
            continue
        rank += 1
        player[POOL_RANK_KEY] = rank
    return players
