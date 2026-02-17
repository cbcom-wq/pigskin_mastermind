# Pigskin Mastermind

A comprehensive fantasy football research, entertainment, and management application.

## Features

### Team Management
- Create and manage fantasy football teams
- Import teams from fantasy football services (ESPN, Yahoo)
- Track team records and performance
- Compare teams and analyze composition

### Data & Analysis
- Player statistics tracking
- Fantasy point calculations (customizable scoring)
- Position-based player management
- Team performance metrics

### Decision-Making Tools
- **Lineup Optimizer**: Automatically generate optimal starting lineups based on projections
- **Trade Analyzer**: Evaluate trade fairness and impact
- **Player Projections**: Generate weekly and yearly fantasy point projections using advanced criteria
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

# Import from ESPN
pigskin import-cmd espn --team-id 12345 --swid YOUR_SWID --espn-s2 YOUR_ESPN_S2 --league-id 67890
```

### Python API

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
- All yearly criteria factors

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

### Example Usage

See the Python API section above for projection usage examples.

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

- [x] Basic player projection system (weekly and yearly)
- [ ] Real-time API integrations with ESPN and Yahoo
- [ ] Historical data analysis
- [ ] Machine learning-based projections
- [ ] Enhanced projection algorithms with position-specific criteria
- [ ] Web interface
- [ ] Mobile app
- [ ] Advanced analytics and visualizations
- [ ] Draft assistant tools
- [ ] Waiver wire recommendations

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is open source and available under the MIT License.
