"""Route tests for the post-draft recap page.

The recap only exists for a draft the running process still holds in memory,
so these tests drive a real draft to completion through the engine's public
API rather than hand-building state.
"""

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.services.mock_draft import draft_engine


POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "K", "DEF", "RB", "WR"]


def _pool():
    return [
        {
            "id": f"ffc_{n}",
            "name": f"Player {n}",
            "position": POSITIONS[n % len(POSITIONS)],
            "nfl_team": "KC",
            "projected_points": 200.0 - n,
            "adp_rank": float(n + 1),
        }
        for n in range(60)
    ]


def _start_draft(rounds=2):
    return draft_engine.create_draft(
        num_teams=2,
        num_rounds=rounds,
        user_pick_position=1,
        player_pool=_pool(),
        lineup_slots={"QB": 1, "RB": 1, "BENCH": 2},
    )


def _play_to_completion(state):
    """Advance AI picks and auto-pick for the user until the draft ends."""
    draft_id = state["draft_id"]
    while True:
        current = draft_engine.get_draft(draft_id)
        if current["status"] == "complete":
            return current
        if str(current["current_slot"]) == str(current["user_pick_position"]):
            draft_engine.make_user_pick(
                draft_id, current["available_players"][0]["id"], advance_ai=False
            )
        else:
            draft_engine.advance_one_ai_pick(draft_id)


@pytest.fixture
def completed_draft():
    return _play_to_completion(_start_draft())


def test_recap_page_renders_for_a_completed_draft(completed_draft):
    client = TestClient(app)

    response = client.get(f"/draft/recap/{completed_draft['draft_id']}")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_recap_page_returns_404_for_an_unknown_draft():
    """Draft state is in memory only, so a restart makes every link stale."""
    client = TestClient(app)

    response = client.get("/draft/recap/does-not-exist")

    assert response.status_code == 404
    assert "no longer available" in response.text.lower()


def test_recap_page_sends_an_unfinished_draft_back_to_the_board():
    state = _start_draft()
    client = TestClient(app)

    response = client.get(f"/draft/recap/{state['draft_id']}", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == f"/draft/board/{state['draft_id']}"


def test_simulate_rejects_a_player_who_is_not_on_the_users_roster(completed_draft):
    client = TestClient(app)

    response = client.post(f"/draft/recap/{completed_draft['draft_id']}/simulate/999999")

    assert response.status_code == 404


def test_simulate_returns_404_for_an_unknown_draft():
    client = TestClient(app)

    response = client.post("/draft/recap/does-not-exist/simulate/1")

    assert response.status_code == 404


def test_simulate_answers_for_a_player_on_the_roster(completed_draft):
    """The endpoint always answers; a simulation that cannot run says so in
    the body rather than failing the request, so the card keeps its game-log
    numbers on screen."""
    user_slot = str(completed_draft["user_pick_position"])
    roster = completed_draft["rosters"][user_slot]
    # This pool has no db_id, matching the ESPN-ADP path.
    draft_engine._drafts[completed_draft["draft_id"]]["rosters"][user_slot][0]["db_id"] = 4242
    client = TestClient(app)

    response = client.post(
        f"/draft/recap/{completed_draft['draft_id']}/simulate/4242"
    )

    assert roster, "draft produced an empty roster"
    assert response.status_code == 200
    assert "ok" in response.json()
