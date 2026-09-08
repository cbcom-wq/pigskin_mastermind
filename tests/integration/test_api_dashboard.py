"""The dashboard page and its live-polling fragment.

Uses the shared ``db``/override fixtures from ``tests/integration/conftest.py``
rather than a private engine: a same-named ``reset_db`` fixture defined here
would shadow conftest's, leaving its own dependency-override pointed at a
database that was never given any tables ("no such table: leagues") -- the
exact failure conftest's own docstring exists to warn about.
"""

import re
from datetime import timedelta

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
from pigskin_mastermind.services.season_scheduler import league_now

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


class TestPlayersFragment:
    """Local ``client``/``seeded`` fixtures, scoped to this class.

    See ``TestAttentionFragment`` above for why these are not module-level.
    """

    @pytest.fixture
    def client(self):
        return TestClient(app)

    @pytest.fixture
    def seeded(self, db):
        """A season team with one rostered player and no saved lineup.

        ``build_players`` skips a team whose roster is empty, so the strip
        needs at least one rostered player to render anything.
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

    def test_renders(self, client, seeded):
        response = client.get("/api/dashboard/players")
        assert response.status_code == 200
        assert "Your players" in response.text

    def test_empty_roster_does_not_500(self, client):
        assert client.get("/api/dashboard/players").status_code == 200


class TestPulseYetToPlay:
    """Closes the review finding on Task 8: the hero's "N of your players
    yet to play" is served by ``/api/dashboard/pulse``, which requests no
    sections at all -- so the count must not depend on the ``players``
    section ever being built for display.

    Local ``client``/``seeded`` fixtures, scoped to this class. See
    ``TestAttentionFragment`` above for why these are not module-level.
    """

    @pytest.fixture
    def client(self):
        return TestClient(app)

    @pytest.fixture
    def seeded(self, db):
        """A season team with one rostered starter whose game has not
        kicked off yet. The kickoff is real wall-clock future time so the
        count is genuinely nonzero no matter when this test runs."""
        seed(db)
        league = db.query(DBLeague).filter_by(league_id="season-x").first()
        team = db.query(DBTeam).filter_by(team_id="t1").first()
        game = db.query(DBNFLGame).filter_by(home_team="CHI").first()
        game.kickoff_at = league_now() + timedelta(days=3)
        db.commit()
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

    def test_shows_a_nonzero_count(self, client, seeded):
        response = client.get("/api/dashboard/pulse")
        assert response.status_code == 200
        match = re.search(r"(\d+)\s+of your players yet to play", response.text)
        assert match is not None
        assert int(match.group(1)) > 0


class TestSlateFragment:
    """Local ``client``/``seeded`` fixtures, scoped to this class. See
    ``TestAttentionFragment`` above for why these are not module-level.
    """

    @pytest.fixture
    def client(self):
        return TestClient(app)

    @pytest.fixture
    def seeded(self, db):
        seed(db)
        return db

    def test_renders(self, client, seeded):
        response = client.get("/api/dashboard/slate")
        assert response.status_code == 200
        assert "CHI" in response.text

    def test_no_schedule_does_not_500(self, client):
        assert client.get("/api/dashboard/slate").status_code == 200


class TestMoversFragment:
    """Local ``client``/``seeded`` fixtures, scoped to this class. See
    ``TestAttentionFragment`` above for why these are not module-level.
    """

    @pytest.fixture
    def client(self):
        return TestClient(app)

    @pytest.fixture
    def seeded(self, db):
        seed(db)
        return db

    def test_renders_with_no_metrics(self, client, seeded):
        response = client.get("/api/dashboard/movers")
        assert response.status_code == 200
        assert "Hot movers" in response.text
