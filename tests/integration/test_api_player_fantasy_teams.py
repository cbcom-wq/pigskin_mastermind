"""A player's "Fantasy Team" is every tracked team that rosters them.

One player row is shared by every league in the database, so the same person
can sit on an ESPN roster and one or more drafted season rosters at once.
Reading only `DBPlayer.team_id` named the ESPN team and silently dropped the
rest — and named nothing at all for a player who is only on a season roster.
"""

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import (
    DBLeague, DBPlayer, DBRosterSpot, DBTeam,
)

client = TestClient(app)
YEAR = 2026


def _league(db, league_id, name, kind):
    lg = DBLeague(league_id=league_id, name=name, year=YEAR, kind=kind,
                  status="in_season", current_week=1, regular_season_weeks=14)
    db.add(lg)
    db.commit()
    return lg


def _team(db, league, slot, name):
    team = DBTeam(team_id=f"{league.league_id}-{slot}", name=name, owner="Me",
                  league_id=league.league_id, is_user_team=True,
                  draft_slot=slot, wins=0, losses=0, ties=0, total_points=0.0)
    db.add(team)
    db.commit()
    return team


@pytest.fixture
def rosters(db):
    """Josh Allen on three teams at once; Puka Nacua on none."""
    espn = _league(db, "e1", "Pigskin Throne", "espn")
    season_a = _league(db, "s1", "Bird Turds", "season")
    season_b = _league(db, "s2", "Second League", "season")

    espn_team = _team(db, espn, 1, "Stable of Stars")
    team_a = _team(db, season_a, 1, "The Scoobies")
    team_b = _team(db, season_b, 1, "Gridiron Ghosts")

    allen = DBPlayer(player_id="nfl_allen", name="Josh Allen", position="QB",
                     nfl_team="BUF", projected_points=22.0,
                     team_id=espn_team.id)
    nacua = DBPlayer(player_id="nfl_nacua", name="Puka Nacua", position="WR",
                     nfl_team="LAR", projected_points=15.0)
    db.add_all([allen, nacua])
    db.commit()

    for team, league in ((team_a, season_a), (team_b, season_b)):
        db.add(DBRosterSpot(league_id=league.id, team_id=team.id,
                            player_id=allen.id, acquired_via="draft"))
    db.commit()
    return {
        "allen": allen, "nacua": nacua,
        "espn_team": espn_team, "team_a": team_a, "team_b": team_b,
    }


ALL_THREE = ("Stable of Stars", "The Scoobies", "Gridiron Ghosts")


def test_search_table_lists_every_team_rostering_a_player(db, rosters):
    response = client.get("/api/players/list?q=Josh")
    assert response.status_code == 200
    for name in ALL_THREE:
        assert name in response.text


def test_player_detail_lists_every_team(db, rosters):
    response = client.get(f"/players/{rosters['allen'].id}")
    assert response.status_code == 200
    for name in ALL_THREE:
        assert name in response.text


def test_player_modal_lists_every_team(db, rosters):
    response = client.get(f"/api/players/{rosters['allen'].id}/modal")
    assert response.status_code == 200
    for name in ALL_THREE:
        assert name in response.text


def test_search_dropdown_subtitle_lists_every_team(db, rosters):
    response = client.get("/api/players/search?q=Josh")
    assert response.status_code == 200
    for name in ALL_THREE:
        assert name in response.text


def test_each_team_links_to_the_page_that_manages_it(db, rosters):
    """A season team is managed on its league's page, an ESPN team is not."""
    response = client.get(f"/players/{rosters['allen'].id}")
    assert f'href="/teams/{rosters["espn_team"].id}"' in response.text
    assert f'href="/season/s1/teams/{rosters["team_a"].id}"' in response.text
    assert f'href="/season/s2/teams/{rosters["team_b"].id}"' in response.text


def test_a_free_agent_names_no_team(db, rosters):
    response = client.get("/api/players/list?q=Puka")
    assert response.status_code == 200
    assert "Puka Nacua" in response.text
    for name in ALL_THREE:
        assert name not in response.text


def test_a_dropped_player_is_no_longer_on_that_team(db, rosters):
    from datetime import datetime

    spot = (
        db.query(DBRosterSpot)
        .filter_by(team_id=rosters["team_a"].id,
                   player_id=rosters["allen"].id)
        .one()
    )
    spot.dropped_at = datetime.utcnow()
    db.commit()

    response = client.get(f"/players/{rosters['allen'].id}")
    assert "The Scoobies" not in response.text
    # The other two are untouched.
    assert "Stable of Stars" in response.text
    assert "Gridiron Ghosts" in response.text
