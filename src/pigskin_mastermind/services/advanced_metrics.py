"""The metric registry — what each advanced metric is and how to read it.

Everything downstream reads only this. The trend series, the league scan, the
percentiles and the page rendering are all metric-agnostic, so adding a metric
is one entry here plus an importer, and touches no analysis code.

Two fields carry real weight:

``higher_is_better``
    Not decoration. The hot scan turns every metric into "did this improve",
    and a falling ``ngs_time_to_throw`` is an improvement while a falling
    ``snap_pct`` is not. Without it the scan would rank a quarterback getting
    the ball out faster as declining.

``kind``
    ``usage`` metrics describe opportunity; ``production`` metrics describe
    what came of it. The buy-low signal is the gap between the two, so a metric
    filed on the wrong side inverts its own contribution to that gap.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional

USAGE = "usage"
PRODUCTION = "production"

#: Percent, yards, seconds, points — used for display only.
PCT = "pct"
YARDS = "yards"
SECONDS = "seconds"
POINTS = "points"


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    kind: str
    unit: str
    positions: FrozenSet[str]
    source: str
    description: str
    higher_is_better: bool = True

    @property
    def is_usage(self) -> bool:
        return self.kind == USAGE


def _m(key, label, kind, unit, positions, source, description,
       higher_is_better=True) -> Metric:
    return Metric(
        key=key, label=label, kind=kind, unit=unit,
        positions=frozenset(positions), source=source,
        description=description, higher_is_better=higher_is_better,
    )


METRICS: Dict[str, Metric] = {
    m.key: m for m in (
        # --- Opportunity -------------------------------------------------
        _m("snap_pct", "Snap share", USAGE, PCT,
           ["QB", "RB", "WR", "TE"], "snap_counts",
           "Share of the offense's snaps. The broadest and most reliable "
           "usage signal, and the first thing to move when a role changes."),
        _m("target_share", "Target share", USAGE, PCT,
           ["RB", "WR", "TE"], "derived",
           "Share of the team's targets that week."),
        _m("rush_share", "Carry share", USAGE, PCT,
           ["QB", "RB"], "derived",
           "Share of the team's carries that week."),
        _m("touch_share", "Touch share", USAGE, PCT,
           ["RB", "WR", "TE"], "derived",
           "Share of the team's carries plus targets — the honest measure "
           "for a back who catches passes."),

        # --- Next Gen Stats: receiving ------------------------------------
        _m("ngs_separation", "Separation", USAGE, YARDS,
           ["WR", "TE"], "ngs_receiving",
           "Average yards of separation at the catch point. Says whether he "
           "is winning, not merely whether he is thrown to."),
        _m("ngs_cushion", "Cushion", USAGE, YARDS,
           ["WR", "TE"], "ngs_receiving",
           "How far off the defender lines up. Shrinking cushion means "
           "defences are respecting him more.",
           False),
        _m("ngs_air_yards_share", "Air yards share", USAGE, PCT,
           ["WR", "TE"], "ngs_receiving",
           "Share of the team's intended air yards — downfield role, not "
           "just volume."),
        _m("ngs_intended_air_yards", "Target depth", USAGE, YARDS,
           ["WR", "TE"], "ngs_receiving",
           "Average depth of target."),
        _m("ngs_catch_pct", "Catch %", PRODUCTION, PCT,
           ["WR", "TE"], "ngs_receiving",
           "Receptions per target."),

        # --- Next Gen Stats: rushing --------------------------------------
        _m("ngs_ryoe", "Rush yards over expected", PRODUCTION, YARDS,
           ["RB", "QB"], "ngs_rushing",
           "Yards beyond what the blocking and box gave him — the closest "
           "thing to isolating the back from his line."),
        _m("ngs_rush_efficiency", "Rush efficiency", PRODUCTION, YARDS,
           ["RB"], "ngs_rushing",
           "Distance travelled per yard gained. Lower is more direct.",
           False),
        _m("ngs_stacked_box_pct", "Stacked box %", USAGE, PCT,
           ["RB"], "ngs_rushing",
           "Share of carries against eight or more defenders — how much the "
           "defence is selling out to stop him.",
           False),

        # --- Next Gen Stats: passing --------------------------------------
        _m("ngs_time_to_throw", "Time to throw", USAGE, SECONDS,
           ["QB"], "ngs_passing",
           "Seconds from snap to release. Falling usually means pressure or "
           "a quicker scheme.",
           False),
        _m("ngs_aggressiveness", "Aggressiveness", USAGE, PCT,
           ["QB"], "ngs_passing",
           "Share of throws into tight coverage."),

        # --- Production ---------------------------------------------------
        _m("fantasy_points", "Fantasy points", PRODUCTION, POINTS,
           ["QB", "RB", "WR", "TE", "K", "DEF"], "game_logs",
           "What the opportunity actually produced. The other half of every "
           "buy-low comparison."),
    )
}


def metric(key: str) -> Optional[Metric]:
    return METRICS.get(key)


def for_position(position: Optional[str]) -> List[Metric]:
    """Metrics that mean anything for *position*, in registry order."""
    if not position:
        return []
    return [m for m in METRICS.values() if position.upper() in m.positions]


def usage_keys() -> List[str]:
    return [m.key for m in METRICS.values() if m.kind == USAGE]


def production_keys() -> List[str]:
    return [m.key for m in METRICS.values() if m.kind == PRODUCTION]


def format_value(key: str, value: Optional[float]) -> str:
    """Render a value the way its unit wants to be read."""
    if value is None:
        return "—"
    entry = METRICS.get(key)
    unit = entry.unit if entry else None
    if unit == PCT:
        return f"{value:.1f}%"
    if unit == SECONDS:
        return f"{value:.2f}s"
    if unit == YARDS:
        return f"{value:.1f}"
    return f"{value:.1f}"
