# Pigskin Mastermind

A comprehensive fantasy football research, entertainment, and management application with a full web UI, REST API, and CLI.

## Documentation

| Document | What it covers |
|---|---|
| [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | Current state, feature status, test results, known issues — **start here** |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, data flow, and per-subsystem deep dives |
| [CLAUDE.md](CLAUDE.md) | Commands, conventions, and gotchas (written for Claude Code, useful to humans) |
| [docs/TUTORIAL.md](docs/TUTORIAL.md) | Python API walkthrough |
| [docs/IMPORT_ALL_PLAYERS.md](docs/IMPORT_ALL_PLAYERS.md) | Free-agent / full-player ESPN import |
| [docs/SEASON_ANIMATION.md](docs/SEASON_ANIMATION.md) | Season animation feature |

## Features

### Web Interface
- **Dashboard**: Overview of teams, players, and total points at a glance
- **Team Management**: Create, view, and manage fantasy teams through a browser
- **Player Management**: Browse, search, and manage players by position
- **League Management**: Configure and track fantasy leagues
- **Lineup Optimization**: Interactive lineup editing with projected-point optimization
- **Trade Analyzer**: Evaluate trade fairness with visual breakdowns
- **Draft Simulator**: Mock draft engine using ESPN ADP or custom player pools with configurable AI opponents and strategies
- **Projection Tuner**: Interactive sliders over every projection coefficient, with per-criteria contribution breakdowns, backtesting, and automated per-position coefficient sweeps
- **Season Animation**: Animated week-by-week cumulative points chart with weekly MVP highlights
- **Game Animations**: Play-by-play field animations for a player, a fantasy roster, or a full NFL game
- **Visualizations**: Static and animated season performance charts

### Data & Statistics
- **ESPN Sync**: Import teams, rosters, and weekly stats from ESPN Fantasy leagues
- **NFL Data Import**: Pull play-by-play and weekly stats via `nfl_data_py` for league-wide analysis
- **Free Agent Pool**: Import all available players (not just rostered) for waiver wire analysis
- **Player Game Logs**: Detailed per-game stat history with season totals
- **Player Projections**: Weekly and season-long fantasy point projections using multi-factor criteria
- **Scoring Settings**: Configurable per-league scoring (PPR, half-PPR, standard, custom)
- **Position Breakdown**: Team composition and performance metrics by position

### Decision-Making Tools
- **Lineup Optimizer**: Automatically fill optimal starting lineup by projected points (including FLEX)
- **Trade Analyzer**: Evaluate trade net value and receive Accept/Reject/Consider recommendations
- **Player Projections**: Weekly and yearly projections based on skill level, opponent defense, weather, momentum, and more
- **Monte Carlo Simulation**: 10,000-iteration outcome distributions per player — expected points, floor, ceiling, boom/bust probabilities, and a histogram
- **Sportsbook Projections**: Fantasy points derived from live player-prop betting lines (The Odds API)
- **Lineup Change Suggestions**: Identify bench players who should start over current starters

### Entertainment Features
- **Team Name Generator**: Generate creative team names (random or player-based)
- **Matchup Predictor**: Predict game outcomes with win probabilities and confidence levels
- **Power Rankings**: League-wide composite rankings (60% win %, 40% points)
- **Weekly Awards**: Automated awards for highest scorer, best record, luckiest team, and more
- **Trash Talk Generator**: Auto-generated trash talk for matchup results

## Installation

```bash
# Clone the repository
git clone https://github.com/cbcom-wq/pigskin_mastermind.git
cd pigskin_mastermind

# Install dependencies
pip install -r requirements.txt

# Install the package in development mode
pip install -e .

# ESPN integration needs the espn_api client, which is NOT bundled with this repo
pip install espn_api
```

Optional environment variables:

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy connection URL | `sqlite:///./pigskin_mastermind.db` |
| `ODDS_API_KEY` | The Odds API key for sportsbook lines and props | unset |

## Launching the Web Interface

The web interface is built with **FastAPI** and served by **Uvicorn**. The database is managed by **SQLAlchemy** and **Alembic**.

### 1. Set up the database

```bash
# Apply all database migrations
alembic upgrade head
```

### 2. Start the web server

```bash
uvicorn pigskin_mastermind.api.main:app --reload
```

The app will be available at **http://127.0.0.1:8000**.

| URL | Description |
|-----|-------------|
| `http://127.0.0.1:8000/` | Dashboard |
| `http://127.0.0.1:8000/teams` | Team management |
| `http://127.0.0.1:8000/players` | Player browser |
| `http://127.0.0.1:8000/leagues` | League management |
| `http://127.0.0.1:8000/trades` | Trade analyzer |
| `http://127.0.0.1:8000/draft` | Mock draft simulator |
| `http://127.0.0.1:8000/docs` | Interactive API docs (Swagger UI) |

To bind to a different host or port:

```bash
uvicorn pigskin_mastermind.api.main:app --host 0.0.0.0 --port 8080 --reload
```

## Command Line Interface

The `pigskin` CLI provides quick access to core features without the web server.

```bash
# Create a new team
pigskin team create --id t1 --name "My Team" --owner "John Doe"

# Add a player to a team
pigskin team add-player --team-id t1 --player-id p1 --name "Patrick Mahomes" --position QB --nfl-team KC --projected 25.5

# Analyze a team
pigskin team analyze --team-id t1

# Optimize lineup
pigskin lineup optimize --team-id t1

# Generate team names
pigskin entertainment generate-name --count 5

# Generate player-based names
pigskin entertainment player-names --player "Patrick Mahomes"

# Import from ESPN
pigskin import-cmd espn --team-id 12345 --swid YOUR_SWID --espn-s2 YOUR_ESPN_S2 --league-id 67890
```

## Python API

```python
from pigskin_mastermind import Team, Player, TeamManager
from pigskin_mastermind.services.decision_tools import LineupOptimizer, TradeAnalyzer
from pigskin_mastermind.services.projection_service import YearlyProjectionService, WeeklyProjectionService
from pigskin_mastermind.models.projection_criteria import YearlyProjectionCriteria, WeeklyProjectionCriteria
from pigskin_mastermind.entertainment import TeamNameGenerator

# Create a team manager
manager = TeamManager()
team = manager.create_team("t1", "My Team", "John Doe")

# Add players
player = Player(
    player_id="p1",
    name="Patrick Mahomes",
    position="QB",
    team="KC",
    projected_points=25.5
)
team.add_player(player)

# Generate yearly projection
yearly_service = YearlyProjectionService()
yearly_criteria = YearlyProjectionCriteria(
    player_skill_level=95.0,
    team_offense_level=90.0,
    historical_average_points=25.0,
    age_deviation_from_optimum=0.0,
    coaching_stability_score=95.0
)
yearly_projection = yearly_service.calculate_projection(player, yearly_criteria)
print(f"Yearly projection: {yearly_projection} points")

# Generate weekly projection
weekly_service = WeeklyProjectionService()
weekly_criteria = WeeklyProjectionCriteria(
    player_skill_level=95.0,
    team_offense_level=90.0,
    historical_average_points=25.0,
    opposing_defense_vs_position_rank=28,
    offensive_momentum_score=20.0,
    weather_impact_score=0.0
)
weekly_report = weekly_service.generate_projection_report(player, weekly_criteria)
print(f"Weekly projection: {weekly_report['projected_points']} points")

# Optimize lineup
optimizer = LineupOptimizer()
result = optimizer.optimize_lineup(team)
print(f"Total projected points: {result['total_projected_points']}")

# Generate team names
generator = TeamNameGenerator()
names = generator.suggest_names(5)
print("Suggested team names:", names)

# Analyze trades
analyzer = TradeAnalyzer()
trade_result = analyzer.evaluate_trade_for_team(team, gives=[player1], receives=[player2])
print(f"Trade recommendation: {trade_result['recommendation']}")
```

## Player Projections

The projection system provides sophisticated fantasy point projections for players using multiple criteria. Two types of projections are available:

### Yearly Projections
Season-long projections using criteria such as:
- Player skill level and team offense strength
- Historical performance averages
- Age deviation from optimal position age
- Coaching staff stability
- Injury risk and recent trends

### Weekly Projections
Week-by-week projections accounting for:
- Opponent defensive strength vs. specific position
- Recent offensive momentum
- Weather conditions and forecast
- Shared base projection criteria plus weekly-specific factors

### Projection Criteria

Both projection types use common base criteria:
- **Player Skill Level** (0-100): Overall player ability rating
- **Team Offense Level** (0-100): Team's offensive strength rating
- **Opponent Defense Level** (0-100): Defense quality (higher = worse defense, better for offense)
- **Positional Touch Percentage** (0-100): Share of team touches at this position
- **Recent Trend Score** (-100 to 100): Performance trend indicator
- **Historical Average Points**: Past fantasy points per game average
- **Fantasy Points Per Touch**: Efficiency metric
- **Injury Risk Score** (0-100): Injury history and risk assessment

**Yearly-specific criteria:**
- **Age Deviation from Optimum** (-10 to 10): Distance from peak age for position
- **Coaching Stability Score** (0-100): Coaching staff consistency rating

**Weekly-specific criteria:**
- **Opposing Defense vs Position Rank** (1-32): Defensive rank against this position
- **Offensive Momentum Score** (-100 to 100): Recent team offensive performance
- **Weather Impact Score** (-100 to 100): Weather conditions impact

## ESPN Integration

### Syncing a League

Import team rosters and weekly stats from an ESPN Fantasy league via the web UI (**Leagues → Sync**) or the API endpoint `POST /leagues/{league_id}/sync`.

### Importing All Players (Free Agents)

Import all available players (including free agents) for waiver wire analysis via the web UI (**Leagues → Import All Players**) or the API endpoint:

```
POST /leagues/{league_id}/import-all-players?year=2024
```

See [docs/IMPORT_ALL_PLAYERS.md](docs/IMPORT_ALL_PLAYERS.md) for full details.

## Project Structure

```
pigskin_mastermind/
├── src/pigskin_mastermind/
│   ├── api/                 # FastAPI application
│   │   ├── main.py          # App entry point and dashboard route
│   │   ├── database.py      # SQLAlchemy engine and session
│   │   └── routes/          # API route handlers (teams, players, leagues,
│   │                        #   trades, draft, stats, visualizations)
│   ├── models/              # Data models (Player, Team, DB models, projections)
│   ├── services/            # Business logic
│   │   ├── team_manager.py       # Team CRUD
│   │   ├── decision_tools.py     # LineupOptimizer, TradeAnalyzer
│   │   ├── projection_service.py # Weekly/yearly projections
│   │   ├── espn_sync.py          # ESPN league/roster sync
│   │   ├── nfl_data_service.py   # NFL-wide stats via nfl_data_py
│   │   ├── mock_draft.py         # Mock draft engine with ESPN ADP
│   │   ├── visualization_service.py # Season animation charts
│   │   ├── stats_service.py      # Player stats and game logs
│   │   └── importer.py           # Fantasy platform importers
│   ├── entertainment/       # Entertainment features
│   ├── templates/           # Jinja2 HTML templates for the web UI
│   ├── utils/               # Utility functions
│   └── cli.py               # Click-based CLI interface
├── alembic/                 # Database migrations
├── alembic.ini              # Alembic configuration
├── tests/                   # Test suite
├── docs/                    # Additional documentation
│   ├── ARCHITECTURE.md      # System design and subsystem deep dives
│   ├── PROJECT_STATUS.md    # Current state, test results, known issues
│   ├── TUTORIAL.md
│   ├── IMPORT_ALL_PLAYERS.md
│   └── SEASON_ANIMATION.md
├── examples/                # Example scripts
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development dependencies
├── setup.py                 # Package setup
└── README.md                # This file
```

## Development

### Running Tests

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run tests — always scope to tests/
pytest tests/

# Run tests with coverage
pytest tests/ --cov=pigskin_mastermind --cov-report=html
```

> Bare `pytest` fails at collection: the vendored ESPN client under
> `src/pigskin_mastermind/lib/espn-api/` ships its own test tree that imports `espn_api` as an
> installed package. Always pass `tests/`.

### Code Quality

```bash
# Format code
black src/ tests/

# Lint code
flake8 src/ tests/
```

## Features Roadmap

- [x] Basic player projection system (weekly and yearly)
- [x] Web interface (FastAPI + Jinja2)
- [x] Database persistence (SQLAlchemy + Alembic)
- [x] ESPN league sync and free-agent import
- [x] Mock draft simulator with ESPN ADP
- [x] Season animation visualization
- [x] NFL play-by-play stats import
- [x] Monte Carlo outcome distributions
- [x] Sportsbook odds and prop-derived projections
- [x] Automated projection-coefficient tuning (per position)
- [ ] Real-time API integrations with Yahoo Fantasy
- [ ] Machine learning-based projections
- [ ] Mobile app
- [ ] Draft assistant tools (waiver wire recommendations)

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the MIT License.
