"""A public consensus projection, fetched from an external ranking site.

This is the least reliable leg of the six and is built to be treated that way:

- **Disabled by default.** Set ``PIGSKIN_CONSENSUS_URL`` to turn it on. There is
  no free public API for the usual consensus sources, so enabling this means
  parsing someone's HTML — check that site's terms before you do, and expect
  the parse to break when they redesign.
- **Swappable.** Everything site-specific is in ``fetch_rows``, a callable
  injected at construction. Pointing this at a different feed, a CSV export, or
  a paid API means replacing that one function; the provider, the orchestrator,
  the blend, and the view do not change.
- **Failure is contained.** The orchestrator records an ``error`` run row for
  this source and renders the other five. A broken scrape degrades the table by
  one column, it does not fail the refresh.

Names are resolved through ``PlayerIdentityService`` like every other importer,
because a consensus page publishes names and nothing else.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.services.player_identity import PlayerIdentityService
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_CONSENSUS,
    ProjectionValue,
)
from pigskin_mastermind.utils.positions import normalize_position

logger = logging.getLogger(__name__)

#: (name, position, projected_points) as published.
ConsensusRow = Tuple[str, Optional[str], float]

#: Where to fetch from. Unset means the provider reports itself unavailable and
#: the orchestrator records a 'skipped' run rather than an error.
CONSENSUS_URL_ENV = "PIGSKIN_CONSENSUS_URL"

_REQUEST_TIMEOUT = 20


class ConsensusProjectionSource:
    """``source='consensus'`` — externally published weekly consensus."""

    key = SOURCE_CONSENSUS
    label = "Experts"
    writes = True

    def __init__(
        self,
        fetch_rows: Optional[Callable[[int, int], Iterable[ConsensusRow]]] = None,
        url: Optional[str] = None,
    ) -> None:
        self.url = url or os.environ.get(CONSENSUS_URL_ENV)
        # Tracked as a flag rather than comparing against ``self._default_fetch``:
        # attribute access on a bound method builds a new object every time, so
        # an identity check there is always True and this source would claim to
        # be available with nothing configured.
        self._injected = fetch_rows is not None
        self._fetch_rows = fetch_rows or self._default_fetch

    @property
    def available(self) -> bool:
        """False when no feed is configured, so this is 'skipped', not 'error'."""
        return self._injected or bool(self.url)

    def project_week(
        self,
        db: Session,
        year: int,
        week: int,
        player_ids: List[int],
    ) -> Dict[int, ProjectionValue]:
        if not player_ids or not self.available:
            return {}

        rows = list(self._fetch_rows(year, week))
        if not rows:
            return {}

        identity = PlayerIdentityService(db)
        wanted = set(player_ids)
        out: Dict[int, ProjectionValue] = {}

        for name, position, points in rows:
            if points is None or points <= 0:
                continue
            normalized = normalize_position(position) if position else None
            try:
                player = identity.resolve(name=name, position=normalized)
            except Exception:
                logger.exception("Consensus name resolution failed for %r", name)
                continue
            if player is None or player.id not in wanted:
                continue

            out[player.id] = ProjectionValue(
                points=round(float(points), 2),
                components={"published_name": name, "source_url": self.url},
            )
        return out

    # ------------------------------------------------------------------

    def _default_fetch(self, year: int, week: int) -> List[ConsensusRow]:
        """Fetch and parse the configured feed.

        Kept deliberately small and tolerant: it pulls table rows with a name
        cell and a trailing numeric cell, which survives most cosmetic markup
        changes and fails loudly rather than silently on a real one.
        """
        if not self.url:
            return []

        import requests  # local import: only needed when the flag is on

        url = self.url.format(year=year, week=week)
        response = requests.get(
            url,
            timeout=_REQUEST_TIMEOUT,
            headers={"User-Agent": "pigskin-mastermind/1.0"},
        )
        response.raise_for_status()
        return parse_consensus_html(response.text)


def parse_consensus_html(html: str) -> List[ConsensusRow]:
    """Extract ``(name, position, points)`` triples from a projections table.

    Split out as a pure function so the fragile half of this source can be
    tested against a saved fixture without any network access.
    """
    rows: List[ConsensusRow] = []
    for match in re.finditer(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [
            re.sub(r"<[^>]+>", " ", cell).strip()
            for cell in re.findall(
                r"<t[dh][^>]*>(.*?)</t[dh]>", match.group(1), re.S | re.I,
            )
        ]
        cells = [re.sub(r"\s+", " ", c) for c in cells if c]
        if len(cells) < 2:
            continue

        name = cells[0]
        if not re.search(r"[A-Za-z]{2,}\s+[A-Za-z]", name):
            continue

        # The projection is the last cell that parses as a number.
        points = None
        for cell in reversed(cells[1:]):
            try:
                points = float(cell.replace(",", ""))
                break
            except ValueError:
                continue
        if points is None:
            continue

        position = None
        position_match = re.search(r"\b(QB|RB|WR|TE|K|DST|D/ST|DEF)\b", " ".join(cells))
        if position_match:
            position = position_match.group(1)

        rows.append((name, position, points))
    return rows
