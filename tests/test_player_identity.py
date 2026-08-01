"""Tests for PlayerIdentityService — cross-source resolution and de-duplication."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
)
from pigskin_mastermind.services.player_identity import (
    PlayerIdentityService,
    _clean_id,
    is_placeholder_name,
    normalize_name,
)
from pigskin_mastermind.utils.positions import is_flex_eligible, normalize_position

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


@pytest.fixture
def svc(db):
    """Identity service with the network-backed ID map stubbed out empty.

    Individual tests install their own map so nothing touches nflverse.
    """
    service = PlayerIdentityService(db)
    service._id_map = {}
    return service


def _set_id_map(svc, entries):
    """Install a fake nflverse cross-ID map from a list of entry dicts."""
    svc._id_map = {}
    for entry in entries:
        for key in ("gsis_id", "espn_id", "pfr_id"):
            if entry.get(key):
                svc._id_map[f"{key}:{entry[key]}"] = entry


def _player(db, player_id, name, position="WR", **kw):
    p = DBPlayer(
        player_id=player_id, name=name, position=position,
        nfl_team=kw.pop("nfl_team", "PHI"), **kw
    )
    db.add(p)
    db.commit()
    return p


# ---------------------------------------------------------------------------
# Position normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("D/ST", "DEF"), ("DST", "DEF"), ("def", "DEF"), ("Defense", "DEF"),
    ("PK", "K"), ("k", "K"), ("QB", "QB"), (" rb ", "RB"),
    ("DT", None), ("LB", None), ("Unknown", None), ("", None), (None, None),
])
def test_normalize_position(raw, expected):
    assert normalize_position(raw) == expected


def test_flex_eligibility_excludes_qb_k_def():
    assert is_flex_eligible("RB") and is_flex_eligible("WR") and is_flex_eligible("TE")
    assert not is_flex_eligible("QB")
    assert not is_flex_eligible("K")
    assert not is_flex_eligible("D/ST")


# ---------------------------------------------------------------------------
# Name and ID helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("A.J. Brown", "aj brown"),
    ("AJ Brown", "aj brown"),
    ("Aaron Jones Sr.", "aaron jones"),
    ("Michael Pittman Jr.", "michael pittman"),
    ("Ken Walker III", "ken walker"),
    ("", ""),
])
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (4362238.0, "4362238"),      # nflverse stores espn ids as floats
    ("4362238", "4362238"),
    ("4362238.0", "4362238"),
    ("00-0033873", "00-0033873"),
    (float("nan"), None),
    ("nan", None), ("<NA>", None), ("", None), (None, None),
])
def test_clean_id(raw, expected):
    assert _clean_id(raw) == expected


def test_is_placeholder_name():
    assert is_placeholder_name("Unknown")
    assert is_placeholder_name("")
    assert not is_placeholder_name("Patrick Mahomes")


# ---------------------------------------------------------------------------
# resolve()
# ---------------------------------------------------------------------------

def test_resolve_by_stored_id_column(db, svc):
    p = _player(db, "espn_15847", "A.J. Brown", espn_id="15847")
    assert svc.resolve(espn_id="15847").id == p.id


def test_resolve_by_legacy_prefixed_player_id(db, svc):
    """Rows created before the ID columns existed are still findable."""
    p = _player(db, "nfl_00-0035676", "Deebo Samuel", espn_id=None)
    assert svc.resolve(gsis_id="00-0035676").id == p.id


def test_resolve_translates_gsis_to_espn_via_id_map(db, svc):
    """The whole point: an nflverse gsis id finds the ESPN-created row."""
    p = _player(db, "espn_4047646", "Justin Jefferson", espn_id="4047646")
    _set_id_map(svc, [{
        "gsis_id": "00-0036322", "espn_id": "4047646", "pfr_id": "JeffJu00",
        "name": "Justin Jefferson", "position": "WR",
    }])
    assert svc.resolve(gsis_id="00-0036322").id == p.id
    assert svc.resolve(pfr_id="JeffJu00").id == p.id


def test_resolve_falls_back_to_normalized_name(db, svc):
    p = _player(db, "espn_1", "A.J. Brown")
    assert svc.resolve(name="AJ Brown", position="WR").id == p.id


def test_resolve_name_match_respects_position(db, svc):
    _player(db, "espn_1", "Josh Allen", position="QB")
    edge = _player(db, "espn_2", "Josh Allen", position="TE")
    assert svc.resolve(name="Josh Allen", position="TE").id == edge.id


def test_resolve_returns_none_for_placeholder_name(db, svc):
    _player(db, "nfl_x", "Unknown", position="WR")
    assert svc.resolve(name="Unknown", position="WR") is None


def test_resolve_returns_none_when_unknown(db, svc):
    assert svc.resolve(espn_id="99999", name="Nobody At All", position="QB") is None


def test_stamp_ids_fans_out_through_the_map(db, svc):
    p = _player(db, "espn_4047646", "Justin Jefferson", espn_id="4047646")
    _set_id_map(svc, [{
        "gsis_id": "00-0036322", "espn_id": "4047646", "pfr_id": "JeffJu00",
        "name": "Justin Jefferson", "position": "WR",
    }])
    svc.stamp_ids(p, espn_id="4047646")
    assert (p.gsis_id, p.pfr_id) == ("00-0036322", "JeffJu00")


def test_id_map_failure_degrades_to_name_matching(db):
    """A missing or broken nfl_data_py must not break resolution."""
    svc = PlayerIdentityService(db)
    p = _player(db, "espn_1", "A.J. Brown")
    # _load_id_map swallows the ImportError from the absent module and caches {}.
    assert svc._load_id_map() == {} or isinstance(svc._load_id_map(), dict)
    assert svc.resolve(name="AJ Brown", position="WR").id == p.id


# ---------------------------------------------------------------------------
# merge_duplicates()
# ---------------------------------------------------------------------------

def _dup_pair(db, svc):
    """An ESPN row and an nflverse row that are really the same player."""
    survivor = _player(db, "espn_4047646", "Justin Jefferson", espn_id="4047646")
    duplicate = _player(db, "nfl_00-0036322", "Unknown", position="Unknown",
                        nfl_team="FA", gsis_id="00-0036322")
    _set_id_map(svc, [{
        "gsis_id": "00-0036322", "espn_id": "4047646", "pfr_id": "JeffJu00",
        "name": "Justin Jefferson", "position": "WR",
    }])
    return survivor, duplicate


def test_merge_moves_stats_and_deletes_duplicate(db, svc):
    survivor, duplicate = _dup_pair(db, svc)
    db.add(DBPlayerSeasonStats(player_id=duplicate.id, year=2024,
                               fantasy_points_total=280.0, wopr=0.72))
    db.add(DBPlayerGameLog(player_id=duplicate.id, year=2024, week=1, fantasy_points=22.4))
    db.commit()

    report = svc.merge_duplicates(dry_run=False)

    assert report.merged == 1
    assert report.season_rows_moved == 1
    assert report.game_log_rows_moved == 1
    assert db.query(DBPlayer).filter_by(player_id="nfl_00-0036322").first() is None

    moved = db.query(DBPlayerSeasonStats).filter_by(player_id=survivor.id, year=2024).one()
    assert moved.wopr == 0.72


def test_merge_dry_run_writes_nothing(db, svc):
    _, duplicate = _dup_pair(db, svc)
    db.add(DBPlayerSeasonStats(player_id=duplicate.id, year=2024, fantasy_points_total=280.0))
    db.commit()

    report = svc.merge_duplicates(dry_run=True)

    assert report.merged == 1
    assert report.dry_run is True
    assert db.query(DBPlayer).filter_by(player_id="nfl_00-0036322").first() is not None


def test_merge_season_collision_folds_fields_by_source_preference(db, svc):
    """Both rows have 2024: nflverse advanced stats win, ESPN's ADP survives."""
    survivor, duplicate = _dup_pair(db, svc)
    db.add(DBPlayerSeasonStats(
        player_id=survivor.id, year=2024,
        fantasy_points_total=280.0, adp=8.5, adp_source="fantasyfootballcalculator",
        snap_pct=None, wopr=None,
    ))
    db.add(DBPlayerSeasonStats(
        player_id=duplicate.id, year=2024,
        fantasy_points_total=275.0, snap_pct=88.4, wopr=0.72, air_yards=0.31,
    ))
    db.commit()

    report = svc.merge_duplicates(dry_run=False)

    assert report.season_collisions == 1
    rows = db.query(DBPlayerSeasonStats).filter_by(year=2024).all()
    assert len(rows) == 1, "collision must fold into one row, not drop or duplicate"
    kept = rows[0]
    assert kept.player_id == survivor.id
    assert kept.adp == 8.5                    # ESPN/FFC field preserved
    assert kept.snap_pct == 88.4              # nflverse field adopted
    assert kept.wopr == 0.72
    assert kept.fantasy_points_total == 280.0  # survivor's non-empty value kept


def test_merge_game_log_collision_fills_only_missing_fields(db, svc):
    survivor, duplicate = _dup_pair(db, svc)
    db.add(DBPlayerGameLog(player_id=survivor.id, year=2024, week=1,
                           fantasy_points=22.4, targets=0, rec_yd=118))
    db.add(DBPlayerGameLog(player_id=duplicate.id, year=2024, week=1,
                           fantasy_points=22.4, targets=11, rec_yd=95))
    db.commit()

    report = svc.merge_duplicates(dry_run=False)

    assert report.game_log_collisions == 1
    logs = db.query(DBPlayerGameLog).filter_by(year=2024, week=1).all()
    assert len(logs) == 1
    assert logs[0].targets == 11    # was empty on the survivor, filled in
    assert logs[0].rec_yd == 118    # survivor already had a value, kept


def test_merge_backfills_profile_fields_onto_survivor(db, svc):
    survivor, duplicate = _dup_pair(db, svc)
    duplicate.college = "LSU"
    duplicate.age = 25
    duplicate.headshot_url = "http://cdn/jj.png"
    db.commit()

    svc.merge_duplicates(dry_run=False)
    db.refresh(survivor)

    assert survivor.college == "LSU"
    assert survivor.age == 25
    assert survivor.headshot_url == "http://cdn/jj.png"
    assert survivor.profile_updated_at is not None


def test_merge_deletes_empty_unresolvable_placeholders(db, svc):
    _player(db, "nfl_00-0000001", "Unknown", position="Unknown", nfl_team="FA",
            gsis_id="00-0000001")
    report = svc.merge_duplicates(dry_run=False)

    assert report.deleted_empty == 1
    assert db.query(DBPlayer).count() == 0


def test_merge_keeps_unresolvable_rows_that_hold_stats(db, svc):
    orphan = _player(db, "nfl_00-0000002", "Some Rookie", position="RB",
                     gsis_id="00-0000002")
    db.add(DBPlayerSeasonStats(player_id=orphan.id, year=2024, fantasy_points_total=44.0))
    db.commit()

    report = svc.merge_duplicates(dry_run=False)

    assert report.unresolved == 1
    assert report.deleted_empty == 0
    assert db.query(DBPlayer).filter_by(player_id="nfl_00-0000002").first() is not None


def test_merge_repairs_placeholder_name_when_it_cannot_merge(db, svc):
    """No ESPN twin, but the ID map still knows who this is."""
    orphan = _player(db, "nfl_00-0036322", "Unknown", position="Unknown",
                     nfl_team="FA", gsis_id="00-0036322")
    db.add(DBPlayerSeasonStats(player_id=orphan.id, year=2024, fantasy_points_total=44.0))
    db.commit()
    _set_id_map(svc, [{
        "gsis_id": "00-0036322", "espn_id": None, "pfr_id": None,
        "name": "Justin Jefferson", "position": "WR",
    }])

    svc.merge_duplicates(dry_run=False)
    db.refresh(orphan)

    assert orphan.name == "Justin Jefferson"
    assert orphan.position == "WR"


def test_merge_never_folds_a_row_into_itself(db, svc):
    """A name match that resolves back to the duplicate must not delete it."""
    orphan = _player(db, "ffc_123", "Solo Player", position="WR")
    db.add(DBPlayerSeasonStats(player_id=orphan.id, year=2026, adp=101.0))
    db.commit()

    report = svc.merge_duplicates(dry_run=False)

    assert report.merged == 0
    assert db.query(DBPlayer).filter_by(player_id="ffc_123").first() is not None


def test_backfill_id_columns_seeds_from_prefixes(db, svc):
    _player(db, "espn_15847", "A.J. Brown")
    _player(db, "nfl_00-0035676", "Deebo Samuel")

    svc.backfill_id_columns()

    assert db.query(DBPlayer).filter_by(player_id="espn_15847").one().espn_id == "15847"
    assert db.query(DBPlayer).filter_by(player_id="nfl_00-0035676").one().gsis_id == "00-0035676"
