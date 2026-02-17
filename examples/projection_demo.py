"""
Example demonstrating the player projection feature.

This script shows how to use the weekly and yearly projection services
to generate fantasy football player projections based on various criteria.
"""

from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.projection_criteria import (
    YearlyProjectionCriteria,
    WeeklyProjectionCriteria,
)
from pigskin_mastermind.services.projection_service import (
    YearlyProjectionService,
    WeeklyProjectionService,
)


def main():
    """Demonstrate projection functionality."""

    # Create some example players
    mahomes = Player(
        player_id="p1",
        name="Patrick Mahomes",
        position="QB",
        team="KC",
    )

    kelce = Player(
        player_id="p2",
        name="Travis Kelce",
        position="TE",
        team="KC",
    )

    print("=" * 60)
    print("PLAYER PROJECTION DEMONSTRATION")
    print("=" * 60)
    print()

    # ========== Yearly Projections ==========
    print("YEARLY PROJECTIONS")
    print("-" * 60)
    print()

    yearly_service = YearlyProjectionService()

    # Patrick Mahomes - Elite QB in his prime
    mahomes_yearly_criteria = YearlyProjectionCriteria(
        player_skill_level=95.0,
        team_offense_level=90.0,
        opponent_defense_level=50.0,  # Average defense
        positional_touch_percentage=30.0,
        recent_trend_score=20.0,
        historical_average_points=25.0,
        fantasy_points_per_touch=0.85,
        injury_risk_score=10.0,
        age_deviation_from_optimum=0.0,  # At peak age
        coaching_stability_score=95.0,
    )

    mahomes_yearly_report = yearly_service.generate_projection_report(
        mahomes, mahomes_yearly_criteria
    )

    print("Player: {0}".format(mahomes_yearly_report['player_name']))
    print("Position: {0}".format(mahomes_yearly_report['position']))
    print(f"Team: {mahomes_yearly_report['team']}")
    print(f"Projected Points (Season): {mahomes_yearly_report['projected_points']}")
    print()

    # Travis Kelce - Elite TE
    kelce_yearly_criteria = YearlyProjectionCriteria(
        player_skill_level=90.0,
        team_offense_level=90.0,
        opponent_defense_level=50.0,
        positional_touch_percentage=20.0,
        recent_trend_score=5.0,
        historical_average_points=15.0,
        fantasy_points_per_touch=0.75,
        injury_risk_score=15.0,
        age_deviation_from_optimum=3.0,  # Slightly past peak
        coaching_stability_score=95.0,
    )

    kelce_yearly_report = yearly_service.generate_projection_report(
        kelce, kelce_yearly_criteria
    )

    print("Player: {0}".format(kelce_yearly_report['player_name']))
    print("Position: {0}".format(kelce_yearly_report['position']))
    print(f"Team: {kelce_yearly_report['team']}")
    print(f"Projected Points (Season): {kelce_yearly_report['projected_points']}")
    print()

    # ========== Weekly Projections ==========
    print("=" * 60)
    print("WEEKLY PROJECTIONS")
    print("-" * 60)
    print()

    weekly_service = WeeklyProjectionService()

    # Mahomes vs weak defense, good weather
    mahomes_weekly_criteria = WeeklyProjectionCriteria(
        player_skill_level=95.0,
        team_offense_level=90.0,
        opponent_defense_level=70.0,  # Weak defense
        positional_touch_percentage=30.0,
        recent_trend_score=25.0,
        historical_average_points=25.0,
        fantasy_points_per_touch=0.85,
        injury_risk_score=10.0,
        opposing_defense_vs_position_rank=28,  # Weak vs QB
        offensive_momentum_score=30.0,  # Hot streak
        weather_impact_score=0.0,  # Good weather
    )

    mahomes_weekly_report = weekly_service.generate_projection_report(
        mahomes, mahomes_weekly_criteria
    )

    print(f"Player: {mahomes_weekly_report['player_name']}")
    print(f"Position: {mahomes_weekly_report['position']}")
    print("Matchup: vs Weak Defense (Rank 28 vs QB)")
    print("Conditions: Hot offensive streak, good weather")
    print(f"Projected Points (Week): {mahomes_weekly_report['projected_points']}")
    print()

    # Kelce vs strong defense, bad weather
    kelce_weekly_criteria = WeeklyProjectionCriteria(
        player_skill_level=90.0,
        team_offense_level=90.0,
        opponent_defense_level=30.0,  # Strong defense
        positional_touch_percentage=20.0,
        recent_trend_score=5.0,
        historical_average_points=15.0,
        fantasy_points_per_touch=0.75,
        injury_risk_score=15.0,
        opposing_defense_vs_position_rank=5,  # Strong vs TE
        offensive_momentum_score=-10.0,  # Slight slump
        weather_impact_score=-30.0,  # Bad weather
    )

    kelce_weekly_report = weekly_service.generate_projection_report(
        kelce, kelce_weekly_criteria
    )

    print(f"Player: {kelce_weekly_report['player_name']}")
    print(f"Position: {kelce_weekly_report['position']}")
    print("Matchup: vs Strong Defense (Rank 5 vs TE)")
    print("Conditions: Slight offensive slump, bad weather")
    print(f"Projected Points (Week): {kelce_weekly_report['projected_points']}")
    print()

    print("=" * 60)
    print("PROJECTION ANALYSIS")
    print("-" * 60)
    print()
    print("Note: These projections consider multiple factors:")
    print("- Player skill and historical performance")
    print("- Team offensive strength")
    print("- Opponent defensive quality")
    print("- Recent trends and momentum")
    print("- Position-specific touches and efficiency")
    print("- Injury risk")
    print()
    print("Yearly projections also consider:")
    print("- Age relative to peak performance")
    print("- Coaching staff stability")
    print()
    print("Weekly projections also consider:")
    print("- Specific opponent defensive rankings")
    print("- Current offensive momentum")
    print("- Weather conditions")
    print()
    print("=" * 60)


if __name__ == "__main__":
    main()
