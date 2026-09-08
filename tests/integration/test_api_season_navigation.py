"""A season league is reachable from the app's own navigation.

Committing a draft redirects to /season/{id} once. Before this, nothing linked
there ever again: the sidebar has no season entry, and /leagues/{id} rendered
the ESPN team grid instead — complete with Import All Players and Claim, which
a drafted league has no credentials or meaning for.
"""

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import (
    DBLeague,
    DBPlayer,
    DBRosterSpot,
    DBTeam,
)

client = TestClient(app)
YEAR = 2026


@pytest.fixture
def season(db):
    lg = DBLeague(
        league_id="s1",
        name="Bird Turds",
        year=YEAR,
        kind="season",
        status="in_season",
        current_week=1,
        regular_season_weeks=14,
    )
    db.add(lg)
    db.commit()

    team = DBTeam(
        team_id="s1-1",
        name="The Scoobies",
        owner="Me",
        league_id="s1",
        is_user_team=True,
        manager_type="human",
        draft_slot=1,
        wins=0,
        losses=0,
        ties=0,
        total_points=0.0,
    )
    db.add(team)
    db.commit()

    player = DBPlayer(
        player_id="nfl_1",
        name="Josh Allen",
        position="QB",
        nfl_team="BUF",
        projected_points=22.0,
    )
    db.add(player)
    db.commit()
    db.add(
        DBRosterSpot(
            league_id=lg.id, team_id=team.id, player_id=player.id, acquired_via="draft"
        )
    )
    db.commit()
    return lg, team


@pytest.fixture
def espn(db):
    lg = DBLeague(league_id="e1", name="Pigskin Throne", year=YEAR, kind="espn")
    db.add(lg)
    db.commit()
    team = DBTeam(
        team_id="e1-1",
        name="Stable of Stars",
        owner="Me",
        league_id="e1",
        is_user_team=True,
        wins=0,
        losses=0,
        ties=0,
        total_points=0.0,
    )
    db.add(team)
    db.commit()
    db.add(
        DBPlayer(
            player_id="espn_1",
            name="Puka Nacua",
            position="WR",
            nfl_team="LAR",
            team_id=team.id,
            projected_points=15.0,
        )
    )
    db.commit()
    return lg, team


def _content(html: str) -> str:
    """The page body with the sidebar removed.

    Every page's sidebar contains a Leagues link, so an unscoped assertion for
    one passes on a page that has no breadcrumb at all.
    """
    start = html.find('<aside id="sidebar"')
    if start == -1:
        return html
    end = html.find("</aside>", start)
    return html[:start] + html[end + len("</aside>") :]


def test_generic_league_page_redirects_to_the_season_home(db, season):
    response = client.get("/leagues/s1", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/season/s1"


def test_the_redirect_lands_on_a_working_season_page(db, season):
    response = client.get("/leagues/s1")
    assert response.status_code == 200
    assert "Standings" in response.text


def test_espn_leagues_still_render_the_team_grid(db, espn):
    """The redirect must not catch the ESPN path."""
    response = client.get("/leagues/e1", follow_redirects=False)
    assert response.status_code == 200
    assert "Stable of Stars" in response.text
    assert "Import All Players" in response.text


def test_leagues_list_links_a_season_league_to_its_season_home(db, season, espn):
    response = client.get("/leagues")
    assert response.status_code == 200
    assert 'href="/season/s1"' in response.text
    assert 'href="/leagues/e1"' in response.text


def test_leagues_list_labels_which_leagues_are_drafted(db, season, espn):
    """A drafted league and an imported one must be tellable apart.

    Asserted on the badge markup rather than the words: "Season" also appears
    in every card's "2026 Season" subtitle and "ESPN" in the sidebar, so a
    plain substring check passes with no badge rendered at all.
    """
    body = _content(client.get("/leagues").text)
    assert 'data-league-kind="season"' in body
    assert 'data-league-kind="espn"' in body


def test_season_home_links_back_to_leagues(db, season):
    response = client.get("/season/s1")
    assert 'href="/leagues"' in _content(response.text)


def test_scoreboard_links_back_to_leagues(db, season):
    response = client.get("/season/s1/scoreboard/1")
    assert response.status_code == 200
    assert 'href="/leagues"' in _content(response.text)


def test_season_team_page_links_back_to_leagues(db, season):
    _, team = season
    response = client.get(f"/season/s1/teams/{team.id}")
    assert response.status_code == 200
    assert 'href="/leagues"' in _content(response.text)


def test_dashboard_sends_a_season_team_to_its_season_page(db, season):
    """``?back=/`` is appended per the app's back-navigation convention --
    see ``services/dashboard.py::team_url``."""
    _, team = season
    response = client.get("/")
    assert response.status_code == 200
    assert f'href="/season/s1/teams/{team.id}?back=/"' in response.text


def test_dashboard_sends_an_espn_team_to_the_legacy_page(db, espn):
    _, team = espn
    response = client.get("/")
    assert f'href="/teams/{team.id}?back=/"' in response.text


def test_teams_list_sends_a_season_team_to_its_season_page(db, season):
    _, team = season
    response = client.get("/teams")
    assert response.status_code == 200
    assert f'href="/season/s1/teams/{team.id}"' in response.text


def test_team_detail_offers_the_season_league_view(db, season):
    _, team = season
    response = client.get(f"/teams/{team.id}")
    assert response.status_code == 200
    assert f'href="/season/s1/teams/{team.id}"' in response.text


# The old dashboard rendered Players/Teams/Positions stat cards here, whose
# regression test (`test_dashboard_counts_players_a_season_team_owns`,
# formerly using `DBPlayer.team_id` vs. `DBRosterSpot` roster-counting logic)
# no longer applies: that stat-card layout was removed by the dashboard
# redesign (see services/dashboard.py). The underlying roster-visibility
# fix it guarded is covered directly in tests/test_dashboard.py.
