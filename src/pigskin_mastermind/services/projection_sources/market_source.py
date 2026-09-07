"""A market-implied projection built from game betting lines.

Why this exists: player props are the ideal market signal, but every provider
checked gates NFL props behind a paid plan (The Odds API wants its ~$99/mo
Business tier), and what ``sportsbook_odds`` actually held was hand-written
seed fixtures for a 2025 week-10 slate. ``nfl_data_py.import_schedules()``, by
contrast, ships real closing spreads and totals for free with no API key — so
that is what this source uses.

The method, stated plainly because the name "Market" should not overclaim:

    implied team total = total_line / 2 ± spread_line / 2
    environment        = implied team total / this week's average implied total
    projection         = the player's recent fantasy average × environment

So it is a recent baseline **re-priced for this week's game environment** — not
a per-player market forecast, which no free source provides. A player in a
49.5-total game where his side is favoured gets marked up; the same player in a
38-total game gets marked down.

Two things it deliberately does not do:

- **It does not attribute historical games to a team.** Game logs carry no team
  column, and ``DBPlayer.nfl_team`` is the player's *current* club, which is
  often the wrong franchise for a past season. Scaling by a league-relative
  environment instead of a specific team's share sidesteps that trap entirely.
- **It borrows nothing from the house model.** Its inputs are realized fantasy
  points and a betting line, which is what keeps it an independent opinion.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBNFLGame, DBPlayer, DBPlayerGameLog,
)
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_MARKET,
    ProjectionValue,
)
from pigskin_mastermind.utils.nfl_teams import normalize_team

logger = logging.getLogger(__name__)

#: Recent games behind the baseline, and how fast older ones decay. Matched to
#: the opportunity source so the two are comparably responsive.
LOOKBACK_GAMES = 4
DECAY = 0.65

#: How far the environment multiplier may move a projection. A line can imply a
#: genuinely extreme game, but a 2x swing on a fantasy projection is never the
#: market's claim — the spread prices the *team*, not one player's usage.
MIN_SCALE = 0.75
MAX_SCALE = 1.30

#: Positions that scale with their OWN team's implied total.
OFFENSE = frozenset({"QB", "RB", "WR", "TE", "K"})


class MarketImpliedSource:
    """``source='market'`` — a recent baseline re-priced by the game's line."""

    key = SOURCE_MARKET
    label = "Market"
    writes = True

    def project_week(
        self,
        db: Session,
        year: int,
        week: int,
        player_ids: List[int],
    ) -> Dict[int, ProjectionValue]:
        if not player_ids:
            return {}

        games = (
            db.query(DBNFLGame)
            .filter(
                DBNFLGame.year == year,
                DBNFLGame.week == week,
                DBNFLGame.total_line.isnot(None),
                DBNFLGame.spread_line.isnot(None),
            )
            .all()
        )
        if not games:
            # The market has not priced this week yet. Saying nothing is the
            # honest outcome; there is no line to imply anything from.
            return {}

        implied = _implied_totals(games)
        if not implied:
            return {}

        average = sum(t for t, _opp in implied.values()) / len(implied)
        if average <= 0:
            return {}

        players = db.query(DBPlayer).filter(DBPlayer.id.in_(player_ids)).all()

        out: Dict[int, ProjectionValue] = {}
        for player in players:
            team = normalize_team(player.nfl_team)
            if not team or team not in implied:
                continue

            own_total, opponent_total = implied[team]
            if player.position in OFFENSE:
                scale = own_total / average
            elif player.position == "DEF":
                # A defense scores when the OPPONENT is expected to score
                # little, so its environment is the inverse of the other side's
                # implied total.
                if opponent_total <= 0:
                    continue
                scale = average / opponent_total
            else:
                continue

            scale = max(MIN_SCALE, min(MAX_SCALE, scale))

            baseline = _recent_average(db, player.id, year, week)
            if baseline is None or baseline <= 0:
                continue

            out[player.id] = ProjectionValue(
                points=round(baseline * scale, 2),
                components={
                    "baseline": round(baseline, 2),
                    "implied_team_total": round(own_total, 2),
                    "opponent_implied_total": round(opponent_total, 2),
                    "week_average_total": round(average, 2),
                    "environment": round(scale, 3),
                },
            )
        return out


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _implied_totals(games) -> Dict[str, Tuple[float, float]]:
    """``team -> (own implied total, opponent implied total)`` for the week.

    ``spread_line`` is published from the HOME team's perspective and is
    positive when the home side is favoured, so the home total takes the plus.
    Flipping that sign inverts every projection this source produces, which is
    why it is written once, here.
    """
    out: Dict[str, Tuple[float, float]] = {}
    for game in games:
        half = game.total_line / 2.0
        edge = game.spread_line / 2.0
        home_total = half + edge
        away_total = half - edge

        home = normalize_team(game.home_team)
        away = normalize_team(game.away_team)
        if home:
            out[home] = (home_total, away_total)
        if away:
            out[away] = (away_total, home_total)
    return out


def _recent_average(
    db: Session, player_id: int, year: int, week: int,
) -> Optional[float]:
    """Decayed average of the player's recent fantasy points.

    Looks back across the season boundary, which matters most in week 1 — the
    only games available then are last season's, and refusing to project until
    week 2 would make this source useless exactly when a lineup is first set.
    """
    logs = (
        db.query(DBPlayerGameLog)
        .filter(
            DBPlayerGameLog.player_id == player_id,
            # Everything strictly before the week being projected.
            (DBPlayerGameLog.year < year)
            | ((DBPlayerGameLog.year == year) & (DBPlayerGameLog.week < week)),
        )
        .order_by(
            DBPlayerGameLog.year.desc(), DBPlayerGameLog.week.desc(),
        )
        .limit(LOOKBACK_GAMES * 2)
        .all()
    )
    # A stored 0.0 with an empty stat line is a bye that predates
    # purge-bye-weeks, not a real game. Averaging it in drags the baseline down
    # for anyone whose bye happens to fall in the lookback window.
    played = [
        log for log in logs
        if (log.fantasy_points or 0) > 0
        or (log.pass_att or 0) or (log.rush_att or 0) or (log.targets or 0)
    ][:LOOKBACK_GAMES]

    if not played:
        return None

    # `logs` came back newest-first; weight the most recent heaviest.
    weights = [DECAY ** index for index in range(len(played))]
    total = sum(weights)
    return sum(
        (log.fantasy_points or 0.0) * weight
        for log, weight in zip(played, weights)
    ) / total
