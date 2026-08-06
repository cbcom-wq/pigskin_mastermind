"""merge_duplicates must fold duplicate team defenses together.

ESPN creates ``espn_-160XX`` defense rows, FFC creates ``ffc_13XX`` ones for the
same teams. Their names never match, so before defense-aware identity these
accumulated as two rows per team — and the ESPN one, stored as ``D/ST``, was
silently dropped from the draft pool.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBPlayer, DBPlayerSeasonStats
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


def _seed_defense_pair(db):
    """The exact shape found in the live DB: ESPN D/ST + FFC DEF for one team."""
    espn = DBPlayer(player_id="espn_-16001", name="Falcons D/ST",
                    position="D/ST", nfl_team="ATL")
    ffc = DBPlayer(player_id="ffc_1334", name="Atlanta Defense",
                   position="DEF", nfl_team="ATL")
    db.add_all([espn, ffc])
    db.flush()
    # ESPN row carries the history; FFC row carries the consensus ADP.
    db.add_all([
        DBPlayerSeasonStats(player_id=espn.id, year=2025,
                            fantasy_points_total=15.0, games_played=1),
        DBPlayerSeasonStats(player_id=ffc.id, year=YEAR, adp=145.2,
                            adp_source="fantasyfootballcalculator"),
    ])
    db.commit()
    return espn, ffc


class TestCanonicalizePositions:
    def test_dst_becomes_def(self, db):
        db.add(DBPlayer(player_id="espn_-16001", name="Falcons D/ST",
                        position="D/ST", nfl_team="ATL"))
        db.commit()

        fixed = PlayerIdentityService(db).canonicalize_positions()

        assert fixed == 1
        assert db.query(DBPlayer).first().position == "DEF"

    def test_real_defensive_position_is_left_alone(self, db):
        """A DT is a real player at a non-fantasy position, not a team defense."""
        db.add(DBPlayer(player_id="espn_4373684", name="Scott Matlock",
                        position="DT", nfl_team="LAC"))
        db.commit()

        fixed = PlayerIdentityService(db).canonicalize_positions()

        assert fixed == 0
        assert db.query(DBPlayer).first().position == "DT"

    def test_canonical_positions_are_untouched(self, db):
        db.add(DBPlayer(player_id="espn_1", name="Bijan Robinson",
                        position="RB", nfl_team="ATL"))
        db.commit()

        assert PlayerIdentityService(db).canonicalize_positions() == 0


class TestFindDefenseExcludesSelf:
    def test_exclude_id_skips_the_named_row(self, db):
        espn, ffc = _seed_defense_pair(db)
        PlayerIdentityService(db).canonicalize_positions()
        svc = PlayerIdentityService(db)

        found = svc.find_defense("DEF", "ATL", exclude_id=ffc.id)

        assert found is not None
        assert found.id == espn.id


class TestMergeDuplicateDefenses:
    def test_ffc_defense_folds_into_the_espn_row(self, db):
        espn, ffc = _seed_defense_pair(db)
        svc = PlayerIdentityService(db)

        report = svc.merge_duplicates(dry_run=False)

        assert report.merged == 1
        remaining = db.query(DBPlayer).filter(DBPlayer.position == "DEF").all()
        assert len(remaining) == 1
        assert remaining[0].player_id == "espn_-16001"

    def test_consensus_adp_survives_the_merge(self, db):
        espn, _ = _seed_defense_pair(db)
        svc = PlayerIdentityService(db)

        svc.merge_duplicates(dry_run=False)

        survivor = db.query(DBPlayer).filter_by(player_id="espn_-16001").first()
        season = db.query(DBPlayerSeasonStats).filter_by(
            player_id=survivor.id, year=YEAR
        ).first()
        assert season is not None
        assert season.adp == pytest.approx(145.2)
        assert season.adp_source == "fantasyfootballcalculator"

    def test_consensus_adp_beats_a_colliding_synthetic_tail_value(self, db):
        """The live-DB shape: both rows have a season row for the same year.

        The ESPN row carries a synthetic ``espn_tail`` ADP (a sort key, not a
        real draft position) and the FFC row carries real consensus ADP. On a
        collision the consensus value must win, or the merge locks in a number
        no human ever drafted at.
        """
        espn, _ = _seed_defense_pair(db)
        db.add(DBPlayerSeasonStats(player_id=espn.id, year=YEAR,
                                   adp=222.8, adp_source="espn_tail"))
        db.commit()

        PlayerIdentityService(db).merge_duplicates(dry_run=False)

        survivor = db.query(DBPlayer).filter_by(player_id="espn_-16001").first()
        season = db.query(DBPlayerSeasonStats).filter_by(
            player_id=survivor.id, year=YEAR
        ).first()
        assert season.adp == pytest.approx(145.2)
        assert season.adp_source == "fantasyfootballcalculator"

    def test_espn_history_is_preserved(self, db):
        _seed_defense_pair(db)
        svc = PlayerIdentityService(db)

        svc.merge_duplicates(dry_run=False)

        survivor = db.query(DBPlayer).filter_by(player_id="espn_-16001").first()
        old = db.query(DBPlayerSeasonStats).filter_by(
            player_id=survivor.id, year=2025
        ).first()
        assert old is not None
        assert old.fantasy_points_total == pytest.approx(15.0)

    def test_position_filter_folds_only_that_position(self, db):
        """Lets a defense cleanup run without touching unrelated duplicates."""
        _seed_defense_pair(db)
        espn_rb = DBPlayer(player_id="espn_1", name="Bijan Robinson",
                           position="RB", nfl_team="ATL")
        nfl_rb = DBPlayer(player_id="nfl_00-0038542", name="Bijan Robinson",
                          position="RB", nfl_team="ATL")
        db.add_all([espn_rb, nfl_rb])
        db.commit()
        svc = PlayerIdentityService(db)

        report = svc.merge_duplicates(dry_run=False, position="DEF")

        assert report.merged == 1
        # The RB duplicate is deliberately left alone.
        assert db.query(DBPlayer).filter_by(player_id="nfl_00-0038542").first() is not None
        assert db.query(DBPlayer).filter_by(player_id="ffc_1334").first() is None

    def test_position_filter_accepts_source_spelling(self, db):
        """'D/ST' must select defenses too — that's how the rows are spelled."""
        _seed_defense_pair(db)
        svc = PlayerIdentityService(db)

        report = svc.merge_duplicates(dry_run=False, position="D/ST")

        assert report.merged == 1

    def test_dry_run_changes_nothing(self, db):
        _seed_defense_pair(db)
        svc = PlayerIdentityService(db)

        report = svc.merge_duplicates(dry_run=True)

        assert report.merged == 1
        assert db.query(DBPlayer).count() == 2
