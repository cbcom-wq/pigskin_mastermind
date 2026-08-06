"""ESPN sync must write canonical NFL team abbreviations.

The vendored ``espn_api`` library reports Washington as ``WSH`` while FFC,
``nfl_data_py``, and the draft pool use ``WAS``.  If the sync writes ``WSH``
back, it silently undoes the ADP importer's canonicalization and the same
franchise lives under two spellings again.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer
from pigskin_mastermind.services.espn_sync import ESPNSyncService
from pigskin_mastermind.services.player_identity import PlayerIdentityService

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


class _FakeESPNPlayer:
    """Minimal stand-in for espn_api's Player object."""

    def __init__(self, pro_team, player_id=555, name="Jayden Daniels", position="QB"):
        self.playerId = player_id
        self.name = name
        self.position = position
        self.proTeam = pro_team
        self.projected_points = 18.0
        self.points = 17.0
        self.stats = {}
        self.injuryStatus = "ACTIVE"


def _service(db):
    """Build the service without running __init__ (which needs ESPN creds)."""
    svc = ESPNSyncService.__new__(ESPNSyncService)
    svc.db = db
    svc.identity = PlayerIdentityService(db)
    return svc


class TestImportPlayerTeamNormalization:
    def test_wsh_is_stored_as_was(self, db):
        svc = _service(db)

        svc._import_player(_FakeESPNPlayer("WSH"), team_db_id=None)
        db.commit()

        player = db.query(DBPlayer).filter_by(player_id="espn_555").first()
        assert player.nfl_team == "WAS"

    def test_free_agent_does_not_wipe_existing_team(self, db):
        db.add(DBPlayer(player_id="espn_555", name="Jayden Daniels",
                        position="QB", nfl_team="WAS"))
        db.commit()

        svc = _service(db)
        svc._import_player(_FakeESPNPlayer("FA"), team_db_id=None)
        db.commit()

        player = db.query(DBPlayer).filter_by(player_id="espn_555").first()
        assert player.nfl_team == "WAS"

    def test_canonical_team_is_written_unchanged(self, db):
        svc = _service(db)

        svc._import_player(_FakeESPNPlayer("KC"), team_db_id=None)
        db.commit()

        player = db.query(DBPlayer).filter_by(player_id="espn_555").first()
        assert player.nfl_team == "KC"
