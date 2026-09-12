"""Ordered play-by-play providers.

Order is the policy. ESPN leads because it is live, costs one JSON GET, and
needs nothing but ``requests`` -- which is also what lets it run on a build
that has no native wheels.

A provider raising must not blank a page another provider could fill, so each
is isolated. This mirrors ``projection_sources``, where partial success is the
designed normal rather than an error.
"""

import logging
from typing import Any, List, Optional, Sequence

from pigskin_mastermind.services.play_by_play.base import Play

logger = logging.getLogger(__name__)


def default_sources() -> List[Any]:
    """The providers to try, in order.

    ESPN only, deliberately. An nflverse provider is a legitimate addition for
    seasons ESPN no longer serves -- but it needs ``pandas``/``pyarrow``, so
    anything registering it must stay off the code path a no-native-wheels
    build imports.
    """
    from pigskin_mastermind.services.play_by_play.espn_source import (
        ESPNPlayByPlaySource,
    )

    return [ESPNPlayByPlaySource()]


def plays_for_game(
    year: int,
    week: int,
    team: str,
    sources: Optional[Sequence[Any]] = None,
) -> List[Play]:
    """Plays for *team*'s game that week, from the first source that has any.

    An empty list is a real answer -- a bye, an unplayed week, or a game no
    provider covers yet. Callers render "no play-by-play available"; they do
    not treat it as a failure.
    """
    for source in sources if sources is not None else default_sources():
        name = getattr(source, "name", source.__class__.__name__)
        try:
            plays = source.plays(year, week, team)
        except Exception:
            logger.exception("play-by-play source %r failed", name)
            continue
        if plays:
            return list(plays)
    return []
