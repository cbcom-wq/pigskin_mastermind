"""Tests for ADPService.import_espn_tail – deep draft pool beyond FFC's cap.

FFC's API returns ~250 players regardless of league size, but a 12-team
15-round draft is 180 picks, so the board empties before the draft ends.
The tail import backfills from ESPN's 1000-player board.
"""

import pytest
from unittest.mock import patch
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


def _seed_ffc_board(db):
    """Two players already on the FFC board, with real consensus ADP."""
    star = DBPlayer(player_id="espn_101", name="Saquon Barkley", position="RB", nfl_team="PHI")
    mid = DBPlayer(player_id="espn_102", name="Jaylen Waddle", position="WR", nfl_team="MIA")
    db.add_all([star, mid])
    db.flush()
    db.add_all([
        DBPlayerSeasonStats(player_id=star.id, year=YEAR, adp=1.6,
                            adp_source=ADPService.ADP_SOURCE_LABEL),
        DBPlayerSeasonStats(player_id=mid.id, year=YEAR, adp=60.0,
                            adp_source=ADPService.ADP_SOURCE_LABEL),
    ])
    db.commit()
    return star, mid


def _espn_board(entries):
    """Shape matching mock_draft.fetch_espn_adp's return value."""
    return [
        {
            "id": f"espn_{e['id']}",
            "name": e["name"],
            "position": e["position"],
            "nfl_team": e.get("team", "FA"),
            "projected_points": e.get("proj", 0.0),
            "adp_rank": e.get("adp", 170.0),
        }
        for e in entries
    ]


def _patch_espn(board):
    return patch(
        "pigskin_mastermind.services.adp_service.fetch_espn_adp",
        return_value=board,
    )


class TestTailOrdering:
    def test_tail_adp_sorts_below_every_ffc_player(self, db):
        _seed_ffc_board(db)
        svc = ADPService(db)
        board = _espn_board([
            {"id": 900, "name": "Deep Sleeper", "position": "WR", "team": "NYJ", "proj": 4.0},
            {"id": 901, "name": "Backup Back", "position": "RB", "team": "LV", "proj": 6.0},
        ])

        with _patch_espn(board):
            svc.import_espn_tail(year=YEAR)

        max_ffc = 60.0
        tail_rows = (
            db.query(DBPlayerSeasonStats)
            .filter(DBPlayerSeasonStats.adp_source == "espn_tail")
            .all()
        )
        assert len(tail_rows) == 2
        assert all(row.adp > max_ffc for row in tail_rows)

    def test_tail_is_ordered_by_projection_not_espn_tied_adp(self, db):
        """~789 ESPN players share a placeholder ADP of 170, so ADP can't order them."""
        _seed_ffc_board(db)
        svc = ADPService(db)
        board = _espn_board([
            # Same placeholder ADP, different projections, worst listed first.
            {"id": 900, "name": "Worse Player", "position": "WR", "team": "NYJ",
             "proj": 2.0, "adp": 170.0},
            {"id": 901, "name": "Better Player", "position": "RB", "team": "LV",
             "proj": 9.0, "adp": 170.0},
        ])

        with _patch_espn(board):
            svc.import_espn_tail(year=YEAR)

        def adp_of(name):
            player = db.query(DBPlayer).filter_by(name=name).first()
            return (
                db.query(DBPlayerSeasonStats)
                .filter_by(player_id=player.id, year=YEAR)
                .first()
                .adp
            )

        assert adp_of("Better Player") < adp_of("Worse Player")


class TestTailMembership:
    def test_ffc_board_player_gets_no_tail_adp_row(self, db):
        star, _ = _seed_ffc_board(db)
        svc = ADPService(db)
        board = _espn_board([
            {"id": 101, "name": "Saquon Barkley", "position": "RB", "team": "PHI", "proj": 20.0},
        ])

        with _patch_espn(board):
            result = svc.import_espn_tail(year=YEAR)

        rows = db.query(DBPlayerSeasonStats).filter_by(player_id=star.id, year=YEAR).all()
        assert len(rows) == 1
        assert rows[0].adp_source == ADPService.ADP_SOURCE_LABEL
        assert rows[0].adp == pytest.approx(1.6)  # consensus ADP untouched
        assert result["imported"] == 0

    def test_ffc_board_player_still_gets_a_team_update(self, db):
        """The regression that would silently reintroduce the stale-team bug.

        Skipping FFC-board players entirely would mean ESPN never corrects the
        teams of the top ~250 players — exactly the ones that were reported wrong.
        """
        _, waddle = _seed_ffc_board(db)
        svc = ADPService(db)
        board = _espn_board([
            {"id": 102, "name": "Jaylen Waddle", "position": "WR", "team": "DEN", "proj": 12.0},
        ])

        with _patch_espn(board):
            result = svc.import_espn_tail(year=YEAR)

        db.refresh(waddle)
        assert waddle.nfl_team == "DEN"
        assert result["team_changes"] == [
            {"name": "Jaylen Waddle", "old": "MIA", "new": "DEN"}
        ]

    def test_existing_player_is_reused_not_duplicated(self, db):
        _seed_ffc_board(db)
        db.add(DBPlayer(player_id="espn_777", name="Bench Warmer", position="TE", nfl_team="GB"))
        db.commit()
        svc = ADPService(db)
        board = _espn_board([
            {"id": 777, "name": "Bench Warmer", "position": "TE", "team": "GB", "proj": 3.0},
        ])

        with _patch_espn(board):
            svc.import_espn_tail(year=YEAR)

        assert db.query(DBPlayer).filter_by(name="Bench Warmer").count() == 1

    def test_tail_rows_are_tagged_espn_tail(self, db):
        _seed_ffc_board(db)
        svc = ADPService(db)
        board = _espn_board([
            {"id": 900, "name": "Deep Sleeper", "position": "WR", "team": "NYJ", "proj": 4.0},
        ])

        with _patch_espn(board):
            svc.import_espn_tail(year=YEAR)

        player = db.query(DBPlayer).filter_by(name="Deep Sleeper").first()
        row = db.query(DBPlayerSeasonStats).filter_by(player_id=player.id, year=YEAR).first()
        assert row.adp_source == "espn_tail"

    def test_returns_error_when_espn_unavailable(self, db):
        _seed_ffc_board(db)
        svc = ADPService(db)

        with _patch_espn(None):
            result = svc.import_espn_tail(year=YEAR)

        assert result["error"]
        assert result["imported"] == 0


class TestDraftPoolReadPath:
    def test_pool_returns_both_sources_ordered_by_adp(self, db):
        _seed_ffc_board(db)
        svc = ADPService(db)
        board = _espn_board([
            {"id": 900, "name": "Deep Sleeper", "position": "WR", "team": "NYJ", "proj": 4.0},
            {"id": 901, "name": "Backup Back", "position": "RB", "team": "LV", "proj": 6.0},
        ])

        with _patch_espn(board):
            svc.import_espn_tail(year=YEAR)

        pool = svc.get_adp_for_draft_pool(year=YEAR)
        names = [p["name"] for p in pool]

        assert names[:2] == ["Saquon Barkley", "Jaylen Waddle"]  # FFC board first
        assert set(names[2:]) == {"Backup Back", "Deep Sleeper"}
        assert [p["adp_rank"] for p in pool] == sorted(p["adp_rank"] for p in pool)

    def test_consensus_rankings_view_excludes_the_tail(self, db):
        """/adp/rankings is a consensus ADP view — synthetic values don't belong."""
        _seed_ffc_board(db)
        svc = ADPService(db)
        board = _espn_board([
            {"id": 900, "name": "Deep Sleeper", "position": "WR", "team": "NYJ", "proj": 4.0},
        ])

        with _patch_espn(board):
            svc.import_espn_tail(year=YEAR)

        names = [p["name"] for p in svc.get_all_adp(year=YEAR)]
        assert "Deep Sleeper" not in names
