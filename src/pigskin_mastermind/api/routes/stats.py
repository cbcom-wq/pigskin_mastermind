"""Stats API routes for player and team statistics."""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBPlayer, DBLeague, DBTeam, DBWeeklyTeamStats, DBWeeklyPlayerStats, get_scoring_settings
from pigskin_mastermind.services.stats_service import StatsService
from pigskin_mastermind.services.projection_criteria_builder import ProjectionCriteriaBuilder
from pigskin_mastermind.services.sportsbook_projection_service import SportsbookProjectionService

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("/players/{player_id}")
async def get_player_stats(
    player_id: int,
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Get player stats summary."""
    service = StatsService(db)
    result = service.get_player_stats(player_id, year=year)
    if not result:
        raise HTTPException(status_code=404, detail="Player not found")
    return result


@router.get("/players/{player_id}/game-logs")
async def get_player_game_logs(
    player_id: int,
    year: Optional[int] = Query(None),
    limit: Optional[int] = Query(None, le=100),
    db: Session = Depends(get_db),
):
    """Get game log history for a player."""
    service = StatsService(db)
    return service.get_player_game_logs(player_id, year=year, limit=limit)


@router.get("/players/{player_id}/trends")
async def get_player_trends(
    player_id: int,
    weeks: int = Query(4, ge=1, le=17),
    db: Session = Depends(get_db),
):
    """Get recent performance trends for a player."""
    service = StatsService(db)
    return service.get_recent_performance(player_id, num_weeks=weeks)


@router.get("/players/compare")
async def compare_players(
    player_ids: str = Query(..., description="Comma-separated player IDs"),
    year: Optional[int] = Query(None),
    db: Session = Depends(get_db),
):
    """Compare multiple players side-by-side."""
    ids = [int(x.strip()) for x in player_ids.split(",") if x.strip()]
    if len(ids) < 2:
        raise HTTPException(status_code=400, detail="At least 2 player IDs required")
    service = StatsService(db)
    return service.compare_players(ids, year=year)


@router.get("/teams/{nfl_team}/defense")
async def get_team_defense(
    nfl_team: str,
    year: int = Query(...),
    db: Session = Depends(get_db),
):
    """Get team defense rankings by position."""
    service = StatsService(db)
    result = service.get_team_defense_rankings(nfl_team, year)
    if not result:
        raise HTTPException(status_code=404, detail="Team stats not found")
    return result


@router.post("/import/espn")
async def import_espn_stats(
    league_id: str = Query(...),
    team_id: int = Query(...),
    years: str = Query("2024", description="Comma-separated years"),
    db: Session = Depends(get_db),
):
    """Trigger ESPN stats import for multiple seasons."""
    from pigskin_mastermind.services.espn_sync import ESPNSyncService

    db_league = db.query(DBLeague).filter_by(league_id=league_id).first()
    if not db_league:
        raise HTTPException(status_code=404, detail="League not found. Set up credentials first.")

    year_list = [int(y.strip()) for y in years.split(",") if y.strip()]
    service = ESPNSyncService(db)
    results = service.import_weekly_stats_multi_season(
        league_id=league_id,
        team_id=team_id,
        espn_s2=db_league.espn_s2,
        swid=db_league.swid,
        years=year_list,
    )
    return {"status": "success", "weeks_imported_by_year": results}


@router.post("/import/nfl")
async def import_nfl_stats(
    years: str = Query("2024", description="Comma-separated years"),
    db: Session = Depends(get_db),
):
    """Trigger nfl_data_py import for league-wide stats."""
    from pigskin_mastermind.services.nfl_data_service import NFLDataService

    year_list = [int(y.strip()) for y in years.split(",") if y.strip()]
    service = NFLDataService(db)

    weekly_count = service.import_weekly_stats(year_list)
    seasonal_count = service.import_seasonal_stats(year_list)
    defense_count = service.import_team_defense_rankings(year_list)
    # Bio and snap share round out the player profile; both were previously
    # implemented but never called from anywhere.
    roster_count = service.import_roster_metadata(year_list)
    snap_count = service.import_snap_counts(year_list)

    return {
        "status": "success",
        "weekly_rows": weekly_count,
        "seasonal_rows": seasonal_count,
        "defense_rows": defense_count,
        "roster_metadata_rows": roster_count,
        "snap_count_rows": snap_count,
    }


@router.post("/refresh")
async def refresh_current_week(
    team_db_id: int = Query(...),
    db: Session = Depends(get_db),
):
    """Poll current week scores for a team."""
    from pigskin_mastermind.services.espn_sync import ESPNSyncService

    service = ESPNSyncService(db)
    try:
        result = service.refresh_current_week(team_db_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/players/{player_id}/play-by-play")
async def get_player_play_by_play(
    player_id: int,
    year: int = Query(..., description="NFL season year, e.g. 2024"),
    week: int = Query(..., ge=1, le=22, description="Week number (1-18 regular season)"),
    db: Session = Depends(get_db),
):
    """Fetch play-by-play data for a player in a specific week.

    Pulls live data from nfl_data_py (backed by the NFL endpoint) so
    in-progress games are supported.
    """
    from pigskin_mastermind.services.nfl_data_service import NFLDataService

    db_player = db.query(DBPlayer).filter_by(id=player_id).first()
    if not db_player:
        raise HTTPException(status_code=404, detail="Player not found")

    service = NFLDataService(db)
    try:
        result = service.get_play_by_play(player_db_id=player_id, year=year, week=week)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    plays = result.get("plays", [])
    return {
        "player_id": player_id,
        "player_name": db_player.name,
        "year": year,
        "week": week,
        "plays": plays,
        "total_plays": len(plays),
        "game_summary": result.get("game_summary", {}),
        "player_stats": result.get("player_stats", {}),
    }


@router.get("/players/{player_id}/game-simulation")
async def get_player_game_simulation(
    player_id: int,
    year: int = Query(..., description="NFL season year, e.g. 2024"),
    week: int = Query(..., ge=1, le=22, description="Week number (1-18 regular season)"),
    db: Session = Depends(get_db),
):
    """Fetch animation-ready game simulation data for a player's week."""
    from pigskin_mastermind.services.player_game_simulation_service import (
        PlayerGameSimulationService,
    )

    # Resolve scoring settings from the player's league
    player = db.query(DBPlayer).filter_by(id=player_id).first()
    league = None
    if player and player.team_id:
        team = db.query(DBTeam).filter_by(id=player.team_id).first()
        if team and team.league_id:
            league = db.query(DBLeague).filter_by(league_id=team.league_id).first()
    scoring = get_scoring_settings(league)

    service = PlayerGameSimulationService(db)
    try:
        result = service.build_simulation(player_db_id=player_id, year=year, week=week)
        result["scoring_settings"] = scoring
        return result
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/teams/{team_db_id}/game-simulation")
async def get_team_game_simulation(
    team_db_id: int,
    year: int = Query(..., description="NFL season year, e.g. 2024"),
    week: int = Query(..., ge=1, le=22, description="Week number"),
    db: Session = Depends(get_db),
):
    """Fetch animation-ready game simulation for an entire team's week."""
    from pigskin_mastermind.services.team_game_simulation_service import (
        TeamGameSimulationService,
    )

    # Resolve scoring settings from the team's league
    team = db.query(DBTeam).filter_by(id=team_db_id).first()
    league = None
    if team and team.league_id:
        league = db.query(DBLeague).filter_by(league_id=team.league_id).first()
    scoring = get_scoring_settings(league)

    service = TeamGameSimulationService(db)
    try:
        return service.build_team_simulation(
            team_db_id=team_db_id, year=year, week=week,
            scoring_settings=scoring,
        )
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/players/{player_id}/projection")
async def get_auto_projection(
    player_id: int,
    week: Optional[int] = Query(None),
    year: int = Query(2025),
    db: Session = Depends(get_db),
):
    """Auto-generate projection using criteria builder.

    If week is provided, returns weekly projection criteria.
    Otherwise, returns yearly projection criteria.
    """
    from pigskin_mastermind.models.player import Player as PlayerModel
    from pigskin_mastermind.services.projection_service import (
        WeeklyProjectionService, YearlyProjectionService
    )
    from pigskin_mastermind.services.master_coefficients import get_effective_coefficients

    db_player = db.query(DBPlayer).filter_by(id=player_id).first()
    if not db_player:
        raise HTTPException(status_code=404, detail="Player not found")

    builder = ProjectionCriteriaBuilder(db)
    effective_coeffs = get_effective_coefficients()

    # Create a Player model for the projection service
    player_model = PlayerModel(
        player_id=db_player.player_id,
        name=db_player.name,
        position=db_player.position,
        team=db_player.nfl_team,
        projected_points=db_player.projected_points,
    )

    if week:
        criteria = builder.build_weekly_criteria(player_id, week, year)
        service = WeeklyProjectionService(coefficients=effective_coeffs)
        report = service.generate_projection_report(player_model, criteria)
    else:
        criteria = builder.build_yearly_criteria(player_id, year)
        service = YearlyProjectionService(coefficients=effective_coeffs)
        report = service.generate_projection_report(player_model, criteria)

    return report


@router.get("/teams/{team_db_id}/weekly-projections")
async def get_team_weekly_projections(
    team_db_id: int,
    week: int = Query(..., ge=1, le=22),
    year: int = Query(2025),
    db: Session = Depends(get_db),
):
    """Generate projections for all players on a team's weekly roster."""
    from pigskin_mastermind.models.player import Player as PlayerModel
    from pigskin_mastermind.models.database import DBWeeklyTeamStats, DBWeeklyPlayerStats
    from pigskin_mastermind.services.projection_service import WeeklyProjectionService
    from pigskin_mastermind.services.master_coefficients import get_effective_coefficients

    team = db.query(DBTeam).filter_by(id=team_db_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    weekly_team = db.query(DBWeeklyTeamStats).filter_by(
        team_id=team_db_id, week=week
    ).first()
    if not weekly_team:
        raise HTTPException(status_code=404, detail="No weekly data for this week")

    rows = (
        db.query(DBWeeklyPlayerStats, DBPlayer)
        .join(DBPlayer, DBWeeklyPlayerStats.player_id == DBPlayer.id)
        .filter(DBWeeklyPlayerStats.weekly_team_stats_id == weekly_team.id)
        .all()
    )

    builder = ProjectionCriteriaBuilder(db)
    service = WeeklyProjectionService(coefficients=get_effective_coefficients())
    projections = {}

    for wp, player in rows:
        try:
            criteria = builder.build_weekly_criteria(player.id, week, year)
            player_model = PlayerModel(
                player_id=player.player_id,
                name=player.name,
                position=player.position,
                team=player.nfl_team,
                projected_points=player.projected_points,
            )
            report = service.generate_projection_report(player_model, criteria)
            projections[player.id] = {
                "projected_points": report["projected_points"],
                "criteria_used": report.get("criteria_used", {}),
            }
        except Exception:
            projections[player.id] = {"projected_points": None, "error": True}

    return {"projections": projections}


@router.get("/teams/{team_db_id}/sportsbook-projections")
async def get_team_sportsbook_projections(
    team_db_id: int,
    week: int = Query(..., ge=1, le=22),
    league_id: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Generate sportsbook-based projections for all players on a team's weekly roster.

    Looks up each player's name in the stored sportsbook prop lines and
    converts them to projected fantasy points using the league (or default)
    scoring settings.
    """
    team = db.query(DBTeam).filter_by(id=team_db_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    weekly_team = db.query(DBWeeklyTeamStats).filter_by(
        team_id=team_db_id, week=week
    ).first()
    if not weekly_team:
        raise HTTPException(status_code=404, detail="No weekly data for this week")

    rows = (
        db.query(DBWeeklyPlayerStats, DBPlayer)
        .join(DBPlayer, DBWeeklyPlayerStats.player_id == DBPlayer.id)
        .filter(DBWeeklyPlayerStats.weekly_team_stats_id == weekly_team.id)
        .all()
    )

    # Use the team's league if caller didn't specify one
    effective_league_id = league_id or team.league_id
    service = SportsbookProjectionService(db)
    projections = {}

    for wp, player in rows:
        try:
            result = service.project_player(
                player.name,
                league_id=effective_league_id,
            )
            projections[player.id] = {
                "projected_points": result["total_projected_points"],
                "categories": result["categories"],
            }
        except Exception:
            projections[player.id] = {"projected_points": None, "error": True}

    return {"projections": projections}
