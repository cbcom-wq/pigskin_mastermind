"""One definition of where a page's "Back" control goes, and what it is called.

The app had four competing conventions for this — a breadcrumb, a hardcoded
"Back to <somewhere>", a ``?back=`` param honoured by three routes, and
nothing at all — so the same word meant "return to where you were" on one
page and "go to this unrelated view" on the next.

Two rules are enforced here rather than in each template:

**A back target must be a path on this site.** It is rendered straight into
an ``href``, so anything that can leave the origin is refused outright and
the page falls back to its own default. Refusing beats sanitising: a
half-cleaned URL that still resolves somewhere is worse than an honest
default.

**The label is derived from the path, never passed alongside it.** A
``&back_label=`` param would be attacker-controlled text sitting next to a
back arrow — spoofable, and twice the plumbing at every link site. Deriving
it means one parameter to thread through and a label that cannot disagree
with the destination.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Pattern, Tuple

__all__ = ["BackTarget", "resolve_back", "label_for_path"]


@dataclass(frozen=True)
class BackTarget:
    """Where a "Back" control points, and the human name of that place."""

    url: str
    label: str


#: Path shape -> human name, tried in order, so a member route must be listed
#: before the collection it lives under. Getting that order wrong is how you
#: end up with "Back to Teams" on a link that goes to one specific team.
_LABELS: List[Tuple[Pattern[str], str]] = [
    (re.compile(r"^/$"), "Dashboard"),
    (re.compile(r"^/players/[^/]+/simulation/?$"), "Player Simulation"),
    (re.compile(r"^/players/[^/]+/?$"), "Player"),
    (re.compile(r"^/players/?$"), "Players"),
    (re.compile(r"^/teams/[^/]+/simulation/?$"), "Team Simulation"),
    (re.compile(r"^/teams/[^/]+/?$"), "Team"),
    (re.compile(r"^/teams/?$"), "Teams"),
    (re.compile(r"^/season/[^/]+/teams/[^/]+/?$"), "Team"),
    (re.compile(r"^/season/[^/]+/scoreboard/[^/]+/?$"), "Scoreboard"),
    (re.compile(r"^/season/[^/]+/?$"), "League"),
    (re.compile(r"^/leagues/[^/]+/?$"), "League"),
    (re.compile(r"^/leagues/?$"), "Leagues"),
    (re.compile(r"^/games/[^/]+/?$"), "Game"),
    (re.compile(r"^/games/?$"), "Scores"),
    (re.compile(r"^/draft/board/[^/]+/?$"), "Draft Board"),
    (re.compile(r"^/draft/recap/[^/]+/?$"), "Draft Recap"),
    (re.compile(r"^/draft/simulate/?$"), "Draft Simulation"),
    (re.compile(r"^/draft(/.*)?$"), "Draft"),
    (re.compile(r"^/metrics/hot/?$"), "Hot Metrics"),
    (re.compile(r"^/trades(/.*)?$"), "Trade Analyzer"),
    (re.compile(r"^/settings(/.*)?$"), "Settings"),
    (re.compile(r"^/projection-tuner/runs/[^/]+/?$"), "Tuner Run"),
    (re.compile(r"^/projection-tuner(/.*)?$"), "Projection Tuner"),
    (re.compile(r"^/visualizations/season-animation(/.*)?$"), "Season Animation"),
]

#: Used when nothing matches. Vague, but it cannot be wrong — which a
#: confident guess at an unregistered route could be.
_UNKNOWN_LABEL = "Previous Page"


def _is_same_site_path(candidate: str) -> bool:
    """True only for a path that cannot resolve off this origin.

    ``//host`` is protocol-relative and ``/\\host`` is treated the same way by
    browsers, so both are rejected despite starting with a slash. Anything
    not starting with a slash is either a scheme (``javascript:``) or
    relative to whatever page happens to be rendering it.
    """
    if not candidate.startswith("/"):
        return False
    return candidate[1:2] not in ("/", "\\")


def label_for_path(url: str) -> str:
    """Human name for a path, ignoring any query string or fragment."""
    path = re.split(r"[?#]", url, maxsplit=1)[0]
    for pattern, label in _LABELS:
        if pattern.match(path):
            return label
    return _UNKNOWN_LABEL


def resolve_back(
    back: Optional[str], default_url: str, default_label: str
) -> BackTarget:
    """Resolve a ``?back=`` param into the target its page should render.

    ``default_url`` / ``default_label`` describe where the page sends you
    when it has no idea where you came from — a direct load, a bookmark, or
    a refused target.
    """
    candidate = (back or "").strip()
    if not candidate or not _is_same_site_path(candidate):
        return BackTarget(default_url, default_label)
    return BackTarget(candidate, label_for_path(candidate))
