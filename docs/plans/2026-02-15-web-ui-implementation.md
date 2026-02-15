# Web UI Application Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a FastAPI + HTMX web application for managing fantasy football teams with ESPN integration.

**Architecture:** Hybrid FastAPI app serving both HTML templates (HTMX fragments) and JSON API endpoints. SQLAlchemy for persistence, existing services for business logic, Tailwind CSS for styling.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, HTMX, Tailwind CSS, Jinja2, pytest

---

## Phase 1: Foundation (MVP)

### Task 1: Project Dependencies

**Files:**
- Modify: `requirements.txt`
- Modify: `requirements-dev.txt`

**Step 1: Add production dependencies**

Add to `requirements.txt`:
```
fastapi>=0.109.0
uvicorn[standard]>=0.27.0
sqlalchemy>=2.0.25
alembic>=1.13.1
jinja2>=3.1.3
python-multipart>=0.0.6
python-dotenv>=1.0.0
cryptography>=42.0.0
```

**Step 2: Add development dependencies**

Add to `requirements-dev.txt`:
```
httpx>=0.26.0
```

**Step 3: Install dependencies**

Run: `pip install -r requirements.txt -r requirements-dev.txt`

**Step 4: Commit**

```bash
git add requirements.txt requirements-dev.txt
git commit -m "feat: add FastAPI and web dependencies"
```

---

### Task 2: Database Models - Base Setup

**Files:**
- Create: `src/pigskin_mastermind/models/database.py`
- Test: `tests/unit/test_database_models.py`

**Step 1: Write test for Player model**

Create `tests/unit/test_database_models.py`:
```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from pigskin_mastermind.models.database import Base, DBPlayer, DBTeam


def test_player_model_creation():
    """Test creating a player in the database"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        player = DBPlayer(
            player_id="p1",
            name="Patrick Mahomes",
            position="QB",
            nfl_team="KC",
            projected_points=25.5
        )
        session.add(player)
        session.commit()

        result = session.query(DBPlayer).filter_by(player_id="p1").first()
        assert result is not None
        assert result.name == "Patrick Mahomes"
        assert result.position == "QB"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_database_models.py::test_player_model_creation -v`
Expected: FAIL with "No module named 'pigskin_mastermind.models.database'"

**Step 3: Create database models**

Create `src/pigskin_mastermind/models/database.py`:
```python
from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

Base = declarative_base()


class DBPlayer(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    position = Column(String, nullable=False)
    nfl_team = Column(String, nullable=False)
    projected_points = Column(Float, default=0.0)
    actual_points = Column(Float, default=0.0)
    stats = Column(JSON, default=dict)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = relationship("DBTeam", back_populates="players")


class DBTeam(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    owner = Column(String, nullable=False)
    league_id = Column(String, nullable=True)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    ties = Column(Integer, default=0)
    total_points = Column(Float, default=0.0)
    espn_team_id = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    players = relationship("DBPlayer", back_populates="team")


class DBLeague(Base):
    __tablename__ = "leagues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    league_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    espn_s2 = Column(String, nullable=True)  # Encrypted
    swid = Column(String, nullable=True)  # Encrypted
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_database_models.py::test_player_model_creation -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pigskin_mastermind/models/database.py tests/unit/test_database_models.py
git commit -m "feat: add SQLAlchemy database models"
```

---

### Task 3: Database Connection and Session

**Files:**
- Create: `src/pigskin_mastermind/api/database.py`
- Test: `tests/unit/test_api_database.py`

**Step 1: Write test for database session**

Create `tests/unit/test_api_database.py`:
```python
from pigskin_mastermind.api.database import get_db, engine
from pigskin_mastermind.models.database import Base


def test_database_session():
    """Test database session creation"""
    Base.metadata.create_all(bind=engine)

    db = next(get_db())
    assert db is not None
    db.close()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_api_database.py::test_database_session -v`
Expected: FAIL with "No module named 'pigskin_mastermind.api.database'"

**Step 3: Create database connection module**

Create `src/pigskin_mastermind/api/database.py`:
```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./pigskin_mastermind.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """Dependency for FastAPI routes to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_api_database.py::test_database_session -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pigskin_mastermind/api/database.py tests/unit/test_api_database.py
git commit -m "feat: add database connection and session management"
```

---

### Task 4: Alembic Setup for Migrations

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/001_initial_schema.py`

**Step 1: Initialize Alembic**

Run: `alembic init alembic`

**Step 2: Configure Alembic**

Modify `alembic/env.py` to import Base and set target_metadata:
```python
from pigskin_mastermind.models.database import Base
target_metadata = Base.metadata
```

Modify `alembic.ini` sqlalchemy.url:
```ini
sqlalchemy.url = sqlite:///./pigskin_mastermind.db
```

**Step 3: Create initial migration**

Run: `alembic revision --autogenerate -m "Initial schema"`

**Step 4: Run migration**

Run: `alembic upgrade head`
Expected: Tables created in database

**Step 5: Commit**

```bash
git add alembic.ini alembic/
git commit -m "feat: add Alembic migrations with initial schema"
```

---

### Task 5: FastAPI App Setup

**Files:**
- Create: `src/pigskin_mastermind/api/main.py`
- Create: `src/pigskin_mastermind/api/__init__.py`
- Test: `tests/integration/test_api_main.py`

**Step 1: Write test for FastAPI app creation**

Create `tests/integration/test_api_main.py`:
```python
from fastapi.testclient import TestClient
from pigskin_mastermind.api.main import app


def test_app_creation():
    """Test FastAPI app is created"""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code in [200, 404]  # Either works or route not found yet
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_api_main.py::test_app_creation -v`
Expected: FAIL with "No module named 'pigskin_mastermind.api.main'"

**Step 3: Create FastAPI app**

Create `src/pigskin_mastermind/api/__init__.py` (empty file)

Create `src/pigskin_mastermind/api/main.py`:
```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import os

app = FastAPI(
    title="Pigskin Mastermind",
    description="Fantasy Football Management Application",
    version="0.1.0"
)

# Get template and static directories
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Create directories if they don't exist
os.makedirs(TEMPLATE_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Setup Jinja2 templates
templates = Jinja2Templates(directory=TEMPLATE_DIR)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/integration/test_api_main.py::test_app_creation -v`
Expected: PASS

**Step 5: Test the app manually**

Run: `uvicorn pigskin_mastermind.api.main:app --reload`
Open browser to `http://localhost:8000/health`
Expected: `{"status": "healthy"}`

**Step 6: Commit**

```bash
git add src/pigskin_mastermind/api/ tests/integration/test_api_main.py
git commit -m "feat: create FastAPI application with health check"
```

---

### Task 6: Base Template with Sidebar

**Files:**
- Create: `src/pigskin_mastermind/templates/base.html`
- Create: `src/pigskin_mastermind/templates/components/_sidebar.html`
- Test: Manual browser test

**Step 1: Create base template**

Create `src/pigskin_mastermind/templates/base.html`:
```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Pigskin Mastermind{% endblock %}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://unpkg.com/htmx.org@1.9.10"></script>
    <style>
        /* Custom styles for mobile sidebar */
        #sidebar {
            transform: translateX(0);
            transition: transform 0.3s ease-in-out;
        }
        #sidebar.hidden-mobile {
            transform: translateX(-100%);
        }
        @media (min-width: 768px) {
            #sidebar.hidden-mobile {
                transform: translateX(0);
            }
        }
    </style>
</head>
<body class="bg-gray-50">
    <div class="flex h-screen">
        <!-- Sidebar -->
        {% include "components/_sidebar.html" %}

        <!-- Main Content -->
        <div class="flex-1 flex flex-col overflow-hidden">
            <!-- Header -->
            <header class="bg-white shadow-sm">
                <div class="flex items-center justify-between px-6 py-4">
                    <button
                        onclick="document.getElementById('sidebar').classList.toggle('hidden-mobile')"
                        class="md:hidden text-gray-600 hover:text-gray-900"
                    >
                        <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"></path>
                        </svg>
                    </button>
                    <h1 class="text-2xl font-bold text-gray-900">
                        {% block page_title %}Pigskin Mastermind{% endblock %}
                    </h1>
                    <div></div>
                </div>
            </header>

            <!-- Page Content -->
            <main class="flex-1 overflow-y-auto p-6">
                {% block content %}{% endblock %}
            </main>
        </div>
    </div>

    <!-- HTMX Loading Indicator -->
    <div id="htmx-loading" class="htmx-indicator fixed top-4 right-4 bg-blue-500 text-white px-4 py-2 rounded shadow-lg">
        <svg class="animate-spin h-5 w-5 inline mr-2" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
        </svg>
        Loading...
    </div>

    {% block extra_js %}{% endblock %}
</body>
</html>
```

**Step 2: Create sidebar component**

Create `src/pigskin_mastermind/templates/components/_sidebar.html`:
```html
<aside id="sidebar" class="w-64 bg-gradient-to-b from-blue-600 to-blue-800 text-white">
    <div class="p-6">
        <h2 class="text-2xl font-bold">🏈 Pigskin</h2>
        <p class="text-blue-200 text-sm">Mastermind</p>
    </div>

    <nav class="mt-6">
        <a href="/" class="flex items-center px-6 py-3 hover:bg-blue-700 transition-colors">
            <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6"></path>
            </svg>
            Dashboard
        </a>

        <a href="/teams" class="flex items-center px-6 py-3 hover:bg-blue-700 transition-colors">
            <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0zm6 3a2 2 0 11-4 0 2 2 0 014 0zM7 10a2 2 0 11-4 0 2 2 0 014 0z"></path>
            </svg>
            Teams
        </a>

        <a href="/lineups" class="flex items-center px-6 py-3 hover:bg-blue-700 transition-colors">
            <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01"></path>
            </svg>
            Lineup Optimizer
        </a>

        <a href="/trades" class="flex items-center px-6 py-3 hover:bg-blue-700 transition-colors">
            <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 7h12m0 0l-4-4m4 4l-4 4m0 6H4m0 0l4 4m-4-4l4-4"></path>
            </svg>
            Trade Analyzer
        </a>

        <div class="border-t border-blue-500 mt-6 pt-6">
            <a href="/settings" class="flex items-center px-6 py-3 hover:bg-blue-700 transition-colors">
                <svg class="w-5 h-5 mr-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path>
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path>
                </svg>
                Settings
            </a>
        </div>
    </nav>
</aside>
```

**Step 3: Create dashboard route to test template**

Add to `src/pigskin_mastermind/api/main.py`:
```python
from fastapi import Request

@app.get("/")
async def dashboard(request: Request):
    """Dashboard page"""
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request}
    )
```

**Step 4: Create dashboard template**

Create `src/pigskin_mastermind/templates/dashboard.html`:
```html
{% extends "base.html" %}

{% block page_title %}Dashboard{% endblock %}

{% block content %}
<div class="grid grid-cols-1 md:grid-cols-3 gap-6">
    <div class="bg-white rounded-lg shadow p-6">
        <h3 class="text-gray-500 text-sm font-medium">Total Teams</h3>
        <p class="text-3xl font-bold text-gray-900 mt-2">0</p>
    </div>

    <div class="bg-white rounded-lg shadow p-6">
        <h3 class="text-gray-500 text-sm font-medium">Total Players</h3>
        <p class="text-3xl font-bold text-gray-900 mt-2">0</p>
    </div>

    <div class="bg-white rounded-lg shadow p-6">
        <h3 class="text-gray-500 text-sm font-medium">Total Points</h3>
        <p class="text-3xl font-bold text-gray-900 mt-2">0.0</p>
    </div>
</div>

<div class="mt-6 bg-white rounded-lg shadow p-6">
    <h2 class="text-xl font-bold text-gray-900 mb-4">Welcome to Pigskin Mastermind</h2>
    <p class="text-gray-600">Get started by creating your first team or importing from ESPN.</p>
</div>
{% endblock %}
```

**Step 5: Test manually**

Run: `uvicorn pigskin_mastermind.api.main:app --reload`
Open browser to `http://localhost:8000/`
Expected: Dashboard with sidebar and stats cards

**Step 6: Commit**

```bash
git add src/pigskin_mastermind/templates/ src/pigskin_mastermind/api/main.py
git commit -m "feat: add base template with sidebar navigation and dashboard"
```

---

### Task 7: Team Routes - List Teams

**Files:**
- Create: `src/pigskin_mastermind/api/routes/teams.py`
- Create: `src/pigskin_mastermind/templates/teams/list.html`
- Test: `tests/integration/test_api_teams.py`

**Step 1: Write test for teams list route**

Create `tests/integration/test_api_teams.py`:
```python
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pigskin_mastermind.api.main import app
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import Base, DBTeam

# Test database setup
TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db


def test_get_teams_empty():
    """Test getting teams when none exist"""
    Base.metadata.create_all(bind=test_engine)
    client = TestClient(app)

    response = client.get("/teams")
    assert response.status_code == 200
    assert b"Teams" in response.content


def test_get_teams_with_data():
    """Test getting teams with existing data"""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Add test team
    db = TestSessionLocal()
    team = DBTeam(
        team_id="t1",
        name="Test Team",
        owner="Test Owner",
        wins=5,
        losses=3
    )
    db.add(team)
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get("/teams")
    assert response.status_code == 200
    assert b"Test Team" in response.content
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_api_teams.py::test_get_teams_empty -v`
Expected: FAIL with 404 or route not found

**Step 3: Create teams router**

Create `src/pigskin_mastermind/api/routes/teams.py`:
```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam
from pigskin_mastermind.api.main import templates

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("")
async def list_teams(request: Request, db: Session = Depends(get_db)):
    """List all teams"""
    teams = db.query(DBTeam).all()
    return templates.TemplateResponse(
        "teams/list.html",
        {"request": request, "teams": teams}
    )
```

**Step 4: Register router in main app**

Add to `src/pigskin_mastermind/api/main.py`:
```python
from pigskin_mastermind.api.routes import teams

app.include_router(teams.router)
```

**Step 5: Create teams list template**

Create `src/pigskin_mastermind/templates/teams/list.html`:
```html
{% extends "base.html" %}

{% block page_title %}Teams{% endblock %}

{% block content %}
<div class="flex justify-between items-center mb-6">
    <h2 class="text-2xl font-bold text-gray-900">My Teams</h2>
    <button
        hx-get="/teams/new"
        hx-target="#team-form-modal"
        class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg"
    >
        + New Team
    </button>
</div>

<div id="team-form-modal"></div>

<div id="teams-grid" class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
    {% if teams %}
        {% for team in teams %}
        <div class="bg-white rounded-lg shadow hover:shadow-lg transition-shadow p-6">
            <h3 class="text-xl font-bold text-gray-900">{{ team.name }}</h3>
            <p class="text-gray-600 text-sm mt-1">Owner: {{ team.owner }}</p>
            <div class="mt-4 flex justify-between items-center">
                <div class="text-sm text-gray-600">
                    <span class="font-medium">{{ team.wins }}-{{ team.losses }}-{{ team.ties }}</span>
                </div>
                <div class="text-sm text-gray-600">
                    <span class="font-medium">{{ "%.1f"|format(team.total_points) }}</span> pts
                </div>
            </div>
            <div class="mt-4 flex gap-2">
                <a href="/teams/{{ team.id }}" class="flex-1 text-center bg-blue-50 hover:bg-blue-100 text-blue-700 px-3 py-2 rounded text-sm">
                    View
                </a>
                <button
                    hx-delete="/teams/{{ team.id }}"
                    hx-target="closest div"
                    hx-swap="outerHTML"
                    hx-confirm="Delete {{ team.name }}?"
                    class="bg-red-50 hover:bg-red-100 text-red-700 px-3 py-2 rounded text-sm"
                >
                    Delete
                </button>
            </div>
        </div>
        {% endfor %}
    {% else %}
        <div class="col-span-3 text-center py-12">
            <p class="text-gray-500">No teams yet. Create your first team to get started!</p>
        </div>
    {% endif %}
</div>
{% endblock %}
```

**Step 6: Run tests to verify they pass**

Run: `pytest tests/integration/test_api_teams.py -v`
Expected: PASS for both tests

**Step 7: Commit**

```bash
git add src/pigskin_mastermind/api/routes/ src/pigskin_mastermind/templates/teams/ tests/integration/test_api_teams.py src/pigskin_mastermind/api/main.py
git commit -m "feat: add teams list route and template"
```

---

### Task 8: Team Routes - Create Team

**Files:**
- Modify: `src/pigskin_mastermind/api/routes/teams.py`
- Create: `src/pigskin_mastermind/templates/teams/_team_form.html`
- Modify: `tests/integration/test_api_teams.py`

**Step 1: Write test for create team**

Add to `tests/integration/test_api_teams.py`:
```python
def test_create_team():
    """Test creating a new team"""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    client = TestClient(app)
    response = client.post(
        "/teams",
        data={
            "name": "New Team",
            "owner": "John Doe",
            "league_id": "league1"
        },
        follow_redirects=False
    )
    assert response.status_code in [200, 201, 303]

    # Verify team was created
    db = TestSessionLocal()
    team = db.query(DBTeam).filter_by(name="New Team").first()
    assert team is not None
    assert team.owner == "John Doe"
    db.close()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_api_teams.py::test_create_team -v`
Expected: FAIL with 405 Method Not Allowed

**Step 3: Add create team route**

Add to `src/pigskin_mastermind/api/routes/teams.py`:
```python
from fastapi import Form
from fastapi.responses import RedirectResponse
import uuid


@router.get("/new")
async def new_team_form(request: Request):
    """Show new team form"""
    return templates.TemplateResponse(
        "teams/_team_form.html",
        {"request": request}
    )


@router.post("")
async def create_team(
    name: str = Form(...),
    owner: str = Form(...),
    league_id: str = Form(None),
    db: Session = Depends(get_db)
):
    """Create a new team"""
    team = DBTeam(
        team_id=str(uuid.uuid4()),
        name=name,
        owner=owner,
        league_id=league_id
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    return RedirectResponse(url="/teams", status_code=303)
```

**Step 4: Create team form template**

Create `src/pigskin_mastermind/templates/teams/_team_form.html`:
```html
<div class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
    <div class="bg-white rounded-lg shadow-xl p-6 w-full max-w-md">
        <h3 class="text-xl font-bold text-gray-900 mb-4">Create New Team</h3>

        <form hx-post="/teams" hx-target="#teams-grid" hx-swap="beforeend">
            <div class="space-y-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        Team Name *
                    </label>
                    <input
                        type="text"
                        name="name"
                        required
                        class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        placeholder="Enter team name"
                    >
                </div>

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        Owner Name *
                    </label>
                    <input
                        type="text"
                        name="owner"
                        required
                        class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        placeholder="Enter owner name"
                    >
                </div>

                <div>
                    <label class="block text-sm font-medium text-gray-700 mb-1">
                        League ID (optional)
                    </label>
                    <input
                        type="text"
                        name="league_id"
                        class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
                        placeholder="Enter league ID"
                    >
                </div>
            </div>

            <div class="mt-6 flex gap-3">
                <button
                    type="submit"
                    class="flex-1 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md"
                >
                    Create Team
                </button>
                <button
                    type="button"
                    onclick="this.closest('.fixed').remove()"
                    class="flex-1 bg-gray-200 hover:bg-gray-300 text-gray-800 px-4 py-2 rounded-md"
                >
                    Cancel
                </button>
            </div>
        </form>
    </div>
</div>
```

**Step 5: Run test to verify it passes**

Run: `pytest tests/integration/test_api_teams.py::test_create_team -v`
Expected: PASS

**Step 6: Commit**

```bash
git add src/pigskin_mastermind/api/routes/teams.py src/pigskin_mastermind/templates/teams/_team_form.html tests/integration/test_api_teams.py
git commit -m "feat: add create team functionality with modal form"
```

---

## Phase 2: Core Features

### Task 9: Lineup Optimizer Integration

**Files:**
- Create: `src/pigskin_mastermind/api/routes/lineups.py`
- Create: `src/pigskin_mastermind/templates/lineups/optimizer.html`
- Create: `src/pigskin_mastermind/templates/lineups/_lineup_result.html`
- Test: `tests/integration/test_api_lineups.py`

**Step 1: Write test for lineup optimization**

Create `tests/integration/test_api_lineups.py`:
```python
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pigskin_mastermind.api.main import app
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import Base, DBTeam, DBPlayer

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db


def test_optimize_lineup():
    """Test lineup optimization"""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Create team with players
    db = TestSessionLocal()
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.commit()

    players = [
        DBPlayer(player_id="p1", name="QB1", position="QB", nfl_team="KC", projected_points=25.0, team_id=team.id),
        DBPlayer(player_id="p2", name="RB1", position="RB", nfl_team="SF", projected_points=20.0, team_id=team.id),
        DBPlayer(player_id="p3", name="RB2", position="RB", nfl_team="DAL", projected_points=18.0, team_id=team.id),
        DBPlayer(player_id="p4", name="WR1", position="WR", nfl_team="MIA", projected_points=22.0, team_id=team.id),
        DBPlayer(player_id="p5", name="WR2", position="WR", nfl_team="BUF", projected_points=19.0, team_id=team.id),
        DBPlayer(player_id="p6", name="TE1", position="TE", nfl_team="KC", projected_points=15.0, team_id=team.id),
        DBPlayer(player_id="p7", name="K1", position="K", nfl_team="BAL", projected_points=10.0, team_id=team.id),
        DBPlayer(player_id="p8", name="DEF1", position="DEF", nfl_team="SF", projected_points=12.0, team_id=team.id),
    ]
    for player in players:
        db.add(player)
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.post(f"/lineups/{team.id}/optimize")
    assert response.status_code == 200
    assert b"Optimized Lineup" in response.content or b"QB" in response.content
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_api_lineups.py::test_optimize_lineup -v`
Expected: FAIL with 404

**Step 3: Create lineup router**

Create `src/pigskin_mastermind/api/routes/lineups.py`:
```python
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam, DBPlayer
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.models.team import Team
from pigskin_mastermind.services.decision_tools import LineupOptimizer
from pigskin_mastermind.api.main import templates

router = APIRouter(prefix="/lineups", tags=["lineups"])


@router.get("")
async def lineup_page(request: Request, db: Session = Depends(get_db)):
    """Lineup optimizer page"""
    teams = db.query(DBTeam).all()
    return templates.TemplateResponse(
        "lineups/optimizer.html",
        {"request": request, "teams": teams}
    )


@router.post("/{team_id}/optimize")
async def optimize_lineup(
    request: Request,
    team_id: int,
    db: Session = Depends(get_db)
):
    """Optimize lineup for a team"""
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    db_players = db.query(DBPlayer).filter(DBPlayer.team_id == team_id).all()

    # Convert DB models to domain models
    players = [
        Player(
            player_id=p.player_id,
            name=p.name,
            position=p.position,
            team=p.nfl_team,
            projected_points=p.projected_points,
            actual_points=p.actual_points,
            stats=p.stats or {}
        )
        for p in db_players
    ]

    team = Team(
        team_id=db_team.team_id,
        name=db_team.name,
        owner=db_team.owner,
        players=players,
        record={"wins": db_team.wins, "losses": db_team.losses, "ties": db_team.ties},
        total_points=db_team.total_points,
        league_id=db_team.league_id
    )

    # Optimize lineup
    optimizer = LineupOptimizer()
    result = optimizer.optimize_lineup(team)

    return templates.TemplateResponse(
        "lineups/_lineup_result.html",
        {"request": request, "result": result, "team": team}
    )
```

**Step 4: Register router**

Add to `src/pigskin_mastermind/api/main.py`:
```python
from pigskin_mastermind.api.routes import teams, lineups

app.include_router(lineups.router)
```

**Step 5: Create optimizer template**

Create `src/pigskin_mastermind/templates/lineups/optimizer.html`:
```html
{% extends "base.html" %}

{% block page_title %}Lineup Optimizer{% endblock %}

{% block content %}
<div class="max-w-4xl mx-auto">
    <h2 class="text-2xl font-bold text-gray-900 mb-6">Lineup Optimizer</h2>

    <div class="bg-white rounded-lg shadow p-6 mb-6">
        <label class="block text-sm font-medium text-gray-700 mb-2">
            Select Team
        </label>
        <select
            id="team-select"
            class="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-blue-500 focus:border-blue-500"
            onchange="if(this.value) { htmx.ajax('POST', '/lineups/' + this.value + '/optimize', {target: '#lineup-result', swap: 'innerHTML'}) }"
        >
            <option value="">Choose a team...</option>
            {% for team in teams %}
            <option value="{{ team.id }}">{{ team.name }} ({{ team.owner }})</option>
            {% endfor %}
        </select>
    </div>

    <div id="lineup-result" class="mt-6">
        <div class="text-center text-gray-500 py-12">
            Select a team to optimize their lineup
        </div>
    </div>
</div>
{% endblock %}
```

**Step 6: Create lineup result template**

Create `src/pigskin_mastermind/templates/lineups/_lineup_result.html`:
```html
<div class="bg-white rounded-lg shadow p-6">
    <div class="flex justify-between items-center mb-6">
        <h3 class="text-xl font-bold text-gray-900">Optimized Lineup - {{ team.name }}</h3>
        <div class="text-lg font-semibold text-blue-600">
            {{ "%.2f"|format(result.total_projected_points) }} projected points
        </div>
    </div>

    <div class="space-y-4">
        {% for position, players in result.lineup.items() %}
        <div>
            <h4 class="text-sm font-semibold text-gray-700 mb-2">{{ position }}</h4>
            <div class="space-y-2">
                {% for player in players %}
                <div class="flex items-center justify-between p-3 bg-gray-50 rounded">
                    <div>
                        <span class="font-medium text-gray-900">{{ player.name }}</span>
                        <span class="text-sm text-gray-600 ml-2">({{ player.team }})</span>
                    </div>
                    <span class="text-sm font-semibold text-blue-600">
                        {{ "%.1f"|format(player.projected_points) }} pts
                    </span>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endfor %}
    </div>

    {% if result.bench %}
    <div class="mt-6 pt-6 border-t">
        <h4 class="text-sm font-semibold text-gray-700 mb-2">Bench</h4>
        <div class="space-y-2">
            {% for player in result.bench %}
            <div class="flex items-center justify-between p-2 text-sm">
                <div>
                    <span class="text-gray-700">{{ player.name }}</span>
                    <span class="text-gray-500 ml-2">({{ player.position }}, {{ player.team }})</span>
                </div>
                <span class="text-gray-600">{{ "%.1f"|format(player.projected_points) }} pts</span>
            </div>
            {% endfor %}
        </div>
    </div>
    {% endif %}
</div>
```

**Step 7: Run test to verify it passes**

Run: `pytest tests/integration/test_api_lineups.py::test_optimize_lineup -v`
Expected: PASS

**Step 8: Commit**

```bash
git add src/pigskin_mastermind/api/routes/lineups.py src/pigskin_mastermind/templates/lineups/ tests/integration/test_api_lineups.py src/pigskin_mastermind/api/main.py
git commit -m "feat: add lineup optimizer with HTMX integration"
```

---

### Task 10: Trade Analyzer Integration

**Files:**
- Create: `src/pigskin_mastermind/api/routes/trades.py`
- Create: `src/pigskin_mastermind/templates/trades/analyzer.html`
- Create: `src/pigskin_mastermind/templates/trades/_trade_result.html`
- Test: `tests/integration/test_api_trades.py`

**Step 1: Write test for trade analysis**

Create `tests/integration/test_api_trades.py`:
```python
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pigskin_mastermind.api.main import app
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import Base, DBTeam, DBPlayer

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

def override_get_db():
    db = TestSessionLocal()
    try:
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db


def test_analyze_trade():
    """Test trade analysis"""
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    # Create team with players
    db = TestSessionLocal()
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.commit()

    p1 = DBPlayer(player_id="p1", name="Player1", position="RB", nfl_team="KC", projected_points=20.0, team_id=team.id)
    p2 = DBPlayer(player_id="p2", name="Player2", position="RB", nfl_team="SF", projected_points=15.0, team_id=team.id)
    db.add(p1)
    db.add(p2)
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.post(
        "/trades/analyze",
        json={
            "team_id": team.id,
            "gives": [p1.id],
            "receives": [p2.id]
        }
    )
    assert response.status_code == 200
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_api_trades.py::test_analyze_trade -v`
Expected: FAIL with 404

**Step 3: Create trades router**

Create `src/pigskin_mastermind/api/routes/trades.py`:
```python
from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List
from pigskin_mastermind.api.database import get_db
from pigskin_mastermind.models.database import DBTeam, DBPlayer
from pigskin_mastermind.models.player import Player
from pigskin_mastermind.services.decision_tools import TradeAnalyzer
from pigskin_mastermind.api.main import templates

router = APIRouter(prefix="/trades", tags=["trades"])


class TradeRequest(BaseModel):
    team_id: int
    gives: List[int]  # Player IDs
    receives: List[int]  # Player IDs


@router.get("")
async def trade_page(request: Request, db: Session = Depends(get_db)):
    """Trade analyzer page"""
    teams = db.query(DBTeam).all()
    return templates.TemplateResponse(
        "trades/analyzer.html",
        {"request": request, "teams": teams}
    )


@router.post("/analyze")
async def analyze_trade(
    request: Request,
    trade: TradeRequest,
    db: Session = Depends(get_db)
):
    """Analyze a trade"""
    db_team = db.query(DBTeam).filter(DBTeam.id == trade.team_id).first()
    if not db_team:
        raise HTTPException(status_code=404, detail="Team not found")

    gives_players = db.query(DBPlayer).filter(DBPlayer.id.in_(trade.gives)).all()
    receives_players = db.query(DBPlayer).filter(DBPlayer.id.in_(trade.receives)).all()

    # Convert to domain models
    gives = [
        Player(
            player_id=p.player_id,
            name=p.name,
            position=p.position,
            team=p.nfl_team,
            projected_points=p.projected_points
        )
        for p in gives_players
    ]

    receives = [
        Player(
            player_id=p.player_id,
            name=p.name,
            position=p.position,
            team=p.nfl_team,
            projected_points=p.projected_points
        )
        for p in receives_players
    ]

    from pigskin_mastermind.models.team import Team
    team = Team(
        team_id=db_team.team_id,
        name=db_team.name,
        owner=db_team.owner
    )

    # Analyze trade
    analyzer = TradeAnalyzer()
    result = analyzer.evaluate_trade_for_team(team, gives, receives)

    return templates.TemplateResponse(
        "trades/_trade_result.html",
        {"request": request, "result": result}
    )
```

**Step 4: Register router**

Add to `src/pigskin_mastermind/api/main.py`:
```python
from pigskin_mastermind.api.routes import teams, lineups, trades

app.include_router(trades.router)
```

**Step 5: Create analyzer template**

Create `src/pigskin_mastermind/templates/trades/analyzer.html`:
```html
{% extends "base.html" %}

{% block page_title %}Trade Analyzer{% endblock %}

{% block content %}
<div class="max-w-4xl mx-auto">
    <h2 class="text-2xl font-bold text-gray-900 mb-6">Trade Analyzer</h2>

    <div class="bg-white rounded-lg shadow p-6">
        <p class="text-gray-600 mb-4">
            Select players you would give away and players you would receive to analyze the trade.
        </p>
        <p class="text-sm text-gray-500">
            Note: This is a simplified interface. Full trade analysis with player selection coming soon.
        </p>
    </div>

    <div id="trade-result" class="mt-6"></div>
</div>
{% endblock %}
```

**Step 6: Create trade result template**

Create `src/pigskin_mastermind/templates/trades/_trade_result.html`:
```html
<div class="bg-white rounded-lg shadow p-6">
    <h3 class="text-xl font-bold text-gray-900 mb-4">Trade Analysis</h3>

    <div class="grid grid-cols-2 gap-6 mb-6">
        <div>
            <h4 class="text-sm font-semibold text-gray-700 mb-2">You Give</h4>
            <ul class="space-y-2">
                {% for player in result.gives %}
                <li class="p-2 bg-red-50 rounded text-sm">{{ player }}</li>
                {% endfor %}
            </ul>
            <p class="mt-2 text-sm text-gray-600">
                Value: <span class="font-semibold">{{ "%.1f"|format(result.gives_value) }} pts</span>
            </p>
        </div>

        <div>
            <h4 class="text-sm font-semibold text-gray-700 mb-2">You Receive</h4>
            <ul class="space-y-2">
                {% for player in result.receives %}
                <li class="p-2 bg-green-50 rounded text-sm">{{ player }}</li>
                {% endfor %}
            </ul>
            <p class="mt-2 text-sm text-gray-600">
                Value: <span class="font-semibold">{{ "%.1f"|format(result.receives_value) }} pts</span>
            </p>
        </div>
    </div>

    <div class="border-t pt-4">
        <div class="flex items-center justify-between">
            <div>
                <p class="text-sm text-gray-600">Net Gain</p>
                <p class="text-2xl font-bold {% if result.net_gain > 0 %}text-green-600{% elif result.net_gain < 0 %}text-red-600{% else %}text-gray-600{% endif %}">
                    {{ "%+.1f"|format(result.net_gain) }} pts
                </p>
            </div>

            <div class="text-right">
                <p class="text-sm text-gray-600">Recommendation</p>
                <span class="inline-block px-4 py-2 rounded-full text-sm font-semibold
                    {% if result.recommendation == 'Accept' %}bg-green-100 text-green-800
                    {% elif result.recommendation == 'Reject' %}bg-red-100 text-red-800
                    {% else %}bg-yellow-100 text-yellow-800{% endif %}">
                    {{ result.recommendation }}
                </span>
            </div>
        </div>
    </div>
</div>
```

**Step 7: Run test to verify it passes**

Run: `pytest tests/integration/test_api_trades.py::test_analyze_trade -v`
Expected: PASS

**Step 8: Commit**

```bash
git add src/pigskin_mastermind/api/routes/trades.py src/pigskin_mastermind/templates/trades/ tests/integration/test_api_trades.py src/pigskin_mastermind/api/main.py
git commit -m "feat: add trade analyzer with HTMX integration"
```

---

## Phase 3: ESPN Integration

### Task 11: ESPN Sync Service

**Files:**
- Create: `src/pigskin_mastermind/services/espn_sync.py`
- Test: `tests/unit/test_espn_sync.py`

**Step 1: Write test for ESPN import**

Create `tests/unit/test_espn_sync.py`:
```python
import pytest
from unittest.mock import Mock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from pigskin_mastermind.models.database import Base, DBTeam, DBPlayer
from pigskin_mastermind.services.espn_sync import ESPNSyncService

TEST_DATABASE_URL = "sqlite:///:memory:"
test_engine = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@patch('pigskin_mastermind.services.espn_sync.League')
def test_import_team_from_espn(mock_league):
    """Test importing a team from ESPN"""
    Base.metadata.create_all(bind=test_engine)
    db = TestSessionLocal()

    # Mock ESPN API response
    mock_team = Mock()
    mock_team.team_id = 1
    mock_team.team_name = "ESPN Team"
    mock_team.owner = "ESPN Owner"
    mock_team.wins = 5
    mock_team.losses = 3
    mock_team.ties = 0
    mock_team.points_for = 950.5

    mock_player = Mock()
    mock_player.playerId = 12345
    mock_player.name = "Patrick Mahomes"
    mock_player.position = "QB"
    mock_player.proTeam = "KC"
    mock_player.projected_points = 25.5
    mock_player.points = 22.3
    mock_player.stats = {}

    mock_team.roster = [mock_player]
    mock_league.return_value.teams = [mock_team]

    # Import team
    service = ESPNSyncService(db)
    result = service.import_team(
        league_id="123456",
        team_id=1,
        espn_s2="test_s2",
        swid="test_swid",
        year=2024
    )

    assert result is not None
    assert result.name == "ESPN Team"

    # Verify database
    db_team = db.query(DBTeam).filter_by(espn_team_id="1").first()
    assert db_team is not None
    assert db_team.name == "ESPN Team"
    assert db_team.wins == 5

    db.close()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_espn_sync.py::test_import_team_from_espn -v`
Expected: FAIL with "No module named 'pigskin_mastermind.services.espn_sync'"

**Step 3: Create ESPN sync service**

Create `src/pigskin_mastermind/services/espn_sync.py`:
```python
import sys
import os
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from datetime import datetime

# Add ESPN API to path
ESPN_API_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "lib", "espn-api"
)
if ESPN_API_PATH not in sys.path:
    sys.path.insert(0, ESPN_API_PATH)

from espn_api.football import League

from pigskin_mastermind.models.database import DBTeam, DBPlayer, DBLeague


class ESPNSyncService:
    """Service for syncing data with ESPN Fantasy API"""

    def __init__(self, db: Session):
        self.db = db

    def import_team(
        self,
        league_id: str,
        team_id: int,
        espn_s2: str,
        swid: str,
        year: int = 2024
    ) -> DBTeam:
        """Import a team from ESPN Fantasy"""
        # Initialize ESPN API
        league = League(
            league_id=int(league_id),
            year=year,
            espn_s2=espn_s2,
            swid=swid
        )

        # Find the team
        espn_team = None
        for team in league.teams:
            if team.team_id == team_id:
                espn_team = team
                break

        if not espn_team:
            raise ValueError(f"Team {team_id} not found in league {league_id}")

        # Create or update team
        db_team = self.db.query(DBTeam).filter_by(espn_team_id=str(team_id)).first()
        if not db_team:
            db_team = DBTeam(
                team_id=f"espn_{league_id}_{team_id}",
                espn_team_id=str(team_id),
                league_id=league_id
            )
            self.db.add(db_team)

        # Update team data
        db_team.name = espn_team.team_name
        db_team.owner = espn_team.owner
        db_team.wins = espn_team.wins
        db_team.losses = espn_team.losses
        db_team.ties = getattr(espn_team, 'ties', 0)
        db_team.total_points = espn_team.points_for
        db_team.last_synced_at = datetime.utcnow()

        self.db.commit()

        # Import players
        for espn_player in espn_team.roster:
            self._import_player(espn_player, db_team.id)

        self.db.commit()
        self.db.refresh(db_team)

        return db_team

    def _import_player(self, espn_player: Any, team_db_id: int) -> DBPlayer:
        """Import a single player"""
        player_id = f"espn_{espn_player.playerId}"

        db_player = self.db.query(DBPlayer).filter_by(player_id=player_id).first()
        if not db_player:
            db_player = DBPlayer(player_id=player_id)
            self.db.add(db_player)

        db_player.name = espn_player.name
        db_player.position = espn_player.position
        db_player.nfl_team = espn_player.proTeam
        db_player.projected_points = getattr(espn_player, 'projected_points', 0.0)
        db_player.actual_points = getattr(espn_player, 'points', 0.0)
        db_player.stats = getattr(espn_player, 'stats', {})
        db_player.team_id = team_db_id

        return db_player

    def sync_team(self, team_db_id: int) -> Dict[str, Any]:
        """Sync an existing team from ESPN"""
        db_team = self.db.query(DBTeam).filter_by(id=team_db_id).first()
        if not db_team:
            raise ValueError(f"Team {team_db_id} not found")

        if not db_team.espn_team_id:
            raise ValueError(f"Team {team_db_id} is not linked to ESPN")

        # Get league credentials
        db_league = self.db.query(DBLeague).filter_by(league_id=db_team.league_id).first()
        if not db_league:
            raise ValueError(f"League credentials not found for {db_team.league_id}")

        # Re-import team
        return self.import_team(
            league_id=db_team.league_id,
            team_id=int(db_team.espn_team_id),
            espn_s2=db_league.espn_s2,
            swid=db_league.swid,
            year=db_league.year
        )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/unit/test_espn_sync.py::test_import_team_from_espn -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/pigskin_mastermind/services/espn_sync.py tests/unit/test_espn_sync.py
git commit -m "feat: add ESPN sync service for importing teams"
```

---

## Remaining Tasks Summary

Due to length constraints, here's a summary of remaining implementation tasks:

### Task 12: ESPN API Routes
- Create `/api/espn/import` endpoint
- Create `/api/espn/sync` endpoint
- Add settings page for ESPN credentials
- Test with integration tests

### Task 13: Error Handling
- Add exception handlers to FastAPI app
- Create error message templates
- Add toast notification system
- Test error scenarios

### Task 14: Dashboard Stats
- Update dashboard to show real team stats
- Add recent activity section
- Add upcoming matchups (if available)

### Task 15: Documentation
- Create README for web app
- Document ESPN credential setup
- Add deployment instructions
- Create user guide

---

## Running the Application

**Development:**
```bash
uvicorn pigskin_mastermind.api.main:app --reload
```

**With Database Migrations:**
```bash
alembic upgrade head
uvicorn pigskin_mastermind.api.main:app --reload
```

**Testing:**
```bash
pytest tests/ -v --cov=pigskin_mastermind
```

---

## Success Criteria

- ✅ FastAPI app runs without errors
- ✅ Database models created via Alembic
- ✅ Teams can be created and listed
- ✅ Lineup optimizer returns valid results
- ✅ Trade analyzer provides recommendations
- ⏳ ESPN import functionality works
- ⏳ Error handling provides user feedback
- ⏳ Dashboard shows accurate stats
