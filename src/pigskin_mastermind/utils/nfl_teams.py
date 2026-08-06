"""Canonical NFL team abbreviation handling.

Every data source spells teams differently: ESPN's fantasy API (and the
vendored ``espn_api`` library) returns ``WSH``, while FantasyFootballCalculator,
``nfl_data_py``, and ``mock_draft._ESPN_TEAM_MAP`` all return ``WAS``.  Older
Pro-Football-Reference exports use three-letter forms like ``GNB`` and ``KAN``,
and relocated franchises still show up as ``SD``, ``OAK``, or ``STL``.

Storing them verbatim means the same franchise lives under two spellings, so a
player looks like they changed teams when they did not, and joins against NFL
team stats silently miss.

Normalize at every import boundary; everything downstream can then assume
:data:`NFL_TEAMS`.
"""

from typing import Optional

#: The only team abbreviations the app stores. Matches FFC / nflverse spelling.
NFL_TEAMS = frozenset({
    'ARI', 'ATL', 'BAL', 'BUF', 'CAR', 'CHI', 'CIN', 'CLE',
    'DAL', 'DEN', 'DET', 'GB', 'HOU', 'IND', 'JAX', 'KC',
    'LAC', 'LAR', 'LV', 'MIA', 'MIN', 'NE', 'NO', 'NYG',
    'NYJ', 'PHI', 'PIT', 'SEA', 'SF', 'TB', 'TEN', 'WAS',
})

_ALIASES = {
    # ESPN / vendored espn_api
    'WSH': 'WAS',
    'JAC': 'JAX',
    # Relocations
    'SD': 'LAC',
    'SDG': 'LAC',
    'OAK': 'LV',
    'STL': 'LAR',
    'LA': 'LAR',
    # Pro-Football-Reference three-letter forms
    'ARZ': 'ARI',
    'BLT': 'BAL',
    'CLV': 'CLE',
    'HST': 'HOU',
    'GNB': 'GB',
    'KAN': 'KC',
    'NWE': 'NE',
    'NOR': 'NO',
    'SFO': 'SF',
    'TAM': 'TB',
    'LVR': 'LV',
}


def normalize_team(team: Optional[str]) -> Optional[str]:
    """Return the canonical NFL team abbreviation, or ``None`` if there isn't one.

    ``None`` means "no usable team" — a free agent, a missing value, or an
    abbreviation this module does not recognize. Callers must treat that as
    *leave the stored value alone*, never as a value to write: a source that
    reports ``FA`` for an unsigned player would otherwise wipe a good team.

    >>> normalize_team('WSH')
    'WAS'
    >>> normalize_team('FA') is None
    True
    """
    if not team:
        return None

    cleaned = str(team).strip().upper()
    if not cleaned:
        return None

    cleaned = _ALIASES.get(cleaned, cleaned)
    return cleaned if cleaned in NFL_TEAMS else None
