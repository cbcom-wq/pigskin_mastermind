"""Tests for the shared back-navigation contract.

``resolve_back`` is what decides whether a page's "Back" control is honest.
Two things it must never do: send the user off-site because a crafted URL
said so, and label a link "Back to X" when X is not where the user came from.
"""

import pytest

from pigskin_mastermind.utils.back_nav import BackTarget, resolve_back

DEFAULT_URL = "/players"
DEFAULT_LABEL = "Players"


def _resolve(back):
    return resolve_back(back, DEFAULT_URL, DEFAULT_LABEL)


# --------------------------------------------------------------------------
# Falling back when there is no usable origin
# --------------------------------------------------------------------------


def test_missing_back_uses_the_page_default():
    assert _resolve(None) == BackTarget("/players", "Players")


def test_empty_back_uses_the_page_default():
    assert _resolve("") == BackTarget("/players", "Players")


def test_whitespace_only_back_uses_the_page_default():
    assert _resolve("   ") == BackTarget("/players", "Players")


# --------------------------------------------------------------------------
# Open-redirect rejection
#
# The back target is rendered as an href, so anything that can leave the
# origin has to be refused rather than sanitised into something plausible.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hostile",
    [
        "https://evil.example/phish",
        "http://evil.example",
        "//evil.example",  # protocol-relative
        "/\\evil.example",  # browsers read a backslash here as a second slash
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "players",  # relative, resolves against the current path
        "../admin",
    ],
)
def test_off_site_back_targets_are_refused(hostile):
    """A rejected target falls back to the default, never to the hostile URL."""
    assert _resolve(hostile) == BackTarget("/players", "Players")


def test_a_path_that_merely_contains_a_colon_is_still_accepted():
    """`javascript:` is refused for its scheme, not for its punctuation."""
    assert _resolve("/games/2025:KC:BUF").url == "/games/2025:KC:BUF"


# --------------------------------------------------------------------------
# Label derivation
#
# The label is derived from the path rather than passed alongside it, so a
# crafted URL cannot put arbitrary words next to the arrow.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,label",
    [
        ("/", "Dashboard"),
        ("/players", "Players"),
        ("/players/42", "Player"),
        ("/players/42/simulation", "Player Simulation"),
        ("/teams", "Teams"),
        ("/teams/7", "Team"),
        ("/teams/7/simulation", "Team Simulation"),
        ("/leagues", "Leagues"),
        ("/leagues/123456", "League"),
        ("/season/123456", "League"),
        ("/season/123456/teams/7", "Team"),
        ("/season/123456/scoreboard/3", "Scoreboard"),
        ("/games", "Scores"),
        ("/games/2025_03_KC_BUF", "Game"),
        ("/draft", "Draft"),
        ("/draft/board/abc123", "Draft Board"),
        ("/draft/recap/abc123", "Draft Recap"),
        ("/draft/simulate", "Draft Simulation"),
        ("/metrics/hot", "Hot Metrics"),
        ("/trades", "Trade Analyzer"),
        ("/settings", "Settings"),
        ("/projection-tuner", "Projection Tuner"),
        ("/projection-tuner/runs/run-9", "Tuner Run"),
        ("/visualizations/season-animation/7", "Season Animation"),
    ],
)
def test_label_is_derived_from_the_path(path, label):
    assert _resolve(path) == BackTarget(path, label)


def test_query_string_does_not_change_the_label():
    """`/teams/7?week=3` is still a team page, and the query must survive."""
    assert _resolve("/teams/7?week=3") == BackTarget("/teams/7?week=3", "Team")


def test_a_collection_label_is_not_used_for_one_of_its_members():
    """The bug this whole change exists to kill: 'Back to Teams' -> /teams/7."""
    assert _resolve("/teams/7").label == "Team"
    assert _resolve("/players/42").label == "Player"


def test_an_unregistered_path_gets_a_neutral_label():
    """Better a vague truth than a confident lie about the destination."""
    assert _resolve("/some/unmapped/page") == BackTarget(
        "/some/unmapped/page", "Previous Page"
    )
