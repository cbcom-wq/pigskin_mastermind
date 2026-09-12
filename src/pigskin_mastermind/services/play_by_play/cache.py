"""Caching in front of ESPN's scoreboard and summary endpoints.

A team simulation runs one player simulation per starter, and each of those
asks for the week's event list and then a game summary.  For a 12-player
roster that was 24 requests and 18.7 seconds -- the same week list fetched 12
times over, and one game's summary refetched for every player in it.

Two different things are being cached, and they do not deserve the same
lifetime:

* the **week's event list** carries live scores and statuses, so it goes stale
  quickly;
* a **game summary** is immutable once the game is final, and volatile while
  it is being played.

Statuses come from the event list we already fetch, so nothing extra is
requested to tell those apart.
"""

import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional, Tuple

# The week list changes as games are played, so it expires quickly.
DEFAULT_TTL = 60.0
# A completed game's plays never change again.
DEFAULT_FINAL_TTL = 3600.0
# An ESPN summary runs ~2 MB, so the cache is capped rather than left to grow.
# A full week's slate is 16 games; this holds a couple of weeks.
DEFAULT_MAX_ENTRIES = 40


class CachingBoxScoreClient:
    """Wraps a ``BoxScoreClient`` and remembers what it has already fetched.

    The clock is injectable so expiry can be tested without sleeping.
    """

    def __init__(
        self,
        inner: Optional[Any] = None,
        ttl: float = DEFAULT_TTL,
        final_ttl: float = DEFAULT_FINAL_TTL,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if inner is None:
            from pigskin_mastermind.services.espn_boxscore import BoxScoreClient

            inner = BoxScoreClient()
        self.inner = inner
        self.ttl = ttl
        self.final_ttl = final_ttl
        self.max_entries = max_entries
        self._clock = clock
        self._lock = threading.Lock()
        self._weeks: Dict[Tuple[int, int], Tuple[float, List[Dict[str, Any]]]] = {}
        self._summaries: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        # event_id -> the status last seen in a week listing.
        self._status: Dict[str, str] = {}

    def _fresh(self, entry: Optional[Tuple[float, Any]]) -> bool:
        return entry is not None and entry[0] > self._clock()

    def week_events(self, year: int, week: int) -> List[Dict[str, Any]]:
        key = (year, week)
        with self._lock:
            entry = self._weeks.get(key)
            if self._fresh(entry):
                return entry[1]

        events = self.inner.week_events(year, week) or []

        with self._lock:
            self._weeks[key] = (self._clock() + self.ttl, events)
            for event in events:
                event_id = event.get("event_id")
                if event_id:
                    self._status[str(event_id)] = str(event.get("status") or "")
        return events

    def _ttl_for(self, event_id: str) -> float:
        """A finished game earns the long lifetime; anything else does not.

        An event we have never seen listed is treated as live -- assuming a
        game is over because we know nothing about it is how a tablet ends up
        showing a frozen third quarter.
        """
        return self.final_ttl if self._status.get(event_id) == "post" else self.ttl

    def event_summary(self, event_id: str) -> Any:
        event_id = str(event_id)
        with self._lock:
            entry = self._summaries.get(event_id)
            if self._fresh(entry):
                return entry[1]

        summary = self.inner.event_summary(event_id)

        with self._lock:
            self._summaries[event_id] = (
                self._clock() + self._ttl_for(event_id),
                summary,
            )
            self._summaries.move_to_end(event_id)
            while len(self._summaries) > self.max_entries:
                self._summaries.popitem(last=False)
        return summary

    def cached_summaries(self) -> int:
        with self._lock:
            return len(self._summaries)

    def clear(self) -> None:
        """Drop everything remembered.  Mostly for tests and manual refresh."""
        with self._lock:
            self._weeks.clear()
            self._summaries.clear()
            self._status.clear()


_shared: Optional[CachingBoxScoreClient] = None
_shared_lock = threading.Lock()


def shared_client() -> CachingBoxScoreClient:
    """The process-wide cache every ESPN play-by-play source shares.

    ``plays_for_game`` builds its own source and ``game_context`` another, so
    without one shared cache those two alone double every request before a
    second player is even considered.
    """
    global _shared
    with _shared_lock:
        if _shared is None:
            _shared = CachingBoxScoreClient()
        return _shared
