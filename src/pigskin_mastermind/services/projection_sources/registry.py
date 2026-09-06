"""The ordered set of weekly projection sources.

Order here is display order in the table. Adding a source means adding a
provider module and one line below — the orchestrator, the blend, the ranking
service, and the template all iterate this list rather than naming sources.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_BLEND_MULTI,
    ProjectionValue,
    WeeklyProjectionProvider,
)
from pigskin_mastermind.services.projection_sources.consensus_source import (
    ConsensusProjectionSource,
)
from pigskin_mastermind.services.projection_sources.espn_source import (
    EspnProjectionSource,
)
from pigskin_mastermind.services.projection_sources.llm_source import (
    LlmProjectionSource,
)
from pigskin_mastermind.services.projection_sources.model_source import (
    ModelProjectionSource,
)
from pigskin_mastermind.services.projection_sources.nflverse_xp_source import (
    NflverseExpectedPointsSource,
)
from pigskin_mastermind.services.projection_sources.sportsbook_source import (
    SportsbookProjectionSource,
)

__all__ = [
    "ProjectionValue",
    "WeeklyProjectionProvider",
    "SOURCE_BLEND_MULTI",
    "build_registry",
    "source_labels",
]


def build_registry(
    keys: Optional[List[str]] = None,
    *,
    league_id: Optional[str] = None,
) -> List[WeeklyProjectionProvider]:
    """Instantiate the providers, optionally restricted to *keys*.

    Constructed per call rather than held as a module singleton: several
    providers hold a criteria builder or a scoring resolution tied to one
    league, and a long-lived instance would carry stale state across refreshes.
    """
    providers: List[WeeklyProjectionProvider] = [
        ModelProjectionSource(),
        EspnProjectionSource(),
        SportsbookProjectionSource(league_id=league_id),
        NflverseExpectedPointsSource(),
        LlmProjectionSource(),
        ConsensusProjectionSource(),
    ]
    if keys is None:
        return providers

    wanted = set(keys)
    return [p for p in providers if p.key in wanted]


def source_labels() -> Dict[str, str]:
    """``source key -> column header``, including the consensus column."""
    labels = {p.key: p.label for p in build_registry()}
    labels[SOURCE_BLEND_MULTI] = "Consensus"
    return labels
