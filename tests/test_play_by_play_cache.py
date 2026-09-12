"""Caching for ESPN play-by-play fetches.

A team simulation builds one player simulation per starter, and each of those
asks ESPN for the week's event list and then a game summary.  For a 12-player
roster that was 24 requests and 18.7 seconds -- the same week list fetched 12
times, and one game's summary refetched for every player who appeared in it.

Freshness still matters for a game in progress, so a completed game is cached
far longer than a live one.
"""

import pytest

from pigskin_mastermind.services.play_by_play.cache import CachingBoxScoreClient


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _RecordingClient:
    def __init__(self, events=None, summaries=None):
        self.events = events if events is not None else []
        self.summaries = summaries or {}
        self.week_calls = 0
        self.summary_calls = []

    def week_events(self, year, week):
        self.week_calls += 1
        return self.events

    def event_summary(self, event_id):
        self.summary_calls.append(event_id)
        return self.summaries.get(event_id, {"id": event_id})


def _event(event_id, status="post", home="CIN", away="JAX"):
    return {
        "event_id": event_id,
        "status": status,
        "home_team": home,
        "away_team": away,
    }


@pytest.fixture
def clock():
    return _Clock()


class TestWeekEvents:
    def test_the_same_week_is_fetched_once(self, clock):
        inner = _RecordingClient(events=[_event("1")])
        client = CachingBoxScoreClient(inner, clock=clock)

        for _ in range(12):
            client.week_events(2025, 2)

        assert inner.week_calls == 1

    def test_a_different_week_is_a_different_entry(self, clock):
        inner = _RecordingClient(events=[_event("1")])
        client = CachingBoxScoreClient(inner, clock=clock)

        client.week_events(2025, 2)
        client.week_events(2025, 3)
        client.week_events(2025, 2)

        assert inner.week_calls == 2

    def test_the_list_goes_stale(self, clock):
        """Scores and statuses change; the week list cannot be cached forever."""
        inner = _RecordingClient(events=[_event("1")])
        client = CachingBoxScoreClient(inner, ttl=60.0, clock=clock)

        client.week_events(2025, 2)
        clock.advance(61)
        client.week_events(2025, 2)

        assert inner.week_calls == 2


class TestEventSummary:
    def test_a_shared_game_is_fetched_once_per_player(self, clock):
        inner = _RecordingClient(events=[_event("g1")])
        client = CachingBoxScoreClient(inner, clock=clock)

        for _ in range(5):
            client.event_summary("g1")

        assert inner.summary_calls == ["g1"]

    def test_a_finished_game_stays_cached_past_the_short_ttl(self, clock):
        """A completed game's plays never change again."""
        inner = _RecordingClient(events=[_event("g1", status="post")])
        client = CachingBoxScoreClient(inner, ttl=60.0, final_ttl=3600.0, clock=clock)

        client.week_events(2025, 2)
        client.event_summary("g1")
        clock.advance(120)
        client.event_summary("g1")

        assert inner.summary_calls == ["g1"]

    def test_a_live_game_is_refetched_at_the_short_ttl(self, clock):
        """A game in progress is exactly the case the tablet cares about."""
        inner = _RecordingClient(events=[_event("g1", status="in")])
        client = CachingBoxScoreClient(inner, ttl=60.0, final_ttl=3600.0, clock=clock)

        client.week_events(2025, 2)
        client.event_summary("g1")
        clock.advance(61)
        client.event_summary("g1")

        assert inner.summary_calls == ["g1", "g1"]

    def test_an_unknown_status_is_treated_as_live(self, clock):
        """Never having seen the game, assume it might still be moving."""
        inner = _RecordingClient(events=[])
        client = CachingBoxScoreClient(inner, ttl=60.0, final_ttl=3600.0, clock=clock)

        client.event_summary("mystery")
        clock.advance(61)
        client.event_summary("mystery")

        assert inner.summary_calls == ["mystery", "mystery"]


class TestSharedAcrossSources:
    def test_two_sources_built_separately_share_one_cache(self, clock):
        """plays_for_game constructs its own source, and game_context another.

        Without a shared cache those two alone double every request, before
        any second player is considered.
        """
        from pigskin_mastermind.services.play_by_play.espn_source import (
            ESPNPlayByPlaySource,
        )

        inner = _RecordingClient(events=[_event("g1")])
        shared = CachingBoxScoreClient(inner, clock=clock)

        first = ESPNPlayByPlaySource(client=shared)
        second = ESPNPlayByPlaySource(client=shared)
        first.plays(2025, 2, "JAX")
        second.game_context(2025, 2, "JAX")

        assert inner.week_calls == 1
        assert inner.summary_calls == ["g1"]


class TestBounded:
    """ESPN summaries run ~2 MB each.

    An unbounded cache is a slow memory leak in a long-running server, and on
    a memory-constrained tablet it is not slow.
    """

    def test_summaries_do_not_grow_without_limit(self, clock):
        inner = _RecordingClient(events=[])
        client = CachingBoxScoreClient(inner, max_entries=3, clock=clock)

        for i in range(6):
            client.event_summary("g%d" % i)

        assert client.cached_summaries() <= 3

    def test_the_oldest_entry_is_the_one_dropped(self, clock):
        inner = _RecordingClient(events=[])
        client = CachingBoxScoreClient(inner, max_entries=2, clock=clock)

        client.event_summary("old")
        client.event_summary("mid")
        client.event_summary("new")  # evicts "old"

        inner.summary_calls.clear()
        client.event_summary("new")
        client.event_summary("old")

        assert inner.summary_calls == ["old"], "expected only the evicted one refetched"
