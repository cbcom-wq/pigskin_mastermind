"""The normalized play shape every play-by-play source produces.

The simulation services used to read nflverse column names straight out of a
dict (``yardline_100``, ``first_down_pass``, ``total_home_score``).  That
coupling is what made the data source unswappable -- and the nflverse source
needs ``pyarrow``, which has no Android wheels, so it can never be the only
option.

``Play`` is named for the domain rather than for one provider's column list.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Play:
    """One play, in the shape the field animation consumes."""

    play_id: str
    description: str
    play_type: Optional[str] = None

    # "pass", "rush", or "other" -- what the actor did, not what was called.
    role: str = "other"

    # Distance to the opponent's end zone, 0-100.
    yardline_100: Optional[float] = None
    yards_gained: float = 0.0

    quarter: Optional[int] = None
    clock: Optional[str] = None
    down: Optional[int] = None
    distance: Optional[int] = None

    home_score: Optional[int] = None
    away_score: Optional[int] = None

    touchdown: bool = False
    first_down: bool = False
    interception: bool = False
    sack: bool = False
    complete_pass: bool = False

    # Expected points added.  ESPN does not publish it, and it stays None
    # rather than 0.0 -- zero is a real EPA value, so defaulting to it would
    # state something false in the same shape as the truth.
    epa: Optional[float] = None

    # Resolved actors, in role order: passer first, then receiver; or the
    # rusher alone.  Empty when the text could not be attributed -- an
    # unattributed play is drawn without a name, never with a guessed one.
    actor_ids: List[str] = field(default_factory=list)
    actor_names: List[str] = field(default_factory=list)
