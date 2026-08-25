"""Turn a stat line into fantasy points. One place, no side effects.

``Player.calculate_points`` assigns ``self.actual_points`` while returning the
value, so it cannot score an arbitrary stat line without mutating a player it
was only supposed to read. Season scoring needs a pure function — it scores
hundreds of lineup rows per poll, none of which own a dataclass instance.

Points allowed is the one stat that is not a multiplier. A shutout is worth 10
points and 40 allowed is worth -4, which no ``value * coefficient`` can express,
so it gets its own step function.
"""

from typing import Any, Dict, Optional, Sequence, Tuple

#: ``(upper_bound_inclusive, points)``, ascending. Anything above the last
#: bound scores ``floor``. These are the standard ESPN tiers.
DEFAULT_PTS_ALLOWED_TIERS: Tuple[Tuple[int, float], ...] = (
    (0, 10.0),
    (6, 7.0),
    (13, 4.0),
    (20, 1.0),
    (27, 0.0),
    (34, -1.0),
)

DEFAULT_PTS_ALLOWED_FLOOR = -4.0

#: Handled by :func:`points_allowed_score`, not by multiplication.
_TIERED_STATS = ("pts_allowed",)


def points_allowed_score(
    points_allowed: int,
    tiers: Optional[Sequence[Tuple[int, float]]] = None,
    floor: float = DEFAULT_PTS_ALLOWED_FLOOR,
) -> float:
    """Points for a defense that allowed *points_allowed* points."""
    for upper, value in (tiers or DEFAULT_PTS_ALLOWED_TIERS):
        if points_allowed <= upper:
            return value
    return floor


def score_stat_line(stats: Dict[str, float], settings: Dict[str, Any]) -> float:
    """Fantasy points for *stats* under *settings*.

    Unknown stat keys are ignored rather than raising: ``stats`` blobs carry
    plenty of non-scoring fields (snap counts, targets, attempts) and a scorer
    that rejected them would be unusable.
    """
    total = 0.0
    for stat, value in stats.items():
        if stat in _TIERED_STATS:
            continue
        multiplier = settings.get(stat)
        if multiplier is None:
            continue
        total += value * multiplier

    if "pts_allowed" in stats:
        total += points_allowed_score(
            stats["pts_allowed"],
            tiers=settings.get("pts_allowed_tiers"),
            floor=settings.get("pts_allowed_floor", DEFAULT_PTS_ALLOWED_FLOOR),
        )

    return total
