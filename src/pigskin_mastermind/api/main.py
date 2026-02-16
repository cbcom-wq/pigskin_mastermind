"""FastAPI application entry point for Pigskin Mastermind."""

from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
import os

from pigskin_mastermind.api.database import get_db, engine
from pigskin_mastermind.models.database import DBTeam, DBPlayer, DBLeague, Base

app = FastAPI(
    title="Pigskin Mastermind",
    description="Fantasy Football Management Application",
    version="0.1.0"
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

# Create tables on startup
Base.metadata.create_all(bind=engine)

# Register routers
from pigskin_mastermind.api.routes.teams import router as teams_router
from pigskin_mastermind.api.routes.leagues import router as leagues_router
from pigskin_mastermind.api.routes.lineups import router as lineups_router
from pigskin_mastermind.api.routes.trades import router as trades_router
from pigskin_mastermind.api.routes.players import router as players_router
from pigskin_mastermind.api.routes.settings import router as settings_router

app.include_router(teams_router)
app.include_router(leagues_router)
app.include_router(lineups_router)
app.include_router(trades_router)
app.include_router(players_router)
app.include_router(settings_router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Dashboard page with real statistics."""
    team_count = db.query(DBTeam).count()
    player_count = db.query(DBPlayer).count()
    total_points = db.query(func.coalesce(func.sum(DBTeam.total_points), 0.0)).scalar()

    # Position breakdown
    position_counts = {}
    pos_rows = (
        db.query(DBPlayer.position, func.count(DBPlayer.id))
        .group_by(DBPlayer.position)
        .order_by(DBPlayer.position)
        .all()
    )
    for pos, count in pos_rows:
        position_counts[pos] = count

    # Recent teams (up to 6)
    recent_teams = db.query(DBTeam).order_by(DBTeam.created_at.desc()).limit(6).all()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "team_count": team_count,
            "player_count": player_count,
            "total_points": total_points,
            "position_counts": position_counts,
            "recent_teams": recent_teams,
        }
    )
