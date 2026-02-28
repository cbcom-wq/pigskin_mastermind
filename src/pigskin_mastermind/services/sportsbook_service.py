"""Sportsbook service for fetching and storing betting lines via The Odds API."""

import os
from datetime import datetime
from typing import List, Optional, Dict, Any

import requests
from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from pigskin_mastermind.models.database import DBSportsbookOdds

_ODDS_API_BASE = "https://api.the-odds-api.com/v4"
_DEFAULT_SPORT = "americanfootball_nfl"
_DEFAULT_REGIONS = "us"
_DEFAULT_GAME_MARKETS = "h2h,spreads,totals"
_DEFAULT_PROP_MARKETS = (
    "player_pass_yds,player_pass_tds,player_rush_yds,player_rush_tds,"
    "player_reception_yds,player_receptions"
)


class SportsbookService:
    """Service for fetching and querying sportsbook betting lines.

    Uses The Odds API (https://the-odds-api.com/).  An API key must be
    supplied via the ``ODDS_API_KEY`` environment variable or the
    ``api_key`` constructor argument.
    """

    def __init__(self, db: Session, api_key: Optional[str] = None):
        self.db = db
        self.api_key = api_key or os.environ.get("ODDS_API_KEY", "")

    # ------------------------------------------------------------------
    # Public fetch + import helpers
    # ------------------------------------------------------------------

    def import_game_odds(
        self,
        sport: str = _DEFAULT_SPORT,
        regions: str = _DEFAULT_REGIONS,
        markets: str = _DEFAULT_GAME_MARKETS,
        bookmakers: Optional[str] = None,
    ) -> int:
        """Fetch game odds from The Odds API and upsert into the database.

        Args:
            sport: Sport key, e.g. ``"americanfootball_nfl"``.
            regions: Comma-separated region codes, e.g. ``"us"``.
            markets: Comma-separated market keys, e.g. ``"h2h,spreads,totals"``.
            bookmakers: Optional comma-separated bookmaker keys to filter.

        Returns:
            Number of rows inserted or updated.
        """
        events = self._fetch_odds(sport=sport, regions=regions, markets=markets,
                                  bookmakers=bookmakers)
        rows = self._events_to_rows(events, sport)
        return self._upsert_rows(rows)

    def import_player_props(
        self,
        event_id: str,
        sport: str = _DEFAULT_SPORT,
        regions: str = _DEFAULT_REGIONS,
        markets: str = _DEFAULT_PROP_MARKETS,
        bookmakers: Optional[str] = None,
    ) -> int:
        """Fetch player props for a specific event and upsert into the database.

        Args:
            event_id: The Odds API event identifier.
            sport: Sport key.
            regions: Comma-separated region codes.
            markets: Comma-separated prop market keys.
            bookmakers: Optional comma-separated bookmaker keys.

        Returns:
            Number of rows inserted or updated.
        """
        events = self._fetch_event_odds(event_id=event_id, sport=sport,
                                        regions=regions, markets=markets,
                                        bookmakers=bookmakers)
        rows = self._events_to_rows(events, sport)
        return self._upsert_rows(rows)

    # ------------------------------------------------------------------
    # Public query helpers
    # ------------------------------------------------------------------

    def get_game_odds(
        self,
        event_id: Optional[str] = None,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        market: Optional[str] = None,
        bookmaker: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query stored game odds.

        Args:
            event_id: Filter by event ID.
            home_team: Partial, case-insensitive filter on home team name.
            away_team: Partial, case-insensitive filter on away team name.
            market: Filter by market key (e.g. ``"h2h"``).
            bookmaker: Filter by bookmaker key.

        Returns:
            List of odds dicts.
        """
        query = self.db.query(DBSportsbookOdds)
        if event_id:
            query = query.filter(DBSportsbookOdds.event_id == event_id)
        if home_team:
            query = query.filter(
                DBSportsbookOdds.home_team.ilike(f"%{home_team}%")
            )
        if away_team:
            query = query.filter(
                DBSportsbookOdds.away_team.ilike(f"%{away_team}%")
            )
        if market:
            query = query.filter(DBSportsbookOdds.market == market)
        if bookmaker:
            query = query.filter(DBSportsbookOdds.bookmaker == bookmaker)

        return [self._row_to_dict(r) for r in query.order_by(
            DBSportsbookOdds.commence_time, DBSportsbookOdds.market
        ).all()]

    def get_player_props(
        self,
        player_name: Optional[str] = None,
        event_id: Optional[str] = None,
        market: Optional[str] = None,
        bookmaker: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query stored player props.

        Args:
            player_name: Partial, case-insensitive filter on ``description`` or
                ``outcome_name``.
            event_id: Filter by event ID.
            market: Filter by market key (e.g. ``"player_pass_yds"``).
            bookmaker: Filter by bookmaker key.

        Returns:
            List of odds dicts.
        """
        prop_markets = (
            "player_pass_yds", "player_pass_tds", "player_rush_yds",
            "player_rush_tds", "player_reception_yds", "player_receptions",
            "player_pass_attempts", "player_pass_completions",
            "player_rush_attempts", "player_anytime_td",
        )
        query = self.db.query(DBSportsbookOdds).filter(
            DBSportsbookOdds.market.in_(prop_markets)
        )
        if event_id:
            query = query.filter(DBSportsbookOdds.event_id == event_id)
        if market:
            query = query.filter(DBSportsbookOdds.market == market)
        if bookmaker:
            query = query.filter(DBSportsbookOdds.bookmaker == bookmaker)
        if player_name:
            like = f"%{player_name}%"
            query = query.filter(
                (DBSportsbookOdds.description.ilike(like)) |
                (DBSportsbookOdds.outcome_name.ilike(like))
            )

        return [self._row_to_dict(r) for r in query.order_by(
            DBSportsbookOdds.market, DBSportsbookOdds.outcome_name
        ).all()]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_odds(
        self,
        sport: str,
        regions: str,
        markets: str,
        bookmakers: Optional[str],
    ) -> List[dict]:
        """Call The Odds API /sports/{sport}/odds endpoint."""
        params: Dict[str, Any] = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers

        url = f"{_ODDS_API_BASE}/sports/{sport}/odds"
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _fetch_event_odds(
        self,
        event_id: str,
        sport: str,
        regions: str,
        markets: str,
        bookmakers: Optional[str],
    ) -> List[dict]:
        """Call The Odds API /sports/{sport}/events/{event_id}/odds endpoint."""
        params: Dict[str, Any] = {
            "apiKey": self.api_key,
            "regions": regions,
            "markets": markets,
            "oddsFormat": "american",
            "dateFormat": "iso",
        }
        if bookmakers:
            params["bookmakers"] = bookmakers

        url = f"{_ODDS_API_BASE}/sports/{sport}/events/{event_id}/odds"
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        # Endpoint returns a single event dict; normalise to list.
        return [data] if isinstance(data, dict) else data

    def _events_to_rows(self, events: List[dict], sport: str) -> List[dict]:
        """Flatten The Odds API response into a list of row dicts."""
        rows = []
        fetched_at = datetime.utcnow()

        for event in events:
            event_id = event.get("id", "")
            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")
            sport_key = event.get("sport_key", sport)
            sport_title = event.get("sport_title", "")
            commence_raw = event.get("commence_time")
            try:
                commence_time = (
                    datetime.fromisoformat(commence_raw.replace("Z", "+00:00"))
                    if commence_raw else None
                )
            except (ValueError, AttributeError):
                commence_time = None

            for bm in event.get("bookmakers", []):
                bookmaker = bm.get("key", "")
                for mkt in bm.get("markets", []):
                    market_key = mkt.get("key", "")
                    for outcome in mkt.get("outcomes", []):
                        rows.append({
                            "event_id": event_id,
                            "sport_key": sport_key,
                            "sport_title": sport_title,
                            "commence_time": commence_time,
                            "home_team": home_team,
                            "away_team": away_team,
                            "bookmaker": bookmaker,
                            "market": market_key,
                            "outcome_name": outcome.get("name", ""),
                            "price": outcome.get("price"),
                            "point": outcome.get("point"),
                            "description": outcome.get("description"),
                            "fetched_at": fetched_at,
                            "updated_at": fetched_at,
                        })
        return rows

    def _upsert_rows(self, rows: List[dict]) -> int:
        """Insert or update rows, using a single bulk lookup to avoid N+1 queries."""
        if not rows:
            return 0

        # Build the set of unique keys we need to look up
        keys = [
            (r["event_id"], r["bookmaker"], r["market"], r["outcome_name"], r.get("description"))
            for r in rows
        ]

        # Bulk-fetch all matching existing rows in one query
        existing_map: Dict[tuple, DBSportsbookOdds] = {}
        for existing in (
            self.db.query(DBSportsbookOdds)
            .filter(
                DBSportsbookOdds.event_id.in_({k[0] for k in keys}),
                DBSportsbookOdds.bookmaker.in_({k[1] for k in keys}),
            )
            .all()
        ):
            key = (
                existing.event_id,
                existing.bookmaker,
                existing.market,
                existing.outcome_name,
                existing.description,
            )
            existing_map[key] = existing

        count = 0
        for row in rows:
            key = (row["event_id"], row["bookmaker"], row["market"], row["outcome_name"], row.get("description"))
            existing = existing_map.get(key)
            if existing:
                for attr, value in row.items():
                    if attr not in ("event_id", "bookmaker", "market", "outcome_name", "description"):
                        setattr(existing, attr, value)
            else:
                new_obj = DBSportsbookOdds(**row)
                self.db.add(new_obj)
                existing_map[key] = new_obj
            count += 1

        self.db.commit()
        return count

    @staticmethod
    def _row_to_dict(row: DBSportsbookOdds) -> Dict[str, Any]:
        return {
            "id": row.id,
            "event_id": row.event_id,
            "sport_key": row.sport_key,
            "sport_title": row.sport_title,
            "commence_time": row.commence_time.isoformat() if row.commence_time else None,
            "home_team": row.home_team,
            "away_team": row.away_team,
            "bookmaker": row.bookmaker,
            "market": row.market,
            "outcome_name": row.outcome_name,
            "price": row.price,
            "point": row.point,
            "description": row.description,
            "fetched_at": row.fetched_at.isoformat() if row.fetched_at else None,
        }
