"""Manual test script for visualization service."""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pigskin_mastermind.models.database import Base, DBTeam, DBPlayer, DBWeeklyTeamStats, DBWeeklyPlayerStats
from pigskin_mastermind.services.visualization_service import SeasonVisualizationService

# Create in-memory database for testing
engine = create_engine('sqlite:///:memory:')
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)
db = SessionLocal()

print("Creating test data...")

# Create a team
team = DBTeam(
    team_id="test1",
    name="Test Fantasy Team",
    owner="Test Owner"
)
db.add(team)
db.commit()

# Create players
players = []
for i in range(3):
    player = DBPlayer(
        player_id=f"p{i+1}",
        name=f"Player {i+1}",
        position=["QB", "RB", "WR"][i],
        nfl_team="KC"
    )
    db.add(player)
    players.append(player)
db.commit()

print(f"Created team: {team.name} with {len(players)} players")

# Create weekly stats for 3 weeks
for week in range(1, 4):
    weekly_team_stat = DBWeeklyTeamStats(
        team_id=team.id,
        week=week,
        points_for=100 + week * 10,
        points_against=95 + week * 8,
        projected_points=105,
        opponent_name=f"Opponent {week}",
        result="W" if week % 2 == 1 else "L"
    )
    db.add(weekly_team_stat)
    db.commit()
    
    # Add player stats for this week
    for i, player in enumerate(players):
        player_stat = DBWeeklyPlayerStats(
            player_id=player.id,
            weekly_team_stats_id=weekly_team_stat.id,
            week=week,
            slot_position=["QB", "RB", "WR"][i],
            projected_points=20 - i * 2,
            actual_points=(25 - i * 3) + week * 2,
            stats={}
        )
        db.add(player_stat)
    db.commit()

print(f"Created weekly stats for {week} weeks")

# Test visualization service
print("\n=== Testing Visualization Service ===\n")
viz_service = SeasonVisualizationService(db)

# Test 1: Get season data
print("Test 1: Getting season data...")
season_data = viz_service.get_season_data(team.id)
if season_data:
    print(f"  ✓ Team: {season_data['team_name']}")
    print(f"  ✓ Weeks: {season_data['weeks']}")
    print(f"  ✓ Players tracked: {len(season_data['player_data'])}")
    print(f"  ✓ Results: {[r['result'] for r in season_data['team_results']]}")
    print(f"  ✓ MVPs: {[m['player'] for m in season_data['mvps']]}")
else:
    print("  ✗ Failed to get season data")

# Test 2: Get season summary
print("\nTest 2: Getting season summary...")
summary = viz_service.get_season_summary(team.id)
if summary:
    print(f"  ✓ Record: {summary['record']}")
    print(f"  ✓ Total points: {summary['total_points']:.1f}")
    print(f"  ✓ Avg points/week: {summary['avg_points_per_week']:.1f}")
    print(f"  ✓ Season MVP: {summary['season_mvp']} ({summary['season_mvp_points']:.1f} pts)")
else:
    print("  ✗ Failed to get summary")

# Test 3: Generate static visualization
print("\nTest 3: Generating static visualization...")
static_img = viz_service.generate_static_visualization(team.id)
if static_img:
    print(f"  ✓ Generated image (base64 length: {len(static_img)} chars)")
else:
    print("  ✗ Failed to generate static visualization")

# Test 4: Generate animation frames
print("\nTest 4: Generating animation frames...")
frames = viz_service.generate_animation_frames(team.id)
if frames:
    print(f"  ✓ Generated {len(frames)} frames")
    for i, frame in enumerate(frames):
        print(f"    - Frame {i+1}: {len(frame)} chars")
else:
    print("  ✗ Failed to generate animation frames")

print("\n=== All Tests Complete ===")

db.close()
