from fastapi.testclient import TestClient
from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import DBTeam
from .conftest import TestSessionLocal


def test_get_teams_empty():
    """Test getting teams when none exist"""
    client = TestClient(app)

    response = client.get("/teams")
    assert response.status_code == 200
    assert b"Teams" in response.content


def test_get_teams_with_data(db):
    """Test getting teams with existing data"""
    team = DBTeam(
        team_id="t1",
        name="Test Team",
        owner="Test Owner",
        wins=5,
        losses=3
    )
    db.add(team)
    db.commit()

    client = TestClient(app)
    response = client.get("/teams")
    assert response.status_code == 200
    assert b"Test Team" in response.content


def test_create_team(db):
    """Test creating a new team"""
    client = TestClient(app)
    response = client.post(
        "/teams",
        data={
            "name": "New Team",
            "owner": "John Doe",
            "league_id": "league1"
        },
        follow_redirects=False
    )
    assert response.status_code in [200, 201, 303]

    # Verify team was created
    team = db.query(DBTeam).filter_by(name="New Team").first()
    assert team is not None
    assert team.owner == "John Doe"
