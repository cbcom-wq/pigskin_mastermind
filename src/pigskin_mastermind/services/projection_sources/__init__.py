"""Weekly projection sources, one module per provider.

See ``base.py`` for the contract and ``registry.py`` for the ordered set.
"""

from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_BLEND_MULTI,
    SOURCE_CONSENSUS,
    SOURCE_ESPN,
    SOURCE_LLM,
    SOURCE_MARKET,
    SOURCE_MODEL,
    SOURCE_NFLVERSE_XP,
    SOURCE_SPORTSBOOK,
    ProjectionValue,
    WeeklyProjectionProvider,
)
from pigskin_mastermind.services.projection_sources.registry import (
    build_registry,
    source_labels,
)

__all__ = [
    "SOURCE_BLEND_MULTI",
    "SOURCE_CONSENSUS",
    "SOURCE_ESPN",
    "SOURCE_LLM",
    "SOURCE_MARKET",
    "SOURCE_MODEL",
    "SOURCE_NFLVERSE_XP",
    "SOURCE_SPORTSBOOK",
    "ProjectionValue",
    "WeeklyProjectionProvider",
    "build_registry",
    "source_labels",
]
