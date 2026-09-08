"""The front page and its fragments.

Every endpoint here is read-only and every one of them renders from
``services/dashboard.py::build_view``. Nothing derives a fact locally: the page
and the polling fragment showing different scores for the same matchup is the
specific failure this arrangement exists to prevent.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.services.dashboard import build_view
from pigskin_mastermind.services.season_scheduler import league_now

router = APIRouter(tags=["dashboard"])


@router.get("/")
async def dashboard_page(request: Request, db: Session = Depends(get_db)):
    """The five-band front page.

    Only bands 1-2 are rendered server-side; the rest load themselves so a slow
    metrics scan cannot delay the scores.
    """
    from pigskin_mastermind.api.main import templates

    view = build_view(db, league_now(), sections=frozenset())
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "view": view},
    )


@router.get("/api/dashboard/pulse")
async def dashboard_pulse(request: Request, db: Session = Depends(get_db)):
    """Bands 1-2 alone, for the live refresh."""
    from pigskin_mastermind.api.main import templates

    view = build_view(db, league_now(), sections=frozenset())
    return templates.TemplateResponse(
        "dashboard/_pulse.html",
        {"request": request, "view": view},
    )
