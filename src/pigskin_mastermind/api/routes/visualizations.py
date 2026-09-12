"""API routes for season visualizations."""

from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from typing import Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.services.visualization_service import SeasonVisualizationService
from fastapi.templating import Jinja2Templates
import os

from pigskin_mastermind.utils.back_nav import resolve_back

router = APIRouter(prefix="/visualizations", tags=["visualizations"])

# Setup templates
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
templates = Jinja2Templates(directory=TEMPLATE_DIR)


@router.get("/season-animation/{team_id}")
async def season_animation_page(
    request: Request,
    team_id: int,
    back: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Display the season animation visualization page.
    
    Args:
        request: FastAPI request object
        team_id: Team ID to visualize
        db: Database session
    """
    viz_service = SeasonVisualizationService(db)
    
    # Get summary data
    summary = viz_service.get_season_summary(team_id)
    
    if not summary:
        return templates.TemplateResponse(
            "error.html",
            {
                "request": request,
                "error": "Team not found or no season data available"
            },
            status_code=404
        )
    
    return templates.TemplateResponse(
        "visualizations/season_animation.html",
        {
            "request": request,
            "team_id": team_id,
            "summary": summary,
            "back": resolve_back(back, f"/teams/{team_id}", "Team"),
        }
    )


@router.get("/api/season-data/{team_id}")
async def get_season_data(
    team_id: int,
    db: Session = Depends(get_db)
):
    """
    Get season data for a team in JSON format.
    
    Args:
        team_id: Team ID
        db: Database session
        
    Returns:
        JSON with season data
    """
    viz_service = SeasonVisualizationService(db)
    data = viz_service.get_season_data(team_id)
    
    if not data:
        return JSONResponse(
            content={"error": "Team not found"},
            status_code=404
        )
    
    # Convert player_data dict to list for JSON serialization
    player_list = []
    for player_id, player_info in data['player_data'].items():
        player_list.append({
            'id': player_id,
            'name': player_info['name'],
            'position': player_info['position'],
            'weeks': player_info['weeks'],
            'cumulative_points': player_info['cumulative_points'],
            'weekly_points': player_info['weekly_points']
        })
    
    return {
        'team_id': data['team_id'],
        'team_name': data['team_name'],
        'weeks': data['weeks'],
        'players': player_list,
        'team_results': data['team_results'],
        'mvps': data['mvps']
    }


@router.get("/api/animation-frames/{team_id}")
async def get_animation_frames(
    team_id: int,
    max_weeks: Optional[int] = Query(None, description="Maximum number of weeks to include"),
    db: Session = Depends(get_db)
):
    """
    Get pre-rendered animation frames.
    
    Args:
        team_id: Team ID
        max_weeks: Optional max weeks to render
        db: Database session
        
    Returns:
        JSON with base64 encoded frames
    """
    viz_service = SeasonVisualizationService(db)
    frames = viz_service.generate_animation_frames(team_id, max_weeks)
    
    if not frames:
        return JSONResponse(
            content={"error": "No data available for animation"},
            status_code=404
        )
    
    return {
        'team_id': team_id,
        'frame_count': len(frames),
        'frames': frames
    }


@router.get("/api/static-visualization/{team_id}")
async def get_static_visualization(
    team_id: int,
    db: Session = Depends(get_db)
):
    """
    Get a static visualization image.
    
    Args:
        team_id: Team ID
        db: Database session
        
    Returns:
        JSON with base64 encoded image
    """
    viz_service = SeasonVisualizationService(db)
    image = viz_service.generate_static_visualization(team_id)
    
    if not image:
        return JSONResponse(
            content={"error": "No data available"},
            status_code=404
        )
    
    return {
        'team_id': team_id,
        'image': image
    }
