"""The rendered back control must match where the user actually came from.

These drive the pages the old code got wrong: a player reached from the hot
metrics board used to offer "Back to Players", a link labelled Back that
landed you somewhere you had never been.
"""

import re
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import DBLeague, DBPlayer, DBTeam

client = TestClient(app)

#: The macro renders one anchor carrying this class per page. Attribute order
#: inside the tag is the macro's business, so it is not matched on.
_BACK_LINK = re.compile(r"<a\s([^>]*)>(.*?)</a>", re.DOTALL)


def back_link(html):
    """(href, visible text) of the page's back control."""
    for attrs, inner in _BACK_LINK.findall(html):
        if not re.search(r'class="[^"]*\bnav-back\b', attrs):
            continue
        href = re.search(r'href="([^"]*)"', attrs).group(1)
        text = re.sub(r"<[^>]+>", " ", inner)
        return href.replace("&amp;", "&"), " ".join(text.split())
    raise AssertionError("page rendered no back control")


@pytest.fixture
def player(db):
    row = DBPlayer(
        player_id="p_nav_1", name="Nav Test WR", position="WR", nfl_team="KC"
    )
    db.add(row)
    db.commit()
    return row.id


@pytest.fixture
def team(db):
    league = DBLeague(league_id="99887", name="Nav League", year=2025, kind="season")
    db.add(league)
    row = DBTeam(team_id="t_nav_1", name="Nav Team", owner="Tester", league_id="99887")
    db.add(row)
    db.commit()
    return row.id


# --------------------------------------------------------------------------
# The player page, reached from each of the places that link to it
# --------------------------------------------------------------------------


def test_player_reached_from_hot_metrics_goes_back_to_hot_metrics(player):
    html = client.get(f"/players/{player}?back=/metrics/hot").text
    assert back_link(html) == ("/metrics/hot", "Back to Hot Metrics")


def test_player_reached_from_a_team_goes_back_to_that_team(player):
    html = client.get(f"/players/{player}?back=/teams/7").text
    assert back_link(html) == ("/teams/7", "Back to Team")


def test_player_reached_directly_falls_back_to_the_player_list(player):
    href, text = back_link(client.get(f"/players/{player}").text)
    assert (href, text) == ("/players", "Back to Players")


def test_player_back_target_keeps_its_query_string(player):
    """A weekly team view must come back on the same week."""
    html = client.get(f"/players/{player}?back=/teams/7%3Fweek%3D3").text
    assert back_link(html) == ("/teams/7?week=3", "Back to Team")


def test_player_page_refuses_an_off_site_back_target(player):
    html = client.get(f"/players/{player}?back=https://evil.example/phish").text
    assert "evil.example" not in html
    assert back_link(html) == ("/players", "Back to Players")


# --------------------------------------------------------------------------
# The team page, which had the same hardcoded-destination bug
# --------------------------------------------------------------------------


def test_team_reached_from_a_league_goes_back_to_that_league(team):
    html = client.get(f"/teams/{team}?back=/leagues/99887").text
    assert back_link(html) == ("/leagues/99887", "Back to League")


def test_team_reached_directly_falls_back_to_the_team_list(team):
    assert back_link(client.get(f"/teams/{team}").text) == ("/teams", "Back to Teams")


# --------------------------------------------------------------------------
# Chaining: a page two hops deep still returns one hop, not to the root
# --------------------------------------------------------------------------


def test_player_simulation_returns_to_the_player_it_came_from(player):
    html = client.get(f"/players/{player}/simulation?back=/players/{player}").text
    assert back_link(html) == (f"/players/{player}", "Back to Player")


def test_player_page_hands_the_simulation_its_own_url_as_the_back_target(player):
    """Otherwise the simulation's Back skips the profile you just read."""
    html = client.get(f"/players/{player}?back=/metrics/hot").text
    match = re.search(r'href="(/players/\d+/simulation[^"]*)"', html)
    assert match, "player page rendered no simulation link"

    sim_href = match.group(1).replace("&amp;", "&")
    target = parse_qs(urlparse(sim_href).query)["back"][0]

    # ...and the profile's own origin rides along, so Back keeps working a
    # second time rather than dead-ending on the player list.
    assert urlparse(target).path == f"/players/{player}"
    assert parse_qs(urlparse(target).query)["back"] == ["/metrics/hot"]


# --------------------------------------------------------------------------
# Links *out* of a page must carry that page's own URL, not a hardcoded one
# --------------------------------------------------------------------------


def test_team_page_hands_its_drilldowns_its_own_url(team):
    """Both drill-downs, so Back from either returns to the week being read."""
    html = client.get(f"/teams/{team}?week=3&back=/leagues/99887").text

    for pattern in (
        r'href="(/teams/\d+/simulation[^"]*)"',
        r'href="(/visualizations/season-animation/[^"]*)"',
    ):
        match = re.search(pattern, html)
        assert match, f"team page rendered no link matching {pattern}"

        href = match.group(1).replace("&amp;", "&")
        target = parse_qs(urlparse(href).query)["back"][0]
        assert urlparse(target).path == f"/teams/{team}"
        assert parse_qs(urlparse(target).query)["week"] == ["3"]


def test_league_page_sends_its_teams_back_to_the_league(db):
    """Arriving at a team from a league used to offer only 'Back to Teams'."""
    db.add(DBLeague(league_id="55501", name="Card League", year=2025, kind="espn"))
    db.add(DBTeam(team_id="t_card", name="Card Team", owner="T", league_id="55501"))
    db.commit()

    html = client.get("/leagues/55501").text
    match = re.search(r'href="(/teams/\d+[^"]*)"', html)
    assert match, "league page rendered no team link"

    href = match.group(1).replace("&amp;", "&")
    assert parse_qs(urlparse(href).query)["back"] == ["/leagues/55501"]
