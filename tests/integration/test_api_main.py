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


def test_dashboard_sidebar_has_projection_tuner_link():
    """Projection tuner should be discoverable from sidebar navigation."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert 'href="/projection-tuner"' in response.text


def test_dashboard_league_card_links_to_the_team_page(db):
    """A user team's league card on the dashboard links to its detail page.

    The old "Recent Teams" grid this test named is gone -- replaced by one
    league card per user team in the pulse band -- but a team still needs to
    be reachable from the dashboard. ``?back=/`` is appended by
    ``services/dashboard.py::team_url`` per the app's back-navigation
    convention, so the assertion checks for the href as a substring rather
    than an exact match.
    """
    from pigskin_mastermind.models.database import DBTeam

    team = DBTeam(team_id="test-1", name="Test Team", owner="Owner", is_user_team=True)
    db.add(team)
    db.commit()
    db.refresh(team)

    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert f'href="/teams/{team.id}?back=/"' in response.text
