from fastapi.testclient import TestClient
from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import DBTeam, DBPlayer


def test_lineup_page():
    """Test lineup optimizer page loads"""
    client = TestClient(app)

    response = client.get("/lineups")
    assert response.status_code == 200
    assert b"Lineup Optimizer" in response.content


def test_optimize_lineup(db):
    """Test lineup optimization"""
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.commit()

    players = [
        DBPlayer(player_id="p1", name="QB1", position="QB", nfl_team="KC", projected_points=25.0, team_id=team.id),
        DBPlayer(player_id="p2", name="RB1", position="RB", nfl_team="SF", projected_points=20.0, team_id=team.id),
        DBPlayer(player_id="p3", name="RB2", position="RB", nfl_team="DAL", projected_points=18.0, team_id=team.id),
        DBPlayer(player_id="p4", name="WR1", position="WR", nfl_team="MIA", projected_points=22.0, team_id=team.id),
        DBPlayer(player_id="p5", name="WR2", position="WR", nfl_team="BUF", projected_points=19.0, team_id=team.id),
        DBPlayer(player_id="p6", name="TE1", position="TE", nfl_team="KC", projected_points=15.0, team_id=team.id),
        DBPlayer(player_id="p7", name="K1", position="K", nfl_team="BAL", projected_points=10.0, team_id=team.id),
        DBPlayer(player_id="p8", name="DEF1", position="DEF", nfl_team="SF", projected_points=12.0, team_id=team.id),
    ]
    for player in players:
        db.add(player)
    db.commit()

    client = TestClient(app)
    response = client.post(f"/lineups/{team.id}/optimize")
    assert response.status_code == 200
    assert b"QB" in response.content
