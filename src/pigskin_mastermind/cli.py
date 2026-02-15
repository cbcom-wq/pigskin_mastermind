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
@click.option('--team-id', required=True, help='ESPN team ID')
@click.option('--swid', required=True, help='ESPN SWID cookie')
@click.option('--espn-s2', required=True, help='ESPN S2 cookie')
@click.option('--league-id', help='ESPN league ID')
def import_espn(team_id, swid, espn_s2, league_id):
    """Import a team from ESPN Fantasy."""
    importer = ImporterFactory.create_importer('espn')
    credentials = {
        'swid': swid,
        'espn_s2': espn_s2,
        'league_id': league_id,
    }
    
    if importer.authenticate(credentials):
        click.echo("Authenticated with ESPN")
        team = importer.import_team(team_id)
        click.echo(f"Imported team: {team.name}")
    else:
        click.echo("Authentication failed", err=True)


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
