"""A renamed ESPN team is the same team.

Managers rename their fantasy team mid-season and between seasons.  The sync
matches on ``(espn_team_id, league_id)`` -- never on the team name -- so a
rename updates the existing row in place.  If it ever matched on name, a
rename would fork a second ``DBTeam`` row and strand ``is_user_team``, the
saved projection weights, and the whole ``weekly_team_stats`` history on the
orphan.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base, DBTeam
from pigskin_mastermind.services import espn_sync as espn_sync_module
from pigskin_mastermind.services.espn_sync import ESPNSyncService
from pigskin_mastermind.services.player_identity import PlayerIdentityService

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

LEAGUE_ID = "1977617326"


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


class _FakeESPNTeam:
    """Minimal stand-in for espn_api's Team object."""

    def __init__(self, team_id, team_name, owner_display):
        self.team_id = team_id
        self.team_name = team_name
        self.owners = [{"displayName": owner_display}]
        self.wins = 3
        self.losses = 1
        self.ties = 0
        self.points_for = 421.5
        self.roster = []


@pytest.fixture
def fake_league(monkeypatch):
    """Patch out the networked ``League`` constructor with a settable roster."""
    holder = {"teams": []}

    class _FakeLeague:
        def __init__(self, *args, **kwargs):
            self.teams = holder["teams"]

    monkeypatch.setattr(espn_sync_module, "League", _FakeLeague)
    return holder


def _service(db):
    """Build the service without running __init__ (which needs ESPN creds)."""
    svc = ESPNSyncService.__new__(ESPNSyncService)
    svc.db = db
    svc.identity = PlayerIdentityService(db)
    return svc


def _import(db, fake_league, team):
    fake_league["teams"] = [team]
    return _service(db).import_team(
        league_id=LEAGUE_ID,
        team_id=team.team_id,
        espn_s2="s2",
        swid="swid",
        year=2026,
    )


class TestRenamedTeamMatches:
    def test_rename_with_same_owner_updates_the_existing_row(self, db, fake_league):
        first = _import(
            db, fake_league, _FakeESPNTeam(1, "Bozos Dubbed Over", "Brandon__COOK")
        )
        original_pk = first.id

        renamed = _import(
            db,
            fake_league,
            _FakeESPNTeam(1, "Goo Goo Gaga Gimme That Points", "Brandon__COOK"),
        )

        assert renamed.id == original_pk
        assert renamed.name == "Goo Goo Gaga Gimme That Points"
        assert renamed.owner == "Brandon__COOK"
        assert db.query(DBTeam).filter_by(league_id=LEAGUE_ID).count() == 1

    def test_rename_preserves_user_claim_and_saved_weights(self, db, fake_league):
        team = _import(
            db, fake_league, _FakeESPNTeam(1, "Bozos Dubbed Over", "Brandon__COOK")
        )
        team.is_user_team = True
        team.projection_weights = {"model": 2.0, "espn": 0.0}
        db.commit()

        renamed = _import(
            db,
            fake_league,
            _FakeESPNTeam(1, "Goo Goo Gaga Gimme That Points", "Brandon__COOK"),
        )

        assert renamed.is_user_team is True
        assert renamed.projection_weights == {"model": 2.0, "espn": 0.0}

    def test_two_teams_renamed_into_each_others_names_do_not_swap(
        self, db, fake_league
    ):
        """Name is not identity, so a name collision must not re-point a row."""
        _import(db, fake_league, _FakeESPNTeam(1, "Waiver Wire", "brandon"))
        _import(db, fake_league, _FakeESPNTeam(2, "Costanzas", "andynewk"))

        _import(db, fake_league, _FakeESPNTeam(1, "Costanzas", "brandon"))
        _import(db, fake_league, _FakeESPNTeam(2, "Waiver Wire", "andynewk"))

        by_espn_id = {
            t.espn_team_id: t
            for t in db.query(DBTeam).filter_by(league_id=LEAGUE_ID).all()
        }
        assert len(by_espn_id) == 2
        assert by_espn_id["1"].owner == "brandon"
        assert by_espn_id["2"].owner == "andynewk"
