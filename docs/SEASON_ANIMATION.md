# Season Animation Visualization Feature

## Overview

The Season Animation Visualization feature provides an interactive, animated view of a fantasy football team's performance throughout the season. It tracks player points accumulation week-by-week, displays team results, and highlights the MVP (Most Valuable Player) for each week.

## Features

### Visual Components

1. **Cumulative Points Chart**
   - Line chart showing each player's cumulative points over the season
   - Displays top 8-10 players by total points
   - Color-coded lines for easy player tracking
   - Animated progression week-by-week

2. **Team Results Display**
   - Win/Loss indicator for each week
   - Color-coded background (green for wins, red for losses)
   - Displays points scored vs. points against
   - Opponent name shown for each matchup

3. **Weekly MVP Highlight**
   - Identifies the highest-scoring starter each week
   - Prominently displays MVP name and points
   - Gold-colored highlight for emphasis

### Interactive Controls

- **Play/Pause**: Control animation playback
- **Reset**: Return to the beginning of the season
- **Speed Control**: Adjust animation speed (0.5x - 3.0x)
- **Progress Bar**: Shows current week in the animation

### Season Summary Dashboard

Displays key statistics:
- Team record (wins-losses)
- Total weeks played
- Total points scored
- Average points per week
- Season MVP (player with most cumulative points)

## Usage

### Accessing the Visualization

1. Navigate to a team's detail page
2. Click the "Season Animation" button in the team header
3. The visualization page will load with season data

### API Endpoints

#### Web Interface
```
GET /visualizations/season-animation/{team_id}
```
Displays the interactive animation page.

#### Data API
```
GET /visualizations/api/season-data/{team_id}
```
Returns JSON with:
- Team information
- Weekly data for all weeks
- Player statistics with cumulative points
- Team results (W/L/T)
- Weekly MVP data

**Response Format:**
```json
{
  "team_id": 1,
  "team_name": "My Team",
  "weeks": [1, 2, 3, ...],
  "players": [
    {
      "id": 1,
      "name": "Player Name",
      "position": "QB",
      "weeks": [1, 2, 3, ...],
      "cumulative_points": [25.5, 48.3, 72.1, ...],
      "weekly_points": [25.5, 22.8, 23.8, ...]
    }
  ],
  "team_results": [
    {
      "week": 1,
      "result": "W",
      "points_for": 120.5,
      "points_against": 105.3,
      "opponent": "Opponent Team"
    }
  ],
  "mvps": [
    {
      "week": 1,
      "player": "Player Name",
      "points": 28.5
    }
  ]
}
```

#### Animation Frames
```
GET /visualizations/api/animation-frames/{team_id}?max_weeks=10
```
Returns pre-rendered base64-encoded PNG images for each week.

**Query Parameters:**
- `max_weeks` (optional): Limit frames to first N weeks

**Response Format:**
```json
{
  "team_id": 1,
  "frame_count": 10,
  "frames": [
    "iVBORw0KGgoAAAANSUhEUgAA...",  // base64 PNG
    "iVBORw0KGgoAAAANSUhEUgAA...",
    ...
  ]
}
```

#### Static Visualization
```
GET /visualizations/api/static-visualization/{team_id}
```
Returns a single static image showing the complete season.

## Technical Implementation

### Service Layer

**`SeasonVisualizationService`** (`services/visualization_service.py`)

Key methods:
- `get_season_data(team_id)`: Aggregates all weekly data
- `generate_static_visualization(team_id)`: Creates final season chart
- `generate_animation_frames(team_id, max_weeks)`: Renders week-by-week frames
- `get_season_summary(team_id)`: Calculates summary statistics

### Data Requirements

The visualization requires:
- `DBWeeklyTeamStats`: Team-level weekly stats (points, opponent, result)
- `DBWeeklyPlayerStats`: Player-level weekly stats (slot position, points)
- Synced data from ESPN or manual entry

### Chart Generation

Uses **matplotlib** with non-interactive backend ('Agg'):
- 2-subplot layout (top: cumulative points, bottom: MVP/results)
- Charts converted to base64-encoded PNG for web delivery
- Optimized for 12x10 inch figures at 100 DPI

### Animation Playback

Client-side JavaScript:
- Loads all frames on page load
- Displays frames sequentially based on speed setting
- Updates progress bar and week counter
- Memory-efficient: ~60-100KB per frame

## Entertainment Module Integration

The feature is integrated into the entertainment module:

```python
from pigskin_mastermind.entertainment import SeasonVisualization

# Get visualization URL
url = SeasonVisualization.get_visualization_url(team_id)

# Get API data URL
data_url = SeasonVisualization.get_api_data_url(team_id)
```

## MVP Calculation

Weekly MVP is determined by:
1. Only starters are considered (slot_position not 'BE' or 'IR')
2. Player with highest actual_points for the week
3. If no starters scored, shows "N/A"

Season MVP is:
- Player with highest cumulative points across all weeks
- Includes all players regardless of starting status

## Performance Considerations

### Frame Generation
- Each frame is ~60-100KB base64-encoded
- Full season (14 weeks) = ~840KB-1.4MB total
- Generated on-demand, cached in browser
- Consider server-side caching for production

### Data Queries
- Single query for team data
- Single query for all weekly stats (ordered by week)
- Multiple queries for player details (one per unique player)
- Consider eager loading for optimization

## Future Enhancements

Possible improvements:
1. **Client-side rendering**: Use Chart.js or D3.js for smoother animations
2. **Real-time updates**: WebSocket integration for live game updates
3. **Comparison mode**: Compare multiple teams side-by-side
4. **Export options**: Download as video or GIF
5. **Interactive tooltips**: Hover for detailed player stats
6. **Projection overlay**: Show projected vs. actual points
7. **Highlight plays**: Show big scoring plays for each week

## Testing

### Unit Tests
Located in `tests/test_visualization_service.py`:
- Tests for data aggregation
- Tests for empty/missing data handling
- Tests for summary calculations

### Manual Testing
1. Create test data with weekly stats
2. Access visualization page
3. Verify:
   - Animation plays correctly
   - Controls work as expected
   - Data displays accurately
   - MVP calculation is correct

## Dependencies

Added to `requirements.txt`:
- `matplotlib>=3.8.0`: Chart generation library
  - Includes numpy and pillow as sub-dependencies
  - No known security vulnerabilities (checked via GitHub Advisory Database)

## Security

- ✅ No user input in chart generation (SQL injection safe)
- ✅ Base64 encoding prevents XSS attacks
- ✅ No file system writes (memory-only operations)
- ✅ CodeQL scan: 0 vulnerabilities found
- ✅ Dependency scan: No vulnerabilities

## Troubleshooting

### No Data Available
- Ensure weekly stats have been imported via ESPN sync
- Check that team has completed at least one week
- Verify team_id is correct

### Animation Not Loading
- Check browser console for errors
- Ensure JavaScript is enabled
- Verify API endpoints return valid data
- Check network tab for failed requests

### Performance Issues
- Limit weeks using max_weeks parameter
- Consider server-side caching
- Use static visualization for quick overview
- Optimize database queries with indexes

## Example Usage

```python
from pigskin_mastermind.services.visualization_service import SeasonVisualizationService
from pigskin_mastermind.api.database import get_db

# Get database session
db = next(get_db())

# Create service
viz_service = SeasonVisualizationService(db)

# Get season data
data = viz_service.get_season_data(team_id=1)

# Generate frames
frames = viz_service.generate_animation_frames(team_id=1, max_weeks=5)

# Get summary
summary = viz_service.get_season_summary(team_id=1)
```
