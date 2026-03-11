"""API routes for the Projection Algorithm Tuner developer tool."""

import threading
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker
from typing import Optional, Dict, List, Any, Tuple

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import (
    DBPlayer, DBPlayerGameLog, DBPlayerSeasonStats,
    DBNFLTeamStats, DBWeeklyPlayerStats, DBLeague,
)
from pigskin_mastermind.services.projection_algorithm_tuner import ProjectionAlgorithmTuner
from pigskin_mastermind.services.projection_tuner import (
    ProjectionTunerService,
    get_default_coefficients,
    get_coefficient_metadata,
    get_criteria_docs,
    active_players_query,
)
from pigskin_mastermind.services.master_coefficients import (
    load_master_coefficients,
    load_master_coefficients_raw,
    save_master_coefficients,
    reset_master_coefficients,
    get_effective_coefficients,
)

router = APIRouter(tags=["projection-tuner"])


# ── Pydantic request models ──────────────────────────────────────────────

class SimulatePlayerRequest(BaseModel):
    player_id: int
    year: int
    week: Optional[int] = None
    projection_type: str = "weekly"  # "weekly" or "yearly"
    # Flat {"skill_multiplier": 0.12} or position-keyed {"QB": {...}, "RB": {...}}
    coefficients: Optional[Dict[str, Any]] = None
    # MC hyper-parameters, e.g. {"touch_std_fraction": 0.3, "base_td_lambda": 0.8}
    mc_params: Optional[Dict[str, Any]] = None


class SimulateBulkRequest(BaseModel):
    position: Optional[str] = None
    year: int
    week: Optional[int] = None
    projection_type: str = "weekly"
    # Flat {"skill_multiplier": 0.12} or position-keyed {"QB": {...}, "RB": {...}}
    coefficients: Optional[Dict[str, Any]] = None
    # MC hyper-parameters, e.g. {"touch_std_fraction": 0.3, "base_td_lambda": 0.8}
    mc_params: Optional[Dict[str, Any]] = None


class CriteriaGridRequest(BaseModel):
    position: Optional[str] = None
    year: int
    week: Optional[int] = None
    projection_type: str = "weekly"
    player_ids: Optional[List[int]] = None  # None = all matching players
    limit: int = Field(50, ge=1, le=200)


class AlgorithmRunRequest(BaseModel):
    year: int
    weeks: Optional[List[int]] = None
    player_ids: Optional[List[int]] = None
    positions: Optional[List[str]] = None
    max_variations: int = 250
    top_n: int = 10
    per_position: bool = True  # tune coefficients independently per position


class AcceptCoefficientsRequest(BaseModel):
    """Accept tuned coefficients as the new master values."""
    # Flat {"skill_multiplier": 0.12} or position-keyed {"default": {...}, "QB": {...}}
    coefficients: Dict[str, Any]
    source_run_id: Optional[str] = None
    source_description: Optional[str] = None


_VALID_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}
_MAX_TUNING_VARIATIONS = 2000
_MAX_TOP_N = 50
_tuning_jobs: Dict[str, Dict[str, Any]] = {}
_tuning_jobs_lock = threading.Lock()
_import_jobs: Dict[str, Dict[str, Any]] = {}
_import_jobs_lock = threading.Lock()
_MAX_IMPORT_LOGS = 500


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalise_algorithm_request(req: AlgorithmRunRequest) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if req.max_variations < 2 or req.max_variations > _MAX_TUNING_VARIATIONS:
        return None, f"max_variations must be between 2 and {_MAX_TUNING_VARIATIONS}"
    if req.top_n < 1 or req.top_n > _MAX_TOP_N:
        return None, f"top_n must be between 1 and {_MAX_TOP_N}"
    if req.top_n > req.max_variations:
        return None, "top_n cannot be greater than max_variations"

    weeks = None
    if req.weeks:
        weeks = sorted({int(w) for w in req.weeks})
        if any(w < 1 or w > 22 for w in weeks):
            return None, "weeks must be between 1 and 22"

    player_ids = None
    if req.player_ids:
        player_ids = sorted({int(pid) for pid in req.player_ids if int(pid) > 0})
        if not player_ids:
            return None, "player_ids must contain at least one positive id"

    positions = None
    if req.positions:
        positions = sorted({p.upper() for p in req.positions if p})
        invalid = [p for p in positions if p not in _VALID_POSITIONS]
        if invalid:
            return None, f"Unsupported positions: {', '.join(invalid)}"

    return {
        "year": req.year,
        "weeks": weeks,
        "player_ids": player_ids,
        "positions": positions,
        "max_variations": req.max_variations,
        "top_n": req.top_n,
        "per_position": req.per_position,
    }, None


def _snapshot_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _tuning_jobs_lock:
        job = _tuning_jobs.get(job_id)
        if not job:
            return None
        return dict(job)


def _update_job(job_id: str, **updates: Any) -> None:
    with _tuning_jobs_lock:
        if job_id not in _tuning_jobs:
            return
        _tuning_jobs[job_id].update(updates)


def _snapshot_import_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _import_jobs_lock:
        job = _import_jobs.get(job_id)
        if not job:
            return None
        copied = dict(job)
        copied["logs"] = list(job.get("logs", []))
        return copied


def _update_import_job(job_id: str, **updates: Any) -> None:
    with _import_jobs_lock:
        if job_id not in _import_jobs:
            return
        _import_jobs[job_id].update(updates)


def _append_import_log(job_id: str, message: str) -> None:
    timestamped = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
    with _import_jobs_lock:
        if job_id not in _import_jobs:
            return
        logs = _import_jobs[job_id].setdefault("logs", [])
        logs.append(timestamped)
        if len(logs) > _MAX_IMPORT_LOGS:
            del logs[:-_MAX_IMPORT_LOGS]


def _run_espn_import_job(job_id: str, year: int, db_factory: Any) -> None:
    _update_import_job(
        job_id,
        status="running",
        started_at=_utc_now_iso(),
        progress_pct=1,
        message="Starting ESPN preload…",
    )
    db = db_factory()
    try:
        league = db.query(DBLeague).first()
        if not league:
            raise ValueError("No ESPN league configured. Add a league first.")
        if not league.espn_s2 or not league.swid:
            raise ValueError("ESPN credentials (espn_s2 / swid) are missing for your league.")

        from pigskin_mastermind.services.espn_sync import ESPNSyncService, TUNER_PLAYER_LIMITS

        _append_import_log(job_id, f"Using league {league.league_id} with limits {TUNER_PLAYER_LIMITS}.")
        service = ESPNSyncService(db)

        def progress_callback(progress_pct: int, message: str) -> None:
            _update_import_job(job_id, progress_pct=progress_pct, message=message)

        def log_callback(message: str) -> None:
            _append_import_log(job_id, message)

        result = service.import_relevant_players_for_tuner(
            league_id=league.league_id,
            espn_s2=league.espn_s2,
            swid=league.swid,
            year=year,
            preload_full_history=True,
            progress_callback=progress_callback,
            log_callback=log_callback,
        )
        pos_summary = ", ".join(
            f"{pos}: {result.get(pos, 0)}"
            for pos in TUNER_PLAYER_LIMITS
        )
        _update_import_job(
            job_id,
            status="completed",
            progress_pct=100,
            finished_at=_utc_now_iso(),
            message=f"Completed ESPN preload for {result['total']} players.",
            result=result,
            summary=(
                f"Imported {result['total']} players ({pos_summary}), preloaded "
                f"{result.get('preloaded_players', 0)} full histories, and stored "
                f"{result.get('history_game_logs', 0)} game logs."
            ),
        )
    except Exception as exc:
        _append_import_log(job_id, f"Import failed: {exc}")
        _update_import_job(
            job_id,
            status="failed",
            finished_at=_utc_now_iso(),
            message=str(exc),
            error=str(exc),
        )
    finally:
        db.close()


def _build_algorithm_visuals(result: Any, report: Dict[str, Any]) -> Dict[str, Any]:
    default_per_pos = result.default_result.per_position_mae or {}
    tuned_per_pos = result.best.per_position_mae or {}
    per_position = []
    for pos in sorted(set(default_per_pos.keys()) | set(tuned_per_pos.keys())):
        default_mae = default_per_pos.get(pos)
        tuned_mae = tuned_per_pos.get(pos)
        delta = None
        if default_mae is not None and tuned_mae is not None:
            delta = round(default_mae - tuned_mae, 4)
        per_position.append(
            {
                "position": pos,
                "default_mae": default_mae,
                "tuned_mae": tuned_mae,
                "mae_reduction": delta,
            }
        )

    top_variations = []
    for idx, variation in enumerate(result.top_variations, start=1):
        top_variations.append(
            {
                "rank": idx,
                "mae": variation.mae,
                "rmse": variation.rmse,
                "sample_count": variation.sample_count,
                "coefficients": variation.coefficients,
            }
        )

    coefficient_changes = []
    for key, vals in report.get("coefficient_changes", {}).items():
        coefficient_changes.append(
            {
                "key": key,
                "default": vals.get("default"),
                "tuned": vals.get("tuned"),
                "change_pct": vals.get("change_pct"),
            }
        )
    coefficient_changes.sort(key=lambda c: abs(c.get("change_pct", 0.0)), reverse=True)

    return {
        "kpis": {
            "default_mae": report["default_accuracy"]["mae"],
            "tuned_mae": report["best_accuracy"]["mae"],
            "default_rmse": report["default_accuracy"]["rmse"],
            "tuned_rmse": report["best_accuracy"]["rmse"],
            "mae_reduction": report["improvement"]["mae_reduction"],
            "mae_improvement_pct": report["improvement"]["mae_improvement_pct"],
            "players_evaluated": report["summary"]["players_evaluated"],
            "total_samples": report["summary"]["total_samples"],
            "variations_tested": report["summary"]["variations_tested"],
        },
        "top_variations": top_variations,
        "per_position_comparison": per_position,
        "coefficient_changes": coefficient_changes,
    }


def _build_algorithm_run_summary(result: Any) -> Dict[str, Any]:
    default_mae = result.default_result.mae
    best_mae = result.best.mae
    mae_reduction = round(default_mae - best_mae, 4)
    mae_pct = round((mae_reduction / default_mae * 100) if default_mae else 0.0, 2)
    return {
        "run_id": result.run_id,
        "timestamp": result.timestamp,
        "year": result.year,
        "weeks": result.weeks,
        "player_count": result.player_count,
        "sample_count": result.sample_count,
        "variations_tested": result.variations_tested,
        "default_mae": default_mae,
        "best_mae": best_mae,
        "mae_reduction": mae_reduction,
        "mae_improvement_pct": mae_pct,
    }


def _build_algorithm_run_detail(result: Any) -> Dict[str, Any]:
    players: Dict[int, Dict[str, Any]] = {}
    projected_vs_actual: List[Dict[str, Any]] = []

    for sample in result.sample_comparisons:
        projected_vs_actual.append(
            {
                "player_id": sample.player_id,
                "player_name": sample.player_name,
                "position": sample.position,
                "week": sample.week,
                "actual_points": sample.actual_points,
                "default_projected_points": sample.default_projected_points,
                "tuned_projected_points": sample.tuned_projected_points,
                "default_error": sample.default_error,
                "tuned_error": sample.tuned_error,
                "error_delta": round(sample.default_error - sample.tuned_error, 4),
            }
        )
        if sample.player_id not in players:
            players[sample.player_id] = {
                "player_id": sample.player_id,
                "player_name": sample.player_name,
                "position": sample.position,
                "samples": 0,
            }
        players[sample.player_id]["samples"] += 1

    players_simulated = sorted(
        players.values(),
        key=lambda p: (-p["samples"], p["position"], p["player_name"]),
    )
    return {
        "player_count": len(players_simulated),
        "sample_count": len(projected_vs_actual),
        "players_simulated": players_simulated,
        "projected_vs_actual": projected_vs_actual,
    }


def _run_algorithm_job(job_id: str, req_data: Dict[str, Any], db_factory: Any) -> None:
    _update_job(
        job_id,
        status="running",
        progress_pct=5,
        started_at=_utc_now_iso(),
        message="Preparing historical samples",
    )
    job_db = db_factory()
    try:
        tuner = ProjectionAlgorithmTuner(job_db)
        _update_job(
            job_id,
            progress_pct=20,
            message="Evaluating coefficient variations"
            + (" (per-position)" if req_data.get("per_position") else ""),
        )
        run_method = (
            tuner.run_per_position
            if req_data.get("per_position")
            else tuner.run
        )
        result = run_method(
            year=req_data["year"],
            weeks=req_data["weeks"],
            player_ids=req_data["player_ids"],
            positions=req_data["positions"],
            max_variations=req_data["max_variations"],
            top_n=req_data["top_n"],
        )
        _update_job(
            job_id,
            progress_pct=85,
            message="Saving and summarizing results",
        )
        saved_path = tuner.save_result(result)
        report = tuner.generate_analysis_report(result)
        visuals = _build_algorithm_visuals(result, report)
        detail = _build_algorithm_run_detail(result)

        _update_job(
            job_id,
            status="completed",
            progress_pct=100,
            finished_at=_utc_now_iso(),
            message="Completed",
            run_id=result.run_id,
            saved_path=saved_path,
            summary=_build_algorithm_run_summary(result),
            result=result.to_dict(),
            report=report,
            visuals=visuals,
            detail=detail,
        )
    except Exception as exc:
        _update_job(
            job_id,
            status="failed",
            progress_pct=100,
            finished_at=_utc_now_iso(),
            message="Failed",
            error=str(exc),
        )
    finally:
        job_db.close()


# ── Page route ────────────────────────────────────────────────────────────

@router.get("/projection-tuner")
async def projection_tuner_page(
    request: Request,
    db: Session = Depends(get_db),
):
    """Render the Projection Algorithm Tuner page."""
    from pigskin_mastermind.api.main import templates

    tuner = ProjectionAlgorithmTuner(db)
    recent_runs = sorted(tuner.load_results(), key=lambda r: r.timestamp, reverse=True)[:50]
    recent_run_summaries = [_build_algorithm_run_summary(r) for r in recent_runs]

    # Get available players for the dropdown
    players = (
        db.query(DBPlayer)
        .filter(DBPlayer.position.in_(["QB", "RB", "WR", "TE"]))
        .order_by(DBPlayer.position, DBPlayer.name)
        .all()
    )

    # Get available years from game logs or season stats
    years_from_logs = (
        db.query(DBPlayerGameLog.year)
        .distinct()
        .order_by(DBPlayerGameLog.year.desc())
        .all()
    )
    years_from_seasons = (
        db.query(DBPlayerSeasonStats.year)
        .distinct()
        .order_by(DBPlayerSeasonStats.year.desc())
        .all()
    )
    year_set = sorted(
        {y[0] for y in years_from_logs} | {y[0] for y in years_from_seasons},
        reverse=True,
    )
    available_years = year_set if year_set else [2024, 2023]

    return templates.TemplateResponse(
        "projection_tuner.html",
        {
            "request": request,
            "players": players,
            "available_years": available_years,
            "coefficients": get_coefficient_metadata(),
            "criteria_docs": get_criteria_docs(),
            "defaults": get_default_coefficients(),
            "algorithm_runs": recent_run_summaries,
            "selected_algorithm_run_id": (
                recent_run_summaries[0]["run_id"] if recent_run_summaries else None
            ),
        },
    )


@router.get("/projection-tuner/runs/{run_id}")
async def projection_tuner_run_detail_page(
    request: Request,
    run_id: str,
    db: Session = Depends(get_db),
):
    """Render a detail page for one persisted algorithm tuning run."""
    from pigskin_mastermind.api.main import templates

    tuner = ProjectionAlgorithmTuner(db)
    run = next((r for r in tuner.load_results() if r.run_id == run_id), None)
    if not run:
        return templates.TemplateResponse(
            "projection_tuner_run_detail.html",
            {
                "request": request,
                "run_id": run_id,
                "error": f"Run {run_id} not found",
                "summary": None,
                "detail": {
                    "player_count": 0,
                    "sample_count": 0,
                    "players_simulated": [],
                    "projected_vs_actual": [],
                },
            },
        )

    return templates.TemplateResponse(
        "projection_tuner_run_detail.html",
        {
            "request": request,
            "run_id": run_id,
            "error": None,
            "summary": _build_algorithm_run_summary(run),
            "detail": _build_algorithm_run_detail(run),
        },
    )


# ── API endpoints ─────────────────────────────────────────────────────────

@router.get("/api/projection-tuner/defaults")
async def get_defaults():
    """Return default coefficients and algorithm documentation."""
    from pigskin_mastermind.models.algorithm_coefficients import (
        PositionCoefficients,
        TUNABLE_POSITIONS,
    )

    pos_coeffs = PositionCoefficients.from_global()
    master_raw = load_master_coefficients_raw()
    master_meta = None
    has_master = False
    if master_raw is not None:
        master_meta = master_raw.pop("_meta", None)
        has_master = True

    return {
        "coefficients": get_coefficient_metadata(),
        "criteria_docs": get_criteria_docs(),
        "defaults": get_default_coefficients(),
        "positions": TUNABLE_POSITIONS,
        "per_position_defaults": pos_coeffs.to_dict(),
        "has_master_coefficients": has_master,
        "master_coefficients": master_raw if has_master else None,
        "master_meta": master_meta,
    }


# ── Master coefficients management ───────────────────────────────────────


@router.get("/api/projection-tuner/master-coefficients")
async def get_master_coefficients():
    """Return the currently active master coefficients.

    If no master coefficients have been accepted yet, the built-in defaults
    are returned with ``is_custom: false``.
    """
    raw = load_master_coefficients_raw()
    if raw is not None:
        meta = raw.pop("_meta", {})
        return {
            "is_custom": True,
            "coefficients": raw,
            "meta": meta,
        }

    # No master file — return built-in defaults
    effective = get_effective_coefficients()
    return {
        "is_custom": False,
        "coefficients": effective.to_dict(),
        "meta": None,
    }


@router.post("/api/projection-tuner/master-coefficients/accept")
async def accept_master_coefficients(req: AcceptCoefficientsRequest):
    """Accept tuned coefficients as the new master values.

    The supplied coefficients become the active values used by production
    projection services.  Both flat (single-set) and position-keyed
    formats are accepted.
    """
    try:
        path = save_master_coefficients(
            req.coefficients,
            source_run_id=req.source_run_id,
            source_description=req.source_description,
        )
        return {
            "status": "accepted",
            "message": "Coefficients accepted as new master values.",
            "path": path,
            "source_run_id": req.source_run_id,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/projection-tuner/master-coefficients/accept-from-run/{run_id}")
async def accept_coefficients_from_run(
    run_id: str,
    db: Session = Depends(get_db),
):
    """Accept the best coefficients from a saved tuning run as master values.

    Loads the specified run, extracts its best (or combined per-position)
    coefficients, and persists them as the new master coefficients.
    """
    tuner = ProjectionAlgorithmTuner(db)
    run = next((r for r in tuner.load_results() if r.run_id == run_id), None)
    if not run:
        return {"status": "error", "message": f"Run {run_id} not found"}

    # Use combined per-position coefficients if available, else the best flat set
    if run.combined_coefficients:
        coefficients = run.combined_coefficients
        description = (
            f"Per-position coefficients from tuning run {run_id} "
            f"(year={run.year}, {run.sample_count} samples, "
            f"MAE {run.best.mae:.4f})"
        )
    else:
        coefficients = run.best.coefficients
        description = (
            f"Best coefficients from tuning run {run_id} "
            f"(year={run.year}, {run.sample_count} samples, "
            f"MAE {run.best.mae:.4f})"
        )

    try:
        path = save_master_coefficients(
            coefficients,
            source_run_id=run_id,
            source_description=description,
        )
        return {
            "status": "accepted",
            "message": f"Coefficients from run {run_id} accepted as new master values.",
            "path": path,
            "source_run_id": run_id,
            "mae": run.best.mae,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@router.post("/api/projection-tuner/master-coefficients/reset")
async def reset_to_default_coefficients():
    """Reset master coefficients back to the built-in defaults.

    Removes the persisted master file so all projection services revert
    to the hard-coded values in ``AlgorithmCoefficients``.
    """
    removed = reset_master_coefficients()
    if removed:
        return {
            "status": "reset",
            "message": "Master coefficients reset to built-in defaults.",
        }
    return {
        "status": "no_change",
        "message": "No custom master coefficients were saved — already using defaults.",
    }


@router.post("/api/projection-tuner/algorithm/run")
async def start_algorithm_tuning_run(
    req: AlgorithmRunRequest,
    db: Session = Depends(get_db),
):
    """Start a background projection-algorithm tuning run."""
    req_data, error = _normalise_algorithm_request(req)
    if error:
        return {"error": error}

    job_id = uuid.uuid4().hex
    created_at = _utc_now_iso()
    with _tuning_jobs_lock:
        _tuning_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "progress_pct": 0,
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "message": "Queued",
            "error": None,
            "request": req_data,
        }

    db_factory = sessionmaker(autocommit=False, autoflush=False, bind=db.get_bind())
    worker = threading.Thread(
        target=_run_algorithm_job,
        args=(job_id, req_data, db_factory),
        daemon=True,
    )
    worker.start()

    return {
        "job_id": job_id,
        "status": "queued",
        "progress_pct": 0,
        "created_at": created_at,
    }


@router.get("/api/projection-tuner/algorithm/jobs/{job_id}")
async def get_algorithm_tuning_job(job_id: str):
    """Return background algorithm tuning job status and output when available."""
    job = _snapshot_job(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}
    return job


@router.get("/api/projection-tuner/algorithm/runs")
async def list_algorithm_tuning_runs(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List persisted algorithm tuning runs (newest first)."""
    tuner = ProjectionAlgorithmTuner(db)
    runs = tuner.load_results()
    runs.sort(key=lambda r: r.timestamp, reverse=True)
    runs = runs[:limit]
    return {
        "runs": [_build_algorithm_run_summary(r) for r in runs],
        "count": len(runs),
    }


@router.get("/api/projection-tuner/algorithm/runs/{run_id}")
async def get_algorithm_tuning_run(
    run_id: str,
    db: Session = Depends(get_db),
):
    """Return full output for a single persisted tuning run."""
    tuner = ProjectionAlgorithmTuner(db)
    run = next((r for r in tuner.load_results() if r.run_id == run_id), None)
    if not run:
        return {"error": f"Run {run_id} not found"}

    report = tuner.generate_analysis_report(run)
    visuals = _build_algorithm_visuals(run, report)
    return {
        "summary": _build_algorithm_run_summary(run),
        "detail": _build_algorithm_run_detail(run),
        "result": run.to_dict(),
        "report": report,
        "visuals": visuals,
    }


@router.post("/api/projection-tuner/simulate-player")
async def simulate_player(
    req: SimulatePlayerRequest,
    db: Session = Depends(get_db),
):
    """Run projection for a single player with custom coefficients."""
    service = ProjectionTunerService(db, coefficients=req.coefficients)
    player = db.query(DBPlayer).filter_by(id=req.player_id).first()
    if not player:
        return {"error": f"Player {req.player_id} not found"}

    try:
        if req.projection_type == "yearly":
            result = service.project_yearly(req.player_id, req.year)
        else:
            if req.week is None:
                return {"error": "week is required for weekly projections"}
            result = service.project_weekly(
                req.player_id, req.week, req.year, mc_params=req.mc_params
            )
    except ValueError as e:
        return {"error": str(e)}

    # Also compute with defaults for comparison
    default_service = ProjectionTunerService(db)
    try:
        if req.projection_type == "yearly":
            default_result = default_service.project_yearly(req.player_id, req.year)
        else:
            default_result = default_service.project_weekly(
                req.player_id, req.week, req.year
            )
    except (ValueError, Exception):
        default_result = None

    return {
        "player": {
            "id": player.id,
            "name": player.name,
            "position": player.position,
            "nfl_team": player.nfl_team,
        },
        "tuned": result,
        "default": default_result,
    }


@router.post("/api/projection-tuner/simulate-bulk")
async def simulate_bulk(
    req: SimulateBulkRequest,
    db: Session = Depends(get_db),
):
    """Run projections across a position group with custom coefficients."""
    tuned_service = ProjectionTunerService(db, coefficients=req.coefficients)
    default_service = ProjectionTunerService(db)

    try:
        if req.projection_type == "yearly":
            tuned = tuned_service.backtest_yearly(req.position, req.year)
            default = default_service.backtest_yearly(req.position, req.year)
        else:
            tuned = tuned_service.backtest_weekly(
                req.position, req.year, req.week, mc_params=req.mc_params
            )
            default = default_service.backtest_weekly(req.position, req.year, req.week)
    except (ValueError, Exception) as e:
        return {"error": str(e)}

    return {
        "tuned": tuned,
        "default": default,
        "position": req.position,
        "year": req.year,
        "week": req.week,
        "projection_type": req.projection_type,
    }


@router.post("/api/projection-tuner/criteria-grid")
async def criteria_grid(
    req: CriteriaGridRequest,
    db: Session = Depends(get_db),
):
    """Return a criteria spreadsheet for multiple players.

    Each row is a player; columns are the criteria fields plus projected/actual points.
    Used to assess whether auto-derived criteria values look sensible.
    """
    from pigskin_mastermind.services.projection_tuner import ProjectionTunerService

    # Build player list
    query = active_players_query(db, req.position)
    if req.player_ids:
        query = query.filter(DBPlayer.id.in_(req.player_ids))
    players = (
        query
        .order_by(
            DBPlayer.projected_points.desc(),
            DBPlayer.actual_points.desc(),
            DBPlayer.name,
        )
        .limit(req.limit)
        .all()
    )

    # Pre-populate structured stats for all players in the grid.
    # This mirrors the on-demand import the player detail page does
    # so every player gets game logs + season stats, not just those
    # previously viewed in the team/player pages.
    from pigskin_mastermind.services.projection_criteria_builder import (
        ProjectionCriteriaBuilder,
    )
    ensure_year = (req.year - 1) if req.projection_type == "yearly" else req.year
    builder = ProjectionCriteriaBuilder(db)
    builder.ensure_players_stats([p.id for p in players], ensure_year)

    service = ProjectionTunerService(db, criteria_builder=builder)
    rows = []
    for player in players:
        try:
            if req.projection_type == "yearly":
                result = service.project_yearly(player.id, req.year)
            else:
                if req.week is None:
                    # No week specified — build criteria directly without projection.
                    # Reuse the request-scoped builder so we keep the ensured-player
                    # cache instead of re-querying completeness for every row.
                    criteria = builder.build_weekly_criteria(player.id, 1, req.year)
                    result = {
                        "criteria": service._criteria_to_dict(criteria),
                        "total": None,
                        "actual_points": None,
                    }
                else:
                    result = service.project_weekly(player.id, req.week, req.year)
        except (ValueError, Exception):
            continue

        rows.append({
            "player_id": player.id,
            "player_name": player.name,
            "position": player.position,
            "nfl_team": player.nfl_team,
            "projected": result.get("total"),
            "actual": result.get("actual_points"),
            "criteria": result.get("criteria", {}),
            "mc_floor": result.get("monte_carlo", {}).get("floor") if result.get("monte_carlo") else None,
            "mc_ceiling": result.get("monte_carlo", {}).get("ceiling") if result.get("monte_carlo") else None,
            "mc_median": result.get("monte_carlo", {}).get("median") if result.get("monte_carlo") else None,
            "mc_std_dev": result.get("monte_carlo", {}).get("std_dev") if result.get("monte_carlo") else None,
            "mc_boom_pct": result.get("monte_carlo", {}).get("boom_probability") if result.get("monte_carlo") else None,
            "mc_bust_pct": result.get("monte_carlo", {}).get("bust_probability") if result.get("monte_carlo") else None,
            "mc_histogram": result.get("monte_carlo", {}).get("histogram") if result.get("monte_carlo") else None,
            "deterministic_total": result.get("deterministic_total"),
        })

    # Determine column order: base fields first, then type-specific
    base_cols = [
        "historical_average_points",
        "player_skill_level",
        "team_offense_level",
        "opponent_defense_level",
        "positional_touch_percentage",
        "recent_trend_score",
        "fantasy_points_per_touch",
        "injury_risk_score",
    ]
    weekly_cols = [
        "opposing_defense_vs_position_rank",
        "offensive_momentum_score",
        "weather_impact_score",
    ]
    yearly_cols = [
        "age_deviation_from_optimum",
        "coaching_stability_score",
    ]
    extra_cols = weekly_cols if req.projection_type == "weekly" else yearly_cols
    criteria_cols = base_cols + extra_cols

    # Compute per-column min/max for heat-map normalization
    col_stats: Dict[str, Any] = {}
    for col in criteria_cols:
        vals = [r["criteria"].get(col) for r in rows if r["criteria"].get(col) is not None]
        if vals:
            col_stats[col] = {"min": min(vals), "max": max(vals)}
        else:
            col_stats[col] = {"min": 0, "max": 0}

    return {
        "rows": rows,
        "criteria_cols": criteria_cols,
        "col_stats": col_stats,
        "projection_type": req.projection_type,
        "year": req.year,
        "week": req.week,
        "player_count": len(rows),
        "truncated": len(players) == req.limit,
    }


@router.get("/api/projection-tuner/players")
async def get_players_for_tuner(
    position: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Return players list, optionally filtered by position."""
    players = active_players_query(db, position).order_by(DBPlayer.name).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "position": p.position,
            "nfl_team": p.nfl_team,
        }
        for p in players
    ]


@router.post("/api/projection-tuner/import-nfl-data")
async def import_nfl_data_for_tuner(
    year: int = Query(..., description="Season year to import, e.g. 2024"),
    db: Session = Depends(get_db),
):
    """Import NFL-wide seasonal stats and defense rankings for a given year.

    Uses nfl_data_py (no ESPN credentials required) to populate
    DBPlayerSeasonStats and DBNFLTeamStats for all skill-position players,
    ensuring the criteria grid has populated values rather than zeros.
    """
    try:
        from pigskin_mastermind.services.nfl_data_service import NFLDataService
        service = NFLDataService(db)
        seasonal_rows = service.import_seasonal_stats([year])
        defense_rows = service.import_team_defense_rankings([year])
        return {
            "status": "ok",
            "year": year,
            "seasonal_rows": seasonal_rows,
            "defense_rows": defense_rows,
            "message": (
                f"Imported {seasonal_rows} player season records "
                f"and {defense_rows} team/defense stats for {year}."
            ),
        }
    except ImportError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "seasonal_rows": 0,
            "defense_rows": 0,
        }
    except Exception as exc:
        # nfl_data_py raises urllib.error.HTTPError (404) when nflverse hasn't
        # published data for the requested year yet.
        exc_str = str(exc)
        if "404" in exc_str or "Not Found" in exc_str:
            return {
                "status": "error",
                "message": (
                    f"No nflverse data available for {year} yet. "
                    "Try 2024 or an earlier year."
                ),
                "seasonal_rows": 0,
                "defense_rows": 0,
            }
        return {
            "status": "error",
            "message": f"Import failed: {exc}",
            "seasonal_rows": 0,
            "defense_rows": 0,
        }


@router.post("/api/projection-tuner/import-relevant-players")
async def import_relevant_players_for_tuner(
    year: int = Query(..., description="Season year to import, e.g. 2025"),
    db: Session = Depends(get_db),
):
    """Start a background ESPN import that preloads full player history.

    The worker first imports the curated player pool, then fetches each
    player's full ESPN history so the tuner can read locally-stored game logs
    rather than doing slow on-demand lookups from grid requests.
    """
    league = db.query(DBLeague).first()
    if not league:
        return {
            "status": "error",
            "message": "No ESPN league configured. Add a league first so credentials are available.",
        }
    if not league.espn_s2 or not league.swid:
        return {
            "status": "error",
            "message": "ESPN credentials (espn_s2 / swid) are missing for your league.",
        }

    job_id = uuid.uuid4().hex
    created_at = _utc_now_iso()
    with _import_jobs_lock:
        _import_jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "progress_pct": 0,
            "created_at": created_at,
            "started_at": None,
            "finished_at": None,
            "message": f"Queued ESPN preload for {year}.",
            "error": None,
            "year": year,
            "logs": [f"[{datetime.now().strftime('%H:%M:%S')}] Job queued for {year}."],
            "result": None,
            "summary": None,
        }

    db_factory = sessionmaker(autocommit=False, autoflush=False, bind=db.get_bind())
    worker = threading.Thread(
        target=_run_espn_import_job,
        args=(job_id, year, db_factory),
        daemon=True,
    )
    worker.start()

    return {
        "job_id": job_id,
        "status": "queued",
        "progress_pct": 0,
        "created_at": created_at,
        "message": f"Queued ESPN preload for {year}.",
    }


@router.get("/api/projection-tuner/import-jobs/{job_id}")
async def get_espn_import_job(job_id: str):
    """Return background ESPN preload job status, summary, and log output."""
    job = _snapshot_import_job(job_id)
    if not job:
        return {"error": f"Job {job_id} not found"}
    return job


@router.post("/api/projection-tuner/compute-season-stats")
async def compute_season_stats_from_logs(
    year: int = Query(..., description="Season year, e.g. 2025"),
    db: Session = Depends(get_db),
):
    """Derive DBPlayerSeasonStats from existing DBPlayerGameLog rows.

    No network calls required — aggregates whatever game logs are already in
    the database (from ESPN syncs, nfl_data_py weekly imports, etc.).
    Ideal when nflverse hasn't yet published a parquet for the current season.
    """
    try:
        from pigskin_mastermind.services.nfl_data_service import NFLDataService
        rows = NFLDataService(db).compute_season_stats_from_game_logs(year)
        return {
            "status": "ok",
            "year": year,
            "rows": rows,
            "message": f"Computed season stats for {rows} players from {year} game logs.",
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Compute failed: {exc}",
            "rows": 0,
        }


@router.get("/api/projection-tuner/diagnose/{player_id}")
async def diagnose_player(
    player_id: int,
    year: int = Query(..., description="Season year to diagnose"),
    db: Session = Depends(get_db),
):
    """Return a data-availability diagnostic for a player + year.

    Shows exactly which data sources are populated vs missing so developers
    understand why criteria values may be 0.
    """
    player = db.query(DBPlayer).filter_by(id=player_id).first()
    if not player:
        return {"error": f"Player {player_id} not found"}

    # ── 1. Season stats ───────────────────────────────────────────────
    season = (
        db.query(DBPlayerSeasonStats)
        .filter_by(player_id=player_id, year=year)
        .first()
    )
    season_check = {
        "found": season is not None,
        "year": year,
        "games_played": season.games_played if season else 0,
        "fantasy_points_total": season.fantasy_points_total if season else 0.0,
        "fantasy_points_avg": season.fantasy_points_avg if season else 0.0,
        "fantasy_points_per_touch": season.fantasy_points_per_touch if season else 0.0,
        "pass_att": season.pass_att if season else 0,
        "rush_att": season.rush_att if season else 0,
        "targets": season.targets if season else 0,
        "snap_pct": season.snap_pct if season else None,
    }

    # ── 2. Game logs ──────────────────────────────────────────────────
    logs = (
        db.query(DBPlayerGameLog)
        .filter_by(player_id=player_id, year=year)
        .order_by(DBPlayerGameLog.week)
        .all()
    )
    log_pts = [g.fantasy_points for g in logs]
    log_check = {
        "count": len(logs),
        "weeks": [g.week for g in logs],
        "fantasy_points_avg": round(sum(log_pts) / len(log_pts), 2) if log_pts else 0.0,
        "fantasy_points_range": (
            {"min": min(log_pts), "max": max(log_pts)} if log_pts else None
        ),
    }

    # ── 3. Weekly player stats (ESPN matchup data) ────────────────────
    weekly = (
        db.query(DBWeeklyPlayerStats)
        .filter(
            DBWeeklyPlayerStats.player_id == player_id,
            DBWeeklyPlayerStats.actual_points > 0,
        )
        .order_by(DBWeeklyPlayerStats.week)
        .all()
    )
    weekly_pts = [w.actual_points for w in weekly]
    weekly_check = {
        "count": len(weekly),
        "actual_points_avg": round(sum(weekly_pts) / len(weekly_pts), 2) if weekly_pts else 0.0,
        "projected_points_avg": (
            round(
                sum(w.projected_points for w in weekly) / len(weekly), 2
            )
            if weekly else 0.0
        ),
        "weeks_with_data": [w.week for w in weekly],
    }

    # ── 4. NFL team stats ─────────────────────────────────────────────
    team_stats = (
        db.query(DBNFLTeamStats)
        .filter_by(nfl_team=player.nfl_team, year=year, week=None)
        .first()
    )
    team_check = {
        "found": team_stats is not None,
        "nfl_team": player.nfl_team,
        "total_yards": team_stats.total_yards if team_stats else 0,
        "points_scored": team_stats.points_scored if team_stats else 0,
        "def_rank_vs_position": (
            getattr(team_stats, f"def_rank_vs_{player.position.lower()}", None)
            if team_stats else None
        ),
    }

    # ── 5. Player metadata ────────────────────────────────────────────
    player_stats = player.stats or {}
    player_check = {
        "injury_status": player_stats.get("injuryStatus", "—"),
        "age": player_stats.get("age", "—"),
        "stats_keys": list(player_stats.keys())[:10],
    }

    # ── 6. Criteria source explanations ──────────────────────────────
    def source_tag(primary_ok, fallback1_ok, fallback1_name, fallback2_ok=False, fallback2_name=""):
        if primary_ok:
            return {"status": "ok", "source": "season_stats"}
        if fallback1_ok:
            return {"status": "fallback", "source": fallback1_name}
        if fallback2_ok:
            return {"status": "fallback", "source": fallback2_name}
        return {"status": "missing", "source": "no data — will be 0"}

    has_season_avg = season and season.fantasy_points_avg and season.fantasy_points_avg > 0
    has_game_logs = len(logs) > 0
    has_weekly = len(weekly) > 0
    has_team_stats = team_stats is not None

    criteria_sources = {
        "historical_average_points": source_tag(
            has_season_avg, has_game_logs, "game_logs", has_weekly, "weekly_player_stats"
        ),
        "player_skill_level": source_tag(
            season is not None, False, "", False, ""
        ),
        "positional_touch_percentage": source_tag(
            season is not None, season and season.snap_pct is not None, "snap_pct fallback"
        ),
        "fantasy_points_per_touch": source_tag(
            season is not None and season.fantasy_points_total > 0, False, ""
        ),
        "recent_trend_score": source_tag(
            has_game_logs, has_weekly, "weekly_player_stats"
        ),
        "team_offense_level": source_tag(has_team_stats, False, ""),
        "opponent_defense_level": {"status": "ok" if has_team_stats else "missing", "source": "nfl_team_stats (opponent)"},
        "injury_risk_score": {
            "status": "ok" if player_stats.get("injuryStatus") is not None else "partial",
            "source": "player.stats.injuryStatus",
        },
    }

    # Highlight any zeros that will cascade
    warnings = []
    if criteria_sources["historical_average_points"]["status"] == "missing":
        warnings.append(
            "⚠️  historical_average_points = 0 — this is the baseline. "
            "All other criteria adjust around it, so 0 here means ~0 total projection. "
            "Run the NFL data import or sync ESPN weekly stats to populate data."
        )
    if not has_season_avg and not has_game_logs and not has_weekly:
        warnings.append(
            "⚠️  No fantasy points data found in season_stats, game_logs, OR weekly_player_stats. "
            f"Try: Settings → Sync ESPN data for {year}."
        )

    return {
        "player": {
            "id": player.id,
            "name": player.name,
            "position": player.position,
            "nfl_team": player.nfl_team,
        },
        "year": year,
        "checks": {
            "season_stats": season_check,
            "game_logs": log_check,
            "weekly_player_stats": weekly_check,
            "nfl_team_stats": team_check,
            "player_metadata": player_check,
        },
        "criteria_sources": criteria_sources,
        "warnings": warnings,
    }
