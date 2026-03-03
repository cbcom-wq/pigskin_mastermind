"""Integration tests for projection tuner algorithm-run endpoints."""

import time

import pytest
from fastapi.testclient import TestClient

from pigskin_mastermind.api.main import app
from pigskin_mastermind.api.routes import projection_tuner as projection_tuner_routes
from pigskin_mastermind.models.database import (
    DBNFLTeamStats,
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
    DBTeam,
)
from pigskin_mastermind.services.projection_algorithm_tuner import (
    ProjectionAlgorithmTuner as BaseProjectionAlgorithmTuner,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_algorithm_runtime(monkeypatch, tmp_path):
    """Isolate algorithm run history and in-memory job state for each test."""

    class TmpProjectionAlgorithmTuner(BaseProjectionAlgorithmTuner):
        def __init__(self, db, results_dir=None):
            super().__init__(db, results_dir=str(tmp_path))

    monkeypatch.setattr(
        projection_tuner_routes,
        "ProjectionAlgorithmTuner",
        TmpProjectionAlgorithmTuner,
    )
    with projection_tuner_routes._tuning_jobs_lock:
        projection_tuner_routes._tuning_jobs.clear()
    yield
    with projection_tuner_routes._tuning_jobs_lock:
        projection_tuner_routes._tuning_jobs.clear()


@pytest.fixture
def tuner_seed_data(db):
    """Seed enough historical data for algorithm tuning runs."""
    team = DBTeam(team_id="alg_t1", name="Alg Team", owner="Tester")
    db.add(team)
    db.flush()

    qb = DBPlayer(
        player_id="alg_qb_1",
        name="Algo QB",
        position="QB",
        nfl_team="KC",
        team_id=team.id,
        stats={"age": 28, "injuryStatus": ""},
    )
    rb = DBPlayer(
        player_id="alg_rb_1",
        name="Algo RB",
        position="RB",
        nfl_team="KC",
        team_id=team.id,
        stats={"age": 25, "injuryStatus": ""},
    )
    db.add(qb)
    db.add(rb)
    db.flush()

    db.add(
        DBPlayerSeasonStats(
            player_id=qb.id,
            year=2024,
            games_played=16,
            pass_att=520,
            pass_cmp=350,
            pass_yd=4300,
            pass_td=30,
            pass_int=9,
            rush_att=45,
            rush_yd=220,
            rush_td=2,
            fantasy_points_total=320.0,
            fantasy_points_avg=20.0,
            fantasy_points_per_touch=0.56,
            snap_pct=0.95,
        )
    )
    db.add(
        DBPlayerSeasonStats(
            player_id=rb.id,
            year=2024,
            games_played=16,
            rush_att=240,
            rush_yd=1150,
            rush_td=10,
            rec=45,
            rec_yd=330,
            rec_td=2,
            targets=55,
            fantasy_points_total=225.0,
            fantasy_points_avg=14.1,
            fantasy_points_per_touch=0.79,
            snap_pct=0.72,
        )
    )

    for week in range(1, 7):
        db.add(
            DBPlayerGameLog(
                player_id=qb.id,
                year=2024,
                week=week,
                opponent="LV",
                pass_yd=260,
                pass_td=2,
                pass_int=1,
                fantasy_points=18.0 + week,
            )
        )
        db.add(
            DBPlayerGameLog(
                player_id=rb.id,
                year=2024,
                week=week,
                opponent="LV",
                rush_yd=78,
                rush_td=1 if week % 2 == 0 else 0,
                rec=4,
                targets=5,
                fantasy_points=11.0 + week,
            )
        )

    db.add(
        DBNFLTeamStats(
            nfl_team="KC",
            year=2024,
            week=None,
            total_yards=5900,
            points_scored=430,
            def_rank_vs_qb=14,
            def_rank_vs_rb=13,
            def_rank_vs_wr=12,
            def_rank_vs_te=11,
        )
    )
    db.add(
        DBNFLTeamStats(
            nfl_team="LV",
            year=2024,
            week=None,
            total_yards=5000,
            points_scored=330,
            def_rank_vs_qb=24,
            def_rank_vs_rb=19,
            def_rank_vs_wr=17,
            def_rank_vs_te=22,
        )
    )

    db.commit()


def _wait_for_job(job_id, timeout_seconds=8.0):
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        response = client.get(f"/api/projection-tuner/algorithm/jobs/{job_id}")
        assert response.status_code == 200
        last = response.json()
        if last.get("status") in {"completed", "failed"}:
            return last
        time.sleep(0.1)
    raise AssertionError(f"Timed out waiting for job {job_id}. Last payload: {last}")


def test_projection_tuner_page_has_algorithm_section(tuner_seed_data):
    response = client.get("/projection-tuner")
    assert response.status_code == 200
    assert b"Algorithm Tuning Runs" in response.content


def test_start_algorithm_run_and_complete(tuner_seed_data):
    response = client.post(
        "/api/projection-tuner/algorithm/run",
        json={"year": 2024, "max_variations": 5, "top_n": 3},
    )
    assert response.status_code == 200
    payload = response.json()
    assert "job_id" in payload

    job = _wait_for_job(payload["job_id"])
    assert job["status"] == "completed", job.get("error")
    assert "run_id" in job
    assert "summary" in job
    assert "report" in job
    assert "visuals" in job
    assert "detail" in job
    assert job["summary"]["variations_tested"] == 5


def test_algorithm_run_history_endpoints(tuner_seed_data):
    start_resp = client.post(
        "/api/projection-tuner/algorithm/run",
        json={"year": 2024, "positions": ["QB"], "max_variations": 5, "top_n": 3},
    )
    assert start_resp.status_code == 200
    job = _wait_for_job(start_resp.json()["job_id"])
    assert job["status"] == "completed", job.get("error")
    run_id = job["run_id"]

    # Verify persisted run detail is available independent of in-memory job state.
    with projection_tuner_routes._tuning_jobs_lock:
        projection_tuner_routes._tuning_jobs.clear()

    list_resp = client.get("/api/projection-tuner/algorithm/runs?limit=10")
    assert list_resp.status_code == 200
    runs_payload = list_resp.json()
    assert runs_payload["count"] >= 1
    assert any(r["run_id"] == run_id for r in runs_payload["runs"])

    detail_resp = client.get(f"/api/projection-tuner/algorithm/runs/{run_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["summary"]["run_id"] == run_id
    assert "detail" in detail
    assert "report" in detail
    assert "visuals" in detail
    assert "top_variations" in detail["visuals"]
    assert detail["detail"]["player_count"] >= 1
    assert detail["detail"]["sample_count"] == detail["summary"]["sample_count"]
    assert detail["detail"]["players_simulated"]
    assert detail["detail"]["projected_vs_actual"]
    assert detail["detail"]["player_count"] == len(detail["detail"]["players_simulated"])
    assert detail["detail"]["sample_count"] == len(detail["detail"]["projected_vs_actual"])
    player = detail["detail"]["players_simulated"][0]
    assert "player_id" in player
    assert "player_name" in player
    assert "position" in player
    assert player["samples"] >= 1
    row = detail["detail"]["projected_vs_actual"][0]
    assert row["player_id"] == player["player_id"]
    assert row["player_name"] == player["player_name"]
    assert "week" in row
    assert "position" in row
    assert "default_projected_points" in row
    assert "tuned_projected_points" in row
    assert "actual_points" in row
    assert "error_delta" in row


def test_projection_tuner_run_detail_page_renders(tuner_seed_data):
    start_resp = client.post(
        "/api/projection-tuner/algorithm/run",
        json={"year": 2024, "max_variations": 5, "top_n": 3},
    )
    assert start_resp.status_code == 200
    job = _wait_for_job(start_resp.json()["job_id"])
    assert job["status"] == "completed", job.get("error")

    detail_resp = client.get(f"/api/projection-tuner/algorithm/runs/{job['run_id']}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()["detail"]
    sample_player = detail["players_simulated"][0]
    sample_row = detail["projected_vs_actual"][0]

    page_resp = client.get(f"/projection-tuner/runs/{job['run_id']}")
    assert page_resp.status_code == 200
    assert b"Players Simulated" in page_resp.content
    assert b"Projected vs Actual Samples" in page_resp.content
    assert b"Week" in page_resp.content
    assert b"Actual" in page_resp.content
    assert b"Default Proj" in page_resp.content
    assert b"Tuned Proj" in page_resp.content
    assert sample_player["player_name"].encode() in page_resp.content
    assert str(sample_row["week"]).encode() in page_resp.content
    assert f'{sample_row["actual_points"]:.2f}'.encode() in page_resp.content
    assert f'{sample_row["default_projected_points"]:.2f}'.encode() in page_resp.content
    assert f'{sample_row["tuned_projected_points"]:.2f}'.encode() in page_resp.content


def test_projection_tuner_page_links_history_to_run_detail(tuner_seed_data):
    start_resp = client.post(
        "/api/projection-tuner/algorithm/run",
        json={"year": 2024, "max_variations": 5, "top_n": 3},
    )
    assert start_resp.status_code == 200
    job = _wait_for_job(start_resp.json()["job_id"])
    assert job["status"] == "completed", job.get("error")

    page_resp = client.get("/projection-tuner")
    assert page_resp.status_code == 200
    assert b"Open Run Detail Page" in page_resp.content
    assert f'/projection-tuner/runs/{job["run_id"]}'.encode() in page_resp.content


def test_algorithm_run_validation_error():
    response = client.post(
        "/api/projection-tuner/algorithm/run",
        json={"year": 2024, "max_variations": 5001, "top_n": 10},
    )
    assert response.status_code == 200
    assert "max_variations must be between 2 and 2000" in response.json()["error"]

