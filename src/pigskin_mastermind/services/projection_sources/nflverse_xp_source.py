"""Opportunity-based expected points from the nflverse weekly frame.

There is no ready-made expected-points feed in the installed ``nfl_data_py``
(no ``import_ff_opportunity``), so this computes one.

The idea: strip out what a player got *lucky* on and keep what he was *given*.
Fantasy points conflate volume with touchdown luck and yards-per-touch
variance; expected points prices each opportunity at the league-average rate
for that position, so a receiver who saw 11 targets is credited for 11 targets
whether or not one of them happened to end in the end zone. That makes this
genuinely independent of the house model, which reasons from realized points.

Two deliberate limits, both from the approved design:

- **No opponent adjustment.** A recency-weighted rate carried forward flat.
  Adding a matchup layer would make this a second model to tune and validate.
- **Backward-looking by construction.** Expected points measures games already
  played; the EWMA is what turns it into a forecast.

Byes need no special handling here, unlike stored game logs. The nflverse
weekly frame has no row for a week a player did not play, which is the same
property that keeps snap counts free of the ``games_played`` divisor bug. The
zero-opportunity filter below makes that robust rather than assumed.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, get_scoring_settings
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_NFLVERSE_XP,
    ProjectionValue,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - import guard mirrors nfl_data_service
    import nfl_data_py as nfl
except ImportError:  # pragma: no cover
    nfl = None


#: Opportunity columns priced at a league-average rate, per position group.
#: Each entry maps an nflverse column to the stat it produces on average.
_OPPORTUNITY_COLUMNS = (
    "attempts", "carries", "targets",
)

#: How many recent games feed the average, and how fast older ones decay.
#: 0.65 puts roughly half the weight on the two most recent games — responsive
#: enough to catch a changed role, slow enough that one blowout script does not
#: become the forecast.
LOOKBACK_GAMES = 4
DECAY = 0.65


class NflverseExpectedPointsSource:
    """``source='nflverse_xp'`` — opportunity priced at league-average rates."""

    key = SOURCE_NFLVERSE_XP
    label = "Opportunity"
    writes = True

    def __init__(self, frame=None) -> None:
        # Injectable so tests supply a small DataFrame instead of downloading
        # a season of weekly data.
        self._frame = frame

    # ------------------------------------------------------------------

    def project_week(
        self,
        db: Session,
        year: int,
        week: int,
        player_ids: List[int],
    ) -> Dict[int, ProjectionValue]:
        if not player_ids:
            return {}

        # Week 1 has no completed games to average, so there is nothing this
        # source can say. Returning early also avoids downloading a season
        # frame that nflverse has not published yet -- which surfaces as a 404
        # and would be recorded as a source failure rather than the ordinary
        # "no history yet" that it is.
        if week <= 1:
            return {}

        frame = self._frame
        if frame is None:
            if nfl is None:
                raise ImportError(
                    "nfl_data_py is not installed. Run: pip install nfl_data_py",
                )
            frame = nfl.import_weekly_data([year])

        # Only completed weeks inform the forecast. Including week itself would
        # leak the result we are trying to predict.
        frame = frame[frame["week"] < week]
        if frame.empty:
            return {}

        rates = self._league_rates(frame, get_scoring_settings())

        gsis_by_player = self._gsis_map(db, player_ids)
        if not gsis_by_player:
            return {}

        out: Dict[int, ProjectionValue] = {}
        by_gsis = {v: k for k, v in gsis_by_player.items()}

        for gsis_id, group in frame[
            frame["player_id"].isin(list(by_gsis.keys()))
        ].groupby("player_id"):
            player_id = by_gsis.get(gsis_id)
            if player_id is None:
                continue

            games = group.sort_values("week")
            per_game = [
                self._expected_points(row, rates)
                for _, row in games.iterrows()
            ]
            # A row with no opportunity at all is an inactive or garbage entry,
            # not a real zero-opportunity game. Averaging it in would deflate
            # the rate exactly the way a stored bye deflates games_played.
            per_game = [p for p in per_game if p is not None]
            if not per_game:
                continue

            points = _ewma(per_game[-LOOKBACK_GAMES:], DECAY)
            out[player_id] = ProjectionValue(
                points=round(points, 2),
                components={
                    "games_used": len(per_game[-LOOKBACK_GAMES:]),
                    "decay": DECAY,
                    "per_game": [round(p, 2) for p in per_game[-LOOKBACK_GAMES:]],
                },
            )
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _gsis_map(self, db: Session, player_ids: List[int]) -> Dict[int, str]:
        """``DBPlayer.id`` -> gsis id, for players that have one.

        Players without a gsis id (ESPN-only rows that never resolved, and
        every team defense) simply have no coverage from this source.
        """
        rows = (
            db.query(DBPlayer.id, DBPlayer.gsis_id)
            .filter(DBPlayer.id.in_(player_ids), DBPlayer.gsis_id.isnot(None))
            .all()
        )
        return {row[0]: row[1] for row in rows if row[1]}

    def _league_rates(self, frame, scoring: dict) -> Dict[str, Dict[str, float]]:
        """Average fantasy points produced per opportunity, per position.

        Computed from the same frame being projected from, so the rates are
        this season's, not a hardcoded constant that ages.
        """
        rates: Dict[str, Dict[str, float]] = {}
        if "position" not in frame.columns:
            return rates

        for position, group in frame.groupby("position"):
            entry: Dict[str, float] = {}
            for column in _OPPORTUNITY_COLUMNS:
                if column not in group.columns:
                    continue
                opportunities = float(group[column].fillna(0).sum())
                if opportunities <= 0:
                    continue
                produced = float(
                    _score_frame(group, scoring, column).fillna(0).sum(),
                )
                entry[column] = produced / opportunities
            if entry:
                rates[str(position)] = entry
        return rates

    def _expected_points(self, row, rates: Dict[str, Dict[str, float]]):
        """Expected points for one game, or None if there was no opportunity."""
        position_rates = rates.get(str(row.get("position")))
        if not position_rates:
            return None

        total = 0.0
        opportunity_seen = False
        for column, rate in position_rates.items():
            count = row.get(column)
            count = 0.0 if count is None or count != count else float(count)
            if count > 0:
                opportunity_seen = True
            total += count * rate

        return total if opportunity_seen else None


# ---------------------------------------------------------------------------
# Pure helpers — kept module-level so they are testable without a DataFrame
# ---------------------------------------------------------------------------


def _ewma(values: List[float], decay: float) -> float:
    """Exponentially-weighted mean, most recent value weighted heaviest.

    ``values`` is oldest-first. Weights are renormalized over however many
    games are present, so a player with two games is not penalised against one
    with four.
    """
    if not values:
        return 0.0
    weights = [decay ** (len(values) - 1 - i) for i in range(len(values))]
    total = sum(weights)
    return sum(v * w for v, w in zip(values, weights)) / total


def _score_frame(group, scoring: dict, column: str):
    """Fantasy points attributable to the play type *column* counts.

    Passing attempts are credited with passing production, carries with
    rushing, targets with receiving — so each opportunity type gets its own
    league rate rather than one blended points-per-touch number.
    """
    zero = group.get("week") * 0  # a same-indexed zero Series

    if column == "attempts":
        return (
            group.get("passing_yards", zero).fillna(0) * scoring.get("pass_yd", 0)
            + group.get("passing_tds", zero).fillna(0) * scoring.get("pass_td", 0)
            + group.get("interceptions", zero).fillna(0) * scoring.get("pass_int", 0)
        )
    if column == "carries":
        return (
            group.get("rushing_yards", zero).fillna(0) * scoring.get("rush_yd", 0)
            + group.get("rushing_tds", zero).fillna(0) * scoring.get("rush_td", 0)
        )
    if column == "targets":
        return (
            group.get("receiving_yards", zero).fillna(0) * scoring.get("rec_yd", 0)
            + group.get("receiving_tds", zero).fillna(0) * scoring.get("rec_td", 0)
            + group.get("receptions", zero).fillna(0) * scoring.get("rec", 0)
        )
    return zero
