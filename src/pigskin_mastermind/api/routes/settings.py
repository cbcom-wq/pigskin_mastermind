"""ESPN Settings routes for credential storage and league sync."""

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime
import json

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBLeague, DBTeam, DEFAULT_SCORING_SETTINGS, get_scoring_settings

router = APIRouter(prefix="/settings", tags=["settings"])

SCORING_LABELS = {
    "pass_yd": "Passing Yards (per yard)",
    "pass_td": "Passing TD",
    "pass_int": "Interception Thrown",
    "rush_yd": "Rushing Yards (per yard)",
    "rush_td": "Rushing TD",
    "rec": "Reception",
    "rec_yd": "Receiving Yards (per yard)",
    "rec_td": "Receiving TD",
    "fumbles_lost": "Fumble Lost",
    "two_pt": "2-Point Conversion",
}


def _toast_response(message: str, type: str = "success"):
    """Create an empty response with an HX-Trigger header to show a toast."""
    response = HTMLResponse("")
    trigger = json.dumps({"showToast": {"message": message, "type": type}})
    response.headers["HX-Trigger"] = trigger
    return response


@router.get("")
async def settings_page(request: Request, db: Session = Depends(get_db)):
    """ESPN settings page."""
    from pigskin_mastermind.api.main import templates
    leagues = db.query(DBLeague).order_by(DBLeague.created_at.desc()).all()
    # Attach resolved scoring settings to each league for the template
    leagues_scoring = {
        league.id: get_scoring_settings(league) for league in leagues
    }
    return templates.TemplateResponse(
        "settings/espn.html",
        {
            "request": request,
            "leagues": leagues,
            "leagues_scoring": leagues_scoring,
            "scoring_labels": SCORING_LABELS,
            "default_scoring": DEFAULT_SCORING_SETTINGS,
        }
    )


@router.post("/leagues")
async def save_league(
    request: Request,
    league_id: str = Form(...),
    name: str = Form(...),
    year: int = Form(...),
    swid: str = Form(...),
    espn_s2: str = Form(...),
    db: Session = Depends(get_db)
):
    """Save or update ESPN league credentials."""
    existing = db.query(DBLeague).filter_by(league_id=league_id).first()
    is_update = existing is not None
    
    if existing:
        existing.name = name
        existing.year = year
        existing.swid = swid
        existing.espn_s2 = espn_s2
    else:
        league = DBLeague(
            league_id=league_id,
            name=name,
            year=year,
            swid=swid,
            espn_s2=espn_s2
        )
        db.add(league)
    db.commit()

    response = RedirectResponse(url="/settings", status_code=303)
    # Add toast notification
    message = f"League '{name}' updated successfully!" if is_update else f"League '{name}' connected successfully!"
    trigger = json.dumps({"showToast": {"message": message, "type": "success"}})
    response.headers["HX-Trigger"] = trigger
    return response


@router.delete("/leagues/{league_db_id}")
async def delete_league(league_db_id: int, db: Session = Depends(get_db)):
    """Delete saved league credentials."""
    league = db.query(DBLeague).filter_by(id=league_db_id).first()
    if league:
        db.delete(league)
        db.commit()
    return _toast_response("League credentials removed", "info")


@router.post("/sync-league/{league_db_id}")
async def sync_league(league_db_id: int, db: Session = Depends(get_db)):
    """Sync all teams from a saved ESPN league."""
    league = db.query(DBLeague).filter_by(id=league_db_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    if not league.espn_s2 or not league.swid:
        return _toast_response("Missing ESPN credentials for this league", "error")

    try:
        from pigskin_mastermind.services.espn_sync import ESPNSyncService
        sync_service = ESPNSyncService(db)

        # Import all teams from the league
        import sys
        import os
        ESPN_API_PATH = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "lib", "espn-api"
        )
        if ESPN_API_PATH not in sys.path:
            sys.path.insert(0, ESPN_API_PATH)

        from espn_api.football import League as ESPNLeague
        espn_league = ESPNLeague(
            league_id=int(league.league_id),
            year=league.year,
            espn_s2=league.espn_s2,
            swid=league.swid
        )

        count = 0
        for espn_team in espn_league.teams:
            sync_service.import_team(
                league_id=league.league_id,
                team_id=espn_team.team_id,
                espn_s2=league.espn_s2,
                swid=league.swid,
                year=league.year
            )
            count += 1

        # Extract and save scoring settings from ESPN
        scoring = sync_service.extract_scoring_settings(espn_league)
        if scoring:
            league.scoring_settings = scoring

        league.last_synced_at = datetime.utcnow()
        db.commit()

        return _toast_response(f"Synced {count} teams from ESPN!", "success")
    except Exception as e:
        return _toast_response(f"Sync failed: {str(e)}", "error")


@router.post("/sync/{team_db_id}")
async def sync_team(team_db_id: int, db: Session = Depends(get_db)):
    """Sync a single team from ESPN."""
    try:
        from pigskin_mastermind.services.espn_sync import ESPNSyncService
        sync_service = ESPNSyncService(db)
        sync_service.sync_team(team_db_id)
        return _toast_response("Team synced successfully!", "success")
    except ValueError as e:
        return _toast_response(str(e), "error")
    except Exception as e:
        return _toast_response(f"Sync failed: {str(e)}", "error")


@router.post("/sync-weekly/{team_db_id}")
async def sync_weekly_stats(team_db_id: int, db: Session = Depends(get_db)):
    """Sync weekly stats for a team from ESPN box scores."""
    try:
        from pigskin_mastermind.services.espn_sync import ESPNSyncService
        sync_service = ESPNSyncService(db)
        weeks = sync_service.sync_weekly_stats(team_db_id)
        response = _toast_response(f"Synced {weeks} weeks of data!", "success")
        # Merge pageRefresh into the trigger so the page reloads after sync
        trigger = json.loads(response.headers["HX-Trigger"])
        trigger["pageRefresh"] = True
        response.headers["HX-Trigger"] = json.dumps(trigger)
        return response
    except ValueError as e:
        return _toast_response(str(e), "error")
    except Exception as e:
        return _toast_response(f"Weekly sync failed: {str(e)}", "error")


@router.post("/populate-stats")
async def populate_stats(db: Session = Depends(get_db)):
    """Parse raw ESPN player JSON stats into structured game logs and season stats."""
    try:
        from pigskin_mastermind.services.espn_sync import ESPNSyncService
        from pigskin_mastermind.models.database import DBLeague
        # Determine the year from the first league, default to 2025
        league = db.query(DBLeague).first()
        year = league.year if league else 2025
        sync_service = ESPNSyncService(db)
        result = sync_service.populate_stats_from_player_json(year=year)
        return _toast_response(
            f"Populated {result['season_stats']} season stats and {result['game_logs']} game logs!",
            "success",
        )
    except Exception as e:
        return _toast_response(f"Stats population failed: {str(e)}", "error")


@router.post("/import-full-season")
async def import_full_season(db: Session = Depends(get_db)):
    """Import player stats for every week of the season from ESPN.

    This re-fetches free agents for each week so that per-week breakdowns
    are available for all completed weeks, then populates structured tables.
    """
    try:
        from pigskin_mastermind.services.espn_sync import ESPNSyncService
        league = db.query(DBLeague).first()
        if not league:
            return _toast_response("No league configured — add one first.", "error")
        if not league.espn_s2 or not league.swid:
            return _toast_response("Missing ESPN credentials for this league.", "error")

        sync_service = ESPNSyncService(db)
        result = sync_service.import_all_players_full_season(
            league_id=league.league_id,
            espn_s2=league.espn_s2,
            swid=league.swid,
            year=league.year,
        )
        return _toast_response(
            f"Imported {result['players']} players, "
            f"{result['game_logs']} game logs, "
            f"{result['season_stats']} season stats!",
            "success",
        )
    except Exception as e:
        return _toast_response(f"Full-season import failed: {str(e)}", "error")


@router.post("/scoring/{league_db_id}")
async def save_scoring_settings(
    league_db_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Save scoring settings for a league."""
    league = db.query(DBLeague).filter_by(id=league_db_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    form = await request.form()
    settings = {}
    for key in DEFAULT_SCORING_SETTINGS:
        val = form.get(key)
        if val is not None:
            try:
                settings[key] = float(val)
            except ValueError:
                pass

    league.scoring_settings = settings
    db.commit()
    return _toast_response("Scoring settings saved!", "success")
