"""Tests for ADPService.refresh_draft_data and freshness metadata."""

import json
from datetime import datetime, timedelta

import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer, DBPlayerSeasonStats
from pigskin_mastermind.services.adp_service import ADPService

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


def _ffc_payload(players):
    return json.dumps({"status": "Success", "players": players}).encode("utf-8")


def _patch_ffc(players):
    mock_resp = MagicMock()
    mock_resp.read.return_value = _ffc_payload(players)
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return patch("pigskin_mastermind.services.adp_service.urlopen", return_value=mock_resp)


def _patch_espn(board):
    return patch("pigskin_mastermind.services.adp_service.fetch_espn_adp", return_value=board)


_FFC_ONE = [{"player_id": 1, "name": "Saquon Barkley", "position": "RB", "team": "PHI",
             "adp": 1.6, "times_drafted": 19, "high": 1, "low": 3, "stdev": 0.8, "bye": 9}]

_ESPN_TAIL = [{"id": "espn_900", "name": "Deep Sleeper", "position": "WR",
               "nfl_team": "NYJ", "projected_points": 4.0, "adp_rank": 170.0}]


class TestRefreshDraftData:
    def test_runs_both_imports_and_merges_counts(self, db):
        svc = ADPService(db)
        with _patch_ffc(_FFC_ONE), _patch_espn(_ESPN_TAIL):
            result = svc.refresh_draft_data(year=YEAR, scoring="ppr")

        assert result["imported"] == 1
        assert result["tail_imported"] == 1
        assert "error" not in result
        assert "tail_error" not in result

        pool = svc.get_adp_for_draft_pool(year=YEAR)
        assert [p["name"] for p in pool] == ["Saquon Barkley", "Deep Sleeper"]

    def test_espn_failure_is_non_fatal(self, db):
        """A refresh must never leave the pool worse than it started."""
        svc = ADPService(db)
        with _patch_ffc(_FFC_ONE), _patch_espn(None):
            result = svc.refresh_draft_data(year=YEAR, scoring="ppr")

        assert result["imported"] == 1        # FFC board still landed
        assert result["tail_error"]
        assert "error" not in result

        pool = svc.get_adp_for_draft_pool(year=YEAR)
        assert [p["name"] for p in pool] == ["Saquon Barkley"]

    def test_ffc_failure_reports_error(self, db):
        svc = ADPService(db)
        with patch("pigskin_mastermind.services.adp_service.urlopen",
                   side_effect=OSError("boom")), _patch_espn(_ESPN_TAIL):
            result = svc.refresh_draft_data(year=YEAR, scoring="ppr")

        assert result["error"]

    def test_team_change_seen_by_both_sources_is_reported_once(self, db):
        db.add(DBPlayer(player_id="espn_500", name="Jaylen Waddle", position="WR",
                        nfl_team="MIA"))
        db.commit()
        svc = ADPService(db)
        ffc = [{"player_id": 500, "name": "Jaylen Waddle", "position": "WR", "team": "DEN",
                "adp": 60.0, "times_drafted": 30, "high": 50, "low": 70, "stdev": 3.0, "bye": 12}]
        espn = [{"id": "espn_500", "name": "Jaylen Waddle", "position": "WR",
                 "nfl_team": "DEN", "projected_points": 12.0, "adp_rank": 60.0}]

        with _patch_ffc(ffc), _patch_espn(espn):
            result = svc.refresh_draft_data(year=YEAR, scoring="ppr")

        assert result["team_changes"] == [
            {"name": "Jaylen Waddle", "old": "MIA", "new": "DEN"}
        ]

    def test_espn_wins_when_the_two_sources_disagree_on_team(self, db):
        db.add(DBPlayer(player_id="espn_500", name="Jaylen Waddle", position="WR",
                        nfl_team="MIA"))
        db.commit()
        svc = ADPService(db)
        ffc = [{"player_id": 500, "name": "Jaylen Waddle", "position": "WR", "team": "BUF",
                "adp": 60.0, "times_drafted": 30, "high": 50, "low": 70, "stdev": 3.0, "bye": 12}]
        espn = [{"id": "espn_500", "name": "Jaylen Waddle", "position": "WR",
                 "nfl_team": "DEN", "projected_points": 12.0, "adp_rank": 60.0}]

        with _patch_ffc(ffc), _patch_espn(espn):
            svc.refresh_draft_data(year=YEAR, scoring="ppr")

        player = db.query(DBPlayer).filter_by(name="Jaylen Waddle").first()
        assert player.nfl_team == "DEN"

    def test_canonicalizes_stored_teams(self, db):
        db.add(DBPlayer(player_id="espn_600", name="Nobody Special", position="TE",
                        nfl_team="WSH"))
        db.commit()
        svc = ADPService(db)

        with _patch_ffc(_FFC_ONE), _patch_espn(_ESPN_TAIL):
            svc.refresh_draft_data(year=YEAR, scoring="ppr")

        player = db.query(DBPlayer).filter_by(name="Nobody Special").first()
        assert player.nfl_team == "WAS"


def _seed_row(db, source, updated_at):
    player = DBPlayer(player_id=f"x_{source}_{updated_at.timestamp()}",
                      name=f"P {source}", position="RB", nfl_team="KC")
    db.add(player)
    db.flush()
    db.add(DBPlayerSeasonStats(player_id=player.id, year=YEAR, adp=10.0,
                               adp_source=source, updated_at=updated_at))
    db.commit()


class TestFreshnessMetadata:
    def test_never_imported_is_stale_with_no_age(self, db):
        meta = ADPService(db).get_adp_metadata(year=YEAR)
        assert meta["stale"] is True
        assert meta["age_days"] is None
        assert meta["last_updated"] is None

    def test_seven_days_old_is_not_stale(self, db):
        _seed_row(db, ADPService.ADP_SOURCE_LABEL, datetime.utcnow() - timedelta(days=7))
        meta = ADPService(db).get_adp_metadata(year=YEAR)
        assert meta["age_days"] == 7
        assert meta["stale"] is False

    def test_eight_days_old_is_stale(self, db):
        _seed_row(db, ADPService.ADP_SOURCE_LABEL, datetime.utcnow() - timedelta(days=8))
        meta = ADPService(db).get_adp_metadata(year=YEAR)
        assert meta["age_days"] == 8
        assert meta["stale"] is True

    def test_counts_each_source_separately(self, db):
        now = datetime.utcnow()
        _seed_row(db, ADPService.ADP_SOURCE_LABEL, now)
        _seed_row(db, ADPService.ESPN_TAIL_SOURCE_LABEL, now)
        _seed_row(db, ADPService.ESPN_TAIL_SOURCE_LABEL, now - timedelta(seconds=1))

        meta = ADPService(db).get_adp_metadata(year=YEAR)
        assert meta["ffc_count"] == 1
        assert meta["tail_count"] == 2
        assert meta["count"] == 3
