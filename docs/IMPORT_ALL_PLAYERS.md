# Importing All Players from ESPN

## Overview

The Pigskin Mastermind application now supports importing the full player pool from ESPN, not just players currently on fantasy team rosters. This is essential for:

- **Waiver Wire Analysis**: Evaluate free agents available for pickup
- **Trade Analysis**: Compare rostered players against available alternatives
- **Season Planning**: Track all relevant NFL players throughout the season

## Implementation

### New Methods in `ESPNSyncService`

#### `import_all_players()`

Imports all available players from ESPN for a given league, including free agents.

```python
service = ESPNSyncService(db)
count = service.import_all_players(
    league_id="123456",
    espn_s2="your_espn_s2_cookie",
    swid="your_swid_cookie",
    year=2024,
    week=None,  # Defaults to current week
    positions=['QB', 'RB', 'WR', 'TE', 'K', 'D/ST'],
    batch_size=500  # Max 500 per position
)
```

**Parameters:**
- `league_id`: ESPN league ID
- `espn_s2`: ESPN S2 authentication cookie
- `swid`: ESPN SWID authentication cookie
- `year`: Season year (default: 2024)
- `week`: Week to fetch player data for (defaults to current week)
- `positions`: List of positions to import (defaults to all positions)
- `batch_size`: Number of players to fetch per position (max 500, default 500)

**Returns:** Number of players imported/updated

#### `sync_all_players()`

Convenience method that uses league credentials from the database:

```python
service = ESPNSyncService(db)
count = service.sync_all_players(
    league_id="123456",
    year=2024
)
```

### CLI Command

Import all players from the command line:

```bash
pigskin-mastermind stats import-all-players --league-id YOUR_LEAGUE_ID
```

**Options:**
- `--league-id`: ESPN league ID (required)
- `--year`: Season year (default: 2024)
- `--week`: Specific week to import (defaults to current)
- `--positions`: Comma-separated positions (default: QB,RB,WR,TE,K,D/ST)
- `--batch-size`: Players per position (default: 500)

**Examples:**

```bash
# Import all positions for current week
pigskin-mastermind stats import-all-players --league-id 123456

# Import only skill positions
pigskin-mastermind stats import-all-players --league-id 123456 --positions QB,RB,WR,TE

# Import for a specific week
pigskin-mastermind stats import-all-players --league-id 123456 --week 10
```

### API Endpoint

Import players via the web API:

```http
POST /leagues/{league_id}/import-all-players?year=2024
```

**Response:**
```json
{
  "message": "Successfully imported 1234 players",
  "type": "success"
}
```

## How It Works

1. **ESPN API Integration**: Uses the ESPN Fantasy Football API's `free_agents()` method to fetch all available players by position
2. **Batch Processing**: Fetches players in batches by position to avoid API rate limits
3. **Database Upsert**: Updates existing player records or creates new ones
4. **Free Agent Tracking**: Players without a `team_id` are free agents

## Data Structure

Players are stored in the `DBPlayer` table with:
- `player_id`: Unique identifier (e.g., "espn_12345")
- `name`: Player name
- `position`: Position (QB, RB, WR, TE, K, D/ST)
- `nfl_team`: NFL team abbreviation
- `team_id`: Foreign key to fantasy team (NULL for free agents)
- `projected_points`: Projected fantasy points
- `actual_points`: Actual fantasy points scored
- `stats`: JSON field with detailed stats

## Free Agent Queries

### Finding Free Agents

```python
# Get all free agents
free_agents = db.query(DBPlayer).filter(DBPlayer.team_id.is_(None)).all()

# Top-scoring free agent RBs
top_rb_fa = db.query(DBPlayer)\
    .filter(DBPlayer.team_id.is_(None), DBPlayer.position == 'RB')\
    .order_by(DBPlayer.projected_points.desc())\
    .limit(20)\
    .all()
```

### API Queries

The existing player search endpoints automatically work with free agents:

```http
GET /api/players/search?q=Jefferson
GET /api/players/list?position=RB&limit=100
```

## Best Practices

1. **Regular Updates**: Run import weekly to keep free agent pool current
2. **Position Filtering**: Import only relevant positions to reduce API load
3. **Timing**: Import after weekly stat updates but before making roster decisions
4. **League Context**: Each league may have different available players based on roster status

## Notes

- ESPN API limits batch size to 500 players per position per request
- Free agents are identified by having `team_id = NULL`
- Player stats are from the most recent week specified
- Rostered players retain their team associations
- The import can be run multiple times safely (upsert logic handles duplicates)

## Troubleshooting

**No players imported:**
- Verify league credentials (espn_s2 and swid) are valid
- Check that the league exists in the database
- Ensure the week parameter is valid for the season

**Missing positions:**
- Some positions may have fewer than batch_size players available
- Check the positions parameter spelling (D/ST not DST)

**Duplicate players:**
- This is expected - the import uses upsert logic to update existing records
- Player IDs are unique across the database
