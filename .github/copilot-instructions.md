# Copilot Instructions

## Build, Test, and Lint

```bash
# Install in development mode
pip install -e .

# Run all tests
pytest

# Run a single test file
pytest tests/test_player.py

# Run a specific test
pytest tests/test_player.py::test_player_creation -v

# Run tests with coverage
pytest --cov=pigskin_mastermind --cov-report=html

# Format code
black src/ tests/

# Lint code
flake8 src/ tests/

# Apply database migrations
alembic upgrade head

# Start the web server
uvicorn pigskin_mastermind.api.main:app --reload
```

## Architecture

This is a fantasy football management app with three interfaces: a FastAPI web UI, a REST API, and a Click CLI (`pigskin` command).

### Dual model system

There are two parallel model layers:
- **Dataclass models** (`models/player.py`, `models/team.py`): Used by the CLI, services, and in-memory operations. `Player` validates position against `['QB', 'RB', 'WR', 'TE', 'K', 'DEF']`.
- **SQLAlchemy ORM models** (`models/database.py`): `DBPlayer`, `DBTeam`, `DBLeague`, etc. Used by API routes and database persistence. Migrations managed by Alembic.

These are not automatically synchronized — API routes work with DB models and convert as needed.

### Service layer

Services in `services/` contain all business logic:
- `team_manager.py` — Team CRUD (in-memory dict, used by CLI)
- `decision_tools.py` — `LineupOptimizer` and `TradeAnalyzer`
- `projection_service.py` — Abstract base with `YearlyProjectionService` and `WeeklyProjectionService` implementations
- `espn_sync.py` — ESPN Fantasy API integration (uses `lib/espn-api/` git submodule, added to `sys.path` at runtime)
- `mock_draft.py` — Draft simulator using ESPN ADP

### Web layer

- FastAPI app entry point: `api/main.py`
- Routes: `api/routes/` (teams, players, leagues, lineups, trades, draft, stats, visualizations, odds, projections, etc.)
- Templates: Jinja2 in `templates/`
- Database: SQLite via SQLAlchemy, configured through `DATABASE_URL` env var (default: `sqlite:///./pigskin_mastermind.db`)
- `get_db()` dependency in `api/database.py` provides session management

### ESPN API integration

The `lib/espn-api/` directory is a git submodule containing the `espn_api` package. It's added to `sys.path` dynamically in `espn_sync.py`. ESPN integration requires SWID and ESPN_S2 browser cookies for private league access.

## Key Conventions

### Fantasy football domain rules

- **FLEX eligibility**: Only RB, WR, TE can fill FLEX slots (never QB, K, or DEF)
- **Default scoring**: PPR (Point Per Reception). Pass yards = 0.04 pts/yd, rush/rec yards = 0.1 pts/yd, pass TD = 4, rush/rec TD = 6
- **Default lineup**: `{'QB': 1, 'RB': 2, 'WR': 2, 'TE': 1, 'FLEX': 1, 'K': 1, 'DEF': 1}`
- **Lineup optimization**: Fill required positions first, then FLEX with highest-projected eligible player

### Player stats

Stats are stored as a flexible `Dict[str, Any]` (not a fixed schema). When adding new stats, update `Player.calculate_points()` and the default `scoring_settings` dict.

### Projection system

Projections use a criteria-based approach with dataclass hierarchies:
- `PlayerProjectionCriteria` (base) → `YearlyProjectionCriteria` / `WeeklyProjectionCriteria`
- `AlgorithmCoefficients` and `PositionCoefficients` allow tuning projection weights
- Services accept optional coefficients at construction for customization

### Adding new importers

New fantasy platform importers inherit from `FantasyServiceImporter` (abstract base class in `services/importer.py`), implement `authenticate()`, `import_team()`, and `get_player_data()`, and register in `ImporterFactory.create_importer()`.
