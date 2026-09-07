"""Decide which rostered players start, and in which slot.

This is the only place a lineup decision is made. The deterministic AI manager,
the auto-fill fallback, and the Claude agent's baseline all call ``plan_lineup``
— so a change to start/sit logic changes every one of them at once, and none of
them can drift into a private interpretation of the rules.

Determinism is load-bearing. The ordering is a stable sort on
``(-projection, player_id)`` with no randomness anywhere, so the same roster in
the same week always produces the same lineup. Without that, a user cannot
reproduce, argue with, or trust a bad week.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, FrozenSet, List, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import (
    DBLeague, DBLineupSlot, DBPlayer, DBRosterSpot, DBTeam,
)
from pigskin_mastermind.services.lineup_locks import LockIndex
from pigskin_mastermind.services.mock_draft import (
    BENCH_SLOT, DEFAULT_LINEUP_SLOTS, FLEX_ELIGIBLE,
)
from pigskin_mastermind.services.nfl_schedule import ScheduleIndex
from pigskin_mastermind.services.injury_status import (
    EXCLUDED, HAIRCUTS, InjuryIndex,
)
from pigskin_mastermind.services.projection_refresh import weekly_projection_map

#: Re-exported from injury_status, which is now the single definition. Kept as
#: names here because other modules and tests import them from lineup_manager.
INJURY_EXCLUDED: FrozenSet[str] = EXCLUDED
INJURY_HAIRCUTS: Dict[str, float] = HAIRCUTS

FLEX_SLOT = "FLEX"


@dataclass
class LineupDecision:
    player_id: int
    name: str
    position: str
    slot: str
    projected_points: float
    locked: bool
    reason: str


@dataclass
class LineupPlan:
    team_id: int
    year: int
    week: int
    decisions: List[LineupDecision] = field(default_factory=list)
    projected_total: float = 0.0

    def starters(self) -> List[LineupDecision]:
        return [d for d in self.decisions if d.slot != BENCH_SLOT]

    def bench(self) -> List[LineupDecision]:
        return [d for d in self.decisions if d.slot == BENCH_SLOT]


def _starter_slots(roster_slots: Dict[str, int]) -> Dict[str, int]:
    """Required starting slots, excluding bench and flex."""
    return {
        slot: count
        for slot, count in roster_slots.items()
        if slot not in (BENCH_SLOT, FLEX_SLOT) and count > 0
    }


def _rank_key(candidate: Dict[str, Any]) -> tuple:
    """Total order: points descending, then player id.

    The id term is not decoration. Without it the order depends on however the
    roster query happened to return rows, and the AI's lineup stops being
    reproducible.
    """
    return (-candidate["points"], candidate["player"].id)


def plan_lineup(
    db: Session,
    team: DBTeam,
    year: int,
    week: int,
    now: datetime,
    league: Optional[DBLeague] = None,
    players: Optional[List[DBPlayer]] = None,
    projections: Optional[Dict[int, float]] = None,
) -> LineupPlan:
    """Best legal lineup for *team* in *week*, as of *now*.

    *players* and *projections* override the two lookups this normally does for
    itself. They exist for the multi-source projections view, where the roster
    comes from an ESPN weekly snapshot rather than ``DBRosterSpot`` and the
    numbers are a consensus the user weighted on the page — neither of which
    the default queries can produce.

    Injecting rather than writing a second optimizer is deliberate. Everything
    below this point — bye-week zeroing, injury exclusion and haircuts,
    preserving a locked player's existing slot, the stable tie-break, and
    required-slots-then-FLEX filling — is the lineup decision, and it must have
    exactly one implementation. A caller supplying its own inputs still gets
    all of it.
    """
    if league is None:
        league = db.query(DBLeague).filter_by(league_id=team.league_id).first()
    roster_slots = (
        (league.roster_slots if league else None) or dict(DEFAULT_LINEUP_SLOTS)
    )

    if players is None:
        players = (
            db.query(DBPlayer)
            .join(DBRosterSpot, DBRosterSpot.player_id == DBPlayer.id)
            .filter(
                DBRosterSpot.team_id == team.id,
                DBRosterSpot.dropped_at.is_(None),
            )
            .all()
        )
    if not players:
        return LineupPlan(team_id=team.id, year=year, week=week)

    if projections is None:
        projections = weekly_projection_map(
            db, [p.id for p in players], year, week,
        )
    locks = LockIndex(db)
    schedule = ScheduleIndex(db)
    injuries = InjuryIndex(db, year, week)

    existing = {
        row.player_id: row.slot
        for row in db.query(DBLineupSlot).filter_by(
            team_id=team.id, year=year, week=week,
        )
    }

    candidates = []
    for player in players:
        # Availability comes from the week's injury report where one exists,
        # and only falls back to DBPlayer.injury_status -- an undated column an
        # old ESPN sync may have left behind -- when the week has no report at
        # all. Acting on a season-old QUESTIONABLE is worse than acting on
        # nothing, because it looks current.
        injury = injuries.verdict(player.id)
        on_bye = schedule.is_bye(player.nfl_team, year, week)
        locked = locks.is_locked(player.nfl_team, year, week, now)

        points = projections.get(player.id, 0.0)
        if on_bye:
            reason = "on bye"
            points = 0.0
        elif injury.excluded:
            reason = injury.reason
            points = 0.0
        elif injury.multiplier != 1.0:
            reason = injury.reason
            points *= injury.multiplier
        elif player.id not in projections:
            reason = "no projection available"
        else:
            reason = ""

        candidates.append({
            "player": player,
            "points": points,
            "eligible": not on_bye and not injury.excluded,
            "locked": locked,
            "reason": reason,
        })

    # Stable and total: ties break on player id, never on iteration order.
    candidates.sort(key=_rank_key)

    assigned: Dict[int, str] = {}
    remaining = dict(_starter_slots(roster_slots))
    flex_remaining = roster_slots.get(FLEX_SLOT, 0)

    # Locked players who already occupy a slot cannot be moved out of it, and
    # that slot's capacity is spent whether or not anyone else wants it. A
    # locked player with NO prior row is a different case: there is no
    # placement to preserve, so they fall through to the normal assignment
    # loops below. Defaulting them to BENCH here would strand them there --
    # on a cold start after kickoff (exactly what the auto-fill fallback
    # does) that benches the entire roster for a zero.
    for candidate in candidates:
        if not candidate["locked"]:
            continue
        slot = existing.get(candidate["player"].id)
        if slot is None:
            continue
        assigned[candidate["player"].id] = slot
        if slot == FLEX_SLOT:
            flex_remaining = max(0, flex_remaining - 1)
        elif slot in remaining:
            remaining[slot] = max(0, remaining[slot] - 1)

    # Required slots first.
    for candidate in candidates:
        player = candidate["player"]
        if player.id in assigned or not candidate["eligible"]:
            continue
        slot = player.position
        if remaining.get(slot, 0) > 0:
            assigned[player.id] = slot
            remaining[slot] -= 1

    # Then flex, from the best remaining eligible player.
    for candidate in candidates:
        player = candidate["player"]
        if flex_remaining <= 0:
            break
        if player.id in assigned or not candidate["eligible"]:
            continue
        if player.position in FLEX_ELIGIBLE:
            assigned[player.id] = FLEX_SLOT
            flex_remaining -= 1

    plan = LineupPlan(team_id=team.id, year=year, week=week)
    for candidate in candidates:
        player = candidate["player"]
        slot = assigned.get(player.id, BENCH_SLOT)
        plan.decisions.append(LineupDecision(
            player_id=player.id,
            name=player.name,
            position=player.position,
            slot=slot,
            projected_points=round(candidate["points"], 2),
            locked=candidate["locked"],
            reason=candidate["reason"],
        ))

    plan.projected_total = round(
        sum(d.projected_points for d in plan.starters()), 2,
    )
    return plan


def apply_plan(db: Session, plan: LineupPlan, set_by: str) -> int:
    """Persist *plan* as ``DBLineupSlot`` rows. Returns rows written."""
    locks = LockIndex(db)

    existing = {
        row.player_id: row
        for row in db.query(DBLineupSlot).filter_by(
            team_id=plan.team_id, year=plan.year, week=plan.week,
        )
    }

    written = 0
    for decision in plan.decisions:
        player = db.query(DBPlayer).filter_by(id=decision.player_id).one()
        kickoff = locks.kickoff(player.nfl_team, plan.year, plan.week)

        row = existing.get(decision.player_id)
        if row is None:
            row = DBLineupSlot(
                team_id=plan.team_id, year=plan.year, week=plan.week,
                player_id=decision.player_id,
            )
            db.add(row)

        row.slot = decision.slot
        row.set_by = set_by
        row.projected_points = decision.projected_points
        row.locked_at = kickoff if decision.locked else None
        written += 1

    db.commit()
    return written
