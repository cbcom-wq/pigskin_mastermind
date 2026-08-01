"""Canonical fantasy position handling.

Every data source spells positions differently: ESPN returns ``D/ST``,
nfl_data_py returns ``DST`` (and a long tail of real defensive positions like
``DT`` that have no fantasy meaning), FantasyFootballCalculator returns
``DEF``.  Storing them verbatim leaks into ``badge-{{ position|lower }}`` CSS
classes that match nothing and breaks the ``QB/RB/WR/TE/K/DEF`` domain rule.

Normalize at every import boundary; everything downstream can then assume
:data:`FANTASY_POSITIONS`.
"""

from typing import Optional

#: The only positions the app models. Matches ``models.player.Player`` validation.
FANTASY_POSITIONS = ('QB', 'RB', 'WR', 'TE', 'K', 'DEF')

#: Positions eligible for a FLEX slot.
FLEX_POSITIONS = ('RB', 'WR', 'TE')

_ALIASES = {
    'D/ST': 'DEF',
    'DST': 'DEF',
    'D': 'DEF',
    'DEFENSE': 'DEF',
    'TEAM DEFENSE': 'DEF',
    'PK': 'K',
    'KICKER': 'K',
    'FB': 'RB',
    'HB': 'RB',
    'WR/RB': 'WR',
    'QUARTERBACK': 'QB',
}


def normalize_position(position: Optional[str]) -> Optional[str]:
    """Return the canonical fantasy position, or ``None`` if there isn't one.

    ``None`` means "not a fantasy position" — an offensive lineman, a linebacker,
    a missing value, or nfl_data_py's ``Unknown`` placeholder. Callers should
    skip those rows rather than storing them.

    >>> normalize_position('D/ST')
    'DEF'
    >>> normalize_position('DT') is None
    True
    """
    if not position:
        return None

    cleaned = str(position).strip().upper()
    if not cleaned:
        return None

    cleaned = _ALIASES.get(cleaned, cleaned)
    return cleaned if cleaned in FANTASY_POSITIONS else None


def is_flex_eligible(position: Optional[str]) -> bool:
    """True when *position* may fill a FLEX slot (RB/WR/TE only)."""
    return normalize_position(position) in FLEX_POSITIONS
