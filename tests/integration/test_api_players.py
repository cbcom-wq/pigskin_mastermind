"""Integration tests for player detail and stats API endpoints."""

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.models.database import (
    DBPlayer, DBTeam, DBPlayerSeasonStats, DBPlayerGameLog
)
from .conftest import TestSessionLocal

client = TestClient(app)


@pytest.fixture
def player_with_stats(db):
    """Create a player with season stats and game logs."""
    team = DBTeam(team_id="t_int_1", name="Integration Team", owner="Tester")
    db.add(team)
    db.flush()

    player = DBPlayer(
        player_id="p_int_1", name="Test QB", position="QB",
        nfl_team="KC", projected_points=22.5, actual_points=25.0,
        team_id=team.id,
    )
    db.add(player)
    db.flush()

    season = DBPlayerSeasonStats(
        player_id=player.id, year=2024, games_played=16,
        pass_att=550, pass_cmp=370,
        pass_yd=4500, pass_td=35, pass_int=10,
        rush_att=60, rush_yd=300, rush_td=3,
        fantasy_points_total=350.0, fantasy_points_avg=21.9,
        fantasy_points_per_touch=1.1, snap_pct=98.5,
    )
    db.add(season)

    for week in range(1, 5):
        log = DBPlayerGameLog(
            player_id=player.id, year=2024, week=week,
            opponent=f"OPP{week}",
            pass_cmp=25, pass_att=35,
            pass_yd=280 + week * 10, pass_td=2, pass_int=1,
            rush_att=5, rush_yd=20, rush_td=0,
            fantasy_points=18.0 + week,
        )
        db.add(log)

    db.commit()
    return player


@pytest.fixture
def wr_player_with_stats(db):
    """Create a WR player with season stats including targets/receptions."""
    player = DBPlayer(
        player_id="p_int_wr", name="Test WR", position="WR",
        nfl_team="SF", projected_points=15.0, actual_points=14.0,
    )
    db.add(player)
    db.flush()

    season = DBPlayerSeasonStats(
        player_id=player.id, year=2024, games_played=16,
        rush_yd=50, rush_td=0,
        targets=120, rec=88, rec_yd=1100, rec_td=8,
        fantasy_points_total=200.0, fantasy_points_avg=12.5,
        snap_pct=85.0,
    )
    db.add(season)

    for week in range(1, 4):
        log = DBPlayerGameLog(
            player_id=player.id, year=2024, week=week,
            opponent=f"OPP{week}",
            targets=8, rec=6, rec_yd=75, rec_td=1,
            fantasy_points=12.0 + week,
        )
        db.add(log)

    db.commit()
    return player


class TestPlayerDetailPage:
    def test_player_detail_page_loads(self, player_with_stats):
        response = client.get(f"/players/{player_with_stats.id}")
        assert response.status_code == 200
        assert b"Test QB" in response.content
        assert b"Season Stats" in response.content
        assert b"Game Logs" in response.content
        assert b"Projections" in response.content

    def test_player_detail_redirects_for_unknown(self):
        response = client.get("/players/99999", follow_redirects=False)
        assert response.status_code == 302
        assert "/players" in response.headers["location"]

    def test_player_detail_shows_season_stats(self, player_with_stats):
        response = client.get(f"/players/{player_with_stats.id}")
        assert response.status_code == 200
        content = response.content
        assert b"2024" in content
        assert b"4500" in content   # pass_yd

    def test_player_detail_shows_game_logs(self, player_with_stats):
        response = client.get(f"/players/{player_with_stats.id}")
        assert response.status_code == 200
        # Should show week entries
        assert b"W1" in response.content
        assert b"W4" in response.content

    def test_wr_player_shows_targets_column(self, wr_player_with_stats):
        response = client.get(f"/players/{wr_player_with_stats.id}")
        assert response.status_code == 200
        content = response.content
        # Targets header and value
        assert b"Tgt" in content
        assert b"1100" in content   # rec_yd


class TestPlayerStatsAPI:
    def test_get_player_stats_json(self, player_with_stats):
        response = client.get(f"/api/stats/players/{player_with_stats.id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Test QB"
        assert data["position"] == "QB"
        assert len(data["seasons"]) == 1
        season = data["seasons"][0]
        assert season["pass_yd"] == 4500
        assert season["targets"] == 0
        assert "pass_att" in season
        assert "pass_cmp" in season
        assert "rush_att" in season

    def test_get_player_stats_year_filter(self, player_with_stats):
        response = client.get(
            f"/api/stats/players/{player_with_stats.id}?year=2024"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["seasons"]) == 1
        assert data["seasons"][0]["year"] == 2024

    def test_get_player_stats_year_filter_no_match(self, player_with_stats):
        response = client.get(
            f"/api/stats/players/{player_with_stats.id}?year=2020"
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["seasons"]) == 0

    def test_get_player_stats_not_found(self):
        response = client.get("/api/stats/players/99999")
        assert response.status_code == 404

    def test_wr_season_stats_includes_targets(self, wr_player_with_stats):
        response = client.get(f"/api/stats/players/{wr_player_with_stats.id}")
        assert response.status_code == 200
        data = response.json()
        season = data["seasons"][0]
        assert season["targets"] == 120
        assert season["rec"] == 88
        assert season["rec_yd"] == 1100


class TestPlayerGameLogsAPI:
    def test_get_game_logs(self, player_with_stats):
        response = client.get(
            f"/api/stats/players/{player_with_stats.id}/game-logs"
        )
        assert response.status_code == 200
        logs = response.json()
        assert len(logs) == 4
        # Most recent first
        assert logs[0]["week"] == 4

    def test_get_game_logs_limited(self, player_with_stats):
        response = client.get(
            f"/api/stats/players/{player_with_stats.id}/game-logs?limit=2"
        )
        assert response.status_code == 200
        logs = response.json()
        assert len(logs) == 2

    def test_get_game_logs_year_filter(self, player_with_stats):
        response = client.get(
            f"/api/stats/players/{player_with_stats.id}/game-logs?year=2023"
        )
        assert response.status_code == 200
        assert response.json() == []


class TestPlayerTrendsAPI:
    def test_get_trends(self, player_with_stats):
        response = client.get(
            f"/api/stats/players/{player_with_stats.id}/trends?weeks=4"
        )
        assert response.status_code == 200
        data = response.json()
        assert data["num_weeks"] == 4
        assert data["recent_avg_points"] > 0
        assert "trend_vs_season" in data

    def test_get_trends_no_data(self):
        # Create an unrostered player with no logs
        with TestSessionLocal() as db:
            p = DBPlayer(
                player_id="p_nodata", name="No Data Player",
                position="RB", nfl_team="FA",
            )
            db.add(p)
            db.commit()
            pid = p.id

        response = client.get(f"/api/stats/players/{pid}/trends")
        assert response.status_code == 200
        data = response.json()
        assert data["num_weeks"] == 0


class TestPlayerSearchAPI:
    def test_search_by_name(self, player_with_stats):
        response = client.get("/api/players/search?q=Test+QB")
        assert response.status_code == 200
        assert b"Test QB" in response.content

    def test_search_by_position(self, player_with_stats):
        response = client.get("/api/players/search?q=QB")
        assert response.status_code == 200
        assert b"Test QB" in response.content

    def test_search_empty_returns_results(self, player_with_stats):
        response = client.get("/api/players/search?q=")
        assert response.status_code == 200

    def test_search_no_match(self, player_with_stats):
        response = client.get("/api/players/search?q=zzznomatch")
        assert response.status_code == 200
        assert b"No players found" in response.content

    def test_list_players(self, player_with_stats):
        response = client.get("/api/players/list")
        assert response.status_code == 200
        assert b"Test QB" in response.content

    def test_list_players_position_filter(self, player_with_stats, wr_player_with_stats):
        response = client.get("/api/players/list?position=WR")
        assert response.status_code == 200
        assert b"Test WR" in response.content
        assert b"Test QB" not in response.content
