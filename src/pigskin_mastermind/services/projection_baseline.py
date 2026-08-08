"""Baseline points-per-game estimates: the anchor every projection starts from.

``baseline_weight`` is the single most impactful coefficient in the projection
formula, so the quality of the number it scales matters more than any of the
adjustment terms. This module owns that number, and fixes three problems with
how it used to be derived:

1. **Look-ahead leakage.** The weekly baseline was
   ``DBPlayerSeasonStats.fantasy_points_avg`` — a full-season average that
   includes the week being projected. A backtest scored against that is
   flattered by information the model would not have had, and week 1 has no
   season average at all.

2. **No shrinkage.** A player with two games counted exactly as much as a
   player with fifteen, so a backup who caught one touchdown projected like a
   starter.

3. **No prior for players without history.** Rookies and anyone the stats
   import missed anchored at 0.0 and projected near zero — precisely the
   players a draft tool must not get wrong. ADP is the market's opinion about
   those players and is already stored, so it serves as the prior.

Everything here is deliberately read-only and free of side effects; the caller
decides what to do with the estimate.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from statistics import median
from typing import Dict, List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBPlayer,
    DBPlayerGameLog,
    DBPlayerSeasonStats,
)

logger = logging.getLogger(__name__)

#: Games of evidence needed before the observed rate outweighs the prior.
#: With k=4, a player with 4 games sits halfway between what they've done and
#: what a typical player at their position does.
DEFAULT_SHRINKAGE_GAMES = 4.0

#: Half-life, in games, of the recency weighting. A game 4 weeks ago counts
#: half as much as the most recent one.
RECENCY_HALF_LIFE_GAMES = 4.0

#: Minimum games for a season to count toward a positional prior. Below this
#: the sample is mostly injury-shortened and one-week call-up noise.
MIN_GAMES_FOR_PEER_POOL = 4

#: Fallback per-game points by position, used only when the database has no
#: usable season anywhere (a fresh install). Rough 0.5-PPR replacement level.
FALLBACK_POSITION_PPG: Dict[str, float] = {
    'QB': 14.0,
    'RB': 8.0,
    'WR': 8.0,
    'TE': 5.5,
    'K': 7.0,
    'DEF': 6.0,
}


@dataclass
class Baseline:
    """A points-per-game estimate plus enough context to explain it.

    Attributes:
        points_per_game: The shrunk estimate.
        observed_points_per_game: The raw rate before shrinkage.
        games_used: How many games backed the observed rate.
        prior: What the estimate was shrunk toward.
        source: Where the evidence came from — ``game_logs``,
            ``season_stats``, ``adp``, or ``position_prior``.
    """

    points_per_game: float
    observed_points_per_game: float
    games_used: int
    prior: float
    source: str


def shrink(observed: float, games: float, prior: float, k: float) -> float:
    """Blend an observed rate toward a prior in proportion to sample size.

    The standard empirical-Bayes form: ``(n·observed + k·prior) / (n + k)``.
    With no games the prior is returned unchanged; with many games the prior
    stops mattering.
    """
    if games <= 0:
        return prior
    if k <= 0:
        return observed
    return (games * observed + k * prior) / (games + k)


class ProjectionBaselines:
    """Computes leakage-free, shrunk baselines. Caches per (position, year).

    Instantiate one per request and reuse it — the positional pools and ADP
    curves are the expensive part, and they are identical for every player at
    a position.
    """

    def __init__(self, db: Session, shrinkage_games: float = DEFAULT_SHRINKAGE_GAMES):
        self.db = db
        self.shrinkage_games = shrinkage_games
        self._position_prior_cache: Dict[tuple, float] = {}
        self._adp_curve_cache: Dict[tuple, Optional[tuple]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def weekly_baseline(
        self,
        player_id: int,
        position: str,
        year: int,
        week: int,
    ) -> Baseline:
        """Points-per-game anchor for *week*, using only prior information.

        Games from *week* onward are excluded, so calling this for week 8 gives
        the same answer whether or not weeks 8–18 have been played. That makes
        a backtest honest and makes an in-season projection reproducible.
        """
        logs = (
            self.db.query(DBPlayerGameLog)
            .filter(
                DBPlayerGameLog.player_id == player_id,
                DBPlayerGameLog.year == year,
                DBPlayerGameLog.week < week,
            )
            .order_by(desc(DBPlayerGameLog.week))
            .all()
        )

        # The prior is what the player did *before* this season, itself shrunk
        # toward the position — so a rookie's prior is the positional level and
        # a veteran's is their own established rate.
        prior = self._prior_year_rate(player_id, position, year)

        if not logs:
            return Baseline(
                points_per_game=prior,
                observed_points_per_game=0.0,
                games_used=0,
                prior=prior,
                source='position_prior' if prior else 'none',
            )

        observed = _recency_weighted_mean(
            [g.fantasy_points or 0.0 for g in logs]
        )
        n = len(logs)
        return Baseline(
            points_per_game=shrink(observed, n, prior, self.shrinkage_games),
            observed_points_per_game=observed,
            games_used=n,
            prior=prior,
            source='game_logs',
        )

    def season_baseline(
        self,
        player_id: int,
        position: str,
        year: int,
        adp: Optional[float] = None,
    ) -> Baseline:
        """Points-per-game anchor for the *year* season, from prior seasons.

        Weights the three preceding seasons 60/30/10. When the player has no
        usable history, falls back to what the market implies from *adp* —
        without that, every rookie projects at replacement level and the draft
        board is useless at exactly the picks that decide a season.
        """
        weights = [0.6, 0.3, 0.1]
        weighted_sum = 0.0
        total_weight = 0.0
        total_games = 0

        for i, w in enumerate(weights):
            season = self._season_row(player_id, year - 1 - i)
            if season is None:
                continue
            games = season.games_played or 0
            if games < 1:
                continue
            avg = _season_ppg(season)
            if avg <= 0:
                continue
            weighted_sum += w * avg
            total_weight += w
            total_games += games

        position_prior = self.position_prior(position, year - 1)

        if total_weight == 0:
            # No playing history. Use the market's view if we have one.
            adp_implied = self.adp_implied_ppg(position, year, adp) if adp else None
            if adp_implied is not None:
                return Baseline(
                    points_per_game=adp_implied,
                    observed_points_per_game=0.0,
                    games_used=0,
                    prior=position_prior,
                    source='adp',
                )
            return Baseline(
                points_per_game=position_prior,
                observed_points_per_game=0.0,
                games_used=0,
                prior=position_prior,
                source='position_prior',
            )

        observed = weighted_sum / total_weight

        # A player with real history still gets nudged toward the market when
        # ADP disagrees sharply — that is usually the market pricing in news
        # (a trade, a new starter, a holdout) that the stats cannot see.
        prior = position_prior
        adp_implied = self.adp_implied_ppg(position, year, adp) if adp else None
        if adp_implied is not None:
            prior = (position_prior + adp_implied) / 2

        # Cap the effective sample at a full season so a three-year veteran
        # isn't treated as having 50 games of evidence about *next* year.
        effective_games = min(total_games, 17)
        return Baseline(
            points_per_game=shrink(
                observed, effective_games, prior, self.shrinkage_games,
            ),
            observed_points_per_game=observed,
            games_used=total_games,
            prior=prior,
            source='season_stats',
        )

    # ------------------------------------------------------------------
    # Positional priors
    # ------------------------------------------------------------------

    def position_prior(self, position: str, year: int) -> float:
        """Typical per-game output at *position* in *year*.

        The median of players with a real sample, not the mean: fantasy scoring
        has a long right tail, and a mean anchored by the top five players is a
        bad thing to shrink a backup toward.
        """
        key = (position, year)
        if key in self._position_prior_cache:
            return self._position_prior_cache[key]

        rows = (
            self.db.query(DBPlayerSeasonStats)
            .join(DBPlayer, DBPlayerSeasonStats.player_id == DBPlayer.id)
            .filter(
                DBPlayer.position == position,
                DBPlayerSeasonStats.year == year,
                DBPlayerSeasonStats.games_played >= MIN_GAMES_FOR_PEER_POOL,
            )
            .all()
        )
        values = [ppg for ppg in (_season_ppg(r) for r in rows) if ppg > 0]

        if values:
            prior = float(median(values))
        else:
            prior = FALLBACK_POSITION_PPG.get(position, 6.0)

        self._position_prior_cache[key] = prior
        return prior

    def _prior_year_rate(self, player_id: int, position: str, year: int) -> float:
        """The player's own established per-game rate coming into *year*."""
        position_prior = self.position_prior(position, year - 1)

        season = self._season_row(player_id, year - 1)
        if season is None or not season.games_played:
            return position_prior

        avg = _season_ppg(season)
        if avg <= 0:
            return position_prior

        return shrink(
            avg, season.games_played, position_prior, self.shrinkage_games,
        )

    # ------------------------------------------------------------------
    # ADP → points curve
    # ------------------------------------------------------------------

    def adp_implied_ppg(
        self, position: str, year: int, adp: Optional[float],
    ) -> Optional[float]:
        """Per-game points the market implies for a player drafted at *adp*.

        Fits ``ppg = a + b·ln(adp)`` on the previous season's actuals for this
        position. Log-linear because draft value decays steeply at the top of
        the board and flattens out in the late rounds — a straight line in raw
        ADP would badly misprice both ends.

        Returns ``None`` when there isn't enough data to fit a curve.
        """
        if adp is None or adp <= 0:
            return None

        fit = self._adp_curve(position, year)
        if fit is None:
            return None

        intercept, slope = fit
        return max(0.0, intercept + slope * math.log(adp))

    def _adp_curve(self, position: str, year: int) -> Optional[tuple]:
        """Least-squares fit of per-game points against ln(ADP)."""
        key = (position, year)
        if key in self._adp_curve_cache:
            return self._adp_curve_cache[key]

        # Pair last season's ADP with last season's actual production.
        rows = (
            self.db.query(DBPlayerSeasonStats)
            .join(DBPlayer, DBPlayerSeasonStats.player_id == DBPlayer.id)
            .filter(
                DBPlayer.position == position,
                DBPlayerSeasonStats.year == year - 1,
                DBPlayerSeasonStats.adp.isnot(None),
                DBPlayerSeasonStats.games_played >= MIN_GAMES_FOR_PEER_POOL,
            )
            .all()
        )

        points: List[tuple] = []
        for r in rows:
            ppg = _season_ppg(r)
            if r.adp and r.adp > 0 and ppg > 0:
                points.append((math.log(r.adp), ppg))

        # Two points define a line but say nothing; require a real sample.
        if len(points) < 8:
            self._adp_curve_cache[key] = None
            return None

        n = len(points)
        mean_x = sum(x for x, _ in points) / n
        mean_y = sum(y for _, y in points) / n
        var_x = sum((x - mean_x) ** 2 for x, _ in points)
        if var_x == 0:
            self._adp_curve_cache[key] = None
            return None

        cov = sum((x - mean_x) * (y - mean_y) for x, y in points)
        slope = cov / var_x
        intercept = mean_y - slope * mean_x

        # A sane curve slopes downward: later pick, fewer points. An upward fit
        # means the sample is junk, and extrapolating it would reward bad ADP.
        if slope >= 0:
            self._adp_curve_cache[key] = None
            return None

        self._adp_curve_cache[key] = (intercept, slope)
        return self._adp_curve_cache[key]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _season_row(
        self, player_id: int, year: int,
    ) -> Optional[DBPlayerSeasonStats]:
        return (
            self.db.query(DBPlayerSeasonStats)
            .filter_by(player_id=player_id, year=year)
            .first()
        )


def _season_ppg(season: DBPlayerSeasonStats) -> float:
    """Per-game points for a season row, preferring the stored average."""
    if season.fantasy_points_avg and season.fantasy_points_avg > 0:
        return season.fantasy_points_avg
    if season.fantasy_points_total and season.games_played:
        return season.fantasy_points_total / season.games_played
    return 0.0


def _recency_weighted_mean(values: List[float]) -> float:
    """Exponentially-weighted mean of *values*, most recent first.

    Exponential rather than the linear ramp used previously: linear weights
    depend on how many games happen to be in the window, so the same recent
    game counts differently in week 5 and week 15.
    """
    if not values:
        return 0.0
    decay = 0.5 ** (1.0 / RECENCY_HALF_LIFE_GAMES)
    total_weight = 0.0
    weighted = 0.0
    for i, v in enumerate(values):
        w = decay ** i
        weighted += w * v
        total_weight += w
    return weighted / total_weight if total_weight else 0.0
