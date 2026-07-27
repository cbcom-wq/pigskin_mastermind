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


@stats.command('import-all-players')
@click.option('--league-id', required=True, help='ESPN league ID')
@click.option('--year', type=int, default=2024, help='Season year')
@click.option('--week', type=int, default=None, help='Week to import (defaults to current week)')
@click.option('--positions', default='QB,RB,WR,TE,K,D/ST', help='Comma-separated positions to import')
@click.option('--batch-size', type=int, default=500, help='Number of players to fetch per position')
def stats_import_all_players(league_id, year, week, positions, batch_size):
    """Import all available players (including free agents) from ESPN.
    
    This command fetches all available players from ESPN for the specified league,
    not just players currently on rosters. This is essential for analyzing free agents
    and making informed waiver wire decisions.
    """
    from pigskin_mastermind.services.espn_sync import ESPNSyncService
    from pigskin_mastermind.models.database import DBLeague

    db = _get_stats_db()
    try:
        db_league = db.query(DBLeague).filter_by(league_id=league_id).first()
        if not db_league:
            click.echo("Error: League not found. Set up credentials first.", err=True)
            return

        position_list = [p.strip() for p in positions.split(',')]
        service = ESPNSyncService(db)
        
        click.echo(f"Importing all players for {year} (week {week or 'current'})...")
        click.echo(f"Positions: {', '.join(position_list)}")
        
        count = service.import_all_players(
            league_id=league_id,
            espn_s2=db_league.espn_s2,
            swid=db_league.swid,
            year=year,
            week=week,
            positions=position_list,
            batch_size=batch_size
        )
        
        click.echo(f"Successfully imported {count} players.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
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


@stats.command('import-adp')
@click.argument('csv_file', type=click.Path(exists=True, dir_okay=False))
@click.option('--year', type=int, required=True, help='Season year the ADP data belongs to')
@click.option('--source', default='csv', show_default=True,
              help='Label for the ADP source (e.g. espn, yahoo, fantasypros)')
def stats_import_adp(csv_file, year, source):
    """Import player ADP data from a CSV file.

    The CSV must contain columns: name, position, adp.
    An optional player_id (gsis-style) column improves matching accuracy.

    Example CSV header: name,position,adp
    """
    from pigskin_mastermind.services.nfl_data_service import NFLDataService

    db = _get_stats_db()
    try:
        service = NFLDataService(db)
        count = service.import_adp_from_csv(csv_file, year=year, adp_source=source)
        click.echo(f"ADP import complete: {count} player(s) updated for {year}.")
    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
    finally:
        db.close()


@stats.command('import-ffc-adp')
@click.option('--year', type=int, default=None, help='Season year (default: current season)')
@click.option('--scoring', default='ppr', show_default=True,
              type=click.Choice(['standard', 'ppr', 'half-ppr', '2qb', 'dynasty'],
                                case_sensitive=False),
              help='Scoring format')
@click.option('--teams', type=int, default=12, show_default=True,
              help='Number of teams in the league')
def stats_import_ffc_adp(year, scoring, teams):
    """Import ADP data from Fantasy Football Calculator.

    Fetches current ADP rankings from the FFC public API and stores
    them in the local database.  Players are matched by name + position.
    """
    from pigskin_mastermind.services.adp_service import ADPService

    db = _get_stats_db()
    try:
        service = ADPService(db)
        result = service.import_from_ffc(year=year, scoring=scoring, num_teams=teams)
        if result.get("error"):
            click.echo(f"Error: {result['error']}", err=True)
            return
        click.echo(
            f"FFC ADP import complete: {result['imported']} imported, "
            f"{result['skipped']} skipped, {result['total']} total from FFC."
        )
    finally:
        db.close()


@stats.command('yearly-rankings')
@click.option('--year', type=int, required=True, help='Season year')
@click.option('--position', default=None,
              type=click.Choice(['QB', 'RB', 'WR', 'TE', 'K', 'DEF'], case_sensitive=False),
              help='Filter by position')
@click.option('--top', type=int, default=None, help='Show only top N players')
def stats_yearly_rankings(year, position, top):
    """Show yearly player rankings sorted by ADP then fantasy points."""
    from pigskin_mastermind.services.stats_service import StatsService

    db = _get_stats_db()
    try:
        service = StatsService(db)
        rankings = service.get_yearly_rankings(year, position=position)
        if not rankings:
            click.echo(f"No rankings data found for {year}.", err=True)
            return

        if top:
            rankings = rankings[:top]

        pos_label = f" ({position.upper()})" if position else ""
        click.echo(f"\n{year} Yearly Rankings{pos_label}")
        click.echo(f"{'Rank':>4} {'Name':<25} {'Pos':<5} {'Team':<5} {'ADP':>6} {'FPts':>8} {'Avg':>6}")
        click.echo("-" * 65)
        for r in rankings:
            adp_str = f"{r['adp']:.1f}" if r['adp'] is not None else "N/A"
            click.echo(
                f"{r['rank']:>4} {r['name']:<25} {r['position']:<5} {r['nfl_team']:<5} "
                f"{adp_str:>6} {r['fantasy_points_total'] or 0:>8.1f} "
                f"{r['fantasy_points_avg']:>6.2f}"
            )
    finally:
        db.close()


@main.group()
def odds():
    """Sportsbook betting odds and player props."""
    pass


def _get_odds_db():
    """Get a database session for CLI odds commands."""
    from pigskin_mastermind.api.database import SessionLocal
    return SessionLocal()


@odds.command('import-games')
@click.option('--sport', default='americanfootball_nfl', show_default=True, help='Sport key')
@click.option('--regions', default='us', show_default=True, help='Comma-separated region codes')
@click.option('--markets', default='h2h,spreads,totals', show_default=True,
              help='Comma-separated market keys')
@click.option('--bookmakers', default=None, help='Comma-separated bookmaker keys to filter')
def odds_import_games(sport, regions, markets, bookmakers):
    """Fetch game odds from The Odds API and store them in the database.

    Requires the ODDS_API_KEY environment variable to be set.
    """
    from pigskin_mastermind.services.sportsbook_service import SportsbookService

    db = _get_odds_db()
    try:
        service = SportsbookService(db)
        count = service.import_game_odds(sport=sport, regions=regions,
                                         markets=markets, bookmakers=bookmakers)
        click.echo(f"Game odds import complete: {count} row(s) upserted.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
    finally:
        db.close()


@odds.command('import-props')
@click.argument('event_id')
@click.option('--sport', default='americanfootball_nfl', show_default=True, help='Sport key')
@click.option('--regions', default='us', show_default=True, help='Comma-separated region codes')
@click.option('--markets',
              default='player_pass_yds,player_pass_tds,player_rush_yds,player_rush_tds,'
                      'player_reception_yds,player_receptions',
              show_default=True, help='Comma-separated prop market keys')
@click.option('--bookmakers', default=None, help='Comma-separated bookmaker keys to filter')
def odds_import_props(event_id, sport, regions, markets, bookmakers):
    """Fetch player props for EVENT_ID from The Odds API and store them.

    Requires the ODDS_API_KEY environment variable to be set.
    """
    from pigskin_mastermind.services.sportsbook_service import SportsbookService

    db = _get_odds_db()
    try:
        service = SportsbookService(db)
        count = service.import_player_props(event_id=event_id, sport=sport,
                                            regions=regions, markets=markets,
                                            bookmakers=bookmakers)
        click.echo(f"Player props import complete: {count} row(s) upserted.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
    finally:
        db.close()


@odds.command('games')
@click.option('--event-id', default=None, help='Filter by event ID')
@click.option('--home-team', default=None, help='Filter by home team (partial match)')
@click.option('--away-team', default=None, help='Filter by away team (partial match)')
@click.option('--market', default=None, help='Filter by market (h2h, spreads, totals)')
@click.option('--bookmaker', default=None, help='Filter by bookmaker key')
def odds_games(event_id, home_team, away_team, market, bookmaker):
    """View stored game odds."""
    from pigskin_mastermind.services.sportsbook_service import SportsbookService

    db = _get_odds_db()
    try:
        service = SportsbookService(db)
        results = service.get_game_odds(event_id=event_id, home_team=home_team,
                                        away_team=away_team, market=market,
                                        bookmaker=bookmaker)
        if not results:
            click.echo("No game odds found.")
            return

        click.echo(f"\n{'Market':<20} {'Home':<20} {'Away':<20} {'Outcome':<25} {'Price':>8} {'Point':>7}")
        click.echo("-" * 105)
        for r in results:
            point_str = f"{r['point']:.1f}" if r['point'] is not None else ""
            click.echo(
                f"{r['market']:<20} {r['home_team']:<20} {r['away_team']:<20} "
                f"{r['outcome_name']:<25} {r['price'] or '':>8} {point_str:>7}"
            )
    finally:
        db.close()


@odds.command('props')
@click.option('--player', default=None, help='Filter by player name (partial match)')
@click.option('--event-id', default=None, help='Filter by event ID')
@click.option('--market', default=None, help='Filter by prop market key')
@click.option('--bookmaker', default=None, help='Filter by bookmaker key')
def odds_props(player, event_id, market, bookmaker):
    """View stored player prop odds."""
    from pigskin_mastermind.services.sportsbook_service import SportsbookService

    db = _get_odds_db()
    try:
        service = SportsbookService(db)
        results = service.get_player_props(player_name=player, event_id=event_id,
                                           market=market, bookmaker=bookmaker)
        if not results:
            click.echo("No player props found.")
            return

        click.echo(f"\n{'Market':<28} {'Player':<25} {'Outcome':<10} {'Price':>8} {'Point':>7}")
        click.echo("-" * 85)
        for r in results:
            point_str = f"{r['point']:.1f}" if r['point'] is not None else ""
            player_label = r.get('description') or r['outcome_name']
            click.echo(
                f"{r['market']:<28} {player_label:<25} {r['outcome_name']:<10} "
                f"{r['price'] or '':>8} {point_str:>7}"
            )
    finally:
        db.close()


@odds.command('seed-props')
@click.option('--all-stars', is_flag=True, default=False,
              help='Seed all built-in star players, not just your roster')
@click.option('--clear', is_flag=True, default=False,
              help='Delete previously seeded data before inserting')
def odds_seed_props(all_stars, clear):
    """Seed realistic sample player-prop lines for offline testing.

    By default seeds props only for players already on your roster.
    Use --all-stars to also include ~60 well-known NFL players.
    Use --clear to remove previously-seeded rows first.
    """
    from pigskin_mastermind.services.sportsbook_seed import seed_sample_props

    db = _get_odds_db()
    try:
        count = seed_sample_props(
            db,
            roster_only=not all_stars,
            clear_existing=clear,
        )
        click.echo(f"Seeded {count} sample prop row(s) into sportsbook_odds.")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
    finally:
        db.close()


if __name__ == '__main__':
    main()
