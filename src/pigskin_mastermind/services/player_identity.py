"""Canonical player identity across ESPN, nfl_data_py, and FFC.

Three importers create ``DBPlayer`` rows under three prefixes — ``espn_<id>``,
``nfl_<gsis_id>``, ``ffc_<id>`` — and nothing ever joined them, so the same
human could exist three times with the stats split between the copies.  This
module is the single place that decides "which row is this player?".

Resolution order, cheapest first:

1. an exact per-source ID column (``espn_id`` / ``gsis_id`` / ``pfr_id``)
2. the legacy prefixed ``player_id``
3. nflverse's cross-ID table (``nfl_data_py.import_ids()``), which maps
   gsis ↔ espn ↔ pfr, translated into whichever ID we already have a row for
4. normalized name + position

:meth:`PlayerIdentityService.merge_duplicates` folds the existing duplicates
together and is safe to re-run after every import.

Usage
-----
>>> from pigskin_mastermind.services.player_identity import PlayerIdentityService
>>> svc = PlayerIdentityService(db_session)
>>> player = svc.resolve(gsis_id="00-0033873")
>>> report = svc.merge_duplicates(dry_run=True)
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
    DBWeeklyPlayerStats,
)
from pigskin_mastermind.utils.nfl_teams import normalize_team
from pigskin_mastermind.utils.positions import normalize_position

logger = logging.getLogger(__name__)

# Generational suffixes ignored when matching player names.
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Placeholder names written by older importers that had no name column to read.
PLACEHOLDER_NAMES = {"unknown", "", "none"}


def normalize_name(name: str) -> str:
    """Normalize a player name for fuzzy matching.

    Lowercases, strips punctuation, collapses whitespace, and drops
    generational suffixes so e.g. "A.J. Brown" == "AJ Brown" and
    "Aaron Jones Sr." == "Aaron Jones".
    """
    if not name:
        return ""
    cleaned = re.sub(r"[.'’-]", "", name.lower())
    parts = [w for w in cleaned.split() if w not in NAME_SUFFIXES]
    return " ".join(parts)


def is_placeholder_name(name: Optional[str]) -> bool:
    """True when *name* is an importer placeholder rather than a real name."""
    return (name or "").strip().lower() in PLACEHOLDER_NAMES


@dataclass
class MergeReport:
    """Outcome of :meth:`PlayerIdentityService.merge_duplicates`."""

    dry_run: bool = True
    merged: int = 0
    deleted_empty: int = 0
    unresolved: int = 0
    season_rows_moved: int = 0
    season_collisions: int = 0
    game_log_rows_moved: int = 0
    game_log_collisions: int = 0
    weekly_rows_moved: int = 0
    weekly_collisions: int = 0
    fields_backfilled: int = 0
    details: List[str] = field(default_factory=list)

    def summary(self) -> str:
        mode = "DRY RUN — nothing written" if self.dry_run else "applied"
        return (
            f"Identity merge ({mode}):\n"
            f"  players merged into an existing row : {self.merged}\n"
            f"  empty placeholder rows deleted      : {self.deleted_empty}\n"
            f"  duplicates left alone (unresolved)  : {self.unresolved}\n"
            f"  season stat rows moved / collided   : {self.season_rows_moved} / {self.season_collisions}\n"
            f"  game log rows moved / collided      : {self.game_log_rows_moved} / {self.game_log_collisions}\n"
            f"  weekly stat rows moved / collided   : {self.weekly_rows_moved} / {self.weekly_collisions}\n"
            f"  profile fields backfilled           : {self.fields_backfilled}"
        )


# Season-stat columns that nfl_data_py knows and ESPN does not. When a merge
# collides, these come from the nflverse row even if the ESPN row wins overall.
_NFL_PREFERRED_SEASON_FIELDS = (
    "air_yards", "yac", "wopr", "snap_count", "snap_pct",
)

# Season-stat columns only ESPN / FFC populate.
_ESPN_PREFERRED_SEASON_FIELDS = (
    "adp", "adp_source", "adp_stdev", "adp_high", "adp_low", "adp_times_drafted",
    "pass_rating",
)

#: ADP source whose values are a sort key, not an observed draft position.
#: Anything real outranks it. Defined here so both the merger and ADPService
#: agree on the label without importing each other.
ESPN_TAIL_ADP_SOURCE = "espn_tail"

# The ADP field group, moved together so a value never separates from its source.
_ADP_FIELDS = (
    "adp", "adp_source", "adp_stdev", "adp_high", "adp_low", "adp_times_drafted",
)

_SEASON_STAT_FIELDS = (
    "games_played", "pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int",
    "rush_att", "rush_yd", "rush_td", "rush_fumbles", "targets", "rec",
    "rec_yd", "rec_td", "fantasy_points_total", "fantasy_points_avg",
    "fantasy_points_per_touch",
)

_GAME_LOG_FIELDS = (
    "opponent", "pass_att", "pass_cmp", "pass_yd", "pass_td", "pass_int",
    "rush_att", "rush_yd", "rush_td", "targets", "rec", "rec_yd", "rec_td",
    "fumbles", "fumbles_lost", "two_pt_conversions", "fantasy_points",
)

# Profile fields copied from a losing row onto the survivor when the survivor
# has nothing there.
_PROFILE_FIELDS = (
    "headshot_url", "bye_week", "injury_status", "jersey", "age", "height",
    "weight", "college", "years_exp", "draft_number", "pos_rank",
    "percent_owned", "percent_started",
)


def _is_empty(value: Any) -> bool:
    """True when a column holds no real information."""
    return value is None or value == 0 or value == 0.0 or value == ""


class PlayerIdentityService:
    """Resolves and de-duplicates :class:`DBPlayer` rows across data sources.

    Parameters
    ----------
    db : sqlalchemy.orm.Session
        Active SQLAlchemy session used for reads and writes.
    """

    def __init__(self, db: Session) -> None:
        self.db = db
        self._id_map: Optional[Dict[str, Dict[str, Any]]] = None

    # ------------------------------------------------------------------
    # Cross-source ID map
    # ------------------------------------------------------------------

    def _load_id_map(self) -> Dict[str, Dict[str, Any]]:
        """Load and cache nflverse's cross-ID table, keyed by every known ID.

        Returns an empty map when ``nfl_data_py`` is unavailable or the fetch
        fails — resolution then falls back to name matching rather than raising.
        """
        if self._id_map is not None:
            return self._id_map

        self._id_map = {}
        try:
            import nfl_data_py as nfl

            frame = nfl.import_ids()
        except Exception as exc:  # network, missing package, schema drift
            logger.warning("Cross-ID map unavailable (%s); falling back to name matching", exc)
            return self._id_map

        for row in frame.to_dict("records"):
            entry = {
                "gsis_id": _clean_id(row.get("gsis_id")),
                "espn_id": _clean_id(row.get("espn_id")),
                "pfr_id": _clean_id(row.get("pfr_id")),
                "name": row.get("name") or row.get("merge_name"),
                "position": normalize_position(row.get("position")),
            }
            for key in ("gsis_id", "espn_id", "pfr_id"):
                value = entry[key]
                if value:
                    self._id_map.setdefault(f"{key}:{value}", entry)

        logger.info("Loaded cross-ID map with %d keys", len(self._id_map))
        return self._id_map

    def lookup_ids(
        self,
        *,
        gsis_id: Optional[str] = None,
        espn_id: Optional[str] = None,
        pfr_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return the cross-ID entry for whichever ID is supplied, if known."""
        id_map = self._load_id_map()
        for key, value in (("gsis_id", gsis_id), ("espn_id", espn_id), ("pfr_id", pfr_id)):
            cleaned = _clean_id(value)
            if cleaned:
                entry = id_map.get(f"{key}:{cleaned}")
                if entry:
                    return entry
        return None

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def resolve(
        self,
        *,
        espn_id: Optional[str] = None,
        gsis_id: Optional[str] = None,
        pfr_id: Optional[str] = None,
        name: Optional[str] = None,
        position: Optional[str] = None,
        nfl_team: Optional[str] = None,
    ) -> Optional[DBPlayer]:
        """Find the existing ``DBPlayer`` for this person, or ``None``.

        Every importer should call this before creating a row. Any combination
        of identifiers may be supplied; more is better.

        Passing *nfl_team* additionally lets team defenses resolve — see
        :meth:`find_defense`.
        """
        espn_id = _clean_id(espn_id)
        gsis_id = _clean_id(gsis_id)
        pfr_id = _clean_id(pfr_id)

        # 0. A team defense is its team. Every source names them differently,
        #    so name matching can never work — check this before anything else.
        defense = self.find_defense(position, nfl_team)
        if defense is not None:
            return defense

        # 1. Direct hit on a stored per-source ID.
        found = self._by_id_columns(espn_id=espn_id, gsis_id=gsis_id, pfr_id=pfr_id)
        if found is not None:
            return found

        # 2. Legacy prefixed player_id, for rows predating the ID columns.
        for candidate in _legacy_player_ids(espn_id=espn_id, gsis_id=gsis_id, pfr_id=pfr_id):
            player = self.db.query(DBPlayer).filter_by(player_id=candidate).first()
            if player is not None:
                return player

        # 3. Translate through the cross-ID map and retry the ID columns.
        entry = self.lookup_ids(gsis_id=gsis_id, espn_id=espn_id, pfr_id=pfr_id)
        if entry is not None:
            found = self._by_id_columns(
                espn_id=entry.get("espn_id"),
                gsis_id=entry.get("gsis_id"),
                pfr_id=entry.get("pfr_id"),
            )
            if found is not None:
                return found
            for candidate in _legacy_player_ids(
                espn_id=entry.get("espn_id"),
                gsis_id=entry.get("gsis_id"),
                pfr_id=entry.get("pfr_id"),
            ):
                player = self.db.query(DBPlayer).filter_by(player_id=candidate).first()
                if player is not None:
                    return player
            # The map can supply a name when the caller had none.
            name = name or entry.get("name")
            position = position or entry.get("position")

        # 4. Name + position.
        return self.find_by_name(name, position)

    def canonicalize_positions(self) -> int:
        """Rewrite non-canonical fantasy positions across the players table.

        ESPN's ``D/ST`` spelling is the one that matters: a defense stored that
        way is filtered out of the draft pool, so it can never be drafted, and
        :meth:`find_defense` cannot match it either.

        Positions that normalize to ``None`` are left alone — a ``DT`` is a real
        player at a non-fantasy position, not a mislabeled team defense.

        Returns the number of rows changed.
        """
        fixed = 0
        for player in self.db.query(DBPlayer).filter(DBPlayer.position.isnot(None)).all():
            canonical = normalize_position(player.position)
            if canonical is not None and canonical != player.position:
                player.position = canonical
                fixed += 1
        if fixed:
            self.db.flush()
        return fixed

    def find_defense(
        self,
        position: Optional[str],
        nfl_team: Optional[str],
        exclude_id: Optional[int] = None,
    ) -> Optional[DBPlayer]:
        """Find a team defense by its NFL team, or ``None``.

        Defenses are the one case where name matching is hopeless: ESPN calls
        Atlanta's "Falcons D/ST", FFC calls it "Atlanta Defense", and nflverse's
        cross-ID table has no entry at all. There is exactly one defense per
        team, so the team *is* the identity.

        Returns ``None`` for any non-DEF position, so two running backs on the
        same team never collapse into each other.

        *exclude_id* skips a specific row — needed when looking for the survivor
        of a duplicate, which would otherwise match itself.
        """
        if normalize_position(position) != 'DEF':
            return None

        canonical_team = normalize_team(nfl_team)
        if canonical_team is None:
            return None

        for player in self.db.query(DBPlayer).filter(DBPlayer.position == 'DEF').all():
            if player.id == exclude_id:
                continue
            if normalize_team(player.nfl_team) == canonical_team:
                return player
        return None

    def find_by_name(
        self, name: Optional[str], position: Optional[str] = None
    ) -> Optional[DBPlayer]:
        """Find a player by exact then normalized name, optionally filtered by position."""
        if not name or is_placeholder_name(name):
            return None

        canonical = normalize_position(position)
        query = self.db.query(DBPlayer).filter(DBPlayer.name.ilike(name.strip()))
        if canonical:
            query = query.filter(DBPlayer.position == canonical)
        exact = query.first()
        if exact is not None:
            return exact

        target = normalize_name(name)
        candidates = self.db.query(DBPlayer)
        if canonical:
            candidates = candidates.filter(DBPlayer.position == canonical)
        for player in candidates.all():
            if normalize_name(player.name) == target:
                return player
        return None

    def stamp_ids(
        self,
        player: DBPlayer,
        *,
        espn_id: Optional[str] = None,
        gsis_id: Optional[str] = None,
        pfr_id: Optional[str] = None,
    ) -> DBPlayer:
        """Record any per-source IDs we now know for *player*.

        Fills the cross-ID columns from nflverse too, so a player first seen
        through ESPN can later be found by gsis or pfr id.
        """
        espn_id = _clean_id(espn_id) or player.espn_id
        gsis_id = _clean_id(gsis_id) or player.gsis_id
        pfr_id = _clean_id(pfr_id) or player.pfr_id

        entry = self.lookup_ids(gsis_id=gsis_id, espn_id=espn_id, pfr_id=pfr_id)
        if entry is not None:
            espn_id = espn_id or entry.get("espn_id")
            gsis_id = gsis_id or entry.get("gsis_id")
            pfr_id = pfr_id or entry.get("pfr_id")

        player.espn_id = espn_id
        player.gsis_id = gsis_id
        player.pfr_id = pfr_id
        return player

    def _by_id_columns(
        self,
        *,
        espn_id: Optional[str] = None,
        gsis_id: Optional[str] = None,
        pfr_id: Optional[str] = None,
    ) -> Optional[DBPlayer]:
        for column, value in (
            (DBPlayer.espn_id, _clean_id(espn_id)),
            (DBPlayer.gsis_id, _clean_id(gsis_id)),
            (DBPlayer.pfr_id, _clean_id(pfr_id)),
        ):
            if value:
                player = self.db.query(DBPlayer).filter(column == value).first()
                if player is not None:
                    return player
        return None

    # ------------------------------------------------------------------
    # De-duplication
    # ------------------------------------------------------------------

    def merge_duplicates(
        self, dry_run: bool = True, position: Optional[str] = None
    ) -> MergeReport:
        """Fold ``nfl_*`` and ``ffc_*`` rows into their ESPN counterparts.

        For each non-ESPN row this re-points ``player_season_stats``,
        ``player_game_logs``, and ``weekly_player_stats`` at the surviving row,
        merges colliding stat rows field-by-field, backfills missing profile
        fields, and deletes the emptied duplicate.

        Rows with no counterpart are left alone unless they are unnamed
        placeholders carrying no stats at all, which are deleted.

        Parameters
        ----------
        dry_run : bool
            When True (default) the transaction is rolled back and only the
            report is returned.
        position : str, optional
            Restrict the merge to one fantasy position, so a targeted cleanup
            (say, duplicate team defenses) can run without touching unrelated
            duplicates. Accepts source spellings — ``D/ST`` selects ``DEF``.
        """
        report = MergeReport(dry_run=dry_run)

        # Defenses can only be matched once their positions are canonical —
        # find_defense looks for DEF, and ESPN's rows arrive spelled D/ST.
        self.canonicalize_positions()

        query = self.db.query(DBPlayer).filter(
            DBPlayer.player_id.like("nfl\\_%", escape="\\")
            | DBPlayer.player_id.like("ffc\\_%", escape="\\")
        )
        wanted = normalize_position(position) if position else None
        if wanted is not None:
            # Positions are canonical by now, so filtering on the canonical
            # value catches rows that arrived spelled D/ST.
            query = query.filter(DBPlayer.position == wanted)
        duplicates = query.all()

        for duplicate in duplicates:
            survivor = self._find_survivor(duplicate)

            if survivor is None:
                if self._has_no_data(duplicate) and is_placeholder_name(duplicate.name):
                    report.deleted_empty += 1
                    report.details.append(f"delete empty placeholder {duplicate.player_id}")
                    self.db.delete(duplicate)
                else:
                    report.unresolved += 1
                    # Still worth stamping IDs so the next import resolves it.
                    self.stamp_ids(duplicate)
                    self._repair_placeholder(duplicate)
                continue

            self._move_season_stats(duplicate, survivor, report)
            self._move_game_logs(duplicate, survivor, report)
            self._move_weekly_stats(duplicate, survivor, report)
            report.fields_backfilled += self._backfill_profile(duplicate, survivor)
            self.stamp_ids(
                survivor,
                espn_id=survivor.espn_id or duplicate.espn_id,
                gsis_id=survivor.gsis_id or duplicate.gsis_id,
                pfr_id=survivor.pfr_id or duplicate.pfr_id,
            )

            report.merged += 1
            report.details.append(
                f"merge {duplicate.player_id} ({duplicate.name}) -> {survivor.player_id} ({survivor.name})"
            )
            # DBPlayer's stat relationships cascade "all, delete-orphan". The FK
            # updates above are still pending, so deleting now would cascade
            # into the rows we just moved. Flush the re-points, then expire the
            # duplicate so its collections reload as empty before the delete.
            self.db.flush()
            self.db.expire(duplicate)
            self.db.delete(duplicate)

        self.db.flush()
        if dry_run:
            self.db.rollback()
        else:
            self.db.commit()
        return report

    def _find_survivor(self, duplicate: DBPlayer) -> Optional[DBPlayer]:
        """Find the row *duplicate* should be folded into (never itself)."""
        entry = self.lookup_ids(
            gsis_id=duplicate.gsis_id,
            espn_id=duplicate.espn_id,
            pfr_id=duplicate.pfr_id,
        )

        candidates: List[Optional[DBPlayer]] = [
            # A defense is its team; its name never matches across sources.
            self.find_defense(duplicate.position, duplicate.nfl_team, exclude_id=duplicate.id),
        ]
        if entry is not None:
            candidates.append(
                self._by_id_columns(
                    espn_id=entry.get("espn_id"),
                    gsis_id=entry.get("gsis_id"),
                    pfr_id=entry.get("pfr_id"),
                )
            )
            espn_id = _clean_id(entry.get("espn_id"))
            if espn_id:
                candidates.append(
                    self.db.query(DBPlayer).filter_by(player_id=f"espn_{espn_id}").first()
                )

        name = duplicate.name
        position = duplicate.position
        if (is_placeholder_name(name) or not normalize_position(position)) and entry is not None:
            name = entry.get("name") or name
            position = entry.get("position") or position
        candidates.append(self.find_by_name(name, position))

        for candidate in candidates:
            if candidate is not None and candidate.id != duplicate.id:
                return candidate
        return None

    def _repair_placeholder(self, player: DBPlayer) -> None:
        """Give an unmergeable ``Unknown`` row a real name/position if we can."""
        entry = self.lookup_ids(
            gsis_id=player.gsis_id, espn_id=player.espn_id, pfr_id=player.pfr_id
        )
        if entry is None:
            return
        if is_placeholder_name(player.name) and entry.get("name"):
            player.name = entry["name"]
        if not normalize_position(player.position) and entry.get("position"):
            player.position = entry["position"]

    def _has_no_data(self, player: DBPlayer) -> bool:
        """True when nothing references this player row."""
        for model in (DBPlayerSeasonStats, DBPlayerGameLog, DBWeeklyPlayerStats):
            if self.db.query(model).filter_by(player_id=player.id).first() is not None:
                return False
        return True

    def _move_season_stats(
        self, duplicate: DBPlayer, survivor: DBPlayer, report: MergeReport
    ) -> None:
        rows = self.db.query(DBPlayerSeasonStats).filter_by(player_id=duplicate.id).all()
        for row in rows:
            existing = (
                self.db.query(DBPlayerSeasonStats)
                .filter_by(player_id=survivor.id, year=row.year)
                .first()
            )
            if existing is None:
                row.player_id = survivor.id
                report.season_rows_moved += 1
                continue

            # uq_player_season collision: fold the loser's values into the
            # keeper rather than dropping the row silently.
            report.season_collisions += 1
            report.details.append(
                f"  season collision {duplicate.player_id} y={row.year} -> merged into survivor"
            )
            for name_ in _NFL_PREFERRED_SEASON_FIELDS:
                if not _is_empty(getattr(row, name_, None)):
                    setattr(existing, name_, getattr(row, name_))
            # Real consensus ADP outranks a synthetic tail value, which is only
            # a sort key. Without this the "fill empties only" rule below would
            # keep the placeholder purely because it got there first.
            if (
                existing.adp_source == ESPN_TAIL_ADP_SOURCE
                and row.adp_source not in (None, ESPN_TAIL_ADP_SOURCE)
            ):
                for name_ in _ADP_FIELDS:
                    setattr(existing, name_, getattr(row, name_, None))
            for name_ in _ESPN_PREFERRED_SEASON_FIELDS + _SEASON_STAT_FIELDS:
                if _is_empty(getattr(existing, name_, None)) and not _is_empty(getattr(row, name_, None)):
                    setattr(existing, name_, getattr(row, name_))
            existing.updated_at = datetime.utcnow()
            self.db.delete(row)

    def _move_game_logs(
        self, duplicate: DBPlayer, survivor: DBPlayer, report: MergeReport
    ) -> None:
        rows = self.db.query(DBPlayerGameLog).filter_by(player_id=duplicate.id).all()
        for row in rows:
            existing = (
                self.db.query(DBPlayerGameLog)
                .filter_by(player_id=survivor.id, year=row.year, week=row.week)
                .first()
            )
            if existing is None:
                row.player_id = survivor.id
                report.game_log_rows_moved += 1
                continue

            report.game_log_collisions += 1
            for name_ in _GAME_LOG_FIELDS:
                if _is_empty(getattr(existing, name_, None)) and not _is_empty(getattr(row, name_, None)):
                    setattr(existing, name_, getattr(row, name_))
            existing.updated_at = datetime.utcnow()
            self.db.delete(row)

    def _move_weekly_stats(
        self, duplicate: DBPlayer, survivor: DBPlayer, report: MergeReport
    ) -> None:
        rows = self.db.query(DBWeeklyPlayerStats).filter_by(player_id=duplicate.id).all()
        for row in rows:
            existing = (
                self.db.query(DBWeeklyPlayerStats)
                .filter_by(
                    player_id=survivor.id,
                    weekly_team_stats_id=row.weekly_team_stats_id,
                )
                .first()
            )
            if existing is None:
                row.player_id = survivor.id
                report.weekly_rows_moved += 1
                continue

            report.weekly_collisions += 1
            self.db.delete(row)

    def _backfill_profile(self, duplicate: DBPlayer, survivor: DBPlayer) -> int:
        """Copy profile fields the survivor is missing. Returns the field count."""
        filled = 0
        for name_ in _PROFILE_FIELDS:
            if _is_empty(getattr(survivor, name_, None)) and not _is_empty(getattr(duplicate, name_, None)):
                setattr(survivor, name_, getattr(duplicate, name_))
                filled += 1
        if filled:
            survivor.profile_updated_at = datetime.utcnow()
        return filled

    # ------------------------------------------------------------------
    # Backfill
    # ------------------------------------------------------------------

    def backfill_id_columns(self) -> int:
        """Populate ``espn_id`` / ``gsis_id`` / ``pfr_id`` for every player.

        Seeds from the legacy ``player_id`` prefix, then fans out through the
        cross-ID map. Returns the number of rows updated.
        """
        updated = 0
        for player in self.db.query(DBPlayer).all():
            before = (player.espn_id, player.gsis_id, player.pfr_id)

            if not player.espn_id and player.player_id.startswith("espn_"):
                player.espn_id = player.player_id[5:]
            if not player.gsis_id and player.player_id.startswith("nfl_"):
                player.gsis_id = player.player_id[4:]

            self.stamp_ids(player)
            if (player.espn_id, player.gsis_id, player.pfr_id) != before:
                updated += 1

        self.db.commit()
        return updated


def _clean_id(value: Any) -> Optional[str]:
    """Coerce an ID from any source into a comparable string, or ``None``.

    nflverse stores numeric IDs as floats, so ``4362238.0`` has to become
    ``"4362238"`` to match ESPN's ``"4362238"``.
    """
    if value is None:
        return None
    if isinstance(value, float):
        if value != value:  # NaN
            return None
        if value.is_integer():
            return str(int(value))
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _legacy_player_ids(
    *,
    espn_id: Optional[str] = None,
    gsis_id: Optional[str] = None,
    pfr_id: Optional[str] = None,
) -> Tuple[str, ...]:
    """Prefixed ``player_id`` values that could hold this player, most trusted first."""
    candidates = []
    if espn_id:
        candidates.append(f"espn_{espn_id}")
    if gsis_id:
        candidates.append(f"nfl_{gsis_id}")
    if pfr_id:
        candidates.append(f"nfl_{pfr_id}")
    return tuple(candidates)
