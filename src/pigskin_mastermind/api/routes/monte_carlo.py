"""API routes for Monte Carlo fantasy simulation."""

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session
from typing import Optional

from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBPlayer
from pigskin_mastermind.services.monte_carlo_service import FantasySimulationEngine
from pigskin_mastermind.services.monte_carlo_input_builder import MonteCarloInputBuilder

router = APIRouter(prefix="/api/projections/monte-carlo", tags=["monte-carlo"])


@router.get("/player/{player_id}")
async def simulate_player(
    player_id: int,
    week: int = Query(..., ge=1, le=18, description="NFL week number"),
    year: int = Query(2025, description="NFL season year"),
    simulations: int = Query(
        10_000, ge=100, le=100_000,
        description="Number of Monte Carlo iterations",
    ),
    seed: Optional[int] = Query(None, description="Random seed for reproducibility"),
    opponent_team: Optional[str] = Query(
        None, description="Opponent team abbreviation (auto-derived if omitted)",
    ),
    detailed: bool = Query(
        False, description="Include percentile distribution and histogram",
    ),
    db: Session = Depends(get_db),
):
    """Run a Monte Carlo simulation for a player's weekly fantasy projection.

    Returns a probability distribution of 0.5 PPR fantasy scores including
    expected value, floor/ceiling, and boom/bust probabilities.
    """
    # Verify the player exists
    db_player = db.query(DBPlayer).filter_by(id=player_id).first()
    if not db_player:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

    try:
        # Build normalised input from DB data
        builder = MonteCarloInputBuilder(db)
        player_input = builder.build_for_player(
            player_id=player_id,
            year=year,
            week=week,
            opponent_team=opponent_team,
        )

        # Run simulation
        engine = FantasySimulationEngine(simulations=simulations, seed=seed)
        result = engine.simulate(player_input)

        if detailed:
            return result.to_detailed_dict()
        return result.to_dict()

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Simulation failed: {str(e)}",
        )


@router.get("/player/{player_id}/compare")
async def compare_scenarios(
    player_id: int,
    week: int = Query(..., ge=1, le=18, description="NFL week number"),
    year: int = Query(2025, description="NFL season year"),
    simulations: int = Query(10_000, ge=100, le=100_000),
    seed: Optional[int] = Query(42, description="Random seed (fixed for fair comparison)"),
    opponent_team: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    """Compare base simulation vs. pessimistic and optimistic scenarios.

    Runs three simulations with the same seed:
    - **base**: as-is from DB data
    - **pessimistic**: worse matchup / higher injury risk
    - **optimistic**: favourable matchup / lower injury risk
    """
    db_player = db.query(DBPlayer).filter_by(id=player_id).first()
    if not db_player:
        raise HTTPException(status_code=404, detail=f"Player {player_id} not found")

    try:
        builder = MonteCarloInputBuilder(db)
        base_input = builder.build_for_player(
            player_id=player_id, year=year, week=week,
            opponent_team=opponent_team,
        )

        engine = FantasySimulationEngine(simulations=simulations, seed=seed)

        # Base scenario
        base_result = engine.simulate(base_input)

        # Pessimistic: tougher defense, higher injury risk
        from dataclasses import replace
        pessimistic_input = replace(
            base_input,
            opponent_defense_level=min(1.0, base_input.opponent_defense_level + 0.15),
            injury_risk_score=min(1.0, base_input.injury_risk_score + 0.2),
            opposing_defense_vs_position_rank=max(
                1, base_input.opposing_defense_vs_position_rank - 8
            ),
        )
        pessimistic_result = engine.simulate(pessimistic_input)

        # Optimistic: weaker defense, lower injury risk
        optimistic_input = replace(
            base_input,
            opponent_defense_level=max(0.0, base_input.opponent_defense_level - 0.15),
            injury_risk_score=max(0.0, base_input.injury_risk_score - 0.2),
            opposing_defense_vs_position_rank=min(
                32, base_input.opposing_defense_vs_position_rank + 8
            ),
        )
        optimistic_result = engine.simulate(optimistic_input)

        return {
            "player_name": base_input.player_name,
            "position": base_input.position,
            "scenarios": {
                "base": base_result.to_dict(),
                "pessimistic": pessimistic_result.to_dict(),
                "optimistic": optimistic_result.to_dict(),
            },
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Comparison failed: {str(e)}",
        )
