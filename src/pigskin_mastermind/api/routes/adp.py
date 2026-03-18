"""ADP (Average Draft Position) API routes.

Provides endpoints for importing ADP data from Fantasy Football
Calculator and looking up stored ADP values.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.services.adp_service import ADPService

router = APIRouter(prefix="/adp", tags=["adp"])


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class ImportFFCRequest(BaseModel):
    year: int = Field(2025, ge=2018, le=2030)
    scoring: str = Field("ppr")
    num_teams: int = Field(12, ge=4, le=20)


class ADPLookupResponse(BaseModel):
    name: str
    position: str
    adp: Optional[float]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/import/ffc")
async def import_ffc_adp(req: ImportFFCRequest, db: Session = Depends(get_db)):
    """Import ADP data from Fantasy Football Calculator.

    Fetches the current ADP rankings from FFC and stores them in the
    local database, matched by player name + position.
    """
    service = ADPService(db)
    result = service.import_from_ffc(
        year=req.year,
        scoring=req.scoring,
        num_teams=req.num_teams,
    )
    if result.get("error"):
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@router.get("/lookup")
async def lookup_adp(
    name: str = Query(..., description="Player name"),
    position: str = Query(..., description="Position (QB, RB, WR, TE, K, DEF)"),
    year: Optional[int] = Query(None, description="Season year"),
    db: Session = Depends(get_db),
):
    """Look up stored ADP for a single player by name + position."""
    service = ADPService(db)
    adp = service.get_adp(name, position, year=year)
    return ADPLookupResponse(name=name, position=position, adp=adp)


@router.get("/rankings")
async def get_adp_rankings(
    year: Optional[int] = Query(None, description="Season year filter"),
    position: Optional[str] = Query(None, description="Position filter"),
    db: Session = Depends(get_db),
):
    """Return all stored ADP entries ordered by ADP ascending.

    This replaces the old ``/draft/adp`` ESPN-specific endpoint with
    locally stored data from Fantasy Football Calculator.
    """
    service = ADPService(db)
    players = service.get_all_adp(year=year, position=position)
    return {
        "source": ADPService.ADP_SOURCE_LABEL,
        "year": year,
        "count": len(players),
        "players": players,
    }
