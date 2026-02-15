from pigskin_mastermind.api.database import get_db, engine
from pigskin_mastermind.models.database import Base


def test_database_session():
    """Test database session creation"""
    Base.metadata.create_all(bind=engine)

    db = next(get_db())
    assert db is not None
    db.close()
