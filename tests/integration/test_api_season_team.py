"""The team page: roster, lineup editing, and the manager buttons."""

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import (
    DBLeague, DBLineupSlot, DBPlayer, DBPlayerProjection, DBRosterSpot, DBTeam,
)

client = TestClient(app)
YEAR = 2026
WEEK = 1


@pytest.fixture
def team(db):
    league = DBLeague(league_id="s1", name="S", year=YEAR, kind="season",
                      status="in_season", current_week=WEEK,
                      roster_slots={"QB": 1, "RB": 1, "BENCH": 1})
    db.add(league)
    db.commit()
    t = DBTeam(team_id="s1-1", name="Mine", owner="B", league_id="s1",
               manager_type="human", is_user_team=True)
    db.add(t)
    db.commit()
    for i, position in enumerate(["QB", "RB", "RB"]):
        p = DBPlayer(player_id=f"p{i}", name=f"Player {i}", position=position,
                     nfl_team="ATL")
        db.add(p)
        db.commit()
        db.add(DBRosterSpot(league_id=league.id, team_id=t.id, player_id=p.id,
                            acquired_via="draft"))
        db.add(DBPlayerProjection(player_id=p.id, year=YEAR, week=WEEK,
                                  source="model", projected_points=20.0 - i))
    db.commit()
    return t


def test_team_page_lists_the_roster(db, team):
    response = client.get(f"/season/s1/teams/{team.id}")
    assert response.status_code == 200
    assert "Player 0" in response.text
    assert "Mine" in response.text


def test_team_page_offers_the_manager_buttons(db, team):
    response = client.get(f"/season/s1/teams/{team.id}")
    assert "auto-set" in response.text
    assert "manage" in response.text


def test_auto_set_applies_the_optimizer(db, team):
    response = client.post(f"/season/s1/teams/{team.id}/auto-set")
    assert response.status_code == 200
    rows = db.query(DBLineupSlot).filter_by(team_id=team.id).all()
    assert len(rows) == 3
    assert {r.slot for r in rows} == {"QB", "RB", "BENCH"}
    assert all(r.set_by == "auto" for r in rows)


def test_manual_lineup_save_marks_rows_user_set(db, team):
    players = db.query(DBPlayer).order_by(DBPlayer.id).all()
    response = client.post(f"/season/s1/teams/{team.id}/lineup", json={
        "week": WEEK,
        "slots": [
            {"player_id": players[0].id, "slot": "QB"},
            {"player_id": players[1].id, "slot": "RB"},
            {"player_id": players[2].id, "slot": "BENCH"},
        ],
    })
    assert response.status_code == 200
    rows = db.query(DBLineupSlot).filter_by(team_id=team.id).all()
    assert all(r.set_by == "user" for r in rows)


def test_an_illegal_manual_lineup_is_refused(db, team):
    players = db.query(DBPlayer).order_by(DBPlayer.id).all()
    response = client.post(f"/season/s1/teams/{team.id}/lineup", json={
        "week": WEEK,
        "slots": [
            {"player_id": players[0].id, "slot": "RB"},
            {"player_id": players[1].id, "slot": "RB"},
            {"player_id": players[2].id, "slot": "BENCH"},
        ],
    })
    assert response.status_code == 400


def test_team_from_another_league_404s(db, team):
    assert client.get(f"/season/other/teams/{team.id}").status_code == 404
