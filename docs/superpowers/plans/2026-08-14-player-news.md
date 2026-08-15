# Player News Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add on-demand ESPN player news to the player detail page — Rotoworld-style blurbs cached in the database.

**Architecture:** New `DBPlayerNews` model + Alembic migration, `PlayerNewsService` that fetches from ESPN's public news API and caches with a 30-minute TTL, wired into the existing player detail route. A `timeago` Jinja2 filter renders relative timestamps.

**Tech Stack:** SQLAlchemy, Alembic, `requests`, FastAPI/Jinja2, Tailwind CSS

## Global Constraints

- `requests` is already installed — no new dependencies.
- ESPN's public API requires no auth: `https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?player={espn_id}`
- Players without an `espn_id` get no news (no error, section omitted).
- Always scope pytest to `tests/` — bare `pytest` fails.
- Database is SQLite via SQLAlchemy. Migrations via Alembic.
- Templates use Tailwind (CDN) and HTMX. Dark cards use `bg-slate-800`.

---

### Task 1: DBPlayerNews Model + Migration

**Files:**
- Modify: `src/pigskin_mastermind/models/database.py` (after `DBSportsbookOdds`, before `DEFAULT_SCORING_SETTINGS`)
- Create: `alembic/versions/f8a1b2c3d4e5_add_player_news.py` (via `alembic revision --autogenerate`)
- Create: `tests/test_player_news_service.py`

**Interfaces:**
- Produces: `DBPlayerNews` model with columns `id`, `player_id` (FK), `espn_headline_id`, `headline`, `description`, `source_url`, `published_at`, `fetched_at`; unique constraint on `(player_id, espn_headline_id)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_player_news_service.py` with a test that imports and instantiates `DBPlayerNews`:

```python
"""Tests for player news feature."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pigskin_mastermind.models.database import Base, DBPlayer, DBPlayerNews


@pytest.fixture
def db():
    """In-memory SQLite session for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def player(db):
    """A test player with an ESPN ID."""
    p = DBPlayer(
        player_id="espn_12345",
        name="Patrick Mahomes",
        position="QB",
        nfl_team="KC",
        espn_id="12345",
    )
    db.add(p)
    db.commit()
    return p


def test_db_player_news_roundtrip(db, player):
    """DBPlayerNews rows can be inserted, queried, and are unique on
    (player_id, espn_headline_id)."""
    news = DBPlayerNews(
        player_id=player.id,
        espn_headline_id="art_001",
        headline="Mahomes throws 5 TDs",
        description="In a dominant performance...",
        source_url="https://espn.com/article/001",
        published_at=datetime(2026, 8, 10, 14, 0),
        fetched_at=datetime.utcnow(),
    )
    db.add(news)
    db.commit()

    rows = db.query(DBPlayerNews).filter_by(player_id=player.id).all()
    assert len(rows) == 1
    assert rows[0].headline == "Mahomes throws 5 TDs"
    assert rows[0].espn_headline_id == "art_001"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_player_news_service.py::test_db_player_news_roundtrip -v`
Expected: FAIL with `ImportError: cannot import name 'DBPlayerNews'`

- [ ] **Step 3: Add DBPlayerNews model to database.py**

In `src/pigskin_mastermind/models/database.py`, add after the `DBSportsbookOdds` class (before `DEFAULT_SCORING_SETTINGS`):

```python
class DBPlayerNews(Base):
    """Cached player news articles from ESPN's public API.

    Fetched on-demand when viewing a player detail page, with a TTL-based
    cache so repeated views within ``max_age_minutes`` serve from the DB
    instead of re-hitting the API.
    """
    __tablename__ = "player_news"
    __table_args__ = (
        UniqueConstraint(
            'player_id', 'espn_headline_id',
            name='uq_player_news_headline',
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    espn_headline_id = Column(String, nullable=False)
    headline = Column(String, nullable=False)
    description = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)
```

Also add a `news` relationship to `DBPlayer` (after the `game_logs` relationship, line 56):

```python
    news = relationship("DBPlayerNews", back_populates="player", cascade="all, delete-orphan")
```

And add the back-reference on `DBPlayerNews`:

```python
    player = relationship("DBPlayer", back_populates="news")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_player_news_service.py::test_db_player_news_roundtrip -v`
Expected: PASS

- [ ] **Step 5: Generate the Alembic migration**

Run: `alembic revision --autogenerate -m "add player_news table"`
Verify the generated migration has `create_table('player_news', ...)` with the correct columns and unique constraint.

- [ ] **Step 6: Apply the migration**

Run: `alembic upgrade head`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add src/pigskin_mastermind/models/database.py alembic/versions/*player_news* tests/test_player_news_service.py
git commit -m "feat(news): add DBPlayerNews model and migration"
```

---

### Task 2: PlayerNewsService

**Files:**
- Create: `src/pigskin_mastermind/services/player_news_service.py`
- Modify: `tests/test_player_news_service.py` (append tests)

**Interfaces:**
- Consumes: `DBPlayerNews` from Task 1, `DBPlayer.espn_id`
- Produces: `PlayerNewsService(db: Session)` with `get_player_news(player: DBPlayer, max_age_minutes: int = 30) -> list[DBPlayerNews]`

- [ ] **Step 1: Write the failing test — cache miss triggers fetch**

Append to `tests/test_player_news_service.py`:

```python
from pigskin_mastermind.services.player_news_service import PlayerNewsService


# Sample ESPN API response payload for mocking
ESPN_RESPONSE = {
    "articles": [
        {
            "id": 99001,
            "headline": "Mahomes leads Chiefs to victory",
            "description": "Patrick Mahomes threw for 300 yards and 3 TDs.",
            "published": "2026-08-10T14:30:00Z",
            "links": {"web": {"href": "https://www.espn.com/nfl/story/_/id/99001"}},
        },
        {
            "id": 99002,
            "headline": "Chiefs prep for Week 2",
            "description": "Kansas City focuses on run game.",
            "published": "2026-08-09T10:00:00Z",
            "links": {"web": {"href": "https://www.espn.com/nfl/story/_/id/99002"}},
        },
    ]
}


@patch("pigskin_mastermind.services.player_news_service.requests.get")
def test_get_player_news_fetches_on_cache_miss(mock_get, db, player):
    """First call for a player hits ESPN and caches the results."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = ESPN_RESPONSE
    mock_get.return_value = mock_resp

    svc = PlayerNewsService(db)
    news = svc.get_player_news(player)

    assert len(news) == 2
    assert news[0].headline == "Mahomes leads Chiefs to victory"
    assert news[1].headline == "Chiefs prep for Week 2"
    mock_get.assert_called_once()

    # Verify persisted in DB
    assert db.query(DBPlayerNews).filter_by(player_id=player.id).count() == 2
```

- [ ] **Step 2: Write the failing test — cache hit skips fetch**

```python
@patch("pigskin_mastermind.services.player_news_service.requests.get")
def test_get_player_news_serves_cache_when_fresh(mock_get, db, player):
    """Second call within TTL returns cached rows without hitting ESPN."""
    # Pre-populate cache
    db.add(DBPlayerNews(
        player_id=player.id,
        espn_headline_id="cached_1",
        headline="Cached headline",
        description="From earlier fetch",
        fetched_at=datetime.utcnow(),  # fresh
    ))
    db.commit()

    svc = PlayerNewsService(db)
    news = svc.get_player_news(player)

    assert len(news) == 1
    assert news[0].headline == "Cached headline"
    mock_get.assert_not_called()
```

- [ ] **Step 3: Write the failing test — stale cache triggers re-fetch**

```python
@patch("pigskin_mastermind.services.player_news_service.requests.get")
def test_get_player_news_refetches_when_stale(mock_get, db, player):
    """Cached rows older than max_age_minutes trigger a fresh ESPN call."""
    db.add(DBPlayerNews(
        player_id=player.id,
        espn_headline_id="old_1",
        headline="Old headline",
        description="Stale",
        fetched_at=datetime.utcnow() - timedelta(minutes=60),
    ))
    db.commit()

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = ESPN_RESPONSE
    mock_get.return_value = mock_resp

    svc = PlayerNewsService(db)
    news = svc.get_player_news(player, max_age_minutes=30)

    # Old row still exists, plus 2 new ones
    assert len(news) >= 2
    mock_get.assert_called_once()
```

- [ ] **Step 4: Write the failing test — no espn_id returns empty**

```python
def test_get_player_news_no_espn_id(db):
    """Player without espn_id returns empty list, no API call."""
    p = DBPlayer(
        player_id="nfl_99999",
        name="No ESPN ID",
        position="WR",
        nfl_team="NYG",
        espn_id=None,
    )
    db.add(p)
    db.commit()

    svc = PlayerNewsService(db)
    news = svc.get_player_news(p)
    assert news == []
```

- [ ] **Step 5: Write the failing test — network error returns cached**

```python
@patch("pigskin_mastermind.services.player_news_service.requests.get")
def test_get_player_news_network_error_returns_cached(mock_get, db, player):
    """Network failure returns stale cache instead of raising."""
    db.add(DBPlayerNews(
        player_id=player.id,
        espn_headline_id="stale_1",
        headline="Stale but usable",
        description="Still good enough",
        fetched_at=datetime.utcnow() - timedelta(hours=2),
    ))
    db.commit()

    mock_get.side_effect = Exception("Connection refused")

    svc = PlayerNewsService(db)
    news = svc.get_player_news(player, max_age_minutes=30)

    assert len(news) == 1
    assert news[0].headline == "Stale but usable"
```

- [ ] **Step 6: Run all tests to verify they fail**

Run: `pytest tests/test_player_news_service.py -v`
Expected: 5 FAIL (import error for `PlayerNewsService`)

- [ ] **Step 7: Implement PlayerNewsService**

Create `src/pigskin_mastermind/services/player_news_service.py`:

```python
"""On-demand player news from ESPN's public API, cached in the database."""

import logging
from datetime import datetime, timedelta
from typing import List

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBPlayerNews

logger = logging.getLogger(__name__)

_ESPN_NEWS_URL = (
    "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news"
)
_REQUEST_TIMEOUT = 10  # seconds


class PlayerNewsService:
    """Fetch and cache player news articles from ESPN.

    On the first request for a player (or when the cache is stale), hits
    ESPN's public news endpoint, upserts the results into ``player_news``,
    and returns them.  Subsequent requests within ``max_age_minutes`` serve
    directly from the database.
    """

    def __init__(self, db: Session):
        self.db = db

    def get_player_news(
        self,
        player: DBPlayer,
        max_age_minutes: int = 30,
    ) -> List[DBPlayerNews]:
        """Return cached news for *player*, refreshing if stale.

        Args:
            player: The DB player to fetch news for.  Must have
                ``espn_id`` set, otherwise returns ``[]``.
            max_age_minutes: How many minutes before the cache is
                considered stale and a fresh ESPN fetch is triggered.

        Returns:
            News rows ordered by ``published_at`` descending (newest first).
            Empty list if the player has no ``espn_id`` or ESPN returned
            nothing.
        """
        if not player.espn_id:
            return []

        # Check cache freshness
        latest_fetch = (
            self.db.query(func.max(DBPlayerNews.fetched_at))
            .filter(DBPlayerNews.player_id == player.id)
            .scalar()
        )

        cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
        if latest_fetch is None or latest_fetch < cutoff:
            self._refresh(player)

        return (
            self.db.query(DBPlayerNews)
            .filter(DBPlayerNews.player_id == player.id)
            .order_by(DBPlayerNews.published_at.desc())
            .all()
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refresh(self, player: DBPlayer) -> None:
        """Fetch from ESPN and upsert into the cache."""
        articles = self._fetch_from_espn(player.espn_id)
        now = datetime.utcnow()

        for art in articles:
            existing = (
                self.db.query(DBPlayerNews)
                .filter_by(
                    player_id=player.id,
                    espn_headline_id=art["espn_headline_id"],
                )
                .first()
            )
            if existing:
                existing.headline = art["headline"]
                existing.description = art.get("description")
                existing.source_url = art.get("source_url")
                existing.published_at = art.get("published_at")
                existing.fetched_at = now
            else:
                self.db.add(DBPlayerNews(
                    player_id=player.id,
                    espn_headline_id=art["espn_headline_id"],
                    headline=art["headline"],
                    description=art.get("description"),
                    source_url=art.get("source_url"),
                    published_at=art.get("published_at"),
                    fetched_at=now,
                ))

        try:
            self.db.commit()
        except Exception:
            logger.warning("Failed to commit news for player %s", player.name)
            self.db.rollback()

    def _fetch_from_espn(self, espn_id: str) -> list:
        """Hit ESPN's public news endpoint and return parsed dicts.

        Never raises — returns ``[]`` on any failure so the caller falls
        back to whatever is cached.
        """
        try:
            resp = requests.get(
                _ESPN_NEWS_URL,
                params={"player": espn_id},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.warning(
                "ESPN news fetch failed for espn_id=%s", espn_id, exc_info=True,
            )
            return []

        articles = []
        for item in data.get("articles", []):
            try:
                articles.append({
                    "espn_headline_id": str(item["id"]),
                    "headline": item["headline"],
                    "description": item.get("description"),
                    "source_url": (
                        item.get("links", {}).get("web", {}).get("href")
                    ),
                    "published_at": _parse_iso(item.get("published")),
                })
            except (KeyError, TypeError):
                # Skip malformed articles
                continue

        return articles


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO 8601 timestamp, returning None on failure."""
    if not value:
        return None
    try:
        # ESPN uses "2026-08-10T14:30:00Z" format
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 8: Run all tests to verify they pass**

Run: `pytest tests/test_player_news_service.py -v`
Expected: 5 PASS

- [ ] **Step 9: Commit**

```bash
git add src/pigskin_mastermind/services/player_news_service.py tests/test_player_news_service.py
git commit -m "feat(news): add PlayerNewsService with ESPN fetch + DB cache"
```

---

### Task 3: Route + Template + Timeago Filter

**Files:**
- Modify: `src/pigskin_mastermind/api/main.py` (register `timeago` filter)
- Modify: `src/pigskin_mastermind/api/routes/players.py` (call news service in `player_detail_page`)
- Modify: `src/pigskin_mastermind/templates/players/details.html` (add news card)

**Interfaces:**
- Consumes: `PlayerNewsService.get_player_news()` from Task 2, `DBPlayerNews` from Task 1
- Produces: `news_items` template variable, `timeago` Jinja2 filter, rendered news card on the player detail page

- [ ] **Step 1: Write the timeago filter test**

Append to `tests/test_player_news_service.py`:

```python
from pigskin_mastermind.api.main import _timeago


def test_timeago_minutes():
    assert _timeago(datetime.utcnow() - timedelta(minutes=5)) == "5 minutes ago"


def test_timeago_hours():
    assert _timeago(datetime.utcnow() - timedelta(hours=3)) == "3 hours ago"


def test_timeago_days():
    assert _timeago(datetime.utcnow() - timedelta(days=2)) == "2 days ago"


def test_timeago_just_now():
    assert _timeago(datetime.utcnow() - timedelta(seconds=30)) == "just now"


def test_timeago_none():
    assert _timeago(None) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_player_news_service.py::test_timeago_minutes -v`
Expected: FAIL with `ImportError: cannot import name '_timeago'`

- [ ] **Step 3: Implement the timeago filter in api/main.py**

Add this function and registration in `src/pigskin_mastermind/api/main.py`. Place the function
before the router imports (after the `templates` definition, around line 33):

```python
from datetime import datetime as _dt, timedelta as _td


def _timeago(value) -> str:
    """Jinja2 filter: convert a datetime to a relative string like '3 hours ago'."""
    if value is None:
        return ""
    now = _dt.utcnow()
    # Handle timezone-aware datetimes by comparing as naive UTC
    if hasattr(value, 'tzinfo') and value.tzinfo is not None:
        value = value.replace(tzinfo=None)
    diff = now - value
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"


templates.env.filters["timeago"] = _timeago
```

- [ ] **Step 4: Run timeago tests to verify they pass**

Run: `pytest tests/test_player_news_service.py -k "timeago" -v`
Expected: 5 PASS

- [ ] **Step 5: Wire the news service into the player detail route**

In `src/pigskin_mastermind/api/routes/players.py`, function `player_detail_page`:

Add this import at the top of the file (with the other imports):

```python
from pigskin_mastermind.services.player_news_service import PlayerNewsService
```

Add this call after the `fantasy_team` line (after line 88 `fantasy_team = player.team.name if player.team else None`), before the season logic:

```python
    # On-demand player news (ESPN)
    news_svc = PlayerNewsService(db)
    news_items = news_svc.get_player_news(player)
```

Add `"news_items": news_items,` to the template context dict (inside the `TemplateResponse` call):

```python
    return templates.TemplateResponse(
        "players/details.html",
        {
            "request": request,
            "player": player,
            "seasons": seasons,
            "latest_season": latest_season,
            "adp_season": adp_season,
            "game_logs": game_logs,
            "trend": trend,
            "fantasy_team": fantasy_team,
            "news_items": news_items,
            "back_url": back,
        },
    )
```

- [ ] **Step 6: Add the news card to the player detail template**

In `src/pigskin_mastermind/templates/players/details.html`, insert the following block after the
closing `</div>` of the Player Header Card (after line 120 `</div>` that closes the header card,
before the `<!-- Season at a glance -->` comment on line 122):

```html
    <!-- Recent News -->
    {% if news_items %}
    <div class="bg-gradient-to-r from-slate-800 to-slate-700 rounded-xl shadow-md border border-slate-600 p-5 mb-6">
        <div class="flex items-center justify-between mb-3">
            <h3 class="text-sm font-semibold text-slate-300 uppercase tracking-wider">Recent News</h3>
            {% if news_items[0].fetched_at %}
            <span class="text-xs text-slate-500">Updated {{ news_items[0].fetched_at | timeago }}</span>
            {% endif %}
        </div>
        <div class="space-y-4 max-h-[28rem] overflow-y-auto pr-1">
            {% for item in news_items %}
            <div class="{% if not loop.last %}border-b border-white/10 pb-4{% endif %}">
                <div class="flex items-start justify-between gap-3">
                    <h4 class="text-sm font-bold text-white leading-snug">{{ item.headline }}</h4>
                    {% if item.published_at %}
                    <span class="text-xs text-slate-400 whitespace-nowrap flex-shrink-0">{{ item.published_at | timeago }}</span>
                    {% endif %}
                </div>
                {% if item.description %}
                <p class="text-sm text-slate-300 mt-1 leading-relaxed">{{ item.description }}</p>
                {% endif %}
                {% if item.source_url %}
                <a href="{{ item.source_url }}" target="_blank" rel="noopener noreferrer"
                   class="inline-flex items-center gap-1 text-xs text-blue-400 hover:text-blue-300 mt-1.5 transition-colors">
                    Read on ESPN
                    <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/>
                    </svg>
                </a>
                {% endif %}
            </div>
            {% endfor %}
        </div>
    </div>
    {% endif %}
```

- [ ] **Step 7: Verify the full test suite still passes**

Run: `pytest tests/ -v`
Expected: All tests pass, no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/pigskin_mastermind/api/main.py src/pigskin_mastermind/api/routes/players.py src/pigskin_mastermind/templates/players/details.html tests/test_player_news_service.py
git commit -m "feat(news): wire player news into detail page with timeago filter"
```
