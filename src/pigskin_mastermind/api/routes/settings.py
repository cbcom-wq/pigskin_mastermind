"""ESPN Settings routes for credential storage and league sync."""

from fastapi import APIRouter, Depends, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime
import json

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBLeague, DBTeam

router = APIRouter(prefix="/settings", tags=["settings"])


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
    return templates.TemplateResponse(
        "settings/espn.html",
        {"request": request, "leagues": leagues}
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
        return _toast_response(f"Synced {weeks} weeks of data!", "success")
    except ValueError as e:
        return _toast_response(str(e), "error")
    except Exception as e:
        return _toast_response(f"Weekly sync failed: {str(e)}", "error")
