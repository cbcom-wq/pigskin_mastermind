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


@main.group()
def stats():
    """Player and team statistics commands."""
    pass


def _get_stats_db():
    """Get a database session for CLI stats commands."""
    from pigskin_mastermind.api.database import SessionLocal
    return SessionLocal()


@stats.command('import-espn')
@click.option('--league-id', required=True, help='ESPN league ID')
@click.option('--team-id', required=True, type=int, help='ESPN team ID')
@click.option('--years', default='2024', help='Comma-separated years (e.g. 2023,2024)')
def stats_import_espn(league_id, team_id, years):
    """Import ESPN stats for multiple seasons."""
    from pigskin_mastermind.services.espn_sync import ESPNSyncService
    from pigskin_mastermind.models.database import DBLeague

    db = _get_stats_db()
    try:
        db_league = db.query(DBLeague).filter_by(league_id=league_id).first()
        if not db_league:
            click.echo("Error: League not found. Set up credentials first.", err=True)
            return

        year_list = [int(y.strip()) for y in years.split(',')]
        service = ESPNSyncService(db)
        results = service.import_weekly_stats_multi_season(
            league_id=league_id,
            team_id=team_id,
            espn_s2=db_league.espn_s2,
            swid=db_league.swid,
            years=year_list,
        )
        for yr, weeks in results.items():
            click.echo(f"  {yr}: {weeks} weeks imported")
        click.echo("ESPN stats import complete.")
    finally:
        db.close()


@stats.command('import-nfl')
@click.option('--years', default='2024', help='Comma-separated years (e.g. 2023,2024)')
def stats_import_nfl(years):
    """Import league-wide NFL stats from nfl_data_py."""
    from pigskin_mastermind.services.nfl_data_service import NFLDataService

    db = _get_stats_db()
    try:
        year_list = [int(y.strip()) for y in years.split(',')]
        service = NFLDataService(db)

        click.echo("Importing weekly stats...")
        weekly = service.import_weekly_stats(year_list)
        click.echo(f"  {weekly} game log rows imported")

        click.echo("Importing seasonal stats...")
        seasonal = service.import_seasonal_stats(year_list)
        click.echo(f"  {seasonal} season stat rows imported")

        click.echo("Computing defense rankings...")
        defense = service.import_team_defense_rankings(year_list)
        click.echo(f"  {defense} team defense rows imported")

        click.echo("NFL stats import complete.")
    finally:
        db.close()


@stats.command('player')
@click.argument('player_id', type=int)
@click.option('--year', type=int, default=None, help='Filter to specific year')
def stats_player(player_id, year):
    """Show stats summary for a player."""
    from pigskin_mastermind.services.stats_service import StatsService

    db = _get_stats_db()
    try:
        service = StatsService(db)
        result = service.get_player_stats(player_id, year=year)
        if not result:
            click.echo(f"Player {player_id} not found.", err=True)
            return

        click.echo(f"\n{result['name']} ({result['position']}) - {result['nfl_team']}")
        for s in result.get('seasons', []):
            click.echo(f"\n  {s['year']} Season ({s['games_played']} games):")
            click.echo(f"    Fantasy: {s['fantasy_points_total']:.1f} total, {s['fantasy_points_avg']:.1f} avg")
            if s['pass_yd']:
                click.echo(f"    Passing: {s['pass_yd']} yds, {s['pass_td']} TD, {s['pass_int']} INT")
            if s['rush_yd']:
                click.echo(f"    Rushing: {s['rush_yd']} yds, {s['rush_td']} TD")
            if s['rec']:
                click.echo(f"    Receiving: {s['rec']} rec, {s['rec_yd']} yds, {s['rec_td']} TD")
    finally:
        db.close()


@stats.command('game-log')
@click.argument('player_id', type=int)
@click.option('--weeks', type=int, default=None, help='Limit to N most recent weeks')
def stats_game_log(player_id, weeks):
    """Show recent game logs for a player."""
    from pigskin_mastermind.services.stats_service import StatsService

    db = _get_stats_db()
    try:
        service = StatsService(db)
        logs = service.get_player_game_logs(player_id, limit=weeks)
        if not logs:
            click.echo(f"No game logs found for player {player_id}.", err=True)
            return

        click.echo(f"\n{'Wk':>3} {'Opp':<5} {'Pts':>6} {'PassYd':>7} {'RushYd':>7} {'RecYd':>6} {'TD':>3}")
        click.echo("-" * 45)
        for g in logs:
            total_td = g['pass_td'] + g['rush_td'] + g['rec_td']
            click.echo(
                f"{g['week']:>3} {(g['opponent'] or '?'):<5} "
                f"{g['fantasy_points']:>6.1f} {g['pass_yd']:>7} "
                f"{g['rush_yd']:>7} {g['rec_yd']:>6} {total_td:>3}"
            )
    finally:
        db.close()


@stats.command('refresh')
@click.option('--team-db-id', required=True, type=int, help='Database team ID')
def stats_refresh(team_db_id):
    """Poll current week scores for a team."""
    from pigskin_mastermind.services.espn_sync import ESPNSyncService

    db = _get_stats_db()
    try:
        service = ESPNSyncService(db)
        result = service.refresh_current_week(team_db_id)
        click.echo(f"Week {result['week']}: {result['players_updated']} players updated, "
                    f"{result['active_games']} active games")
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
    finally:
        db.close()


@stats.command('defense')
@click.argument('nfl_team')
@click.option('--year', type=int, default=2024, help='Season year')
def stats_defense(nfl_team, year):
    """Show defense rankings for an NFL team."""
    from pigskin_mastermind.services.stats_service import StatsService

    db = _get_stats_db()
    try:
        service = StatsService(db)
        result = service.get_team_defense_rankings(nfl_team.upper(), year)
        if not result:
            click.echo(f"No defense stats found for {nfl_team} in {year}.", err=True)
            return

        click.echo(f"\n{result['nfl_team']} Defense Rankings ({result['year']})")
        click.echo(f"  Points Allowed: {result['points_allowed']}")
        click.echo(f"  Pass Yards Allowed: {result['pass_yards_allowed']}")
        click.echo(f"  Rush Yards Allowed: {result['rush_yards_allowed']}")
        click.echo(f"\n  Positional Rankings (1=best D, 32=worst):")
        click.echo(f"    vs QB: {result['def_rank_vs_qb'] or 'N/A'}")
        click.echo(f"    vs RB: {result['def_rank_vs_rb'] or 'N/A'}")
        click.echo(f"    vs WR: {result['def_rank_vs_wr'] or 'N/A'}")
        click.echo(f"    vs TE: {result['def_rank_vs_te'] or 'N/A'}")
    finally:
        db.close()


if __name__ == '__main__':
    main()
