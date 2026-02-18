"""Test script for importing all players from ESPN."""

import sys
from pigskin_mastermind.api.database import SessionLocal
from pigskin_mastermind.models.database import DBLeague, DBPlayer
from pigskin_mastermind.services.espn_sync import ESPNSyncService


def test_import_all_players():
    """Test importing all available players from ESPN."""
    db = SessionLocal()
    
    try:
        # Get the first league with credentials
        league = db.query(DBLeague).first()
        
        if not league:
            print("No league found in database. Please add a league first.")
            return
        
        print(f"Testing player import for league: {league.league_id}")
        print(f"Year: {league.year}")
        
        # Count existing players
        initial_count = db.query(DBPlayer).count()
        print(f"Players in database before import: {initial_count}")
        
        # Create service and import players
        service = ESPNSyncService(db)
        
        # Import just QB and RB for testing (smaller dataset)
        count = service.import_all_players(
            league_id=league.league_id,
            espn_s2=league.espn_s2,
            swid=league.swid,
            year=league.year,
            positions=['QB', 'RB'],
            batch_size=100  # Smaller batch for testing
        )
        
        print(f"Imported {count} players")
        
        # Count players after
        final_count = db.query(DBPlayer).count()
        print(f"Players in database after import: {final_count}")
        print(f"New players added: {final_count - initial_count}")
        
        # Show sample of free agent players (no team_id)
        free_agents = db.query(DBPlayer).filter(DBPlayer.team_id.is_(None)).limit(10).all()
        
        if free_agents:
            print("\nSample of free agents imported:")
            for player in free_agents:
                print(f"  - {player.name} ({player.position}) - {player.nfl_team} - {player.projected_points:.1f} pts")
        
        print("\n✅ Test completed successfully!")
        
    except Exception as e:
        print(f"❌ Error during test: {e}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()


if __name__ == "__main__":
    test_import_all_players()
