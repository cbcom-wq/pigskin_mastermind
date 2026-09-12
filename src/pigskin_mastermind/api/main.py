"""FastAPI application entry point for Pigskin Mastermind."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime as _dt

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from pigskin_mastermind.api.database import engine
from pigskin_mastermind.api.routes.adp import router as adp_router
from pigskin_mastermind.api.routes.dashboard import router as dashboard_router
from pigskin_mastermind.api.routes.draft import router as draft_router
from pigskin_mastermind.api.routes.games import router as games_router
from pigskin_mastermind.api.routes.leagues import router as leagues_router
from pigskin_mastermind.api.routes.metrics import router as metrics_router
from pigskin_mastermind.api.routes.monte_carlo import router as monte_carlo_router
from pigskin_mastermind.api.routes.odds import router as odds_router
from pigskin_mastermind.api.routes.players import router as players_router
from pigskin_mastermind.api.routes.projection_tuner import (
    router as projection_tuner_router,
)
from pigskin_mastermind.api.routes.projections import router as projections_router
from pigskin_mastermind.api.routes.season import router as season_router
from pigskin_mastermind.api.routes.settings import router as settings_router
from pigskin_mastermind.api.routes.stats import router as stats_router
from pigskin_mastermind.api.routes.teams import router as teams_router
from pigskin_mastermind.api.routes.trades import router as trades_router
from pigskin_mastermind.api.routes.visualizations import (
    router as visualizations_router,
)
from pigskin_mastermind.api.routes.weekly_projections import (
    router as weekly_projections_router,
)
from pigskin_mastermind.models.database import Base

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


def _timeago(value) -> str:
    """Jinja2 filter: convert a datetime to a relative string like '3 hours ago'."""
    if value is None:
        return ""
    now = _dt.utcnow()
    # Handle timezone-aware datetimes by comparing as naive UTC
    if hasattr(value, "tzinfo") and value.tzinfo is not None:
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


def _kickoff(value):
    """A kickoff as "Sun 1:00 PM".

    Not ``strftime('%a %-I:%M %p')``: ``%-I`` is a glibc extension and raises
    ValueError on Windows. Stripping the pad by hand is portable.
    """
    if value is None:
        return ""
    return value.strftime("%a %I:%M %p").replace(" 0", " ", 1)


templates.env.filters["timeago"] = _timeago
templates.env.filters["kickoff"] = _kickoff

# Create tables on startup
Base.metadata.create_all(bind=engine)

# Register routers
app.include_router(teams_router)
app.include_router(leagues_router)
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
app.include_router(weekly_projections_router)
app.include_router(metrics_router)
app.include_router(dashboard_router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}
