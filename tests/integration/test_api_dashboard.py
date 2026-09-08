"""The dashboard page and its live-polling fragment.

Uses the shared ``db``/override fixtures from ``tests/integration/conftest.py``
rather than a private engine: a same-named ``reset_db`` fixture defined here
would shadow conftest's, leaving its own dependency-override pointed at a
database that was never given any tables ("no such table: leagues") -- the
exact failure conftest's own docstring exists to warn about.
"""

from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import DBLeague, DBNFLGame, DBTeam

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
