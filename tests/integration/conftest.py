import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pigskin_mastermind.api.main import app
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import Base

# Shared test database - StaticPool ensures all connections share same in-memory DB
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


@pytest.fixture(autouse=True)
def override_db_dependency():
    """Install the test DB override for integration tests only.

    Assigning this at module import leaked the override into every other test
    module that builds a TestClient — they inherited a database whose tables
    reset_db had already dropped. Setting and unsetting it per test keeps the
    blast radius inside this directory.
    """
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(autouse=True)
def reset_db():
    """Reset database before each test."""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db():
    """Get a test database session."""
    session = TestSessionLocal()
    yield session
    session.close()
