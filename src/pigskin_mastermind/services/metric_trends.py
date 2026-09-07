"""Trends for one player, and the league-wide hot-movers scan.

The scan answers one question: whose *opportunity* has moved ahead of their
*production*, and whose the reverse. Volume leads scoring, so a player whose
snap and target share have climbed for three weeks while his points have not is
the buy-low case; the player whose points are holding up on shrinking usage is
the sell-high one.

Everything here is metric-agnostic. It reads
``services/advanced_metrics.py::METRICS`` for direction and kind and never
names a metric itself, so adding one requires no change in this module.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBPlayerAdvancedMetric
from pigskin_mastermind.services.advanced_metrics import METRICS

#: Weeks in the recent window and in the baseline it is compared against.
WINDOW = 3
BASELINE = 3

#: A z-delta below this is noise, not a move. Applied after scaling so it means
#: the same thing for every metric.
MIN_SIGNAL = 0.35


@dataclass
class MetricSeries:
    """One player's weekly values for one metric."""

    metric: str
    label: str
    points: List[Tuple[int, float]] = field(default_factory=list)
    unit: str = ""
    higher_is_better: bool = True

    @property
    def weeks_covered(self) -> int:
        """How many weeks this actually spans.

        Rendered next to every trend. A metric's history depends on which feed
        produced it and those differ by years, so a fixed "last 3 weeks" label
        would claim more than the data supports.
        """
        return len(self.points)

    @property
    def latest(self) -> Optional[float]:
        return self.points[-1][1] if self.points else None

    @property
    def average(self) -> Optional[float]:
        return (
            statistics.fmean(v for _w, v in self.points) if self.points else None
        )

    def sparkline(self, height: int = 20, width: int = 90) -> str:
        """An inline SVG polyline. Empty string when there is nothing to draw."""
        if len(self.points) < 2:
            return ""
        values = [v for _w, v in self.points]
        low, high = min(values), max(values)
        span = (high - low) or 1.0
        step = width / (len(values) - 1)
        coords = [
            f"{index * step:.1f},{height - ((v - low) / span) * height:.1f}"
            for index, v in enumerate(values)
        ]
        return " ".join(coords)


@dataclass
class Mover:
    """One player's usage-versus-production divergence."""

    player_id: int
    name: str
    position: Optional[str]
    nfl_team: Optional[str]
    usage_delta: float          # z-scaled, direction-corrected
    production_delta: float
    divergence: float           # usage - production
    drivers: List[Tuple[str, float]] = field(default_factory=list)
    weeks_covered: int = 0

    #: A rookie *in the season being scanned* — from ``rookie_season``, not a
    #: current experience count, so a backfill of an earlier year is still
    #: right about who was a rookie then.
    is_rookie: bool = False
    draft_round: Optional[int] = None

    @property
    def verdict(self) -> str:
        return "buy" if self.divergence > 0 else "sell"

    @property
    def draft_label(self) -> str:
        """``R1``..``R7``, or ``UDFA`` when undrafted.

        Worth showing beside the rookie badge: a first-rounder's role expanding
        is a team confirming an investment, an undrafted rookie's is a team
        discovering something. Same signal, different confidence.
        """
        return f"R{self.draft_round}" if self.draft_round else "UDFA"

    @property
    def rookie_rising(self) -> bool:
        """A rookie whose opportunity is genuinely growing."""
        return self.is_rookie and self.usage_delta > 0


# ---------------------------------------------------------------------------
# One player
# ---------------------------------------------------------------------------


def player_series(
    db: Session, player_id: int, year: int, metrics: Optional[Sequence[str]] = None,
) -> List[MetricSeries]:
    """Every stored metric series for one player in one season."""
    query = (
        db.query(
            DBPlayerAdvancedMetric.metric,
            DBPlayerAdvancedMetric.week,
            DBPlayerAdvancedMetric.value,
        )
        .filter(
            DBPlayerAdvancedMetric.player_id == player_id,
            DBPlayerAdvancedMetric.year == year,
        )
    )
    if metrics:
        query = query.filter(DBPlayerAdvancedMetric.metric.in_(list(metrics)))

    grouped: Dict[str, List[Tuple[int, float]]] = {}
    for metric, week, value in query.all():
        grouped.setdefault(metric, []).append((week, value))

    out: List[MetricSeries] = []
    # Registry order, so the page is stable rather than dictionary-ordered.
    for key, entry in METRICS.items():
        points = sorted(grouped.get(key, []))
        if not points:
            continue
        out.append(MetricSeries(
            metric=key, label=entry.label, points=points,
            unit=entry.unit, higher_is_better=entry.higher_is_better,
        ))
    return out


def percentile(
    db: Session, metric: str, year: int, week: int, value: float,
) -> Optional[float]:
    """Where *value* sits in that metric's league distribution for the week."""
    values = [
        row[0]
        for row in db.query(DBPlayerAdvancedMetric.value).filter(
            DBPlayerAdvancedMetric.year == year,
            DBPlayerAdvancedMetric.week == week,
            DBPlayerAdvancedMetric.metric == metric,
        ).all()
    ]
    if len(values) < 5:
        return None
    below = sum(1 for v in values if v < value)
    pct = below / len(values) * 100.0

    entry = METRICS.get(metric)
    if entry is not None and not entry.higher_is_better:
        pct = 100.0 - pct
    return round(pct, 1)


# ---------------------------------------------------------------------------
# The league scan
# ---------------------------------------------------------------------------


def hot_movers(
    db: Session,
    year: int,
    week: int,
    *,
    window: int = WINDOW,
    baseline: int = BASELINE,
    limit: int = 40,
    position: Optional[str] = None,
) -> List[Mover]:
    """Players whose opportunity and production have moved apart.

    For each metric: the mean of the last *window* weeks against the mean of
    the *baseline* weeks before it, then z-scored against that metric's own
    league distribution. Scaling is what makes the comparison legitimate — a
    4-point snap-share move and a 0.3-yard separation move are otherwise
    incommensurable, and the larger raw number would always win.
    """
    recent_weeks = [w for w in range(week - window + 1, week + 1) if w >= 1]
    prior_weeks = [
        w for w in range(week - window - baseline + 1, week - window + 1)
        if w >= 1
    ]
    if not recent_weeks or not prior_weeks:
        return []

    rows = (
        db.query(
            DBPlayerAdvancedMetric.player_id,
            DBPlayerAdvancedMetric.metric,
            DBPlayerAdvancedMetric.week,
            DBPlayerAdvancedMetric.value,
        )
        .filter(
            DBPlayerAdvancedMetric.year == year,
            DBPlayerAdvancedMetric.week.in_(recent_weeks + prior_weeks),
        )
        .all()
    )
    if not rows:
        return []

    # player -> metric -> {week: value}
    collected: Dict[int, Dict[str, Dict[int, float]]] = {}
    for player_id, metric, week_number, value in rows:
        collected.setdefault(player_id, {}).setdefault(metric, {})[
            week_number
        ] = value

    raw = _raw_deltas(collected, recent_weeks, prior_weeks)
    if not raw:
        return []

    scales = _metric_scales(raw)
    players = _player_index(db, set(raw), position)

    movers: List[Mover] = []
    for player_id, deltas in raw.items():
        player = players.get(player_id)
        if player is None:
            continue

        usage: List[float] = []
        production: List[float] = []
        drivers: List[Tuple[str, float]] = []
        covered = 0

        for metric_key, (delta, weeks_seen) in deltas.items():
            entry = METRICS.get(metric_key)
            scale = scales.get(metric_key)
            if entry is None or not scale:
                continue
            scaled = delta / scale
            if not entry.higher_is_better:
                scaled = -scaled

            covered = max(covered, weeks_seen)
            if entry.is_usage:
                usage.append(scaled)
                if abs(scaled) >= MIN_SIGNAL:
                    drivers.append((entry.label, round(scaled, 2)))
            else:
                production.append(scaled)

        # Both halves are required. Without a usage metric there is no signal,
        # and without production the "divergence" would just be a mover list.
        if not usage or not production:
            continue

        usage_delta = statistics.fmean(usage)
        production_delta = statistics.fmean(production)
        divergence = usage_delta - production_delta
        if abs(divergence) < MIN_SIGNAL:
            continue

        # The opportunity side has to have actually moved, in the direction
        # the verdict claims.
        #
        # Without this, `usage - production` scores a player whose scoring
        # collapsed on flat usage exactly like one whose role genuinely
        # expanded — and the first is not a buy-low, it is a player who got
        # worse. On real 2025 data that failure dominated the board: the top
        # "buy" was a quarterback at usage +0.15 against production -4.39.
        #
        # The premise of the page is that volume leads scoring, so a buy
        # requires volume to have risen and a sell requires it to have fallen.
        if divergence > 0 and usage_delta < MIN_SIGNAL:
            continue
        if divergence < 0 and usage_delta > -MIN_SIGNAL:
            continue

        drivers.sort(key=lambda item: -abs(item[1]))
        movers.append(Mover(
            player_id=player_id,
            name=player.name,
            position=player.position,
            nfl_team=player.nfl_team,
            is_rookie=player.rookie_season == year,
            draft_round=player.draft_round,
            usage_delta=round(usage_delta, 2),
            production_delta=round(production_delta, 2),
            divergence=round(divergence, 2),
            drivers=drivers[:3],
            weeks_covered=covered,
        ))

    movers.sort(key=lambda m: (-abs(m.divergence), m.player_id))
    return movers[:limit]


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _raw_deltas(collected, recent_weeks, prior_weeks):
    """player -> metric -> (recent mean - prior mean, weeks covered)."""
    out: Dict[int, Dict[str, Tuple[float, int]]] = {}
    for player_id, metrics in collected.items():
        for metric_key, by_week in metrics.items():
            recent = [by_week[w] for w in recent_weeks if w in by_week]
            prior = [by_week[w] for w in prior_weeks if w in by_week]
            # One game is not a trend, and neither is a comparison against one.
            if len(recent) < 2 or not prior:
                continue
            out.setdefault(player_id, {})[metric_key] = (
                statistics.fmean(recent) - statistics.fmean(prior),
                len(recent) + len(prior),
            )
    return out


def _metric_scales(raw) -> Dict[str, float]:
    """Standard deviation of each metric's deltas, for z-scaling.

    Computed from the deltas actually observed rather than a constant, so a
    metric's scale reflects how much it really moves week to week.
    """
    pooled: Dict[str, List[float]] = {}
    for deltas in raw.values():
        for metric_key, (delta, _weeks) in deltas.items():
            pooled.setdefault(metric_key, []).append(delta)

    scales: Dict[str, float] = {}
    for metric_key, values in pooled.items():
        if len(values) < 5:
            continue
        deviation = statistics.pstdev(values)
        if deviation > 0:
            scales[metric_key] = deviation
    return scales


def _player_index(db: Session, player_ids, position: Optional[str]):
    query = db.query(DBPlayer).filter(DBPlayer.id.in_(list(player_ids)))
    if position:
        query = query.filter(DBPlayer.position == position.upper())
    return {player.id: player for player in query.all()}
