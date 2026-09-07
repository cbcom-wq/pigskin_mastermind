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


#: Full club names -> canonical abbreviation. Sportsbook feeds (and The Odds
#: API in particular) identify teams as "Kansas City Chiefs" rather than "KC",
#: so a join between odds events and ``DBNFLGame`` needs this. Keyed on the
#: nickname alone, because that is the one part that is stable: "LA Chargers",
#: "Los Angeles Chargers" and "Chargers" all differ in the city, never the name.
_NICKNAMES = {
    'CARDINALS': 'ARI', 'FALCONS': 'ATL', 'RAVENS': 'BAL', 'BILLS': 'BUF',
    'PANTHERS': 'CAR', 'BEARS': 'CHI', 'BENGALS': 'CIN', 'BROWNS': 'CLE',
    'COWBOYS': 'DAL', 'BRONCOS': 'DEN', 'LIONS': 'DET', 'PACKERS': 'GB',
    'TEXANS': 'HOU', 'COLTS': 'IND', 'JAGUARS': 'JAX', 'CHIEFS': 'KC',
    'CHARGERS': 'LAC', 'RAMS': 'LAR', 'RAIDERS': 'LV', 'DOLPHINS': 'MIA',
    'VIKINGS': 'MIN', 'PATRIOTS': 'NE', 'SAINTS': 'NO', 'GIANTS': 'NYG',
    'JETS': 'NYJ', 'EAGLES': 'PHI', 'STEELERS': 'PIT', 'SEAHAWKS': 'SEA',
    '49ERS': 'SF', 'BUCCANEERS': 'TB', 'TITANS': 'TEN', 'COMMANDERS': 'WAS',
    # Former names still seen in older feeds.
    'REDSKINS': 'WAS', 'WASHINGTON': 'WAS', 'FOOTBALL': 'WAS',
    'OILERS': 'TEN',
}


def resolve_team(team: Optional[str]) -> Optional[str]:
    """Canonical abbreviation from an abbreviation *or* a full club name.

    Kept separate from :func:`normalize_team` rather than folded into it. That
    function is called at every import boundary and its contract — ``None``
    means "leave the stored value alone" — is load-bearing; widening it to
    match on free text would risk a stray word resolving to a team and
    overwriting a good value. This one is for the places that genuinely receive
    prose names, chiefly sportsbook feeds.

    >>> resolve_team('Kansas City Chiefs')
    'KC'
    >>> resolve_team('WSH')
    'WAS'
    >>> resolve_team('Rochester Jackalopes') is None
    True
    """
    direct = normalize_team(team)
    if direct:
        return direct
    if not team:
        return None

    words = str(team).strip().upper().replace('.', '').split()
    # Last word first: "New York Giants" and "Giants" both end in the nickname.
    for word in reversed(words):
        hit = _NICKNAMES.get(word)
        if hit:
            return hit
    return None
