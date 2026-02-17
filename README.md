# Pigskin Mastermind

A comprehensive fantasy football research, entertainment, and management application.

## Features

### Team Management
- Create and manage fantasy football teams
- **Import teams from ESPN Fantasy Football** with full roster and statistics
- Import entire leagues with all teams at once
- Import historical weekly data for analysis
- Track team records and performance
- Compare teams and analyze composition

### Data & Analysis
- **Real-time ESPN API integration** for live player and team data
- Player statistics tracking with detailed breakdowns
- Fantasy point calculations (customizable scoring)
- Position-based player management
- Team performance metrics
- Historical data for trend analysis and projections

### Decision-Making Tools
- **Lineup Optimizer**: Automatically generate optimal starting lineups based on projections
- **Trade Analyzer**: Evaluate trade fairness and impact
- Lineup change suggestions
- Player comparison tools

### Entertainment Features
- **Team Name Generator**: Generate creative team names
- **Matchup Predictor**: Predict game outcomes with win probabilities
- **Power Rankings**: Generate league-wide power rankings
- **Weekly Awards**: Automatic award generation (highest scorer, best record, etc.)
- Friendly trash talk generator

## Installation

```bash
# Clone the repository
git clone https://github.com/cbcom-wq/pigskin_mastermind.git
cd pigskin_mastermind

# Install dependencies
pip install -r requirements.txt

# Install the package in development mode
pip install -e .
```

## Usage

### Command Line Interface

The application includes a CLI tool for quick access to features:

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

# Import from ESPN Fantasy Football
# Import a single team
pigskin import-cmd espn --team-id 1 --league-id 123456 --swid "{YOUR_SWID}" --espn-s2 "YOUR_ESPN_S2"

# Import entire league
pigskin import-cmd espn-league --league-id 123456 --swid "{YOUR_SWID}" --espn-s2 "YOUR_ESPN_S2"

# Import historical weekly stats
pigskin import-cmd espn-weekly --team-id 1 --league-id 123456 --swid "{YOUR_SWID}" --espn-s2 "YOUR_ESPN_S2"
```

See [ESPN API Integration Guide](docs/ESPN_API_GUIDE.md) for detailed instructions on getting your ESPN credentials and using the import features.
```

### Python API

```python
from pigskin_mastermind import Team, Player, TeamManager
from pigskin_mastermind.services.decision_tools import LineupOptimizer, TradeAnalyzer
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

## Project Structure

```
pigskin_mastermind/
├── src/pigskin_mastermind/
│   ├── models/              # Data models (Player, Team)
│   ├── services/            # Business logic (TeamManager, Importers, DecisionTools)
│   ├── entertainment/       # Entertainment features (NameGenerator, Awards)
│   ├── utils/               # Utility functions
│   └── cli.py               # Command-line interface
├── tests/                   # Test suite
├── requirements.txt         # Production dependencies
├── requirements-dev.txt     # Development dependencies
├── setup.py                 # Package setup
└── README.md               # This file
```

## Development

### Running Tests

```bash
# Install development dependencies
pip install -r requirements-dev.txt

# Run tests
pytest

# Run tests with coverage
pytest --cov=pigskin_mastermind --cov-report=html
```

### Code Quality

```bash
# Format code
black src/ tests/

# Lint code
flake8 src/ tests/
```

## Features Roadmap

- [x] ~~Real-time API integrations with ESPN~~ ✅ **Completed!**
- [x] ~~Historical data analysis~~ ✅ **Completed!**
- [ ] Yahoo Fantasy API integration
- [ ] Machine learning-based projections
- [x] ~~Web interface~~ ✅ **Completed!**
- [ ] Mobile app
- [ ] Advanced analytics and visualizations
- [ ] Draft assistant tools
- [ ] Waiver wire recommendations

## Documentation

- [ESPN API Integration Guide](docs/ESPN_API_GUIDE.md) - Complete guide for importing ESPN Fantasy data
- [Additional Data Sources](docs/DATA_SOURCES.md) - Information about other potential data sources
- [Tutorial](docs/TUTORIAL.md) - Getting started guide

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

For adding new data source integrations, see [DATA_SOURCES.md](docs/DATA_SOURCES.md) for recommendations and implementation patterns.

## License

This project is open source and available under the MIT License.
