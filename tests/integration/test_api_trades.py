from fastapi.testclient import TestClient
from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import DBTeam, DBPlayer


def test_trade_page():
    """Test trade analyzer page loads"""
    client = TestClient(app)

    response = client.get("/trades")
    assert response.status_code == 200
    assert b"Trade Analyzer" in response.content


def test_analyze_trade(db):
    """Test trade analysis"""
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.commit()

    p1 = DBPlayer(player_id="p1", name="Player1", position="RB", nfl_team="KC", projected_points=20.0, team_id=team.id)
    p2 = DBPlayer(player_id="p2", name="Player2", position="RB", nfl_team="SF", projected_points=15.0, team_id=team.id)
    db.add(p1)
    db.add(p2)
    db.commit()

    client = TestClient(app)
    response = client.post(
        "/trades/analyze",
        json={
            "team_id": team.id,
            "gives": [p1.id],
            "receives": [p2.id]
        }
    )
    assert response.status_code == 200
    assert b"Trade Analysis" in response.content
