"""The dashboard page and its live-polling fragment.

Uses the shared ``db``/override fixtures from ``tests/integration/conftest.py``
rather than a private engine: a same-named ``reset_db`` fixture defined here
would shadow conftest's, leaving its own dependency-override pointed at a
database that was never given any tables ("no such table: leagues") -- the
exact failure conftest's own docstring exists to warn about.
"""

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import (
    DBLeague,
    DBNFLGame,
    DBPlayer,
    DBRosterSpot,
    DBTeam,
)

client = TestClient(app)


def seed(db):
    lg = DBLeague(
        league_id="season-x",
        name="Bird Turds",
        year=2026,
        kind="season",
        current_week=1,
    )
    db.add(lg)
    db.add(
        DBTeam(
            team_id="t1",
            name="The Scoobies",
            owner="Brandon",
            league_id="season-x",
            is_user_team=True,
        )
    )
    db.add(DBNFLGame(year=2026, week=1, home_team="CHI", away_team="DET"))
    db.commit()


class TestDashboardPage:
    def test_renders(self, db):
        seed(db)
        response = client.get("/")
        assert response.status_code == 200
        assert "The Scoobies" in response.text

    def test_no_quick_actions_block(self, db):
        seed(db)
        assert "Quick Actions" not in client.get("/").text

    def test_pulse_fragment_renders_the_same_team(self, db):
        seed(db)
        page = client.get("/").text
        fragment = client.get("/api/dashboard/pulse").text
        assert "The Scoobies" in page
        assert "The Scoobies" in fragment

    def test_no_polling_when_nothing_is_live(self, db):
        """A schedule with no kickoff times can never be inside a window."""
        seed(db)
        assert 'hx-trigger="every 30s"' not in client.get("/api/dashboard/pulse").text


class TestAttentionFragment:
    """Local ``client``/``seeded`` fixtures, scoped to this class.

    A module-level ``@pytest.fixture def client()`` would rebind the plain
    ``client = TestClient(app)`` name used bare (not via injection) by
    ``TestDashboardPage`` above. Defining these as class-scoped fixtures keeps
    that module attribute intact.
    """

    @pytest.fixture
    def client(self):
        return TestClient(app)

    @pytest.fixture
    def seeded(self, db):
        """A season team with one rostered player and no saved lineup.

        The roster spot is what matters: ``build_attention`` skips a team with
        no rostered players entirely, so without it "Nothing set for week 1"
        would never render even though no ``DBLineupSlot`` rows exist.
        """
        seed(db)
        league = db.query(DBLeague).filter_by(league_id="season-x").first()
        team = db.query(DBTeam).filter_by(team_id="t1").first()
        player = DBPlayer(
            player_id="test_qb1",
            name="Test Quarterback",
            position="QB",
            nfl_team="CHI",
        )
        db.add(player)
        db.commit()
        db.add(
            DBRosterSpot(
                league_id=league.id,
                team_id=team.id,
                player_id=player.id,
            )
        )
        db.commit()
        return db

    def test_renders_an_item_for_a_team_with_no_lineup(self, client, seeded):
        response = client.get("/api/dashboard/attention")
        assert response.status_code == 200
        assert "Attention needed" in response.text
        assert "Nothing set for week 1" in response.text

    def test_says_nothing_needs_attention_when_clean(self, client):
        """No user teams at all: the panel must render, not 500."""
        response = client.get("/api/dashboard/attention")
        assert response.status_code == 200
        assert "Nothing needs your attention" in response.text

    def test_no_control_says_back(self, client, seeded):
        """The dashboard is a sidebar page; only nav.back_link may say Back."""
        assert "Back" not in client.get("/api/dashboard/attention").text
