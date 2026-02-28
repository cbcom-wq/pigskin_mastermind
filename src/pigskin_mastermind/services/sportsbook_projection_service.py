"""Sportsbook-based weekly fantasy scoring projections.

Translates player prop betting lines into expected stat values and converts
those values to fantasy points using the league (or default) scoring settings.
This provides an *independent* projection source driven entirely by the
sportsbook market, complementing the criteria-based ``ProjectionService``.
"""

from statistics import median
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBLeague,
    DBSportsbookOdds,
    get_scoring_settings,
)

# ---------------------------------------------------------------------------
# Market → scoring-settings key mapping
# ---------------------------------------------------------------------------
# Each entry maps an Odds-API prop market key to a tuple of
# (scoring_settings_key, display_label).
MARKET_TO_SCORING: Dict[str, tuple] = {
    "player_pass_yds": ("pass_yd", "Passing Yards"),
    "player_pass_tds": ("pass_td", "Passing TDs"),
    "player_rush_yds": ("rush_yd", "Rushing Yards"),
    "player_rush_tds": ("rush_td", "Rushing TDs"),
    "player_reception_yds": ("rec_yd", "Receiving Yards"),
    "player_receptions": ("rec", "Receptions"),
}


class SportsbookProjectionService:
    """Build a weekly fantasy-point projection from sportsbook player props.

    The service:
    1. Queries stored ``DBSportsbookOdds`` rows for the requested player.
    2. Groups prop lines by market and takes the *median* line across
       bookmakers to arrive at a consensus expected stat value.
    3. Multiplies each expected stat by the corresponding fantasy-scoring
       weight from the league (or default 0.5-PPR) settings.
    4. Returns a total projected fantasy score along with a per-category
       breakdown.
    """

    def __init__(self, db: Session):
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def project_player(
        self,
        player_name: str,
        *,
        event_id: Optional[str] = None,
        league_id: Optional[str] = None,
        bookmaker: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate a sportsbook-derived weekly projection for *player_name*.

        Args:
            player_name: Full or partial player name (case-insensitive).
            event_id: Restrict to props from a single event / game.
            league_id: If supplied, use that league's scoring settings;
                otherwise fall back to default 0.5-PPR.
            bookmaker: Restrict to a single bookmaker's lines.

        Returns:
            A dict with ``player_name``, ``total_projected_points``, a
            ``categories`` list (one entry per prop market with the
            expected stat, multiplier, and fantasy-point contribution),
            and ``scoring_settings`` used.
        """
        scoring = self._resolve_scoring(league_id)
        props = self._fetch_props(player_name, event_id=event_id, bookmaker=bookmaker)

        # Determine the canonical player name from prop descriptions
        canonical_name = self._resolve_canonical_name(props, player_name)

        grouped = self._group_lines_by_market(props)
        categories: List[Dict[str, Any]] = []
        total = 0.0

        for market_key, lines in sorted(grouped.items()):
            mapping = MARKET_TO_SCORING.get(market_key)
            if mapping is None:
                continue  # unsupported market – skip

            scoring_key, label = mapping
            multiplier = scoring.get(scoring_key, 0)
            expected_stat = median(lines)
            fantasy_points = round(expected_stat * multiplier, 2)
            total += fantasy_points

            categories.append({
                "market": market_key,
                "label": label,
                "expected_stat": expected_stat,
                "scoring_key": scoring_key,
                "multiplier": multiplier,
                "fantasy_points": fantasy_points,
                "bookmaker_count": len(lines),
            })

        return {
            "player_name": canonical_name,
            "total_projected_points": round(total, 2),
            "categories": categories,
            "scoring_settings": scoring,
            "event_id": event_id,
        }

    def project_event(
        self,
        event_id: str,
        *,
        league_id: Optional[str] = None,
        bookmaker: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Project every player with props in *event_id*.

        Returns:
            A list of per-player projection dicts (same shape as
            ``project_player``), sorted descending by total points.
        """
        player_names = self._distinct_players_for_event(event_id)
        results = []
        for name in player_names:
            proj = self.project_player(
                name,
                event_id=event_id,
                league_id=league_id,
                bookmaker=bookmaker,
            )
            if proj["total_projected_points"] > 0:
                results.append(proj)
        results.sort(key=lambda p: p["total_projected_points"], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_scoring(self, league_id: Optional[str]) -> dict:
        """Load scoring settings from a league or return defaults."""
        league = None
        if league_id:
            league = (
                self.db.query(DBLeague)
                .filter(DBLeague.league_id == league_id)
                .first()
            )
        return get_scoring_settings(league)

    def _fetch_props(
        self,
        player_name: str,
        *,
        event_id: Optional[str] = None,
        bookmaker: Optional[str] = None,
    ) -> List[DBSportsbookOdds]:
        """Return all prop rows matching *player_name* (via description)."""
        prop_markets = tuple(MARKET_TO_SCORING.keys())
        like = f"%{player_name}%"

        query = self.db.query(DBSportsbookOdds).filter(
            DBSportsbookOdds.market.in_(prop_markets),
            DBSportsbookOdds.description.ilike(like),
        )
        if event_id:
            query = query.filter(DBSportsbookOdds.event_id == event_id)
        if bookmaker:
            query = query.filter(DBSportsbookOdds.bookmaker == bookmaker)

        return query.all()

    def _distinct_players_for_event(self, event_id: str) -> List[str]:
        """Return unique player descriptions for a given event."""
        prop_markets = tuple(MARKET_TO_SCORING.keys())
        rows = (
            self.db.query(DBSportsbookOdds.description)
            .filter(
                DBSportsbookOdds.event_id == event_id,
                DBSportsbookOdds.market.in_(prop_markets),
                DBSportsbookOdds.description.isnot(None),
            )
            .distinct()
            .all()
        )
        return [r[0] for r in rows if r[0]]

    @staticmethod
    def _resolve_canonical_name(
        props: List[DBSportsbookOdds],
        fallback: str,
    ) -> str:
        """Pick the most-common description as the canonical player name."""
        if not props:
            return fallback
        names: Dict[str, int] = {}
        for p in props:
            if p.description:
                names[p.description] = names.get(p.description, 0) + 1
        if not names:
            return fallback
        return max(names, key=names.get)  # type: ignore[arg-type]

    @staticmethod
    def _group_lines_by_market(
        props: List[DBSportsbookOdds],
    ) -> Dict[str, List[float]]:
        """Group prop rows by market, collecting *one line per bookmaker*.

        For Over/Under pairs from the same bookmaker, only the ``point``
        from the *Over* row is used (it equals the Under point).  If a
        bookmaker appears more than once for the same market, only the
        first row is kept so that each bookmaker contributes one value to
        the median calculation.
        """
        grouped: Dict[str, Dict[str, float]] = {}  # market -> {bookmaker -> point}
        for prop in props:
            if prop.point is None:
                continue
            # Use Over lines; skip Under to avoid double-counting
            if prop.outcome_name and prop.outcome_name.lower() == "under":
                continue

            market = prop.market
            bk = prop.bookmaker
            if market not in grouped:
                grouped[market] = {}
            # First line per bookmaker wins
            if bk not in grouped[market]:
                grouped[market][bk] = prop.point

        return {
            mkt: list(bk_map.values()) for mkt, bk_map in grouped.items()
        }
