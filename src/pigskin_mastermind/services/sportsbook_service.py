"""Sportsbook odds service using The Odds API.

Fetches NFL game odds (moneylines, spreads, totals) and player prop
betting lines from The Odds API (https://the-odds-api.com).

Requires a free API key set via the ``ODDS_API_KEY`` environment variable.
"""

import os
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests
from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBSportsbookOdds

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.the-odds-api.com/v4"
_NFL_SPORT = "americanfootball_nfl"
_DEFAULT_REGION = "us"
_DEFAULT_ODDS_FORMAT = "american"

# Game-level market keys recognised by The Odds API
GAME_MARKETS = ["h2h", "spreads", "totals"]

# Player prop market keys recognised by The Odds API
PLAYER_PROP_MARKETS = [
    "player_pass_yds",
    "player_pass_tds",
    "player_rush_yds",
    "player_rush_tds",
    "player_receptions",
    "player_reception_yds",
    "player_reception_tds",
    "player_anytime_td",
]


class SportsBookService:
    """Service for importing and querying sportsbook odds data."""

    def __init__(self, db: Session, api_key: Optional[str] = None):
        self.db = db
        self.api_key = api_key or os.environ.get("ODDS_API_KEY", "")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {"Accept": "application/json"}

    def _params(self, **extra: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {"apiKey": self.api_key}
        params.update(extra)
        return params

    # ------------------------------------------------------------------
    # Fetch game odds
    # ------------------------------------------------------------------

    def fetch_game_odds(
        self,
        markets: Optional[List[str]] = None,
        region: str = _DEFAULT_REGION,
        odds_format: str = _DEFAULT_ODDS_FORMAT,
    ) -> List[Dict[str, Any]]:
        """Fetch current NFL game odds from The Odds API.

        Args:
            markets: List of market keys (default: h2h, spreads, totals).
            region: Bookmaker region (default: us).
            odds_format: Odds format (default: american).

        Returns:
            Raw JSON list of events from the API.

        Raises:
            RuntimeError: If the API key is missing or the request fails.
        """
        if not self.api_key:
            raise RuntimeError(
                "ODDS_API_KEY is not set. Get a free key at https://the-odds-api.com"
            )

        if markets is None:
            markets = list(GAME_MARKETS)

        url = f"{_BASE_URL}/sports/{_NFL_SPORT}/odds"
        params = self._params(
            regions=region,
            markets=",".join(markets),
            oddsFormat=odds_format,
        )
        resp = requests.get(url, params=params, headers=self._headers(), timeout=30)
        resp.raise_for_status()

        remaining = resp.headers.get("x-requests-remaining")
        if remaining is not None:
            logger.info("Odds API requests remaining: %s", remaining)

        return resp.json()

    # ------------------------------------------------------------------
    # Fetch player props for a specific event
    # ------------------------------------------------------------------

    def fetch_player_props(
        self,
        event_id: str,
        markets: Optional[List[str]] = None,
        region: str = _DEFAULT_REGION,
        odds_format: str = _DEFAULT_ODDS_FORMAT,
    ) -> Dict[str, Any]:
        """Fetch player prop odds for a specific NFL event.

        Args:
            event_id: The Odds API event identifier.
            markets: Player-prop market keys (default: all supported props).
            region: Bookmaker region.
            odds_format: Odds format.

        Returns:
            Raw JSON dict for the event from the API.

        Raises:
            RuntimeError: If the API key is missing or the request fails.
        """
        if not self.api_key:
            raise RuntimeError(
                "ODDS_API_KEY is not set. Get a free key at https://the-odds-api.com"
            )

        if markets is None:
            markets = list(PLAYER_PROP_MARKETS)

        url = f"{_BASE_URL}/sports/{_NFL_SPORT}/events/{event_id}/odds"
        params = self._params(
            regions=region,
            markets=",".join(markets),
            oddsFormat=odds_format,
        )
        resp = requests.get(url, params=params, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Import helpers – persist to DB
    # ------------------------------------------------------------------

    def import_game_odds(
        self,
        markets: Optional[List[str]] = None,
    ) -> int:
        """Fetch current NFL game odds and persist them.

        Existing rows for the same event/bookmaker/market/outcome are
        updated in place so the table always reflects the latest lines.

        Returns:
            Number of odds rows created or updated.
        """
        events = self.fetch_game_odds(markets=markets)
        return self._persist_game_events(events)

    def import_player_props(
        self,
        event_id: str,
        markets: Optional[List[str]] = None,
    ) -> int:
        """Fetch player prop odds for an event and persist them.

        Returns:
            Number of odds rows created or updated.
        """
        event = self.fetch_player_props(event_id, markets=markets)
        return self._persist_prop_event(event)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_game_odds(
        self,
        team: Optional[str] = None,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query stored game odds, optionally filtered by team or market.

        Args:
            team: NFL team abbreviation or name to filter on (home or away).
            market: Market key to filter (e.g. 'h2h', 'spreads').

        Returns:
            List of odds dicts.
        """
        query = self.db.query(DBSportsbookOdds).filter(
            DBSportsbookOdds.player_name.is_(None)
        )
        if team:
            like_team = f"%{team}%"
            query = query.filter(
                (DBSportsbookOdds.home_team.ilike(like_team))
                | (DBSportsbookOdds.away_team.ilike(like_team))
            )
        if market:
            query = query.filter(DBSportsbookOdds.market == market)
        return [self._row_to_dict(r) for r in query.all()]

    def get_player_odds(
        self,
        player_name: Optional[str] = None,
        market: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query stored player prop odds.

        Args:
            player_name: Player name (partial match).
            market: Prop market key to filter.

        Returns:
            List of odds dicts.
        """
        query = self.db.query(DBSportsbookOdds).filter(
            DBSportsbookOdds.player_name.isnot(None)
        )
        if player_name:
            query = query.filter(
                DBSportsbookOdds.player_name.ilike(f"%{player_name}%")
            )
        if market:
            query = query.filter(DBSportsbookOdds.market == market)
        return [self._row_to_dict(r) for r in query.all()]

    # ------------------------------------------------------------------
    # Internal persistence helpers
    # ------------------------------------------------------------------

    def _persist_game_events(self, events: List[Dict[str, Any]]) -> int:
        count = 0
        for event in events:
            event_id = event.get("id", "")
            home = event.get("home_team", "")
            away = event.get("away_team", "")
            commence = _parse_iso(event.get("commence_time"))

            for bookmaker in event.get("bookmakers", []):
                bk_key = bookmaker.get("key", "")
                for mkt in bookmaker.get("markets", []):
                    mkt_key = mkt.get("key", "")
                    for outcome in mkt.get("outcomes", []):
                        outcome_name = outcome.get("name", "")
                        price = outcome.get("price")
                        point = outcome.get("point")

                        row = self._upsert_odds(
                            event_id=event_id,
                            home_team=home,
                            away_team=away,
                            commence_time=commence,
                            market=mkt_key,
                            bookmaker=bk_key,
                            outcome_name=outcome_name,
                            price=price,
                            point=point,
                            player_name=None,
                        )
                        if row:
                            count += 1
        self.db.commit()
        return count

    def _persist_prop_event(self, event: Dict[str, Any]) -> int:
        count = 0
        event_id = event.get("id", "")
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        commence = _parse_iso(event.get("commence_time"))

        for bookmaker in event.get("bookmakers", []):
            bk_key = bookmaker.get("key", "")
            for mkt in bookmaker.get("markets", []):
                mkt_key = mkt.get("key", "")
                for outcome in mkt.get("outcomes", []):
                    outcome_name = outcome.get("name", "")
                    price = outcome.get("price")
                    point = outcome.get("point")

                    # Player name may be nested in description
                    player = outcome.get("description") or outcome_name

                    row = self._upsert_odds(
                        event_id=event_id,
                        home_team=home,
                        away_team=away,
                        commence_time=commence,
                        market=mkt_key,
                        bookmaker=bk_key,
                        outcome_name=outcome_name,
                        price=price,
                        point=point,
                        player_name=player,
                    )
                    if row:
                        count += 1
        self.db.commit()
        return count

    def _upsert_odds(
        self,
        *,
        event_id: str,
        home_team: str,
        away_team: str,
        commence_time: Optional[datetime],
        market: str,
        bookmaker: str,
        outcome_name: str,
        price: Optional[int],
        point: Optional[float],
        player_name: Optional[str],
    ) -> DBSportsbookOdds:
        """Insert or update an odds row."""
        query = self.db.query(DBSportsbookOdds).filter_by(
            event_id=event_id,
            market=market,
            bookmaker=bookmaker,
            outcome_name=outcome_name,
        )
        if player_name is not None:
            query = query.filter_by(player_name=player_name)
        else:
            query = query.filter(DBSportsbookOdds.player_name.is_(None))

        row = query.first()
        if not row:
            row = DBSportsbookOdds(
                event_id=event_id,
                home_team=home_team,
                away_team=away_team,
                commence_time=commence_time,
                market=market,
                bookmaker=bookmaker,
                outcome_name=outcome_name,
                player_name=player_name,
            )
            self.db.add(row)

        row.price = price
        row.point = point
        row.updated_at = datetime.utcnow()
        return row

    @staticmethod
    def _row_to_dict(row: DBSportsbookOdds) -> Dict[str, Any]:
        return {
            "id": row.id,
            "event_id": row.event_id,
            "sport": row.sport,
            "home_team": row.home_team,
            "away_team": row.away_team,
            "commence_time": row.commence_time.isoformat() if row.commence_time else None,
            "market": row.market,
            "bookmaker": row.bookmaker,
            "outcome_name": row.outcome_name,
            "price": row.price,
            "point": row.point,
            "player_name": row.player_name,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def _parse_iso(val: Optional[str]) -> Optional[datetime]:
    """Parse an ISO 8601 timestamp string to a datetime."""
    if not val:
        return None
    try:
        # Handle the trailing 'Z' that The Odds API uses
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        return datetime.fromisoformat(val)
    except (ValueError, TypeError):
        return None
