"""The contract every weekly projection source implements.

Kept free of database and network access: a provider is handed a session and a
player list and returns numbers. That is what lets the orchestrator treat six
very different sources — a local model, an ESPN column, a betting market, an
nflverse frame, an agent's rows, a scraped page — as interchangeable, and what
lets each one fail on its own without taking the others down.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Source keys
# ---------------------------------------------------------------------------
#
# These are the ``player_projections.source`` values. They live here rather
# than in projection_refresh so a provider can import them without pulling in
# the season refresh service (which imports the criteria builder, which is
# expensive and reaches for nfl_data_py).

SOURCE_MODEL = "model"
SOURCE_ESPN = "espn"
SOURCE_SPORTSBOOK = "sportsbook"
SOURCE_NFLVERSE_XP = "nflverse_xp"
SOURCE_LLM = "llm"
SOURCE_CONSENSUS = "consensus"

#: The multi-source consensus. Deliberately NOT ``blend``.
#:
#: ``projection_refresh._READ_PRIORITY`` is ``(blend, model)`` and backs
#: ``weekly_projection_map()``, which ``lineup_manager.plan_lineup()`` calls for
#: every lineup decision in the app — the AI managers, the first-kickoff
#: auto-fill, and the web auto-set button. No weekly ``blend`` row exists today,
#: so that read always falls through to ``model``. Writing this consensus under
#: the name ``blend`` would silently switch all of them onto it as a side effect
#: of shipping a display feature. ``blend_multi`` is invisible to that read
#: path; adopting it for lineups later is a deliberate edit with its own tests.
SOURCE_BLEND_MULTI = "blend_multi"


@dataclass
class ProjectionValue:
    """One source's projection for one player in one week.

    ``components`` is the per-source explanation the table shows on hover —
    the prop lines behind a sportsbook number, the criteria behind a model
    number. It is what keeps the view from being six unexplained numbers.
    """

    points: float
    floor: Optional[float] = None
    ceiling: Optional[float] = None
    components: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class WeeklyProjectionProvider(Protocol):
    """A single weekly projection source."""

    #: ``player_projections.source`` value this provider owns.
    key: str

    #: Human label for the column header.
    label: str

    #: False for providers that only read rows someone else wrote, so the
    #: orchestrator does not upsert their output back over the original.
    writes: bool

    def project_week(
        self,
        db: Session,
        year: int,
        week: int,
        player_ids: List[int],
    ) -> Dict[int, ProjectionValue]:
        """Project *player_ids* for *week*.

        A provider is free to cover a subset of ``player_ids``, or none of
        them: sportsbook props exist for a few hundred players, and ``llm``
        rows exist only for players an agent has actually been run on.
        Returning ``{}`` means "no coverage", which is an ordinary outcome and
        not an error. Raising means the source itself is broken.
        """
        ...
