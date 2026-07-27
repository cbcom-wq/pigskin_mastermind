"""Season helpers.

The fantasy season is identified by the calendar year the NFL season
starts in.  Draft prep for season N (ADP data, mock drafts) runs from
roughly March of year N; January and February still belong to season
N-1 (playoffs / pre-combine, before any year-N ADP data exists).
"""

from datetime import date
from typing import Optional

# First month whose ADP data belongs to the current calendar year's season.
_SEASON_ROLLOVER_MONTH = 3


def current_fantasy_season(today: Optional[date] = None) -> int:
    """Return the fantasy season year for ``today`` (default: today).

    >>> current_fantasy_season(date(2026, 7, 26))
    2026
    >>> current_fantasy_season(date(2027, 1, 15))
    2026
    """
    d = today or date.today()
    return d.year if d.month >= _SEASON_ROLLOVER_MONTH else d.year - 1
