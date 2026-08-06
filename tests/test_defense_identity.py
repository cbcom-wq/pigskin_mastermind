"""Team defenses are identified by their NFL team, never by name.

Every source names a defense differently — ESPN says "Falcons D/ST", FFC says
"Atlanta Defense", nflverse has no entry at all. Name matching therefore always
fails, and each import creates yet another row for the same defense.
"""

import json

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer, DBPlayerSeasonStats
from pigskin_mastermind.services.adp_service import ADPService
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


class TestResolveDefenseByTeam:
    def test_finds_espn_defense_from_an_ffc_style_name(self, db):
        db.add(DBPlayer(player_id="espn_-16001", name="Falcons D/ST",
                        position="DEF", nfl_team="ATL"))
        db.commit()

        found = PlayerIdentityService(db).resolve(
            name="Atlanta Defense", position="DEF", nfl_team="ATL"
        )

        assert found is not None
        assert found.player_id == "espn_-16001"

    def test_matches_across_team_abbreviation_spellings(self, db):
        db.add(DBPlayer(player_id="espn_-16028", name="Commanders D/ST",
                        position="DEF", nfl_team="WAS"))
        db.commit()

        found = PlayerIdentityService(db).resolve(
            name="Washington Defense", position="DEF", nfl_team="WSH"
        )

        assert found is not None
        assert found.player_id == "espn_-16028"

    def test_does_not_match_a_different_team(self, db):
        db.add(DBPlayer(player_id="espn_-16001", name="Falcons D/ST",
                        position="DEF", nfl_team="ATL"))
        db.commit()

        found = PlayerIdentityService(db).resolve(
            name="Buffalo Defense", position="DEF", nfl_team="BUF"
        )

        assert found is None

    def test_team_matching_does_not_apply_to_offensive_players(self, db):
        """Two RBs on one team must not collapse into each other."""
        db.add(DBPlayer(player_id="espn_1", name="Bijan Robinson",
                        position="RB", nfl_team="ATL"))
        db.commit()

        found = PlayerIdentityService(db).resolve(
            name="Tyler Allgeier", position="RB", nfl_team="ATL"
        )

        assert found is None


def _ffc_response(players):
    return json.dumps({"status": "Success", "players": players}).encode("utf-8")


class TestFFCImportReusesExistingDefense:
    def test_import_does_not_create_a_second_defense_row(self, db):
        """The regression that put the DB in this state to begin with."""
        db.add(DBPlayer(player_id="espn_-16001", name="Falcons D/ST",
                        position="DEF", nfl_team="ATL"))
        db.commit()
        svc = ADPService(db)
        payload = [{"player_id": 1334, "name": "Atlanta Defense", "position": "DEF",
                    "team": "ATL", "adp": 145.2, "times_drafted": 10,
                    "high": 130, "low": 160, "stdev": 8.0, "bye": 5}]
        mock_resp = MagicMock()
        mock_resp.read.return_value = _ffc_response(payload)
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp):
            result = svc.import_from_ffc(year=YEAR)

        assert result["created"] == 0
        assert db.query(DBPlayer).filter_by(position="DEF").count() == 1

        player = db.query(DBPlayer).filter_by(player_id="espn_-16001").first()
        season = db.query(DBPlayerSeasonStats).filter_by(
            player_id=player.id, year=YEAR
        ).first()
        assert season.adp == pytest.approx(145.2)
