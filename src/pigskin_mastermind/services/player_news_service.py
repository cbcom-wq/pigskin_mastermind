"""On-demand player news from ESPN's public API, cached in the database."""

import logging
from datetime import datetime, timedelta
from typing import List, Optional

import requests
from sqlalchemy import func
from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBPlayerNews

logger = logging.getLogger(__name__)

_ESPN_NEWS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/news"
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
                self.db.add(
                    DBPlayerNews(
                        player_id=player.id,
                        espn_headline_id=art["espn_headline_id"],
                        headline=art["headline"],
                        description=art.get("description"),
                        source_url=art.get("source_url"),
                        published_at=art.get("published_at"),
                        fetched_at=now,
                    )
                )

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
                "ESPN news fetch failed for espn_id=%s",
                espn_id,
                exc_info=True,
            )
            return []

        articles = []
        for item in data.get("articles", []):
            try:
                articles.append(
                    {
                        "espn_headline_id": str(item["id"]),
                        "headline": item["headline"],
                        "description": item.get("description"),
                        "source_url": (
                            item.get("links", {}).get("web", {}).get("href")
                        ),
                        "published_at": _parse_iso(item.get("published")),
                    }
                )
            except (KeyError, TypeError):
                # Skip malformed articles
                continue

        return articles


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp, returning None on failure."""
    if not value:
        return None
    try:
        # ESPN uses "2026-08-10T14:30:00Z" format
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
