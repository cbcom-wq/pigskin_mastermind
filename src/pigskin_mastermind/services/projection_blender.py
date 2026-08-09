"""Weighted consensus across projection sources.

Kept free of database and network access on purpose: the arithmetic that
decides every ranking in the app should be readable in one screen and
testable without fixtures.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional

#: Season scope. No season-long prop market exists, so the house model leads
#: and ESPN plus the ADP-implied curve act as independent second opinions.
SEASON_WEIGHTS: Dict[str, float] = {
    "model": 0.50,
    "espn": 0.30,
    "adp": 0.20,
}

#: Weekly scope. A betting market is the sharpest short-term signal there is,
#: so props lead when they exist. They usually do not yet -- the odds table
#: currently holds one stale slate -- and renormalization is what makes that
#: degrade to model-only cleanly instead of as a special case.
WEEKLY_WEIGHTS: Dict[str, float] = {
    "sportsbook": 0.45,
    "model": 0.35,
    "espn": 0.20,
}


@dataclass
class BlendResult:
    """A consensus value plus the arithmetic that produced it.

    ``sources`` and ``weights_used`` are kept so the UI can show why two
    sources disagree rather than presenting one unexplained number.
    """

    points: float
    weights_used: Dict[str, float] = field(default_factory=dict)
    sources: Dict[str, float] = field(default_factory=dict)


def blend(
    sources: Dict[str, Optional[float]],
    weights: Dict[str, float],
) -> Optional[BlendResult]:
    """Combine *sources* using *weights*, renormalized over what is present.

    Renormalizing is the whole point. Multiplying a present source by its
    nominal weight and summing would treat a missing source as a zero-point
    vote, so a player two sources both call 300 would blend to 200 purely
    because a third source was silent.

    A source present with value ``0.0`` is a real vote -- that is how a bye
    week or an OUT designation reaches the blend -- so only ``None`` and
    unweighted keys are treated as absent.

    Returns ``None`` when no weighted source has a value.
    """
    present = {
        name: float(value)
        for name, value in sources.items()
        if value is not None and name in weights
    }
    if not present:
        return None

    total_weight = sum(weights[name] for name in present)
    if total_weight <= 0:
        return None

    normalized = {name: weights[name] / total_weight for name in present}
    points = sum(normalized[name] * value for name, value in present.items())

    return BlendResult(
        points=points,
        weights_used=normalized,
        sources=present,
    )
