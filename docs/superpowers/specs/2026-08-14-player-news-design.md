# Player News Feature — Design Spec

**Date:** 2026-08-14
**Status:** Approved

## Overview

Add on-demand, ESPN-sourced player news to the player detail page. Short Rotoworld-style blurbs —
headline, description, timestamp, link — cached in the database with a TTL so repeated views don't
re-hit the API.

## Data Model

New table `player_news` via Alembic migration.

| Column            | Type       | Notes                                        |
|-------------------|------------|----------------------------------------------|
| `id`              | Integer PK | Auto-increment                               |
| `player_id`       | String FK  | → `players.player_id`, cascade delete        |
| `espn_headline_id`| String     | ESPN's article/headline ID, for dedup        |
| `headline`        | String     | Title text                                   |
| `description`     | Text       | Short blurb / analysis paragraph             |
| `source_url`      | String     | Link to full article on ESPN                 |
| `published_at`    | DateTime   | When ESPN published the article              |
| `fetched_at`      | DateTime   | When we fetched it — drives TTL freshness    |

**Unique constraint:** `(player_id, espn_headline_id)` — re-fetches upsert, never duplicate.

**TTL logic:** The route checks `MAX(fetched_at)` for the player. If older than 30 minutes,
re-fetch from ESPN and upsert. Otherwise serve cached rows.

Old rows accumulate naturally (useful for scrolling back). No cleanup mechanism needed initially —
rows are small text.

## Service Layer

New file: `services/player_news_service.py`

Class: `PlayerNewsService(db: Session)`

### `get_player_news(player: DBPlayer, max_age_minutes: int = 30) -> list[DBPlayerNews]`

1. If `player.espn_id` is `None`, return `[]` immediately.
2. Query `MAX(fetched_at)` from `player_news` where `player_id = player.player_id`.
3. If the result is within `max_age_minutes`, return cached rows ordered by `published_at DESC`.
4. Otherwise, call `_fetch_from_espn(player.espn_id)`.
5. Upsert results into `player_news` (match on `player_id + espn_headline_id`; update
   `headline`, `description`, `source_url`, `published_at`, `fetched_at` on conflict).
6. Commit and return the rows.

### `_fetch_from_espn(espn_id: str) -> list[dict]`

- **Endpoint:** `https://site.api.espn.com/apis/site/v2/sports/football/nfl/news?player={espn_id}`
- Public, no auth required.
- Uses `requests.get()` with a short timeout (10s).
- Parses `response.json()["articles"]`, extracting:
  - `article["headline"]` → headline
  - `article["description"]` → description
  - `article["links"]["web"]["href"]` → source_url
  - `article["published"]` → published_at (ISO 8601 parse)
  - `str(article["id"])` → espn_headline_id (for dedup, converted to string)
- Returns a list of dicts ready for upsert.

### Error handling

- Network errors, timeouts, non-200 responses, JSON parse failures: log a warning, return
  whatever is cached (stale news beats no news). Never raise to the route.
- Missing fields in an individual article: skip that article, continue with the rest.

## Route Integration

**File:** `api/routes/players.py`, function `player_detail_page`

- Import `PlayerNewsService`.
- After loading the player and stats, call `PlayerNewsService(db).get_player_news(player)`.
- Pass `news_items` into the template context.
- No new route needed — news is part of the existing detail page.

## Template

**File:** `templates/players/details.html`

New "Recent News" card inserted between the header/bio strip and the tabbed stats section.

### Layout

- **Card container:** `bg-slate-800` rounded card matching existing page style.
- **Section header:** "Recent News" with a small "Updated X minutes ago" timestamp.
- **News items:** Each blurb is a compact block:
  - **Headline** (bold, `text-white`) with **relative timestamp** ("2 hours ago") right-aligned in
    `text-slate-400`.
  - **Description** paragraph below in `text-slate-300`.
  - Small "Read on ESPN →" link in `text-blue-400` / `hover:text-blue-300`.
- **Scrollable container:** `max-h` set to show ~5 items, `overflow-y-auto` for more.
- **Conditional rendering:** If `news_items` is empty, the entire section is omitted — no empty
  state, no "No news found" text.

### Relative timestamps

Use a Jinja2 filter or inline macro to convert `published_at` to relative time
("2 hours ago", "3 days ago"). Implemented as a template filter registered in `api/main.py`
for reuse.

## Files Changed

| File | Change |
|------|--------|
| `models/database.py` | Add `DBPlayerNews` model |
| `services/player_news_service.py` | New service (fetch + cache logic) |
| `api/routes/players.py` | Call news service, pass to template |
| `api/main.py` | Register `timeago` template filter |
| `templates/players/details.html` | Add news card section |
| `alembic/versions/xxxx_add_player_news.py` | Migration for `player_news` table |

## Dependencies

- `requests` — already installed (used by `sportsbook_service.py`).
- No new packages required.

## Out of Scope

- Bulk/CLI refresh command (could add later).
- Multi-source aggregation (only ESPN for now).
- News for players without `espn_id`.
- Roster-wide "all my players' news" dashboard (future).
- Cleanup/pruning of old news rows.
