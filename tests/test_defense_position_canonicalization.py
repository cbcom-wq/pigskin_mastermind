"""Team defenses must be stored as DEF, and non-fantasy positions must stay out of the pool.

ESPN spells team defenses ``D/ST``. A row stored that way is silently dropped by
``ADPService.get_adp_for_draft_pool()`` (which filters on ``FANTASY_POSITIONS``),
so the defense gets ADP written to it and then never appears in a draft.
"""

import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer, DBPlayerSeasonStats
from pigskin_mastermind.services.adp_service import ADPService
from pigskin_mastermind.services.espn_sync import ESPNSyncService
from pigskin_mastermind.services.player_identity import PlayerIdentityService

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026


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


class _FakeBoxPlayer:
    """Minimal stand-in for espn_api's BoxPlayer."""

    def __init__(self, position, player_id=-16001, name="Falcons D/ST", pro_team="ATL"):
        self.playerId = player_id
        self.name = name
        self.position = position
        self.proTeam = pro_team
        self.points = 12.0
        self.projected_points = 8.0
        self.slot_position = "D/ST"
        self.stats = {}


def _sync_service(db):
    svc = ESPNSyncService.__new__(ESPNSyncService)
    svc.db = db
    svc.identity = PlayerIdentityService(db)
    return svc


class TestBoxScorePathNormalizesPosition:
    def test_dst_from_box_score_is_stored_as_def(self, db):
        """The leak: this path created players with a raw, unnormalized position."""
        svc = _sync_service(db)

        svc._create_player_from_box(_FakeBoxPlayer("D/ST"), team_db_id=None)
        db.commit()

        player = db.query(DBPlayer).filter_by(player_id="espn_-16001").first()
        assert player.position == "DEF"

    def test_offensive_position_is_unchanged(self, db):
        svc = _sync_service(db)

        svc._create_player_from_box(
            _FakeBoxPlayer("RB", player_id=123, name="Bijan Robinson"), team_db_id=None
        )
        db.commit()

        player = db.query(DBPlayer).filter_by(player_id="espn_123").first()
        assert player.position == "RB"


class TestPoolExcludesNonFantasyPositions:
    def test_tail_skips_a_player_stored_at_a_non_fantasy_position(self, db):
        """A real DT must never receive draft ADP, however ESPN labels the board entry.

        ESPN's board occasionally returns an IDP under a fantasy slot id. Resolving
        by espn_id then lands on a row stored as 'DT', and writing ADP there
        produces a season row that the pool filter silently discards.
        """
        db.add(DBPlayer(player_id="espn_4373684", name="Scott Matlock",
                        position="DT", nfl_team="LAC", espn_id="4373684"))
        db.commit()
        svc = ADPService(db)
        board = [{
            "id": "espn_4373684", "name": "Scott Matlock", "position": "DEF",
            "nfl_team": "LAC", "projected_points": 1.0, "adp_rank": 170.0,
        }]

        with patch("pigskin_mastermind.services.adp_service.fetch_espn_adp",
                   return_value=board):
            svc.import_espn_tail(year=YEAR)

        player = db.query(DBPlayer).filter_by(player_id="espn_4373684").first()
        rows = db.query(DBPlayerSeasonStats).filter_by(player_id=player.id).all()
        assert rows == []

    def test_tail_still_writes_for_a_canonical_defense(self, db):
        db.add(DBPlayer(player_id="espn_-16001", name="Falcons D/ST",
                        position="DEF", nfl_team="ATL", espn_id="-16001"))
        db.commit()
        svc = ADPService(db)
        board = [{
            "id": "espn_-16001", "name": "Falcons D/ST", "position": "DEF",
            "nfl_team": "ATL", "projected_points": 6.0, "adp_rank": 170.0,
        }]

        with patch("pigskin_mastermind.services.adp_service.fetch_espn_adp",
                   return_value=board):
            svc.import_espn_tail(year=YEAR)

        pool_names = [p["name"] for p in svc.get_adp_for_draft_pool(year=YEAR)]
        assert pool_names == ["Falcons D/ST"]
