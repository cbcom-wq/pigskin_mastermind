"""Committing a draft through the web layer.

The draft engine is an in-process singleton, so this endpoint is the only way a
draft can become a league — a CLI process cannot see the draft at all.
"""

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import DBLeague, DBPlayer, DBTeam
from pigskin_mastermind.services.mock_draft import draft_engine

client = TestClient(app)


@pytest.fixture
def pool(db):
    players = []
    for i, pos in enumerate(["QB", "RB", "WR", "TE", "QB", "RB", "WR", "TE"]):
        p = DBPlayer(player_id=f"ffc_{i}", name=f"Player {i}",
                     position=pos, nfl_team="ATL")
        db.add(p)
        players.append(p)
    db.commit()
    return [
        {"id": p.player_id, "db_id": p.id, "name": p.name, "position": p.position,
         "nfl_team": p.nfl_team, "projected_points": 100.0 - i,
         "adp_rank": float(i + 1)}
        for i, p in enumerate(players)
    ]


def _finish_draft(pool, num_teams=4):
    state = draft_engine.create_draft(
        num_teams=num_teams, num_rounds=2, user_pick_position=1, player_pool=pool,
    )
    draft_id = state["draft_id"]
    while draft_engine.get_draft(draft_id)["status"] == "in_progress":
        current = draft_engine.get_draft(draft_id)
        if current["current_slot"] == 1:
            draft_engine.make_user_pick(draft_id, current["available_players"][0]["id"])
        else:
            draft_engine.advance_one_ai_pick(draft_id)
    return draft_id


def test_commit_creates_a_league(db, pool):
    draft_id = _finish_draft(pool)
    response = client.post("/season/commit-draft", json={
        "draft_id": draft_id, "name": "Sunday Money",
        "user_team_name": "Brandon's Best", "owner": "Brandon", "year": 2026,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Sunday Money"
    assert body["teams"] == 4
    assert body["redirect_url"].startswith("/season/")

    league = db.query(DBLeague).filter_by(league_id=body["league_id"]).one()
    assert league.kind == "season"
    assert db.query(DBTeam).filter_by(league_id=league.league_id).count() == 4


def test_incomplete_draft_returns_400_with_a_usable_message(db, pool):
    state = draft_engine.create_draft(
        num_teams=4, num_rounds=2, user_pick_position=1, player_pool=pool,
    )
    response = client.post("/season/commit-draft", json={
        "draft_id": state["draft_id"], "name": "L",
        "user_team_name": "M", "owner": "B", "year": 2026,
    })
    assert response.status_code == 400
    assert "not complete" in response.json()["detail"]


def test_unknown_draft_returns_404(db):
    response = client.post("/season/commit-draft", json={
        "draft_id": "nope", "name": "L",
        "user_team_name": "M", "owner": "B", "year": 2026,
    })
    assert response.status_code == 404


def test_unresolved_players_are_listed_in_the_error(db):
    ghosts = [
        {"id": f"espn_g{i}", "name": f"Ghost {i}", "position": "WR",
         "nfl_team": "ZZZ", "projected_points": 10.0, "adp_rank": float(i)}
        for i in range(8)
    ]
    draft_id = _finish_draft(ghosts)
    response = client.post("/season/commit-draft", json={
        "draft_id": draft_id, "name": "L",
        "user_team_name": "M", "owner": "B", "year": 2026,
    })
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert len(detail["unresolved"]) == 8
    assert detail["unresolved"][0]["name"].startswith("Ghost")
