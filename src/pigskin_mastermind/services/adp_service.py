"""Dedicated ADP (Average Draft Position) service.

This service owns all ADP data.  It fetches ADP from Fantasy Football
Calculator's public API, persists it in the local database, and exposes
a simple ``get_adp(name, position)`` lookup so the rest of the codebase
never needs to know where ADP came from.

Usage
-----
>>> from pigskin_mastermind.services.adp_service import ADPService
>>> svc = ADPService(db_session)
>>> svc.import_from_ffc(scoring="ppr", num_teams=12)  # defaults to current season
>>> adp = svc.get_adp("Saquon Barkley", "RB")
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBPlayerSeasonStats
from pigskin_mastermind.utils.season import current_fantasy_season

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

# Generational suffixes ignored when matching player names
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _pick_to_overall(value: Any, num_teams: int) -> Optional[float]:
    """Convert an FFC pick value to an overall pick number.

    FFC formats ``high``/``low`` as round.pick strings (e.g. "2.05" =
    round 2, pick 5). Numeric values are returned as-is.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.fullmatch(r"(\d+)\.(\d+)", str(value).strip())
    if match:
        rnd, pick = int(match.group(1)), int(match.group(2))
        return float((rnd - 1) * num_teams + pick)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_name(name: str) -> str:
    """Normalize a player name for fuzzy matching.

    Lowercases, strips punctuation, collapses whitespace, and drops
    generational suffixes so e.g. "A.J. Brown" == "AJ Brown" and
    "Aaron Jones Sr." == "Aaron Jones".
    """
    cleaned = re.sub(r"[.'’-]", "", name.lower())
    parts = [w for w in cleaned.split() if w not in _NAME_SUFFIXES]
    return " ".join(parts)


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
        year: Optional[int] = None,
        scoring: str = "ppr",
        num_teams: int = 12,
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetch ADP data from Fantasy Football Calculator's REST API.

        Parameters
        ----------
        year : int, optional
            Season year (e.g. 2026). Defaults to the current fantasy season.
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

        year = year or current_fantasy_season()
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
                "ffc_id": entry.get("player_id"),
            })

        return players if players else None

    def import_from_ffc(
        self,
        year: Optional[int] = None,
        scoring: str = "ppr",
        num_teams: int = 12,
        create_missing: bool = True,
    ) -> Dict[str, Any]:
        """Fetch FFC ADP and persist into the local database.

        Matches players by ``name + position`` against existing
        ``DBPlayer`` rows (exact then normalized name).  Creates
        ``DBPlayerSeasonStats`` rows as needed.

        Parameters
        ----------
        year : int, optional
            Season year. Defaults to the current fantasy season.
        scoring : str
            FFC scoring format slug.
        num_teams : int
            League size.
        create_missing : bool
            When True (default), FFC players with no matching ``DBPlayer``
            get a minimal player row created (with an ``ffc_``-prefixed
            ``player_id``) so the draft pool mirrors the full FFC board.

        Returns
        -------
        dict
            Summary with ``imported``, ``created``, ``skipped``,
            ``total``, ``source``, and ``last_updated`` keys.
        """
        year = year or current_fantasy_season()
        players = self.fetch_ffc_adp(year=year, scoring=scoring, num_teams=num_teams)
        if players is None:
            return {
                "imported": 0,
                "created": 0,
                "skipped": 0,
                "total": 0,
                "source": self.ADP_SOURCE_LABEL,
                "error": "Failed to fetch data from Fantasy Football Calculator",
            }

        imported = 0
        created = 0
        skipped = 0
        now = datetime.utcnow()

        for entry in players:
            name = entry["name"]
            position = entry["position"]

            db_player = self._find_player(name, position)
            if db_player is None:
                if not create_missing:
                    skipped += 1
                    continue
                db_player = self._create_minimal_player(entry)
                created += 1

            season = (
                self.db.query(DBPlayerSeasonStats)
                .filter_by(player_id=db_player.id, year=year)
                .first()
            )
            if not season:
                season = DBPlayerSeasonStats(player_id=db_player.id, year=year)
                self.db.add(season)

            season.adp = entry["adp"]
            season.adp_source = self.ADP_SOURCE_LABEL
            season.adp_stdev = entry.get("stdev")
            season.adp_high = _pick_to_overall(entry.get("high"), num_teams)
            season.adp_low = _pick_to_overall(entry.get("low"), num_teams)
            season.adp_times_drafted = entry.get("times_drafted")
            season.updated_at = now
            imported += 1

        self.db.commit()

        return {
            "imported": imported,
            "created": created,
            "skipped": skipped,
            "total": len(players),
            "source": self.ADP_SOURCE_LABEL,
            "last_updated": now.isoformat(),
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
            ``adp_stdev``, ``headshot_url``, ordered by ADP ascending.
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
                "adp_stdev": season.adp_stdev,
                "headshot_url": player.headshot_url or "",
            }
            for player, season in rows
            if player.position in ("QB", "RB", "WR", "TE", "K", "DEF")
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_player(self, name: str, position: str) -> Optional[DBPlayer]:
        """Find a DBPlayer by name and position.

        Tries an exact case-insensitive name match first, then a
        normalized match (punctuation and Jr/Sr/III-style suffixes
        stripped) so FFC spellings like "AJ Brown" still find
        "A.J. Brown".
        """
        position = position.upper().strip()
        exact = (
            self.db.query(DBPlayer)
            .filter(
                DBPlayer.name.ilike(name.strip()),
                DBPlayer.position == position,
            )
            .first()
        )
        if exact is not None:
            return exact

        target = _normalize_name(name)
        candidates = (
            self.db.query(DBPlayer).filter(DBPlayer.position == position).all()
        )
        for player in candidates:
            if _normalize_name(player.name) == target:
                return player
        return None

    def _create_minimal_player(self, entry: Dict[str, Any]) -> DBPlayer:
        """Create a minimal DBPlayer row for an unmatched FFC entry.

        The ``ffc_`` prefix on ``player_id`` keeps FFC-created rows
        identifiable (and cleanable) later.
        """
        ffc_id = entry.get("ffc_id")
        slug = re.sub(r"[^a-z0-9]+", "-", entry["name"].lower()).strip("-")
        player = DBPlayer(
            player_id=f"ffc_{ffc_id or slug}",
            name=entry["name"],
            position=entry["position"],
            nfl_team=entry.get("team") or "FA",
            projected_points=0.0,
        )
        self.db.add(player)
        self.db.flush()  # assign player.id for the season-stats FK
        return player
