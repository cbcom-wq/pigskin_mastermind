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

from sqlalchemy import func
from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayer,
    DBPlayerProjection,
    DBPlayerSeasonStats,
)
from pigskin_mastermind.services.mock_draft import fetch_espn_adp
from pigskin_mastermind.services.player_identity import (
    ESPN_TAIL_ADP_SOURCE,
    PlayerIdentityService,
)
from pigskin_mastermind.utils.nfl_teams import normalize_team
from pigskin_mastermind.utils.positions import FANTASY_POSITIONS, normalize_position
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

# Minimum games before a season's per-game average is trusted as a projection.
_MIN_GAMES_FOR_AVERAGE = 4

#: Draft data older than this prompts the user to refresh. Lives here so the
#: server and the draft page cannot disagree about what "out of date" means.
STALE_AFTER_DAYS = 7


def _strip_espn_prefix(player_id: Optional[str]) -> Optional[str]:
    """Turn a pool id like ``espn_4047646`` back into the bare ESPN id."""
    if not player_id:
        return None
    text = str(player_id)
    return text[5:] if text.startswith("espn_") else text


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
    #: Players below FFC's board, backfilled from ESPN with a synthetic ADP.
    ESPN_TAIL_SOURCE_LABEL = ESPN_TAIL_ADP_SOURCE
    #: Every source the draft pool draws from, best consensus data first.
    DRAFT_POOL_SOURCES = (ADP_SOURCE_LABEL, ESPN_TAIL_SOURCE_LABEL)

    def __init__(self, db: Session) -> None:
        self.db = db
        self.identity = PlayerIdentityService(db)

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
            ``total``, ``source``, ``last_updated``, and ``team_changes``
            (a list of ``{name, old, new}`` for players whose NFL team moved).
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
                "team_changes": [],
                "error": "Failed to fetch data from Fantasy Football Calculator",
            }

        imported = 0
        created = 0
        skipped = 0
        team_changes: List[Dict[str, str]] = []
        now = datetime.utcnow()

        for entry in players:
            name = entry["name"]
            position = entry["position"]

            db_player = self._find_player(name, position, entry.get("team"))
            if db_player is None:
                if not create_missing:
                    skipped += 1
                    continue
                db_player = self._create_minimal_player(entry)
                created += 1
            else:
                # Rosters move in the offseason. Without this, an existing row
                # keeps whatever the last ESPN sync wrote — the stale-team bug.
                change = self._apply_team_update(db_player, entry.get("team"))
                if change:
                    team_changes.append(change)

            # FFC ships the bye week alongside ADP — the only free source we
            # have for players who were never on an ESPN roster.
            bye = entry.get("bye")
            if bye:
                try:
                    db_player.bye_week = int(bye)
                    db_player.profile_updated_at = now
                except (TypeError, ValueError):
                    pass

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
            "team_changes": team_changes,
        }

    def refresh_draft_data(
        self,
        year: Optional[int] = None,
        scoring: str = "ppr",
        num_teams: int = 12,
    ) -> Dict[str, Any]:
        """Refresh everything the draft pool depends on, in one action.

        Runs FFC first (real consensus ADP for the top of the board), then the
        ESPN tail (depth, and the more complete roster feed). Order matters for
        teams: ESPN runs second, so where the two disagree ESPN's value is what
        persists.

        The ESPN leg is **non-fatal**. It targets an undocumented public
        endpoint, and a refresh must never leave the pool worse than it started
        — a failure there yields a shallower board, not a broken one.

        Returns
        -------
        dict
            The FFC summary, plus ``tail_imported`` / ``tail_created`` /
            ``tail_total``, a merged ``team_changes``, ``teams_canonicalized``,
            and ``tail_error`` when the ESPN leg failed.
        """
        year = year or current_fantasy_season()

        result = self.import_from_ffc(year=year, scoring=scoring, num_teams=num_teams)
        if result.get("error"):
            return result

        tail = self.import_espn_tail(year=year)

        changes: Dict[str, Dict[str, str]] = {}
        for change in list(result.get("team_changes", [])) + list(tail.get("team_changes", [])):
            existing = changes.get(change["name"])
            if existing is None:
                changes[change["name"]] = change
            else:
                # Same player corrected twice — keep the original "old" so the
                # report reads from where they started, not from FFC's guess.
                existing["new"] = change["new"]

        result["team_changes"] = [c for c in changes.values() if c["old"] != c["new"]]
        result["tail_imported"] = tail.get("imported", 0)
        result["tail_created"] = tail.get("created", 0)
        result["tail_total"] = tail.get("total", 0)
        if tail.get("error"):
            result["tail_error"] = tail["error"]

        result["teams_canonicalized"] = self.canonicalize_stored_teams()
        return result

    def import_espn_tail(
        self,
        year: Optional[int] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """Backfill the draft pool below FFC's board using ESPN's deeper list.

        FFC's API returns roughly 250 players no matter what league size you
        ask for, but a 12-team 15-round draft is 180 picks — so the pool runs
        dry in the late rounds. ESPN's board carries ~1000 players.

        Two things are deliberately decoupled here:

        * **Team updates apply to every ESPN player**, including those already
          on the FFC board. ESPN's ``proTeamId`` is the most complete roster
          feed we have, and skipping the FFC players would leave the top ~250
          — the ones most visible in a draft — on stale teams.
        * **ADP rows are written only for players FFC did not rank**, so real
          consensus ADP is never overwritten with a synthetic value.

        Tail ADP is ``max_ffc_adp + rank``, ordered by ESPN's projection rather
        than its ADP: ESPN reports a placeholder ADP (~170) for hundreds of
        undrafted players, so their ADP cannot order them. The resulting value
        is a sort key, not a claim about real draft position — the
        ``espn_tail`` ``adp_source`` is what tells the two apart.

        Returns
        -------
        dict
            ``imported``, ``created``, ``total``, ``source``, ``team_changes``,
            and ``error`` (set when ESPN is unreachable).
        """
        year = year or current_fantasy_season()
        board = fetch_espn_adp(year=year, limit=limit)
        if not board:
            return {
                "imported": 0,
                "created": 0,
                "total": 0,
                "source": self.ESPN_TAIL_SOURCE_LABEL,
                "team_changes": [],
                "error": "Failed to fetch player board from ESPN",
            }

        ranked_player_ids = {
            row.player_id
            for row in self.db.query(DBPlayerSeasonStats.player_id)
            .filter(
                DBPlayerSeasonStats.year == year,
                DBPlayerSeasonStats.adp.isnot(None),
                DBPlayerSeasonStats.adp_source == self.ADP_SOURCE_LABEL,
            )
            .all()
        }
        max_ffc_adp = (
            self.db.query(func.max(DBPlayerSeasonStats.adp))
            .filter(
                DBPlayerSeasonStats.year == year,
                DBPlayerSeasonStats.adp_source == self.ADP_SOURCE_LABEL,
            )
            .scalar()
        ) or 0.0

        imported = 0
        created = 0
        team_changes: List[Dict[str, str]] = []
        now = datetime.utcnow()

        # Best projection first — ESPN's tied placeholder ADP can't order these.
        ordered = sorted(
            board,
            key=lambda entry: float(entry.get("projected_points") or 0.0),
            reverse=True,
        )

        tail_rank = 0
        for entry in ordered:
            position = normalize_position(entry.get("position"))
            if not position:
                continue

            db_player = self.identity.resolve(
                espn_id=_strip_espn_prefix(entry.get("id")),
                name=entry.get("name"),
                position=position,
                nfl_team=entry.get("nfl_team"),
            )
            if db_player is None:
                db_player = self._create_player_from_espn(entry, position)
                created += 1

            # Applies to FFC-board players too — see the docstring.
            change = self._apply_team_update(db_player, entry.get("nfl_team"))
            if change:
                team_changes.append(change)

            if db_player.id in ranked_player_ids:
                continue  # FFC already ranked them; leave consensus ADP alone.

            if db_player.position not in FANTASY_POSITIONS:
                # ESPN's board occasionally returns an IDP under a fantasy slot
                # id, and resolving by espn_id then lands on a row stored at a
                # real defensive position like 'DT'. get_adp_for_draft_pool
                # filters those out, so writing ADP here only creates a season
                # row nothing can ever draft.
                continue

            tail_rank += 1
            season = (
                self.db.query(DBPlayerSeasonStats)
                .filter_by(player_id=db_player.id, year=year)
                .first()
            )
            if not season:
                season = DBPlayerSeasonStats(player_id=db_player.id, year=year)
                self.db.add(season)

            season.adp = round(max_ffc_adp + tail_rank, 2)
            season.adp_source = self.ESPN_TAIL_SOURCE_LABEL
            season.updated_at = now
            imported += 1

        self.db.commit()

        return {
            "imported": imported,
            "created": created,
            "total": len(board),
            "source": self.ESPN_TAIL_SOURCE_LABEL,
            "team_changes": team_changes,
            "last_updated": now.isoformat(),
        }

    ESPN_PROJECTION_SOURCE = "espn"

    def import_espn_projections(
        self,
        year: Optional[int] = None,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """Persist ESPN's season point projections from the public board.

        ESPN's ``ratings["0"].totalRating`` is a genuine season total -- the
        2026 board returns 416.6 for McCaffrey and 375.0 for Nacua. The app
        never used it: ``_create_player_from_espn`` writes it to
        ``DBPlayer.projected_points`` but only for players it *creates*, so
        every established star kept the per-game value espn_sync wrote, and
        most kept 0.0.

        Writes season-scope rows (``week=None``) at season scale.
        """
        year = year or current_fantasy_season()
        board = fetch_espn_adp(year=year, limit=limit)
        if not board:
            return {
                "imported": 0, "skipped": 0, "total": 0, "year": year,
                "error": "Failed to fetch player board from ESPN",
            }

        now = datetime.utcnow()
        imported = 0
        skipped = 0

        for entry in board:
            points = float(entry.get("projected_points") or 0.0)
            if points <= 0:
                # ESPN reports 0.0 for players it has no projection for.
                # Storing that would rank them as genuinely worthless.
                skipped += 1
                continue

            position = normalize_position(entry.get("position"))
            if not position:
                skipped += 1
                continue

            player = self.identity.resolve(
                espn_id=_strip_espn_prefix(entry.get("id")),
                name=entry.get("name"),
                position=position,
                nfl_team=normalize_team(entry.get("nfl_team")),
            )
            if player is None:
                skipped += 1
                continue

            row = (
                self.db.query(DBPlayerProjection)
                .filter_by(
                    player_id=player.id, year=year, week=None,
                    source=self.ESPN_PROJECTION_SOURCE,
                )
                .first()
            )
            if row is None:
                row = DBPlayerProjection(
                    player_id=player.id, year=year, week=None,
                    source=self.ESPN_PROJECTION_SOURCE,
                )
                self.db.add(row)

            row.projected_points = points
            row.computed_at = now
            row.components = {"espn_total_rating": points,
                              "espn_adp": entry.get("adp_rank")}
            imported += 1

        self.db.commit()
        return {
            "imported": imported, "skipped": skipped,
            "total": len(board), "year": year, "error": None,
        }

    def _create_player_from_espn(self, entry: Dict[str, Any], position: str) -> DBPlayer:
        """Create a minimal ``DBPlayer`` for an ESPN board entry."""
        player = DBPlayer(
            player_id=entry.get("id") or f"espn_{entry['name']}",
            name=entry["name"],
            position=position,
            nfl_team=normalize_team(entry.get("nfl_team")) or "FA",
            projected_points=float(entry.get("projected_points") or 0.0),
        )
        self.db.add(player)
        self.db.flush()  # assign player.id for the season-stats FK
        self.identity.stamp_ids(player, espn_id=_strip_espn_prefix(entry.get("id")))
        return player

    def canonicalize_stored_teams(self) -> int:
        """Rewrite non-canonical ``nfl_team`` spellings across the players table.

        The importers only touch players their source ships. Anyone else — a
        retired player, a deep-bench body ESPN's board omits — would otherwise
        keep a stale spelling like ``WSH`` forever, and go on missing joins
        against NFL team stats.

        Values that normalize to nothing (``FA``, ``None``) are left alone:
        those are not misspellings, they are genuinely teamless players.

        Returns the number of rows changed.
        """
        fixed = 0
        for player in self.db.query(DBPlayer).filter(DBPlayer.nfl_team.isnot(None)).all():
            canonical = normalize_team(player.nfl_team)
            if canonical is not None and canonical != player.nfl_team:
                player.nfl_team = canonical
                fixed += 1

        if fixed:
            self.db.commit()
        return fixed

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
            Player dicts with ``id``, ``db_id``, ``name``, ``position``,
            ``nfl_team``, ``projected_points``, ``adp_rank``, ``adp_stdev``,
            ``headshot_url``, ``bye_week``, and ``injury_status``, ordered by
            ADP ascending.  ``db_id`` is the primary key, used by the draft
            board to link into the player profile.
        """
        query = (
            self.db.query(DBPlayer, DBPlayerSeasonStats)
            .join(DBPlayerSeasonStats, DBPlayerSeasonStats.player_id == DBPlayer.id)
            .filter(DBPlayerSeasonStats.adp.isnot(None))
            .filter(DBPlayerSeasonStats.adp_source.in_(self.DRAFT_POOL_SOURCES))
        )

        if year is not None:
            query = query.filter(DBPlayerSeasonStats.year == year)

        query = query.order_by(DBPlayerSeasonStats.adp.asc())
        rows = query.all()

        return [
            {
                "id": player.player_id,
                "db_id": player.id,
                "name": player.name,
                "position": player.position,
                "nfl_team": player.nfl_team,
                "projected_points": self._pool_projection(player),
                "adp_rank": season.adp,
                "adp_stdev": season.adp_stdev,
                "headshot_url": player.headshot_url or "",
                "bye_week": player.bye_week,
                "injury_status": player.injury_status,
            }
            for player, season in rows
            if player.position in FANTASY_POSITIONS
        ]

    def _pool_projection(self, player: DBPlayer) -> float:
        """Projected points for the draft pool, with a last-season fallback.

        Players the FFC board created have ``projected_points == 0``, and a
        pool of zeros flattens the AI drafter's projection nudge and makes the
        post-draft grade meaningless.

        The fallback must match the unit of ``DBPlayer.projected_points``,
        which ESPN populates per game — so this uses the season *average*, not
        the total. Mixing the two would hand players with a season total a
        ~17x advantage in the drafter's within-position normalization.
        """
        if player.projected_points:
            return player.projected_points

        latest = (
            self.db.query(DBPlayerSeasonStats)
            .filter(
                DBPlayerSeasonStats.player_id == player.id,
                DBPlayerSeasonStats.fantasy_points_avg > 0,
                # Some ESPN-sourced season rows record a full-season total
                # against games_played=1, which makes avg == total. Requiring a
                # real sample keeps those out of the pool.
                DBPlayerSeasonStats.games_played >= _MIN_GAMES_FOR_AVERAGE,
            )
            .order_by(DBPlayerSeasonStats.year.desc())
            .first()
        )
        return round(latest.fantasy_points_avg, 1) if latest else 0.0

    def get_adp_metadata(self, year: Optional[int] = None) -> Dict[str, Any]:
        """Return freshness info for locally stored FFC ADP data.

        Parameters
        ----------
        year : int, optional
            Season year. Defaults to the current fantasy season.

        Staleness is decided here rather than in the page so the server and
        the UI cannot disagree about what "out of date" means.

        Returns
        -------
        dict
            ``year``, ``last_updated`` (ISO string or ``None``), ``count``,
            ``ffc_count``, ``tail_count``, ``age_days`` (``None`` when nothing
            has been imported), and ``stale``.
        """
        year = year or current_fantasy_season()
        rows = (
            self.db.query(DBPlayerSeasonStats.updated_at, DBPlayerSeasonStats.adp_source)
            .filter(
                DBPlayerSeasonStats.adp.isnot(None),
                DBPlayerSeasonStats.adp_source.in_(self.DRAFT_POOL_SOURCES),
                DBPlayerSeasonStats.year == year,
            )
            .all()
        )
        timestamps = [r.updated_at for r in rows if r.updated_at is not None]
        last_updated = max(timestamps) if timestamps else None
        age_days = (
            (datetime.utcnow() - last_updated).days if last_updated is not None else None
        )

        return {
            "year": year,
            "last_updated": last_updated.isoformat() if last_updated else None,
            "count": len(rows),
            "ffc_count": sum(1 for r in rows if r.adp_source == self.ADP_SOURCE_LABEL),
            "tail_count": sum(
                1 for r in rows if r.adp_source == self.ESPN_TAIL_SOURCE_LABEL
            ),
            "age_days": age_days,
            "stale": age_days is None or age_days > STALE_AFTER_DAYS,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_team_update(
        self,
        player: DBPlayer,
        source_team: Optional[str],
    ) -> Optional[Dict[str, str]]:
        """Point *player* at *source_team*, returning the change if there was one.

        Shared by the FFC and ESPN importers so both spell teams the same way
        and report changes in the same shape.

        Two cases deliberately write without reporting a change:

        * an unusable ``source_team`` (``FA``, blank, unrecognized) leaves the
          stored value alone — an unsigned player must not wipe a good team;
        * a stored value that is merely a different spelling of the same
          franchise (``WSH`` vs ``WAS``) is canonicalized in place, because
          that is not a roster move.
        """
        canonical = normalize_team(source_team)
        if canonical is None:
            return None

        stored = player.nfl_team
        stored_canonical = normalize_team(stored)

        if stored_canonical == canonical:
            # Same franchise — rewrite only if the stored spelling was stale.
            if stored != canonical:
                player.nfl_team = canonical
            return None

        player.nfl_team = canonical
        return {"name": player.name, "old": stored, "new": canonical}

    def _find_player(
        self,
        name: str,
        position: str,
        nfl_team: Optional[str] = None,
    ) -> Optional[DBPlayer]:
        """Find a DBPlayer by name and position.

        Delegates to :class:`PlayerIdentityService` so FFC shares one matcher
        with the ESPN and nfl_data_py importers — exact name first, then a
        normalized match with punctuation and Jr/Sr/III-style suffixes stripped,
        so "AJ Brown" still finds "A.J. Brown".

        *nfl_team* additionally resolves team defenses, whose names never match
        across sources ("Atlanta Defense" vs "Falcons D/ST").
        """
        defense = self.identity.find_defense(position, nfl_team)
        if defense is not None:
            return defense
        return self.identity.find_by_name(name, position)

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
            position=normalize_position(entry["position"]) or entry["position"],
            nfl_team=normalize_team(entry.get("team")) or "FA",
            projected_points=0.0,
        )
        self.db.add(player)
        self.db.flush()  # assign player.id for the season-stats FK
        # Stamp whatever cross-source IDs nflverse knows, so the next ESPN or
        # nfl_data_py import recognizes this row instead of making another one.
        self.identity.stamp_ids(player)
        return player
