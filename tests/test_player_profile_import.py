"""Tests for player profile data reaching (and staying in) the database.

Covers the three bugs that left profiles empty:
  * ESPN syncs overwrote ``stats`` wholesale, taking bio with it
  * snap counts were looked up by PFR id against gsis-keyed rows
  * the seasonal feed has no name column, so rows were written as "Unknown"
"""

import pytest
from types import SimpleNamespace
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


def _espn_player(**kw):
    """A stand-in for espn_api's Player object."""
    defaults = dict(
        playerId=4047646,
        name="Justin Jefferson",
        position="WR",
        proTeam="MIN",
        projected_points=18.4,
        points=0.0,
        stats={"0": {"points": 0.0}},
        injuryStatus="QUESTIONABLE",
        injured=True,
        jersey="18",
        posRank=3,
        percent_owned=99.8,
        percent_started=97.1,
        # Week 6 missing → that's the bye
        schedule={w: {"team": "GB"} for w in range(1, 19) if w != 6},
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# ESPN profile persistence
# ---------------------------------------------------------------------------

def test_espn_import_persists_profile_fields(db):
    from pigskin_mastermind.services.espn_sync import ESPNSyncService

    svc = ESPNSyncService(db)
    svc.identity._id_map = {}
    player = svc._import_player(_espn_player(), team_db_id=None)
    db.commit()

    assert player.injury_status == "QUESTIONABLE"
    assert player.injured is True
    assert player.jersey == "18"
    assert player.pos_rank == 3
    assert player.percent_owned == 99.8
    assert player.percent_started == 97.1
    assert player.espn_id == "4047646"
    assert player.profile_updated_at is not None


def test_espn_import_derives_bye_week_from_schedule(db):
    from pigskin_mastermind.services.espn_sync import ESPNSyncService

    svc = ESPNSyncService(db)
    svc.identity._id_map = {}
    player = svc._import_player(_espn_player(), team_db_id=None)

    assert player.bye_week == 6


def test_bye_week_not_guessed_from_a_partial_schedule(db):
    """Several missing weeks means we don't know the bye — don't invent one."""
    from pigskin_mastermind.services.espn_sync import ESPNSyncService

    svc = ESPNSyncService(db)
    svc.identity._id_map = {}
    player = svc._import_player(
        _espn_player(schedule={1: {"team": "GB"}, 2: {"team": "CHI"}}),
        team_db_id=None,
    )

    assert player.bye_week is None


def test_espn_resync_does_not_clear_bio_columns(db):
    """The regression that made bio worthless: bio lived in ``stats``, and
    every sync replaced ``stats`` wholesale."""
    from pigskin_mastermind.services.espn_sync import ESPNSyncService

    svc = ESPNSyncService(db)
    svc.identity._id_map = {}
    player = svc._import_player(_espn_player(), team_db_id=None)
    db.commit()

    # nfl_data_py fills in bio the ESPN feed does not carry
    player.college = "LSU"
    player.age = 25
    player.height = "6-1"
    player.weight = 195
    player.years_exp = 5
    db.commit()

    # ... then ESPN is synced again with a fresh stats payload
    svc._import_player(_espn_player(stats={"3": {"points": 21.7}}), team_db_id=None)
    db.commit()
    db.refresh(player)

    assert player.college == "LSU"
    assert player.age == 25
    assert player.height == "6-1"
    assert player.weight == 195
    assert player.years_exp == 5
    assert player.stats == {"3": {"points": 21.7}}  # stats itself still refreshes


def test_espn_import_normalizes_dst_position(db):
    from pigskin_mastermind.services.espn_sync import ESPNSyncService

    svc = ESPNSyncService(db)
    svc.identity._id_map = {}
    player = svc._import_player(
        _espn_player(name="Ravens D/ST", position="D/ST", proTeam="BAL", schedule={}),
        team_db_id=None,
    )

    assert player.position == "DEF"


def test_espn_ignores_missing_ownership_sentinel(db):
    """ESPN reports -1 when it has no ownership data — that is not 0%."""
    from pigskin_mastermind.services.espn_sync import ESPNSyncService

    svc = ESPNSyncService(db)
    svc.identity._id_map = {}
    player = svc._import_player(
        _espn_player(percent_owned=-1, percent_started=-1), team_db_id=None
    )

    assert player.percent_owned is None
    assert player.percent_started is None


# ---------------------------------------------------------------------------
# nfl_data_py resolution
# ---------------------------------------------------------------------------

def test_snap_counts_resolve_via_pfr_id(db):
    """The old lookup built ``nfl_<pfr_id>`` and could never match a row."""
    from pigskin_mastermind.services.nfl_data_service import NFLDataService

    player = DBPlayer(player_id="espn_4047646", name="Justin Jefferson",
                      position="WR", nfl_team="MIN", espn_id="4047646")
    db.add(player)
    db.commit()

    svc = NFLDataService(db)
    svc.identity._id_map = {
        "pfr_id:JeffJu00": {
            "gsis_id": "00-0036322", "espn_id": "4047646", "pfr_id": "JeffJu00",
            "name": "Justin Jefferson", "position": "WR",
        }
    }

    resolved = svc._resolve_or_create(pfr_id="JeffJu00", create=False)
    assert resolved is not None and resolved.id == player.id


def test_seasonal_row_without_a_name_is_not_written_as_unknown(db):
    """The seasonal feed has no name/position column; a nameless row helps
    nobody and pollutes search."""
    from pigskin_mastermind.services.nfl_data_service import NFLDataService

    svc = NFLDataService(db)
    svc.identity._id_map = {}

    assert svc._resolve_or_create(gsis_id="00-0099999", name=None, position=None) is None
    assert db.query(DBPlayer).count() == 0


def test_seasonal_row_gets_its_name_from_the_id_map(db):
    from pigskin_mastermind.services.nfl_data_service import NFLDataService

    svc = NFLDataService(db)
    svc.identity._id_map = {
        "gsis_id:00-0036322": {
            "gsis_id": "00-0036322", "espn_id": None, "pfr_id": "JeffJu00",
            "name": "Justin Jefferson", "position": "WR",
        }
    }

    created = svc._resolve_or_create(gsis_id="00-0036322", name=None, position=None)
    assert created is not None
    assert created.name == "Justin Jefferson"
    assert created.position == "WR"


# ---------------------------------------------------------------------------
# nflverse value formatting
#
# These all surfaced the moment the importers were actually wired up: nflverse
# stores jersey and height as floats and snap share as a 0-1 fraction.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (1.0, "1"), ("1.0", "1"), (18, "18"), ("18", "18"),
    (None, None), ("nan", None), ("", None),
])
def test_format_jersey(raw, expected):
    from pigskin_mastermind.services.nfl_data_service import _format_jersey
    assert _format_jersey(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (72.0, "6-0"), ("73", "6-1"), (69.0, "5-9"),
    ("6-1", "6-1"),           # already formatted, left alone
    (None, None), ("nan", None), (0, None),
])
def test_format_height(raw, expected):
    from pigskin_mastermind.services.nfl_data_service import _format_height
    assert _format_height(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (0.9229411764, 92.3),     # nflverse offense_pct is a 0-1 fraction
    (1.0, 100.0),
    (0.0, 0.0),
    (85.0, 85.0),             # already a percentage, not rescaled
    (None, None),
])
def test_offense_pct_scaled_to_percentage(raw, expected):
    from pigskin_mastermind.services.nfl_data_service import _to_percentage
    assert _to_percentage(raw) == expected


def test_snap_pct_is_zero_to_one_hundred_for_the_projection_builder():
    """snap_pct had two contradictory meanings: the builder multiplied by 100
    while the UI printed it directly. It is now canonically 0-100."""
    import inspect
    from pigskin_mastermind.services import projection_criteria_builder as pcb
    source = inspect.getsource(pcb)
    assert "snap_pct * 100" not in source


# ---------------------------------------------------------------------------
# FFC bye week + draft pool
# ---------------------------------------------------------------------------

_FFC_ENTRY = {
    "name": "Justin Jefferson", "position": "WR", "team": "MIN",
    "adp": 8.5, "adp_formatted": "1.09", "times_drafted": 412,
    "high": "1.01", "low": "2.03", "stdev": 3.1, "bye": 6, "ffc_id": 1234,
}


def test_ffc_import_persists_the_bye_week(db):
    """FFC has always returned the bye; it used to be dropped on the floor."""
    svc = ADPService(db)
    svc.identity._id_map = {}

    with patch.object(ADPService, "fetch_ffc_adp", return_value=[dict(_FFC_ENTRY)]):
        svc.import_from_ffc(year=2026)

    player = db.query(DBPlayer).filter_by(name="Justin Jefferson").one()
    assert player.bye_week == 6


def test_draft_pool_falls_back_to_last_season_points(db):
    """FFC-created rows have no persisted projection; a pool of zeros flattens
    the AI drafter's projection nudge and the post-draft grade."""
    svc = ADPService(db)
    svc.identity._id_map = {}

    with patch.object(ADPService, "fetch_ffc_adp", return_value=[dict(_FFC_ENTRY)]):
        svc.import_from_ffc(year=2026)

    player = db.query(DBPlayer).filter_by(name="Justin Jefferson").one()
    assert player.projected_points == 0.0
    db.add(DBPlayerSeasonStats(player_id=player.id, year=2025, games_played=17,
                               fantasy_points_total=281.4, fantasy_points_avg=16.6))
    db.commit()

    pool = svc.get_adp_for_draft_pool(year=2026)
    assert len(pool) == 1
    # The season TOTAL (281.4) — not the 16.6 per-game average, and not
    # DBPlayer.projected_points, which two importers write in two different
    # units and is no longer consulted by the pool at all.
    assert pool[0]["projected_points"] == 281.4


def test_draft_pool_projection_units_stay_comparable(db):
    """A pool mixing per-game and season-total values silently breaks the AI
    drafter's within-position normalization. DBPlayer.projected_points is
    ignored entirely, so an ESPN-sourced per-game value can no longer leak
    into the pool."""
    svc = ADPService(db)
    svc.identity._id_map = {}

    espn_backed = DBPlayer(player_id="espn_1", name="Espn Guy", position="RB",
                           nfl_team="DET", projected_points=18.8)
    ffc_backed = DBPlayer(player_id="ffc_2", name="Ffc Guy", position="RB",
                          nfl_team="ATL", projected_points=0.0)
    db.add_all([espn_backed, ffc_backed])
    db.flush()
    db.add_all([
        DBPlayerSeasonStats(player_id=espn_backed.id, year=2026, adp=1.4,
                            adp_source=ADPService.ADP_SOURCE_LABEL),
        DBPlayerSeasonStats(player_id=ffc_backed.id, year=2026, adp=2.2,
                            adp_source=ADPService.ADP_SOURCE_LABEL),
        DBPlayerSeasonStats(player_id=ffc_backed.id, year=2025, games_played=17,
                            fantasy_points_total=331.3, fantasy_points_avg=19.5),
    ])
    db.commit()

    values = [p["projected_points"] for p in svc.get_adp_for_draft_pool(year=2026)]
    # No entry should land in the per-game band (~0-40): espn_backed's
    # DBPlayer.projected_points (18.8) is ignored entirely, and ffc_backed's
    # fallback resolves to the season TOTAL (331.3), not the 19.5 average.
    assert not [v for v in values if 0 < v < 40], f"a per-game value leaked into the pool: {values}"
    # Pin the real season-total value so an all-zeros pool can't pass this
    # band check vacuously.
    assert 331.3 in values


def test_draft_pool_ignores_single_game_season_rows(db):
    """Some ESPN season rows carry a full-season total against games_played=1,
    making fantasy_points_avg equal the total. Requiring a real sample keeps
    that corrupt row out of the fallback."""
    svc = ADPService(db)
    svc.identity._id_map = {}

    player = DBPlayer(player_id="ffc_9", name="Bad Row", position="RB",
                      nfl_team="PHI", projected_points=0.0)
    db.add(player)
    db.flush()
    db.add_all([
        DBPlayerSeasonStats(player_id=player.id, year=2026, adp=2.1,
                            adp_source=ADPService.ADP_SOURCE_LABEL),
        # Corrupt: a season total recorded as one game
        DBPlayerSeasonStats(player_id=player.id, year=2025, games_played=1,
                            fantasy_points_total=213.8, fantasy_points_avg=213.8),
        # Healthy: a real 16-game sample
        DBPlayerSeasonStats(player_id=player.id, year=2024, games_played=16,
                            fantasy_points_total=355.3, fantasy_points_avg=22.2),
    ])
    db.commit()

    # The season TOTAL from the healthy row (355.3), not its per-game average.
    assert svc.get_adp_for_draft_pool(year=2026)[0]["projected_points"] == 355.3


def test_draft_pool_carries_profile_fields_for_the_board(db):
    svc = ADPService(db)
    svc.identity._id_map = {}

    with patch.object(ADPService, "fetch_ffc_adp", return_value=[dict(_FFC_ENTRY)]):
        svc.import_from_ffc(year=2026)

    player = db.query(DBPlayer).filter_by(name="Justin Jefferson").one()
    player.injury_status = "QUESTIONABLE"
    db.commit()

    entry = svc.get_adp_for_draft_pool(year=2026)[0]
    assert entry["db_id"] == player.id
    assert entry["bye_week"] == 6
    assert entry["injury_status"] == "QUESTIONABLE"


def test_ffc_import_reuses_an_existing_espn_row(db):
    """FFC should stop minting ``ffc_`` shells for players we already have."""
    existing = DBPlayer(player_id="espn_4047646", name="Justin Jefferson",
                        position="WR", nfl_team="MIN", projected_points=310.0)
    db.add(existing)
    db.commit()

    svc = ADPService(db)
    svc.identity._id_map = {}
    with patch.object(ADPService, "fetch_ffc_adp", return_value=[dict(_FFC_ENTRY)]):
        result = svc.import_from_ffc(year=2026)

    assert result["created"] == 0
    assert db.query(DBPlayer).count() == 1
