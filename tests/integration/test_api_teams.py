from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pigskin_mastermind.api.main import app
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import Base, DBTeam

# Test database setup - StaticPool ensures all connections share same in-memory DB
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


def test_get_teams_empty():
    """Test getting teams when none exist"""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    client = TestClient(app)

    response = client.get("/teams")
    assert response.status_code == 200
    assert b"Teams" in response.content


def test_get_teams_with_data():
    """Test getting teams with existing data"""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    db = TestSessionLocal()
    team = DBTeam(
        team_id="t1",
        name="Test Team",
        owner="Test Owner",
        wins=5,
        losses=3
    )
    db.add(team)
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/teams")
    assert response.status_code == 200
    assert b"Test Team" in response.content


def test_create_team():
    """Test creating a new team"""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

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
    db = TestSessionLocal()
    team = db.query(DBTeam).filter_by(name="New Team").first()
    assert team is not None
    assert team.owner == "John Doe"
    db.close()
