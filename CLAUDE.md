# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Pigskin Mastermind is a fantasy football management application providing team management, decision-making tools, and entertainment features. The codebase is structured as a Python package with both a programmatic API and CLI interface.

## Development Commands

### Setup and Installation
```bash
# Install in development mode
pip install -e .

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install -r requirements-dev.txt
```

### Testing
```bash
# Run all tests
pytest

# Run tests with coverage report
pytest --cov=pigskin_mastermind --cov-report=html

# Run a single test file
pytest tests/test_player.py

# Run a specific test
pytest tests/test_player.py::test_function_name -v
```

### Code Quality
```bash
# Format code with Black
black src/ tests/

# Lint code with flake8
flake8 src/ tests/
```

### CLI Usage
The package provides a `pigskin` CLI command (defined in cli.py entry point):
```bash
# Team management
pigskin team create --id t1 --name "Team Name" --owner "Owner"
pigskin team add-player --team-id t1 --player-id p1 --name "Player" --position QB --nfl-team KC
pigskin team analyze --team-id t1

# Lineup optimization
pigskin lineup optimize --team-id t1

# Entertainment features
pigskin entertainment generate-name --count 5
pigskin entertainment player-names --player "Patrick Mahomes"

# Importing from services
pigskin import-cmd espn --team-id ID --swid COOKIE --espn-s2 COOKIE --league-id LEAGUE
```

## Architecture

### Core Models (models/)
- **Player** (`models/player.py`): Dataclass representing fantasy players
  - Validates position against `['QB', 'RB', 'WR', 'TE', 'K', 'DEF']`
  - Calculates fantasy points using customizable scoring settings (default PPR)
  - Tracks both projected and actual points
  - Stats stored as dictionary for flexibility

- **Team** (`models/team.py`): Dataclass representing fantasy teams
  - Manages player roster with add/remove/get operations
  - Tracks record (wins/losses/ties) and total points
  - `get_players_by_position()` filters roster by position
  - `get_starting_lineup()` uses lineup rules to optimize starters

### Services (services/)
- **TeamManager** (`services/team_manager.py`): Central service for team CRUD operations
  - Maintains in-memory dict of teams (not persisted)
  - Provides team analysis with position breakdown
  - Supports JSON import/export of team data

- **LineupOptimizer** (`services/decision_tools.py`): Optimizes fantasy lineups
  - Default lineup rules: `{'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1, 'K': 1, 'DEF': 1}`
  - FLEX position can be filled by RB/WR/TE (best remaining player by projected points)
  - Returns lineup dict with starters by position, bench list, and total projected points
  - `suggest_lineup_changes()` identifies better bench players

- **TradeAnalyzer** (`services/decision_tools.py`): Evaluates trade fairness
  - Calculates net value based on projected points
  - Fairness score threshold: < 5.0 points difference
  - Tracks positional needs (positions gained/lost)
  - Provides recommendations: "Accept", "Reject", or "Consider"

- **Importers** (`services/importer.py`): Abstract base class pattern for fantasy platforms
  - `ImporterFactory.create_importer('espn')` or `'yahoo'` returns platform-specific importer
  - ESPN importer requires SWID and ESPN_S2 cookies for authentication
  - Yahoo importer requires OAuth access token
  - **Note**: Current implementations are stubs; real API integration needed

### Entertainment Features (entertainment/)
- **TeamNameGenerator**: Random and player-based team name generation
  - Combines prefixes + nouns or returns football puns
  - `generate_player_based_name()` creates variations on player's last name

- **MatchupPredictor**: Predicts game outcomes
  - Win probability calculation based on projected point differential
  - Confidence levels: High (>15 pts), Medium (>5 pts), Low (≤5 pts)

- **LeagueEntertainment**: League-wide features
  - Power rankings: composite score (60% win%, 40% points)
  - Weekly awards: highest/lowest scorer, best record, "luckiest team"
  - Trash talk generator for matchup results

### CLI (cli.py)
- Built with Click framework
- Command groups: `team`, `lineup`, `import-cmd`, `entertainment`
- TeamManager instances are created per-command (no persistence between CLI calls)

## Important Implementation Notes

### Fantasy Football Domain Logic
1. **Position Eligibility for FLEX**: Only RB, WR, TE can fill FLEX spots (not QB, K, or DEF)
2. **Scoring Settings**: Default is PPR (Point Per Reception). When modifying scoring:
   - Pass yards: typically 0.04 (1 point per 25 yards)
   - Rush/Rec yards: typically 0.1 (1 point per 10 yards)
   - TDs: Pass TD = 4, Rush/Rec TD = 6
3. **Lineup Optimization**: Always fills required positions first, then FLEX with highest-scoring eligible player

### ESPN API Integration
- The `lib/espn-api/` subdirectory contains the ESPN Fantasy API client library
- Located at: `src/pigskin_mastermind/lib/espn-api/`
- This is a git submodule tracking the espn_api Python package
- For real ESPN integration, use `espn_api.football.League` from this library
- Requires private league access via SWID and ESPN_S2 cookies from browser

### Data Persistence
- **Current state**: TeamManager stores teams in memory only (lost on restart)
- For persistence, consider adding save/load methods using JSON files or database

### Testing Patterns
- Models use dataclasses, so test validation and methods
- Services should be tested with mock data (no real API calls)
- CLI tests would use Click's `CliRunner` for command testing

## Project Structure
```
src/pigskin_mastermind/
├── models/          # Data models (Player, Team)
├── services/        # Business logic (TeamManager, Importers, DecisionTools)
├── entertainment/   # Entertainment features
├── utils/           # Utility functions (currently minimal)
├── lib/espn-api/    # ESPN API client library (git submodule)
└── cli.py           # Click-based CLI interface

tests/               # Pytest test suite
docs/                # Documentation (TUTORIAL.md)
examples/            # Example usage (demo.py)
```

## Common Development Workflows

### Adding a New Player Stat
1. Stats are stored in `Player.stats` dict (flexible schema)
2. Update `Player.calculate_points()` to include new stat in scoring
3. Add to default `scoring_settings` dict if standard stat
4. Update tests to verify scoring calculation

### Adding a New Fantasy Service Importer
1. Create new class inheriting from `FantasyServiceImporter` in `services/importer.py`
2. Implement `authenticate()`, `import_team()`, and `get_player_data()` methods
3. Add to `ImporterFactory.create_importer()` switch statement
4. Add CLI command in `cli.py` under `import_cmd` group

### Extending Lineup Rules
1. Modify `LineupOptimizer.__init__()` default lineup_rules or pass custom rules
2. Update `optimize_lineup()` to handle new position types
3. For new FLEX-like positions, add to flex_positions list in optimization logic
