# ESPN Fantasy Football API Integration Guide

This guide explains how to import player and team statistics from ESPN Fantasy Football into Pigskin Mastermind.

## Table of Contents

1. [Overview](#overview)
2. [Getting ESPN Credentials](#getting-espn-credentials)
3. [Available Data](#available-data)
4. [CLI Usage](#cli-usage)
5. [Web Interface Usage](#web-interface-usage)
6. [API Details](#api-details)
7. [Troubleshooting](#troubleshooting)

## Overview

Pigskin Mastermind integrates with ESPN Fantasy Football to automatically import:

- **Team Data**: Team name, owner, record (wins/losses/ties), total points, and roster
- **Player Data**: Player names, positions, NFL teams, projected points, actual points, and detailed stats
- **Historical Data**: Week-by-week team and player performance for the entire season
- **Live Data**: Current week scores and lineup information

The integration uses the unofficial ESPN Fantasy API via the `espn-api` Python package.

## Getting ESPN Credentials

ESPN Fantasy uses cookies for authentication. You'll need to extract two cookies from your browser:

### Step 1: Log into ESPN Fantasy

1. Go to https://fantasy.espn.com/football
2. Log in to your ESPN account
3. Navigate to your fantasy league

### Step 2: Extract Cookies

#### Using Chrome/Edge

1. Press `F12` to open Developer Tools
2. Go to the **Application** tab
3. In the left sidebar, expand **Cookies** and select `https://fantasy.espn.com`
4. Find and copy these two cookies:
   - `SWID` - Looks like: `{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}`
   - `espn_s2` - A long string of random characters (64+ characters)

#### Using Firefox

1. Press `F12` to open Developer Tools
2. Go to the **Storage** tab
3. Expand **Cookies** and select `https://fantasy.espn.com`
4. Find and copy the `SWID` and `espn_s2` cookies

#### Using Safari

1. Enable Developer Menu: Preferences → Advanced → Show Develop menu
2. Develop → Show Web Inspector
3. Go to the **Storage** tab → **Cookies** → `fantasy.espn.com`
4. Copy the `SWID` and `espn_s2` cookies

### Step 3: Find Your League ID

Your League ID is in the URL when viewing your league:
```
https://fantasy.espn.com/football/league?leagueId=123456789
                                                  ^^^^^^^^^
                                                  This is your League ID
```

### Step 4: Find Your Team ID

Your Team ID is in the URL when viewing your team:
```
https://fantasy.espn.com/football/team?leagueId=123456789&teamId=1
                                                                  ^
                                                                  This is your Team ID
```

Team IDs typically start at 1 and go up to the number of teams in your league.

## Available Data

### Current Season Data

The ESPN API provides access to:

#### Team Information
- Team name and owner
- Season record (wins, losses, ties)
- Total points scored
- Current roster with all players
- Standing/ranking in the league

#### Player Information
- Player name and position (QB, RB, WR, TE, K, DEF)
- NFL team
- Projected fantasy points
- Actual fantasy points scored
- Detailed statistics:
  - Passing: yards, touchdowns, interceptions
  - Rushing: attempts, yards, touchdowns
  - Receiving: receptions, yards, touchdowns
  - Kicking: field goals, extra points
  - Defense: sacks, interceptions, touchdowns, points allowed

#### Weekly Historical Data
- Team scores and results for each week
- Opponent information
- Player lineups for each week
- Week-by-week player performance
- Slot positions (starter vs bench)

### Live/Current Week Data

The API provides real-time access to:
- Current week scores (updates as games are played)
- Live player statistics during games
- Current lineup configurations
- Projected vs actual points

## CLI Usage

### Import a Single Team

```bash
# Basic team import (saves to database)
pigskin import-cmd espn \
  --team-id 1 \
  --league-id 123456789 \
  --swid "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}" \
  --espn-s2 "AEBxxx...longstring...xxx" \
  --year 2024

# Import for testing (in-memory only, no database)
pigskin import-cmd espn \
  --team-id 1 \
  --league-id 123456789 \
  --swid "{SWID_HERE}" \
  --espn-s2 "ESPN_S2_HERE" \
  --use-importer
```

### Import an Entire League

```bash
# Import all teams in a league
pigskin import-cmd espn-league \
  --league-id 123456789 \
  --swid "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}" \
  --espn-s2 "AEBxxx...longstring...xxx" \
  --year 2024
```

### Import Weekly Statistics (Historical Data)

```bash
# Import week-by-week data for analysis
pigskin import-cmd espn-weekly \
  --team-id 1 \
  --league-id 123456789 \
  --swid "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}" \
  --espn-s2 "AEBxxx...longstring...xxx" \
  --year 2024
```

This imports:
- Box scores for all completed weeks
- Player performances for each week
- Lineup configurations (who started/benched)
- Win/loss results

### Example Output

```
Connecting to ESPN Fantasy API...
✓ Connected to league: My Fantasy League
  Teams: 12
  Current Week: 14

Importing 12 teams...
  ✓ The Mahomes Depot (10-3-0)
  ✓ Kelce's Kingdom (9-4-0)
  ✓ Hill's Thrills (8-5-0)
  ...

✓ Successfully imported 12/12 teams!
  Access teams at: http://localhost:8000/leagues/123456789
```

## Web Interface Usage

### Step 1: Save League Credentials

1. Start the web server:
   ```bash
   uvicorn pigskin_mastermind.api.main:app --reload
   ```

2. Navigate to http://localhost:8000/settings

3. Click "Add League" and enter:
   - League ID
   - League Name (your choice)
   - Year
   - SWID cookie
   - ESPN_S2 cookie

4. Click "Save"

### Step 2: Sync Teams

1. From the settings page, click "Sync League" next to your saved league
2. All teams will be imported automatically
3. Navigate to http://localhost:8000/leagues/{league_id} to view teams

### Step 3: Sync Weekly Stats (Optional)

1. Go to a team's detail page
2. Click "Sync Weekly Stats"
3. Historical data will be imported for analysis

### Step 4: Mark Your Team

1. On the league page, click "Claim" next to your team
2. This marks it as your team for quick access

## API Details

### ESPN API Package

Pigskin Mastermind uses the `espn-api` package (version 0.7.0+):
- Repository: https://github.com/cwendt94/espn-api
- PyPI: https://pypi.org/project/espn-api/

### Data Models

#### League Object
```python
league = League(
    league_id=123456789,
    year=2024,
    espn_s2="your_cookie",
    swid="{your_swid}"
)

# Access data
league.teams          # List of all teams
league.current_week   # Current NFL week
league.box_scores()   # Get scores for a week
```

#### Team Object
```python
team = league.teams[0]

# Team properties
team.team_id          # ESPN team ID
team.team_name        # Team name
team.wins             # Number of wins
team.losses           # Number of losses
team.ties             # Number of ties
team.points_for       # Total points scored
team.roster           # List of players
team.owners           # List of owner info
team.outcomes         # W/L/T results by week
```

#### Player Object
```python
player = team.roster[0]

# Player properties
player.playerId           # ESPN player ID
player.name              # Player name
player.position          # Position (QB, RB, etc.)
player.proTeam           # NFL team abbreviation
player.projected_points  # Projected fantasy points
player.points            # Actual fantasy points
player.stats             # Detailed statistics dict
```

### Rate Limits

ESPN doesn't publish official rate limits, but to be respectful:
- Avoid making more than 1 request per second
- Cache data when possible
- Don't sync too frequently (once per day is usually sufficient)

### Data Freshness

- **Team/Player data**: Updates in real-time during games
- **Weekly data**: Available after each week's games complete
- **Season data**: Updates throughout the season

## Troubleshooting

### Authentication Errors

**Problem**: "Authentication failed" or "Invalid credentials"

**Solutions**:
1. Make sure you're logged into ESPN Fantasy in your browser
2. Re-extract the cookies - they may have expired
3. Verify the SWID includes the curly braces: `{XXXXXXXX-...}`
4. Ensure espn_s2 is the full cookie value (very long string)
5. For private leagues, make sure you're a member of the league

### Team/League Not Found

**Problem**: "Team X not found in league Y"

**Solutions**:
1. Verify the League ID is correct
2. Verify the Team ID is correct (1-12 typically)
3. Ensure the year is correct (defaults to 2024)
4. Check that you have access to the league

### No Module Named 'espn_api'

**Problem**: ImportError when trying to import

**Solution**:
```bash
pip install espn-api
```

### Private Leagues

For private leagues, you MUST provide valid authentication cookies. Public leagues may work without authentication, but it's recommended to always use cookies for consistency.

### Historical Data Not Available

If weekly stats aren't importing:
- The season may not have started yet
- Only completed weeks have data
- Box scores may not be available for future weeks

### Database Issues

If the database import fails:
1. Check that SQLite/PostgreSQL is accessible
2. Ensure the database file has write permissions
3. Try running with `--use-importer` flag for in-memory testing

## Examples

### Complete Workflow Example

```bash
# 1. Import your league
pigskin import-cmd espn-league \
  --league-id 123456789 \
  --swid "{ABC-123}" \
  --espn-s2 "LONG_COOKIE_STRING" \
  --year 2024

# 2. Import weekly stats for your team
pigskin import-cmd espn-weekly \
  --team-id 1 \
  --league-id 123456789 \
  --swid "{ABC-123}" \
  --espn-s2 "LONG_COOKIE_STRING"

# 3. Start the web interface
uvicorn pigskin_mastermind.api.main:app --reload

# 4. Open browser to http://localhost:8000
```

### Python API Example

```python
from espn_api.football import League

# Connect to league
league = League(
    league_id=123456789,
    year=2024,
    espn_s2="your_espn_s2_cookie",
    swid="{your_swid}"
)

# Get your team
my_team = league.teams[0]
print(f"{my_team.team_name}: {my_team.wins}-{my_team.losses}")

# Get all players
for player in my_team.roster:
    print(f"{player.name} ({player.position}): {player.points} points")

# Get box scores for current week
box_scores = league.box_scores(league.current_week)
for box in box_scores:
    print(f"{box.home_team.team_name} vs {box.away_team.team_name}")
    print(f"Score: {box.home_score} - {box.away_score}")
```

## Additional Resources

- [ESPN Fantasy API Documentation (community)](https://github.com/cwendt94/espn-api/wiki)
- [ESPN Fantasy Football](https://fantasy.espn.com/football)
- [Pigskin Mastermind Documentation](../README.md)
- [Issue Tracker](https://github.com/cbcom-wq/pigskin_mastermind/issues)

## Need Help?

If you encounter issues:
1. Check this troubleshooting guide
2. Search existing GitHub issues
3. Open a new issue with details about the error
