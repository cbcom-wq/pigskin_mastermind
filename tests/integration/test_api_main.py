from fastapi.testclient import TestClient
from pigskin_mastermind.api.main import app


def test_health_check():
    """Test health check endpoint"""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_dashboard():
    """Test dashboard page loads"""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert b"Dashboard" in response.content


def test_dashboard_stat_cards_are_links():
    """Test that stat cards with hover effects are clickable links."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    content = response.text
    # Teams card links to /teams
    assert 'href="/teams"' in content
    # Players card links to /players
    assert 'href="/players"' in content


def test_dashboard_recent_team_cards_are_links(db):
    """Test that recent team cards link to their detail pages."""
    from pigskin_mastermind.models.database import DBTeam
    team = DBTeam(team_id="test-1", name="Test Team", owner="Owner", is_user_team=True)
    db.add(team)
    db.commit()
    db.refresh(team)

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert f'href="/teams/{team.id}"' in response.text
