# Web UI Application Design
**Date:** 2026-02-15
**Status:** Approved
**Goal:** Build an interactive web application for managing and researching fantasy football teams

## Overview

Create a web-based user interface for Pigskin Mastermind that allows users to manage fantasy teams, import data from ESPN, optimize lineups, analyze trades, and predict matchups. The application will use a hybrid architecture with FastAPI serving both HTML templates (for HTMX interactions) and JSON API endpoints (for complex operations).

## Requirements

### Functional Requirements
- View and manage multiple fantasy football teams
- Import team data from ESPN Fantasy platform
- Optimize starting lineups based on projected points
- Analyze trades with recommendations
- Predict matchup outcomes
- Sync latest stats and rosters from ESPN

### Non-Functional Requirements
- Single-user application (no authentication required)
- Runs locally with minimal setup
- Responsive design (desktop and mobile)
- Fast interactions via HTMX (no page reloads)
- Persistent data storage with SQLite

## Architecture

### Tech Stack
- **Backend:** FastAPI + SQLAlchemy + Pydantic
- **Frontend:** HTMX + Tailwind CSS + Jinja2 templates
- **Database:** SQLite with SQLAlchemy ORM
- **ESPN API:** Existing `lib/espn-api` library

### Approach: Hybrid FastAPI Application

The application serves both HTML templates for HTMX and JSON endpoints for complex operations. This provides:
- Clean separation between UI and business logic
- Minimal refactoring of existing models/services
- Flexibility to add API consumers later (mobile app, CLI)
- HTMX gets HTML fragments for fast UI updates
- JSON endpoints for ESPN import and data-heavy operations

### Project Structure
```
pigskin_mastermind/
├── src/pigskin_mastermind/
│   ├── models/              # Pydantic + SQLAlchemy models
│   │   ├── player.py
│   │   ├── team.py
│   │   └── database.py      # NEW - SQLAlchemy models
│   ├── services/            # Business logic
│   │   ├── team_manager.py  # Add DB operations
│   │   ├── decision_tools.py
│   │   └── espn_sync.py     # NEW - ESPN API integration
│   ├── api/                 # NEW - FastAPI app
│   │   ├── main.py          # FastAPI app setup
│   │   ├── routes/          # Route handlers
│   │   │   ├── teams.py
│   │   │   ├── lineups.py
│   │   │   ├── trades.py
│   │   │   └── espn.py
│   │   └── dependencies.py  # DB session, service factories
│   ├── templates/           # NEW - Jinja2 templates
│   │   ├── base.html        # Base layout with sidebar
│   │   ├── dashboard.html
│   │   ├── teams/
│   │   ├── lineups/
│   │   └── trades/
│   └── static/              # NEW - CSS, JS, images
│       ├── css/
│       └── js/
```

## Database Schema

### Tables

**players**
- `id` (Integer, PK, autoincrement)
- `player_id` (String, unique) - ESPN ID or custom
- `name` (String)
- `position` (String) - QB, RB, WR, TE, K, DEF
- `nfl_team` (String) - Team abbreviation
- `projected_points` (Float)
- `actual_points` (Float)
- `stats` (JSON) - Flexible stats storage
- `team_id` (Integer, FK → teams.id, nullable)
- `created_at` (DateTime)
- `updated_at` (DateTime)

**teams**
- `id` (Integer, PK, autoincrement)
- `team_id` (String, unique) - ESPN ID or custom
- `name` (String)
- `owner` (String)
- `league_id` (String, nullable)
- `wins` (Integer, default=0)
- `losses` (Integer, default=0)
- `ties` (Integer, default=0)
- `total_points` (Float, default=0.0)
- `espn_team_id` (String, nullable) - For sync tracking
- `last_synced_at` (DateTime, nullable)
- `created_at` (DateTime)
- `updated_at` (DateTime)
- **Relationship:** One-to-Many with players

**leagues**
- `id` (Integer, PK)
- `league_id` (String, unique) - ESPN league ID
- `name` (String)
- `year` (Integer)
- `espn_s2` (String, nullable) - Encrypted
- `swid` (String, nullable) - Encrypted
- `last_synced_at` (DateTime, nullable)
- `created_at` (DateTime)

### Migration Strategy
- Use Alembic for database migrations
- Initial migration creates all tables
- Seed script can import from existing demo data

## API Structure

### Dashboard Routes (HTML)
- `GET /` → Dashboard template (overview, recent activity, upcoming matchups)

### Team Routes
- `GET /teams` → Teams list template
- `GET /teams/{id}` → Team detail template
- `GET /teams/{id}/roster` → Roster table fragment (HTMX)
- `POST /teams` → Create team, return team card fragment
- `PUT /teams/{id}` → Update team, return updated fragment
- `DELETE /teams/{id}` → Delete team, return empty fragment

### Lineup Routes
- `GET /lineups` → Lineup optimizer page template
- `POST /lineups/{team_id}/optimize` → Return optimized lineup fragment
- `GET /lineups/{team_id}/suggestions` → Return lineup change suggestions

### Trade Routes
- `GET /trades` → Trade analyzer page template
- `POST /trades/analyze` → Analyze trade, return analysis fragment
- `POST /trades/evaluate` → Quick evaluation, return recommendation badge

### ESPN Integration Routes (JSON API)
- `POST /api/espn/import` → Import team from ESPN (JSON response)
- `POST /api/espn/sync` → Sync latest data from ESPN
- `GET /api/espn/leagues` → List available leagues (for config)

### Player Routes
- `POST /teams/{team_id}/players` → Add player to roster
- `DELETE /teams/{team_id}/players/{player_id}` → Remove player
- `PUT /teams/{team_id}/players/{player_id}` → Update player stats

### Response Patterns
- HTML routes return full pages or HTMX fragments (based on `HX-Request` header)
- JSON routes prefixed with `/api/` for clarity
- Use FastAPI dependency injection for DB sessions and services
- Return 422 for validation errors, 404 for not found, 500 for server errors

## Frontend Structure

### Template Hierarchy
```
templates/
├── base.html              # Base layout: sidebar, header, HTMX scripts
├── dashboard.html         # Landing page with stats overview
├── teams/
│   ├── list.html         # Teams grid/list view
│   ├── detail.html       # Single team view with roster
│   ├── _team_card.html   # Reusable team card fragment
│   └── _roster_table.html # Roster table fragment
├── lineups/
│   ├── optimizer.html    # Lineup optimizer interface
│   └── _lineup_result.html # Optimized lineup fragment
├── trades/
│   ├── analyzer.html     # Trade analysis form
│   └── _trade_result.html # Analysis result fragment
└── components/
    ├── _sidebar.html     # Navigation sidebar
    └── _loading.html     # Loading indicator
```

### Sidebar Navigation
- Dashboard (home icon)
- Teams (list icon)
- Lineup Optimizer (strategy icon)
- Trade Analyzer (swap icon)
- Settings (gear icon - for ESPN credentials)

### HTMX Patterns
- **hx-get/post** on buttons/forms to request fragments
- **hx-target** to specify where response goes
- **hx-swap** to control how content updates (innerHTML, outerHTML, etc.)
- **hx-indicator** for loading states
- **hx-confirm** for destructive actions (delete team)

### Tailwind Setup
- Use CDN for initial development (can switch to build process later)
- Responsive design: mobile-first, sidebar collapses on small screens
- Color scheme: Professional sports theme (blue/green accents)
- Components: Cards for teams, tables for rosters, badges for positions

## Data Flow

### Key User Workflows

**ESPN Team Import:**
1. User enters ESPN credentials in settings
2. `POST /api/espn/import` with league_id, team_id, credentials
3. FastAPI route → `ESPNSyncService`
4. Call `lib/espn-api` to fetch team data
5. Create/update Team + Players in SQLite via SQLAlchemy
6. Return JSON with status and imported data
7. HTMX redirects to `/teams/{id}` (newly imported team)

**Lineup Optimization:**
1. User clicks "Optimize Lineup" button
2. `POST /lineups/{team_id}/optimize` (HTMX request)
3. FastAPI route → Load team from DB
4. Pass to `LineupOptimizer` service (existing logic)
5. Render `_lineup_result.html` template with optimized lineup
6. HTMX swaps content into target div

**Trade Analysis:**
1. User selects players to trade in form
2. `POST /trades/analyze` with team_id, gives[], receives[] (HTMX)
3. FastAPI route → Load players from DB
4. Pass to `TradeAnalyzer` service (existing logic)
5. Render `_trade_result.html` with analysis
6. HTMX displays result card with recommendation

### Service Layer Integration
- Existing services (LineupOptimizer, TradeAnalyzer, TeamManager) remain business logic
- New `ESPNSyncService` wraps ESPN API library
- FastAPI routes orchestrate: fetch from DB → call service → render response
- Services don't know about HTTP or templates (clean separation)

### State Management
- Database is source of truth
- No session state needed (single-user, no auth)
- HTMX handles UI state via DOM updates

## ESPN Integration

### ESPN Sync Service

**Key Methods:**

`import_team(league_id, team_id, espn_s2, swid)`
- One-time import of team from ESPN
- Initialize `espn_api.football.League`
- Fetch team roster and stats
- Create Team + Players in database
- Mark `team.espn_team_id` for future syncs
- Return imported team

`sync_team(team_id)`
- Update existing team from ESPN
- Load team from DB (must have espn_team_id)
- Fetch latest from ESPN API
- Update player stats (projected_points, actual_points)
- Update team record and total_points
- Set `team.last_synced_at` timestamp
- Return sync summary

`sync_league(league_id)`
- Sync all teams in a league
- Iterate teams, call sync_team for each
- Return summary of changes

### Credential Management
- Store ESPN credentials in `.env` file (gitignored)
- Encrypt `espn_s2` and `swid` in database using `cryptography` library
- Settings page allows updating credentials
- Credentials validated on first use, cached for session

### Sync Strategy
- **Manual sync:** User clicks "Sync from ESPN" button on team page
- **Selective sync:** Choose to sync only stats, only roster, or both
- **Conflict resolution:** Manual edits override ESPN data (track `manually_edited` flag)
- **Error handling:** Show clear messages if league is private, credentials expired, etc.

### Data Mapping
```
ESPN Player → DB Player:
  espn_id → player_id
  name → name
  position → position (mapped to standard positions)
  proTeam → nfl_team
  projected_points → projected_points
  stats → stats (JSON)
```

### Limitations
- ESPN API rate limits (throttle requests)
- Private leagues require valid cookies
- Projections may differ from ESPN display (different scoring settings)

## Error Handling

### API Error Responses

**Validation Errors (422):**
- Pydantic validation failures
- Return JSON with field-level errors for API routes
- For HTMX routes: render error message fragment in red alert box

**Not Found Errors (404):**
- Team/Player doesn't exist
- HTMX: Show "Team not found" message in target div
- JSON: Return `{error: "Team not found", code: "TEAM_NOT_FOUND"}`

**ESPN API Errors:**
- **Authentication failed:** Show modal with "Update ESPN credentials" link
- **Rate limit hit:** Display "ESPN API busy, try again in a moment" with retry button
- **League not found:** Clear error message with troubleshooting tips
- **Network timeout:** Show retry option, don't lose form data

**Database Errors:**
- Connection errors: Graceful degradation message
- Constraint violations: User-friendly message (e.g., "Team name already exists")
- Log detailed errors server-side for debugging

### User Feedback
- **Success:** Green toast notification (auto-dismiss)
- **Warnings:** Yellow banner (e.g., "Last ESPN sync was 3 days ago")
- **Errors:** Red alert box (dismissible)
- **Loading:** Spinner overlays during operations

### Logging
- FastAPI middleware logs all requests
- ESPN API calls logged with sanitized credentials
- Errors include stack traces (dev) or error IDs (prod)

## Testing Strategy

### Test Structure
```
tests/
├── unit/
│   ├── test_models.py           # Pydantic model validation
│   ├── test_services.py         # Business logic (existing tests)
│   └── test_espn_sync.py        # ESPN sync service
├── integration/
│   ├── test_api_teams.py        # Team API endpoints
│   ├── test_api_lineups.py      # Lineup endpoints
│   ├── test_api_trades.py       # Trade endpoints
│   └── test_espn_integration.py # ESPN API mocking
└── e2e/
    └── test_workflows.py        # Full user workflows (optional)
```

### Testing Approach

**Unit Tests:**
- Existing service tests continue working (LineupOptimizer, TradeAnalyzer)
- Add tests for new ESPNSyncService with mocked ESPN API
- Test Pydantic models for validation edge cases

**Integration Tests:**
- Use FastAPI `TestClient` for route testing
- SQLite in-memory database for test isolation
- Mock ESPN API library responses
- Test HTMX routes return proper HTML fragments
- Test JSON API routes return proper structure

**Key Test Cases:**
- Import team from ESPN → verify DB records created
- Optimize lineup with various roster compositions
- Trade analysis with edge cases (empty trades, invalid players)
- Error scenarios (missing team, invalid ESPN credentials)
- HTMX responses contain expected HTML elements

**Manual Testing Checklist:**
- Dashboard loads and displays stats
- Sidebar navigation works
- Team import from ESPN succeeds
- Lineup optimizer returns sensible results
- Trade analyzer provides recommendations
- Responsive design on mobile/tablet

**Coverage Goal:**
- 80%+ for services and API routes
- Focus on business logic over templates
- All ESPN sync paths covered with mocks

## Implementation Priorities

### Phase 1: Foundation (MVP)
1. Set up FastAPI app structure
2. Create SQLAlchemy models and Alembic migrations
3. Build base template with sidebar navigation
4. Implement Teams routes and templates
5. Basic HTMX interactions (view teams, add/remove players)

### Phase 2: Core Features
1. Lineup optimizer integration
2. Trade analyzer integration
3. Dashboard with overview stats
4. Responsive design and mobile support

### Phase 3: ESPN Integration
1. ESPN sync service implementation
2. Credential management and encryption
3. Import team workflow
4. Sync team workflow
5. Error handling for ESPN API issues

### Phase 4: Polish
1. Loading states and animations
2. Error handling and user feedback
3. Settings page
4. Manual testing and bug fixes
5. Documentation and deployment guide

## Success Criteria

- User can view and manage multiple teams
- User can import team from ESPN with valid credentials
- Lineup optimizer produces valid starting lineups
- Trade analyzer provides clear recommendations
- Application is responsive on desktop and mobile
- No data loss between sessions (persistent SQLite)
- Clear error messages guide user to resolution
- All core features tested with 80%+ coverage
