import pytest
from unittest.mock import Mock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from pigskin_mastermind.models.database import Base, DBTeam, DBPlayer
from pigskin_mastermind.services.espn_sync import ESPNSyncService

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def db():
    session = TestSessionLocal()
    yield session
    session.close()


@patch('pigskin_mastermind.services.espn_sync.League')
def test_import_team_from_espn(mock_league, db):
    """Test importing a team from ESPN"""
    mock_team = Mock()
    mock_team.team_id = 1
    mock_team.team_name = "ESPN Team"
    mock_team.owners = [{'displayName': 'ESPN Owner', 'firstName': 'ESPN', 'lastName': 'Owner'}]
    mock_team.wins = 5
    mock_team.losses = 3
    mock_team.ties = 0
    mock_team.points_for = 950.5

    mock_player = Mock()
    mock_player.playerId = 12345
    mock_player.name = "Patrick Mahomes"
    mock_player.position = "QB"
    mock_player.proTeam = "KC"
    mock_player.projected_points = 25.5
    mock_player.points = 22.3
    mock_player.stats = {}

    mock_team.roster = [mock_player]
    mock_league.return_value.teams = [mock_team]

    service = ESPNSyncService(db)
    result = service.import_team(
        league_id="123456",
        team_id=1,
        espn_s2="test_s2",
        swid="test_swid",
        year=2024
    )

    assert result is not None
    assert result.name == "ESPN Team"

    db_team = db.query(DBTeam).filter_by(espn_team_id="1").first()
    assert db_team is not None
    assert db_team.name == "ESPN Team"
    assert db_team.wins == 5

    db_player = db.query(DBPlayer).filter_by(player_id="espn_12345").first()
    assert db_player is not None
    assert db_player.name == "Patrick Mahomes"
    assert db_player.position == "QB"


@patch('pigskin_mastermind.services.espn_sync.League')
def test_import_team_not_found(mock_league, db):
    """Test importing a team that doesn't exist in the league"""
    mock_league.return_value.teams = []

    service = ESPNSyncService(db)
    with pytest.raises(ValueError, match="Team 99 not found"):
        service.import_team(
            league_id="123456",
            team_id=99,
            espn_s2="test_s2",
            swid="test_swid",
        )
