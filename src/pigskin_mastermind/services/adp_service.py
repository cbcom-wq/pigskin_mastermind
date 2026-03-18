"""Dedicated ADP (Average Draft Position) service.

This service owns all ADP data.  It fetches ADP from Fantasy Football
Calculator's public API, persists it in the local database, and exposes
a simple ``get_adp(name, position)`` lookup so the rest of the codebase
never needs to know where ADP came from.

Usage
-----
>>> from pigskin_mastermind.services.adp_service import ADPService
>>> svc = ADPService(db_session)
>>> svc.import_from_ffc(year=2025, scoring="ppr", num_teams=12)
>>> adp = svc.get_adp("Saquon Barkley", "RB")
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBPlayerSeasonStats

logger = logging.getLogger(__name__)

# Fantasy Football Calculator API base
_FFC_API_BASE = "https://fantasyfootballcalculator.com/api/v1/adp"

# Map FFC position labels to project conventions
_FFC_POSITION_MAP: Dict[str, str] = {
    "QB": "QB",
    "RB": "RB",
    "WR": "WR",
    "TE": "TE",
    "PK": "K",
    "K": "K",
    "DEF": "DEF",
}

# Allowed scoring format slugs for FFC API
_FFC_SCORING_FORMATS = {"standard", "ppr", "half-ppr", "2qb", "dynasty"}


class ADPService:
    """Single source of truth for player ADP data.

    Wraps Fantasy Football Calculator as the import source and stores
    values in ``DBPlayerSeasonStats.adp`` / ``adp_source`` so they
    survive across restarts.

    Parameters
    ----------
    db : sqlalchemy.orm.Session
        Active SQLAlchemy session used for reads and writes.
    """

    ADP_SOURCE_LABEL = "fantasyfootballcalculator"

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def fetch_ffc_adp(
        self,
        year: int = 2025,
        scoring: str = "ppr",
        num_teams: int = 12,
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetch ADP data from Fantasy Football Calculator's REST API.

        Parameters
        ----------
        year : int
            Season year (e.g. 2025).
        scoring : str
            Scoring format slug: ``standard``, ``ppr``, ``half-ppr``,
            ``2qb``, or ``dynasty``.
        num_teams : int
            League size (commonly 8, 10, 12, or 14).

        Returns
        -------
        list[dict] or None
            List of player dicts with keys ``name``, ``position``,
            ``team``, ``adp``, ``adp_formatted``, ``times_drafted``,
            ``high``, ``low``, ``stdev``, ``bye``.  Returns ``None``
            on network/parse failure.
        """
        if scoring not in _FFC_SCORING_FORMATS:
            raise ValueError(
                f"Invalid scoring format '{scoring}'. "
                f"Must be one of: {', '.join(sorted(_FFC_SCORING_FORMATS))}"
            )

        url = f"{_FFC_API_BASE}/{scoring}?teams={num_teams}&year={year}"
        req = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; PigskinMastermind/1.0)",
            },
        )

        try:
            with urlopen(req, timeout=15) as response:
                data = json.loads(response.read())
        except (URLError, OSError, ValueError) as exc:
            logger.warning("FFC ADP fetch failed for %s/%s: %s", year, scoring, exc)
            return None

        if data.get("status") != "Success":
            logger.warning("FFC ADP returned non-success status: %s", data.get("status"))
            return None

        raw_players = data.get("players", [])
        if not raw_players:
            return None

        players: List[Dict[str, Any]] = []
        for entry in raw_players:
            name = entry.get("name")
            position = _FFC_POSITION_MAP.get(entry.get("position", ""), "")
            if not name or not position:
                continue

            players.append({
                "name": name,
                "position": position,
                "team": entry.get("team", "FA"),
                "adp": float(entry.get("adp", 0)),
                "adp_formatted": entry.get("adp_formatted", ""),
                "times_drafted": entry.get("times_drafted", 0),
                "high": entry.get("high"),
                "low": entry.get("low"),
                "stdev": entry.get("stdev"),
                "bye": entry.get("bye"),
            })

        return players if players else None

    def import_from_ffc(
        self,
        year: int = 2025,
        scoring: str = "ppr",
        num_teams: int = 12,
    ) -> Dict[str, Any]:
        """Fetch FFC ADP and persist into the local database.

        Matches players by ``name + position`` against existing
        ``DBPlayer`` rows.  Creates ``DBPlayerSeasonStats`` rows as
        needed.

        Parameters
        ----------
        year : int
            Season year.
        scoring : str
            FFC scoring format slug.
        num_teams : int
            League size.

        Returns
        -------
        dict
            Summary with ``imported``, ``skipped``, ``total``, and
            ``source`` keys.
        """
        players = self.fetch_ffc_adp(year=year, scoring=scoring, num_teams=num_teams)
        if players is None:
            return {"imported": 0, "skipped": 0, "total": 0, "source": self.ADP_SOURCE_LABEL, "error": "Failed to fetch data from Fantasy Football Calculator"}

        imported = 0
        skipped = 0

        for entry in players:
            name = entry["name"]
            position = entry["position"]
            adp_value = entry["adp"]

            db_player = self._find_player(name, position)
            if db_player is None:
                skipped += 1
                continue

            season = (
                self.db.query(DBPlayerSeasonStats)
                .filter_by(player_id=db_player.id, year=year)
                .first()
            )
            if not season:
                season = DBPlayerSeasonStats(player_id=db_player.id, year=year)
                self.db.add(season)

            season.adp = adp_value
            season.adp_source = self.ADP_SOURCE_LABEL
            season.updated_at = datetime.utcnow()
            imported += 1

        self.db.commit()

        return {
            "imported": imported,
            "skipped": skipped,
            "total": len(players),
            "source": self.ADP_SOURCE_LABEL,
        }

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get_adp(self, name: str, position: str, year: Optional[int] = None) -> Optional[float]:
        """Look up ADP for a player by name and position.

        Parameters
        ----------
        name : str
            Player display name (case-insensitive match).
        position : str
            Position abbreviation (QB, RB, WR, TE, K, DEF).
        year : int, optional
            If provided, restrict to ADP stored for that season.
            Otherwise returns the most recently stored ADP across
            all seasons (lowest year-number = oldest, but we return
            the latest by updated_at).

        Returns
        -------
        float or None
            The stored ADP value, or ``None`` if not found.
        """
        db_player = self._find_player(name, position)
        if db_player is None:
            return None

        query = (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=db_player.id)
            .filter(DBPlayerSeasonStats.adp.isnot(None))
        )
        if year is not None:
            query = query.filter_by(year=year)

        # Return the most recently updated ADP
        season = query.order_by(DBPlayerSeasonStats.updated_at.desc()).first()
        return season.adp if season else None

    def get_all_adp(
        self,
        year: Optional[int] = None,
        position: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return all stored ADP entries, ordered by ADP ascending.

        Parameters
        ----------
        year : int, optional
            Filter to a specific season.
        position : str, optional
            Filter to a specific position.

        Returns
        -------
        list[dict]
            Each dict contains ``name``, ``position``, ``nfl_team``,
            ``adp``, ``adp_source``, ``year``, ``player_id``.
        """
        query = (
            self.db.query(DBPlayer, DBPlayerSeasonStats)
            .join(DBPlayerSeasonStats, DBPlayerSeasonStats.player_id == DBPlayer.id)
            .filter(DBPlayerSeasonStats.adp.isnot(None))
            .filter(DBPlayerSeasonStats.adp_source == self.ADP_SOURCE_LABEL)
        )

        if year is not None:
            query = query.filter(DBPlayerSeasonStats.year == year)
        if position:
            query = query.filter(DBPlayer.position == position.upper())

        query = query.order_by(DBPlayerSeasonStats.adp.asc())
        rows = query.all()

        return [
            {
                "name": player.name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "adp": season.adp,
                "adp_source": season.adp_source,
                "year": season.year,
                "player_id": player.player_id,
                "headshot_url": player.headshot_url or "",
            }
            for player, season in rows
        ]

    def get_adp_for_draft_pool(
        self,
        year: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return ADP data formatted for the mock draft player pool.

        Produces dicts compatible with ``MockDraftEngine.create_draft``
        ``player_pool`` parameter.

        Parameters
        ----------
        year : int, optional
            Filter to a specific season.

        Returns
        -------
        list[dict]
            Player dicts with ``id``, ``name``, ``position``,
            ``nfl_team``, ``projected_points``, ``adp_rank``,
            ``headshot_url``, ordered by ADP ascending.
        """
        query = (
            self.db.query(DBPlayer, DBPlayerSeasonStats)
            .join(DBPlayerSeasonStats, DBPlayerSeasonStats.player_id == DBPlayer.id)
            .filter(DBPlayerSeasonStats.adp.isnot(None))
            .filter(DBPlayerSeasonStats.adp_source == self.ADP_SOURCE_LABEL)
        )

        if year is not None:
            query = query.filter(DBPlayerSeasonStats.year == year)

        query = query.order_by(DBPlayerSeasonStats.adp.asc())
        rows = query.all()

        return [
            {
                "id": player.player_id,
                "name": player.name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "projected_points": player.projected_points or 0.0,
                "adp_rank": season.adp,
                "headshot_url": player.headshot_url or "",
            }
            for player, season in rows
            if player.position in ("QB", "RB", "WR", "TE", "K", "DEF")
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_player(self, name: str, position: str) -> Optional[DBPlayer]:
        """Find a DBPlayer by name (case-insensitive) and position."""
        return (
            self.db.query(DBPlayer)
            .filter(
                DBPlayer.name.ilike(name.strip()),
                DBPlayer.position == position.upper().strip(),
            )
            .first()
        )
