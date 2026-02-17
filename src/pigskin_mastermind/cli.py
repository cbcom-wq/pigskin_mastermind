"""Command-line interface for Pigskin Mastermind."""

import click
import json
from pigskin_mastermind.services.team_manager import TeamManager
from pigskin_mastermind.services.importer import ImporterFactory
from pigskin_mastermind.services.decision_tools import LineupOptimizer, TradeAnalyzer
from pigskin_mastermind.entertainment import TeamNameGenerator, LeagueEntertainment
from pigskin_mastermind.models.player import Player


@click.group()
@click.version_option(version='0.1.0')
def main():
    """
    Pigskin Mastermind - Fantasy Football Manager
    
    A comprehensive tool for fantasy football research, entertainment, and management.
    """
    pass


@main.group()
def team():
    """Manage fantasy football teams."""
    pass


@team.command('create')
@click.option('--id', 'team_id', required=True, help='Team ID')
@click.option('--name', required=True, help='Team name')
@click.option('--owner', required=True, help='Owner name')
@click.option('--league', 'league_id', help='League ID')
def create_team(team_id, name, owner, league_id):
    """Create a new fantasy team."""
    manager = TeamManager()
    team = manager.create_team(team_id, name, owner, league_id)
    click.echo(f"Created team: {team.name} (ID: {team.team_id})")
    click.echo(f"Owner: {team.owner}")


@team.command('add-player')
@click.option('--team-id', required=True, help='Team ID')
@click.option('--player-id', required=True, help='Player ID')
@click.option('--name', required=True, help='Player name')
@click.option('--position', required=True, type=click.Choice(['QB', 'RB', 'WR', 'TE', 'K', 'DEF']))
@click.option('--nfl-team', required=True, help='NFL team')
@click.option('--projected', type=float, default=0.0, help='Projected points')
def add_player(team_id, player_id, name, position, nfl_team, projected):
    """Add a player to a team."""
    manager = TeamManager()
    team = manager.get_team(team_id)
    if not team:
        click.echo(f"Error: Team {team_id} not found", err=True)
        return
    
    player = Player(
        player_id=player_id,
        name=name,
        position=position,
        team=nfl_team,
        projected_points=projected,
    )
    team.add_player(player)
    click.echo(f"Added {player.name} ({player.position}) to {team.name}")


@team.command('analyze')
@click.option('--team-id', required=True, help='Team ID')
def analyze_team(team_id):
    """Analyze a team's composition and performance."""
    manager = TeamManager()
    team = manager.get_team(team_id)
    if not team:
        click.echo(f"Error: Team {team_id} not found", err=True)
        return
    
    analysis = manager.analyze_team(team_id)
    click.echo(f"\nAnalysis for {analysis['team_name']}")
    click.echo(f"Owner: {analysis['owner']}")
    click.echo(f"Record: {analysis['record']}")
    click.echo(f"Total Points: {analysis['total_points']}")
    click.echo(f"Players: {analysis['player_count']}")
    click.echo(f"\nPosition Breakdown:")
    for position, data in analysis['position_breakdown'].items():
        click.echo(f"  {position}: {data['count']} players, {data['total_points']:.2f} points")


@main.group()
def lineup():
    """Optimize and manage lineups."""
    pass


@lineup.command('optimize')
@click.option('--team-id', required=True, help='Team ID')
def optimize_lineup(team_id):
    """Generate the optimal lineup for a team."""
    manager = TeamManager()
    team = manager.get_team(team_id)
    if not team:
        click.echo(f"Error: Team {team_id} not found", err=True)
        return
    
    optimizer = LineupOptimizer()
    result = optimizer.optimize_lineup(team)
    
    click.echo(f"\nOptimal Lineup for {team.name}")
    click.echo(f"Total Projected Points: {result['total_projected_points']:.2f}")
    click.echo("\nStarters:")
    for position, players in result['lineup'].items():
        click.echo(f"\n{position}:")
        for player in players:
            click.echo(f"  - {player.name} ({player.team}) - {player.projected_points:.2f} pts")
    
    if result['bench']:
        click.echo("\nBench:")
        for player in result['bench']:
            click.echo(f"  - {player.name} ({player.position}, {player.team}) - {player.projected_points:.2f} pts")


@main.group()
def import_cmd():
    """Import teams from fantasy services."""
    pass


@import_cmd.command('espn')
@click.option('--team-id', required=True, type=int, help='ESPN team ID')
@click.option('--league-id', required=True, help='ESPN league ID')
@click.option('--swid', required=True, help='ESPN SWID cookie')
@click.option('--espn-s2', required=True, help='ESPN S2 cookie')
@click.option('--year', default=2024, type=int, help='Season year (default: 2024)')
@click.option('--use-importer', is_flag=True, help='Use simple importer (no database)')
def import_espn(team_id, league_id, swid, espn_s2, year, use_importer):
    """Import a team from ESPN Fantasy.
    
    By default, this command imports the team and saves it to the database
    for persistent access through the web interface. Use --use-importer to
    import without database persistence (for testing).
    """
    if use_importer:
        # Use the importer for in-memory import (original behavior)
        importer = ImporterFactory.create_importer('espn')
        credentials = {
            'swid': swid,
            'espn_s2': espn_s2,
            'league_id': league_id,
            'year': str(year),
        }
        
        if importer.authenticate(credentials):
            click.echo("✓ Authenticated with ESPN")
            team = importer.import_team(str(team_id))
            click.echo(f"\n✓ Imported team: {team.name}")
            click.echo(f"  Owner: {team.owner}")
            click.echo(f"  Record: {team.record['wins']}-{team.record['losses']}-{team.record['ties']}")
            click.echo(f"  Total Points: {team.total_points:.2f}")
            click.echo(f"  Players: {len(team.players)}")
        else:
            click.echo("✗ Authentication failed", err=True)
            return
    else:
        # Use ESPNSyncService for database persistence
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from pigskin_mastermind.models.database import Base
        from pigskin_mastermind.services.espn_sync import ESPNSyncService
        import os
        
        # Use database from environment or default to SQLite
        db_url = os.getenv('DATABASE_URL', 'sqlite:///pigskin_mastermind.db')
        engine = create_engine(db_url)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        db = SessionLocal()
        try:
            sync_service = ESPNSyncService(db)
            click.echo("Importing team from ESPN...")
            db_team = sync_service.import_team(
                league_id=league_id,
                team_id=team_id,
                espn_s2=espn_s2,
                swid=swid,
                year=year
            )
            click.echo(f"\n✓ Successfully imported team to database!")
            click.echo(f"  Team: {db_team.name}")
            click.echo(f"  Owner: {db_team.owner}")
            click.echo(f"  Record: {db_team.wins}-{db_team.losses}-{db_team.ties}")
            click.echo(f"  Total Points: {db_team.total_points:.2f}")
            click.echo(f"  Players: {len(db_team.players)}")
            click.echo(f"\n  Database ID: {db_team.id}")
            click.echo(f"  You can now access this team at: http://localhost:8000/teams/{db_team.id}")
        except ValueError as e:
            click.echo(f"✗ Error: {e}", err=True)
        except Exception as e:
            click.echo(f"✗ Import failed: {e}", err=True)
        finally:
            db.close()


@import_cmd.command('espn-league')
@click.option('--league-id', required=True, help='ESPN league ID')
@click.option('--swid', required=True, help='ESPN SWID cookie')
@click.option('--espn-s2', required=True, help='ESPN S2 cookie')
@click.option('--year', default=2024, type=int, help='Season year (default: 2024)')
def import_espn_league(league_id, swid, espn_s2, year):
    """Import all teams from an ESPN Fantasy league."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from pigskin_mastermind.models.database import Base
    from pigskin_mastermind.services.espn_sync import ESPNSyncService
    from espn_api.football import League
    import os
    
    try:
        # Connect to ESPN
        click.echo("Connecting to ESPN Fantasy API...")
        league = League(
            league_id=int(league_id),
            year=year,
            espn_s2=espn_s2,
            swid=swid
        )
        
        click.echo(f"✓ Connected to league: {getattr(league, 'name', 'Unknown')}")
        click.echo(f"  Teams: {len(league.teams)}")
        click.echo(f"  Current Week: {league.current_week}")
        
        # Setup database
        db_url = os.getenv('DATABASE_URL', 'sqlite:///pigskin_mastermind.db')
        engine = create_engine(db_url)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        db = SessionLocal()
        sync_service = ESPNSyncService(db)
        
        # Import all teams
        click.echo(f"\nImporting {len(league.teams)} teams...")
        imported = 0
        for team in league.teams:
            try:
                db_team = sync_service.import_team(
                    league_id=league_id,
                    team_id=team.team_id,
                    espn_s2=espn_s2,
                    swid=swid,
                    year=year
                )
                click.echo(f"  ✓ {db_team.name} ({db_team.wins}-{db_team.losses}-{db_team.ties})")
                imported += 1
            except Exception as e:
                click.echo(f"  ✗ Failed to import team {team.team_id}: {e}", err=True)
        
        db.close()
        click.echo(f"\n✓ Successfully imported {imported}/{len(league.teams)} teams!")
        click.echo(f"  Access teams at: http://localhost:8000/leagues/{league_id}")
        
    except Exception as e:
        click.echo(f"✗ Failed to import league: {e}", err=True)


@import_cmd.command('espn-weekly')
@click.option('--team-id', required=True, type=int, help='ESPN team ID')
@click.option('--league-id', required=True, help='ESPN league ID')
@click.option('--swid', required=True, help='ESPN SWID cookie')
@click.option('--espn-s2', required=True, help='ESPN S2 cookie')
@click.option('--year', default=2024, type=int, help='Season year (default: 2024)')
def import_espn_weekly(team_id, league_id, swid, espn_s2, year):
    """Import weekly statistics and historical data for a team.
    
    This imports box score data for all completed weeks, including:
    - Team scores and opponents for each week
    - Player lineups and performances for each week
    - Win/loss/tie results for each week
    
    This data is useful for historical analysis and tracking performance trends.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from pigskin_mastermind.models.database import Base
    from pigskin_mastermind.services.espn_sync import ESPNSyncService
    import os
    
    try:
        # Setup database
        db_url = os.getenv('DATABASE_URL', 'sqlite:///pigskin_mastermind.db')
        engine = create_engine(db_url)
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        
        db = SessionLocal()
        sync_service = ESPNSyncService(db)
        
        # First ensure the team is imported
        click.echo("Ensuring team is imported...")
        try:
            db_team = sync_service.import_team(
                league_id=league_id,
                team_id=team_id,
                espn_s2=espn_s2,
                swid=swid,
                year=year
            )
            click.echo(f"✓ Team: {db_team.name}")
        except Exception as e:
            click.echo(f"✗ Failed to import team: {e}", err=True)
            db.close()
            return
        
        # Import weekly stats
        click.echo("\nImporting weekly statistics...")
        weeks_imported = sync_service.import_weekly_stats(
            league_id=league_id,
            team_id=team_id,
            espn_s2=espn_s2,
            swid=swid,
            year=year
        )
        
        db.close()
        click.echo(f"\n✓ Successfully imported {weeks_imported} weeks of data!")
        click.echo(f"  View detailed stats at: http://localhost:8000/teams/{db_team.id}")
        
    except Exception as e:
        click.echo(f"✗ Failed to import weekly stats: {e}", err=True)


@main.group()
def entertainment():
    """Entertainment features for fantasy football."""
    pass


@entertainment.command('generate-name')
@click.option('--count', default=5, help='Number of names to generate')
def generate_name(count):
    """Generate team name suggestions."""
    generator = TeamNameGenerator()
    names = generator.suggest_names(count)
    click.echo("\nTeam Name Suggestions:")
    for i, name in enumerate(names, 1):
        click.echo(f"{i}. {name}")


@entertainment.command('player-names')
@click.option('--player', required=True, help='Player name')
def player_names(player):
    """Generate team names based on a player."""
    generator = TeamNameGenerator()
    names = generator.generate_player_based_name(player)
    click.echo(f"\nTeam Names based on {player}:")
    for i, name in enumerate(names, 1):
        click.echo(f"{i}. {name}")


if __name__ == '__main__':
    main()
