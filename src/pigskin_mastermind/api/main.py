from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import os

from pigskin_mastermind.api.database import get_db, engine
from pigskin_mastermind.models.database import DBTeam, DBPlayer, Base

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
from pigskin_mastermind.api.routes.lineups import router as lineups_router
from pigskin_mastermind.api.routes.trades import router as trades_router
app.include_router(teams_router)
app.include_router(lineups_router)
app.include_router(trades_router)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}


@app.get("/")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    """Dashboard page"""
    team_count = db.query(DBTeam).count()
    player_count = db.query(DBPlayer).count()
    total_points = sum(t.total_points for t in db.query(DBTeam).all())
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "team_count": team_count,
            "player_count": player_count,
            "total_points": total_points,
        }
    )
