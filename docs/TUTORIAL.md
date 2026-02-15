# Pigskin Mastermind Tutorial

## Getting Started

### Installation

1. Clone the repository:
```bash
git clone https://github.com/cbcom-wq/pigskin_mastermind.git
cd pigskin_mastermind
```

2. Install the package:
```bash
pip install -e .
```

3. Verify installation:
```bash
pigskin --version
```

## Basic Usage

### Creating Teams

#### Using Python API
```python
from pigskin_mastermind.services.team_manager import TeamManager

# Create a team manager
manager = TeamManager()

# Create a new team
team = manager.create_team(
    team_id="my_team_1",
    name="The Champions",
    owner="John Doe"
)

print(f"Created team: {team.name}")
```

#### Using CLI
```bash
pigskin team create --id my_team_1 --name "The Champions" --owner "John Doe"
```

### Adding Players

#### Using Python API
```python
from pigskin_mastermind.models.player import Player

# Create a player
player = Player(
    player_id="mahomes_15",
    name="Patrick Mahomes",
    position="QB",
    team="KC",
    projected_points=25.5
)

# Add to team
team.add_player(player)
```

#### Using CLI
```bash
pigskin team add-player \
    --team-id my_team_1 \
    --player-id mahomes_15 \
    --name "Patrick Mahomes" \
    --position QB \
    --nfl-team KC \
    --projected 25.5
```

## Advanced Features

### Lineup Optimization

The lineup optimizer automatically selects your best starting lineup based on projected points:

```python
from pigskin_mastermind.services.decision_tools import LineupOptimizer

optimizer = LineupOptimizer()
result = optimizer.optimize_lineup(team)

print(f"Total projected points: {result['total_projected_points']}")

# View starters
for position, players in result['lineup'].items():
    for player in players:
        print(f"{position}: {player.name} - {player.projected_points} pts")

# View bench
for player in result['bench']:
    print(f"Bench: {player.name}")
```

Or using CLI:
```bash
pigskin lineup optimize --team-id my_team_1
```

### Trade Analysis

Evaluate trades before you make them:

```python
from pigskin_mastermind.services.decision_tools import TradeAnalyzer

analyzer = TradeAnalyzer()

# Analyze a trade
result = analyzer.evaluate_trade_for_team(
    team=my_team,
    gives=[player1, player2],  # Players you're trading away
    receives=[player3]          # Players you're receiving
)

print(f"Net gain: {result['net_gain']} points")
print(f"Recommendation: {result['recommendation']}")
```

### Team Analysis

Get detailed insights about your team:

```python
from pigskin_mastermind.services.team_manager import TeamManager

manager = TeamManager()
analysis = manager.analyze_team(team_id)

print(f"Team: {analysis['team_name']}")
print(f"Total Points: {analysis['total_points']}")
print(f"Average Points per Player: {analysis['avg_points_per_player']}")

# Position breakdown
for position, data in analysis['position_breakdown'].items():
    print(f"{position}: {data['count']} players, {data['total_points']} pts")
```

Or using CLI:
```bash
pigskin team analyze --team-id my_team_1
```

## Entertainment Features

### Generate Team Names

Get creative team name suggestions:

```python
from pigskin_mastermind.entertainment import TeamNameGenerator

generator = TeamNameGenerator()

# Random names
names = generator.suggest_names(5)
for name in names:
    print(name)

# Player-based names
player_names = generator.generate_player_based_name("Patrick Mahomes")
for name in player_names:
    print(name)
```

Or using CLI:
```bash
pigskin entertainment generate-name --count 5
pigskin entertainment player-names --player "Patrick Mahomes"
```

### Matchup Predictions

Predict outcomes of head-to-head matchups:

```python
from pigskin_mastermind.entertainment import MatchupPredictor

predictor = MatchupPredictor()
result = predictor.predict_matchup(team1, team2)

print(f"Predicted winner: {result['predicted_winner']}")
print(f"Win probability: {result['win_probability']:.1%}")
print(f"Projected margin: {result['projected_margin']} points")
print(f"Confidence: {result['confidence']}")
```

### League Power Rankings

Generate power rankings for your league:

```python
from pigskin_mastermind.entertainment import LeagueEntertainment

entertainment = LeagueEntertainment()
rankings = entertainment.generate_power_rankings(teams)

for team in rankings:
    print(f"{team['rank']}. {team['team_name']} - {team['power_score']:.3f}")
```

### Weekly Awards

Automatically generate awards for your league:

```python
awards = entertainment.generate_weekly_awards(teams)

print(f"Highest Scorer: {awards['highest_scorer']['team']}")
print(f"  Points: {awards['highest_scorer']['points']}")

print(f"Best Record: {awards['best_record']['team']}")
print(f"  Record: {awards['best_record']['record']}")
```

## Importing from Fantasy Services

### ESPN Fantasy

```python
from pigskin_mastermind.services.importer import ImporterFactory

# Create ESPN importer
importer = ImporterFactory.create_importer('espn')

# Authenticate
credentials = {
    'swid': 'your_swid_cookie',
    'espn_s2': 'your_espn_s2_cookie',
    'league_id': 'your_league_id'
}

if importer.authenticate(credentials):
    # Import team
    team = importer.import_team('team_id')
    print(f"Imported: {team.name}")
```

Or using CLI:
```bash
pigskin import-cmd espn \
    --team-id 12345 \
    --swid YOUR_SWID \
    --espn-s2 YOUR_ESPN_S2 \
    --league-id 67890
```

### Yahoo Fantasy

```python
# Create Yahoo importer
importer = ImporterFactory.create_importer('yahoo')

# Authenticate with OAuth token
credentials = {
    'access_token': 'your_oauth_token'
}

if importer.authenticate(credentials):
    team = importer.import_team('team_id')
```

## Working with Player Statistics

### Calculating Fantasy Points

```python
from pigskin_mastermind.models.player import Player

player = Player(
    player_id="rb_1",
    name="Running Back",
    position="RB",
    team="KC"
)

# Add stats
player.update_stats({
    'rush_yd': 100,
    'rush_td': 2,
    'rec': 5,
    'rec_yd': 40
})

# Calculate points with default scoring
points = player.calculate_points()
print(f"Fantasy points: {points}")

# Custom scoring settings
custom_scoring = {
    'rush_yd': 0.1,
    'rush_td': 6,
    'rec': 0.5,  # Half PPR
    'rec_yd': 0.1,
}
points = player.calculate_points(custom_scoring)
```

## Export and Import Team Data

### Export Team

```python
from pigskin_mastermind.services.team_manager import TeamManager

manager = TeamManager()
team_json = manager.export_team_data(team_id)

# Save to file
with open('my_team.json', 'w') as f:
    f.write(team_json)
```

### Import Team

```python
# Load from file
with open('my_team.json', 'r') as f:
    team_json = f.read()

# Import team
team = manager.import_team_data(team_json)
print(f"Imported: {team.name}")
```

## Tips and Best Practices

1. **Regular Updates**: Keep player projections updated weekly for best results
2. **Backup Data**: Export your team data regularly
3. **Compare Multiple Scenarios**: Use the trade analyzer to compare different trade options
4. **Monitor Power Rankings**: Track your team's position over time
5. **Use Lineup Optimizer**: Run it before each game week to ensure optimal lineup

## Troubleshooting

### Common Issues

**Issue**: Import fails with authentication error
- **Solution**: Make sure your credentials are current and valid

**Issue**: CLI command not found
- **Solution**: Reinstall the package: `pip install -e .`

**Issue**: Tests failing
- **Solution**: Make sure all dependencies are installed: `pip install -r requirements-dev.txt`

## Next Steps

- Explore the example scripts in the `examples/` directory
- Check out the API documentation
- Join the community discussions
- Contribute to the project

## Support

For questions or issues:
- Open an issue on GitHub
- Check the documentation
- Review the examples
