"""Season league pages render with real data."""

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import DBLeague, DBMatchup, DBTeam

client = TestClient(app)
YEAR = 2026


@pytest.fixture
def league(db):
    lg = DBLeague(league_id="s1", name="Sunday Money", year=YEAR, kind="season",
                  status="in_season", current_week=3, regular_season_weeks=14)
    db.add(lg)
    db.commit()
    teams = []
    for i in range(2):
        t = DBTeam(team_id=f"s1-{i}", name=f"Team {i}", owner="o",
                   league_id="s1", manager_type="ai" if i else "human",
                   is_user_team=(i == 0), wins=2 - i, losses=i,
                   total_points=300.0 - i * 20)
        db.add(t)
        teams.append(t)
    db.commit()
    db.add(DBMatchup(league_id=lg.id, year=YEAR, week=3, bracket_slot=0,
                     home_team_id=teams[0].id, away_team_id=teams[1].id,
                     home_points=101.5, away_points=98.2, status="in_progress"))
    db.commit()
    return lg


def test_league_home_renders_standings(db, league):
    response = client.get("/season/s1")
    assert response.status_code == 200
    assert "Sunday Money" in response.text
    assert "Team 0" in response.text
    assert "2-0" in response.text


def test_league_home_shows_the_current_week(db, league):
    response = client.get("/season/s1")
    assert "Week 3" in response.text


def test_unknown_league_404s(db):
    assert client.get("/season/nope").status_code == 404


def test_scoreboard_renders_matchup_points(db, league):
    response = client.get("/season/s1/scoreboard/3")
    assert response.status_code == 200
    assert "101.5" in response.text
    assert "98.2" in response.text


def test_scoreboard_for_an_unplayed_week_is_empty_not_broken(db, league):
    response = client.get("/season/s1/scoreboard/9")
    assert response.status_code == 200


def test_literal_routes_are_not_swallowed_by_the_league_catch_all(db, league):
    """Registration order is load-bearing.

    FastAPI matches in registration order, so `GET /season/{league_key}` must be
    registered *after* the literal-prefix routes. Registered first, it would
    swallow `/season/runs/5` as league_key="runs" and the agent proposal
    endpoints would silently 404.
    """
    response = client.get("/season/runs/999999")
    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found"
