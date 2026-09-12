"""Source selection for play-by-play.

Ordered providers, first non-empty answer wins.  The order is the policy: ESPN
is live and portable, so it leads; anything slower or heavier is a fallback.
"""

from pigskin_mastermind.services.play_by_play.base import Play
from pigskin_mastermind.services.play_by_play.registry import plays_for_game


class _Source:
    def __init__(self, name, plays, boom=False):
        self.name = name
        self._plays = plays
        self._boom = boom
        self.calls = 0

    def plays(self, year, week, team):
        self.calls += 1
        if self._boom:
            raise RuntimeError("upstream exploded")
        return self._plays


def _play(pid):
    return Play(play_id=pid, description="a play")


class TestSourceOrder:
    def test_uses_the_first_source_that_has_plays(self):
        first = _Source("espn", [_play("1")])
        second = _Source("nflverse", [_play("2")])

        plays = plays_for_game(2025, 2, "JAX", sources=[first, second])

        assert [p.play_id for p in plays] == ["1"]
        assert second.calls == 0, "fell through despite the first source answering"

    def test_falls_through_when_a_source_has_nothing(self):
        empty = _Source("espn", [])
        backup = _Source("nflverse", [_play("2")])

        plays = plays_for_game(2025, 2, "JAX", sources=[empty, backup])

        assert [p.play_id for p in plays] == ["2"]

    def test_a_raising_source_does_not_take_down_the_rest(self):
        """One provider's outage must not blank a page the next could fill."""
        broken = _Source("espn", None, boom=True)
        backup = _Source("nflverse", [_play("2")])

        plays = plays_for_game(2025, 2, "JAX", sources=[broken, backup])

        assert [p.play_id for p in plays] == ["2"]

    def test_no_source_having_plays_is_an_empty_list_not_an_error(self):
        assert plays_for_game(2025, 2, "JAX", sources=[_Source("espn", [])]) == []

    def test_defaults_to_espn(self):
        from pigskin_mastermind.services.play_by_play.registry import default_sources

        assert [s.name for s in default_sources()] == ["espn"]
