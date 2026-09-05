"""FastAPI application entry point for Pigskin Mastermind."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
import os

from pigskin_mastermind.api.database import get_db, engine
from pigskin_mastermind.models.database import DBTeam, DBLeague, Base

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run the season scheduler for the life of the process.

    Imported lazily so that merely importing this module (the CLI and the test
    suite both do) never pulls in the scheduler's dependency tree.
    """
    from pigskin_mastermind.services.season_scheduler import run_scheduler

    stop_event = asyncio.Event()
    task = asyncio.create_task(run_scheduler(stop_event))
    try:
        yield
    finally:
        # Ask it to stop first; cancel only forces the point if it is mid-sleep.
        stop_event.set()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Season scheduler failed on shutdown")


app = FastAPI(
    title="Pigskin Mastermind",
    description="Fantasy Football Management Application",
    version="0.1.0",
    lifespan=lifespan,
)

# Get template and static directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Create directories if they don't exist
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory=TEMPLATE_DIR)

from datetime import datetime as _dt, timedelta as _td


def _timeago(value) -> str:
    """Jinja2 filter: convert a datetime to a relative string like '3 hours ago'."""
    if value is None:
        return ""
    now = _dt.utcnow()
    # Handle timezone-aware datetimes by comparing as naive UTC
    if hasattr(value, 'tzinfo') and value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    diff = now - value
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"


templates.env.filters["timeago"] = _timeago

# Create tables on startup
Base.metadata.create_all(bind=engine)

# Register routers
from pigskin_mastermind.api.routes.teams import router as teams_router
from pigskin_mastermind.api.routes.leagues import router as leagues_router
from pigskin_mastermind.api.routes.lineups import router as lineups_router
from pigskin_mastermind.api.routes.trades import router as trades_router
from pigskin_mastermind.api.routes.players import router as players_router
from pigskin_mastermind.api.routes.settings import router as settings_router
from pigskin_mastermind.api.routes.visualizations import router as visualizations_router
from pigskin_mastermind.api.routes.stats import router as stats_router
from pigskin_mastermind.api.routes.draft import router as draft_router
from pigskin_mastermind.api.routes.games import router as games_router
from pigskin_mastermind.api.routes.odds import router as odds_router
from pigskin_mastermind.api.routes.projections import router as projections_router
from pigskin_mastermind.api.routes.projection_tuner import router as projection_tuner_router
from pigskin_mastermind.api.routes.monte_carlo import router as monte_carlo_router
from pigskin_mastermind.api.routes.adp import router as adp_router
from pigskin_mastermind.api.routes.season import router as season_router

app.include_router(teams_router)
app.include_router(leagues_router)
app.include_router(lineups_router)
app.include_router(trades_router)
app.include_router(players_router)
app.include_router(settings_router)
app.include_router(visualizations_router)
app.include_router(stats_router)
app.include_router(draft_router)
app.include_router(games_router)
app.include_router(odds_router)
app.include_router(projections_router)
app.include_router(projection_tuner_router)
app.include_router(monte_carlo_router)
app.include_router(adp_router)
app.include_router(season_router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Dashboard page with real statistics."""
    from pigskin_mastermind.services.season_league import (
        roster_players, season_league_ids,
    )

    user_teams = db.query(DBTeam).filter(DBTeam.is_user_team == True).all()
    team_count = len(user_teams)
    total_points = db.query(func.coalesce(func.sum(DBTeam.total_points), 0.0)).filter(DBTeam.is_user_team == True).scalar()

    # Counting `DBPlayer.team_id` directly misses every season league: those
    # rosters live in DBRosterSpot, so a drafted team contributed nothing here.
    # See services/season_league.py::roster_players().
    rosters = {t.id: roster_players(db, t) for t in user_teams}
    player_count = sum(len(players) for players in rosters.values())

    # Position breakdown - only for user's teams
    position_counts = {}
    for players in rosters.values():
        for player in players:
            if player.position:
                position_counts[player.position] = position_counts.get(player.position, 0) + 1
    position_counts = dict(sorted(position_counts.items()))

    # Recent teams (up to 6) - only user's teams
    recent_teams = db.query(DBTeam).filter(DBTeam.is_user_team == True).order_by(DBTeam.created_at.desc()).limit(6).all()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "team_count": team_count,
            "player_count": player_count,
            "total_points": total_points,
            "position_counts": position_counts,
            "recent_teams": recent_teams,
            "season_leagues": season_league_ids(db),
        }
    )
