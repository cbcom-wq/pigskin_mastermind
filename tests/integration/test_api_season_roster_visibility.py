"""Season rosters are visible on the legacy team/league pages.

Season leagues store ownership in ``DBRosterSpot`` (league-scoped); the ESPN
path stores it in ``DBPlayer.team_id``. Pages that read only the legacy column
report a freshly drafted season team as having no players at all.
"""

import re

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import (
    DBLeague, DBPlayer, DBPlayerProjection, DBRosterSpot, DBTeam,
)

client = TestClient(app)
YEAR = 2026

ROSTER = [
    ("Josh Allen", "QB", "BUF"),
    ("Bijan Robinson", "RB", "ATL"),
    ("Puka Nacua", "WR", "LAR"),
]


@pytest.fixture
def season_league(db):
    """A season league whose roster lives entirely in ``DBRosterSpot``."""
    lg = DBLeague(league_id="s1", name="Bird Turds", year=YEAR, kind="season",
                  status="in_season", current_week=1, regular_season_weeks=14)
    db.add(lg)
    db.commit()

    team = DBTeam(team_id="s1-1", name="The Scoobies", owner="Me",
                  league_id="s1", is_user_team=True, manager_type="human",
                  draft_slot=1, wins=0, losses=0, ties=0, total_points=0.0)
    db.add(team)
    db.commit()

    for name, position, nfl_team in ROSTER:
        player = DBPlayer(player_id=f"nfl_{name.replace(' ', '_')}", name=name,
                          position=position, nfl_team=nfl_team,
                          projected_points=12.0)
        db.add(player)
        db.commit()
        db.add(DBRosterSpot(league_id=lg.id, team_id=team.id,
                            player_id=player.id, acquired_via="draft"))
    db.commit()
    return lg, team


@pytest.fixture
def espn_league(db):
    """The legacy path: ownership on ``DBPlayer.team_id``."""
    lg = DBLeague(league_id="e1", name="Pigskin Throne", year=YEAR, kind="espn")
    db.add(lg)
    db.commit()

    team = DBTeam(team_id="e1-1", name="Stable of Stars", owner="Me",
                  league_id="e1", is_user_team=True, wins=0, losses=0, ties=0,
                  total_points=0.0)
    db.add(team)
    db.commit()

    db.add(DBPlayer(player_id="espn_1", name="Amon-Ra St. Brown", position="WR",
                    nfl_team="CIN", team_id=team.id, projected_points=15.0))
    db.commit()
    return lg, team


def _player_counts(html: str):
    """The rendered value of every "Players" stat cell on the page."""
    return [
        int(m.group(1))
        for m in re.finditer(r"Players</p>\s*<p[^>]*>\s*(\d+)\s*</p>", html)
    ]


def test_league_page_counts_rosters(db, espn_league):
    """The league team grid renders a real roster count.

    Only ESPN leagues reach this page now — /leagues/{id} redirects a season
    league to its own home (see test_api_season_navigation.py). The count still
    resolves through roster_players(), so it stays right for either kind if
    that routing ever changes.
    """
    response = client.get("/leagues/e1")
    assert response.status_code == 200
    assert "Stable of Stars" in response.text
    assert _player_counts(response.text) == [1]


def test_team_detail_lists_season_roster_players(db, season_league):
    _, team = season_league
    response = client.get(f"/teams/{team.id}")
    assert response.status_code == 200
    for name, _, _ in ROSTER:
        assert name in response.text
    assert "No players on this roster yet" not in response.text


def test_teams_list_counts_a_season_roster(db, season_league):
    response = client.get("/teams")
    assert response.status_code == 200
    assert "The Scoobies" in response.text
    assert _player_counts(response.text) == [len(ROSTER)]


def test_projections_panel_sees_a_season_roster(db, season_league):
    """Lineup optimization lives on the team's projections panel.

    The standalone /lineups page is gone; this fragment is the only place a
    lineup is optimized in the UI, so it is the surface that has to resolve a
    season roster through ``roster_players()``.
    """
    _, team = season_league
    for player in db.query(DBPlayer).all():
        db.add(DBPlayerProjection(player_id=player.id, year=YEAR, week=1,
                                  source="model", projected_points=11.0))
    db.commit()

    response = client.get(f"/teams/{team.id}/projections?week=1&year={YEAR}")
    assert response.status_code == 200
    for name, _, _ in ROSTER:
        assert name in response.text


def test_trade_analyzer_sees_a_season_roster(db, season_league):
    _, team = season_league
    response = client.get(f"/trades/team-players?team_id={team.id}")
    assert response.status_code == 200
    assert "No players on this team" not in response.text
    assert "Josh Allen" in response.text


def test_espn_rosters_still_read_the_legacy_column(db, espn_league):
    """Regression guard: the ESPN path must not change."""
    _, team = espn_league
    response = client.get(f"/teams/{team.id}")
    assert response.status_code == 200
    assert "Amon-Ra St. Brown" in response.text
