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
