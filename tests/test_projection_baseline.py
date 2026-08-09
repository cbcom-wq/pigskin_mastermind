"""Properties of the shrunk, leakage-free baseline."""

from pigskin_mastermind.services.projection_baseline import (
    shrink,
    FALLBACK_POSITION_PPG,
    DEFAULT_SHRINKAGE_GAMES,
)


def test_small_sample_lands_nearer_the_prior():
    """A 2-game player is mostly prior; a 15-game player is mostly themselves.

    This is the whole point of shrinkage: without it a backup with two good
    games outranks a proven starter, which is exactly how the draft pool
    used to misprice replacement-level players.
    """
    prior, observed, k = 8.0, 20.0, DEFAULT_SHRINKAGE_GAMES

    two_games = shrink(observed, 2, prior, k)
    fifteen_games = shrink(observed, 15, prior, k)

    assert abs(two_games - prior) < abs(two_games - observed)
    assert abs(fifteen_games - observed) < abs(fifteen_games - prior)
    assert two_games < fifteen_games < observed


def test_zero_games_returns_the_prior_exactly():
    assert shrink(99.0, 0, 8.0, DEFAULT_SHRINKAGE_GAMES) == 8.0


def test_zero_k_disables_shrinkage():
    assert shrink(20.0, 2, 8.0, 0) == 20.0


def test_every_fantasy_position_has_a_fallback_prior():
    """A missing position prior silently becomes 0.0 and zeroes the player."""
    assert set(FALLBACK_POSITION_PPG) == {"QB", "RB", "WR", "TE", "K", "DEF"}
    assert all(v > 0 for v in FALLBACK_POSITION_PPG.values())
