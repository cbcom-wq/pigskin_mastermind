"""Week-scoped injury status for lineup decisions.

``DBPlayer.injury_status`` is a single undated column written by whatever ESPN
sync ran last. On a database holding an archived season it leaves year-old
``QUESTIONABLE`` flags on players, and ``plan_lineup`` discounts projections
from them — so a start/sit call gets made on stale information that looks
current. ``DBPlayerInjury`` fixes that by carrying a year and a week.

This index is the single place that decides which of the two to believe.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBPlayerInjury

#: Statuses that mean the player will not take the field. Never started.
#: ``IR``/``SUSPENDED``/``NA`` only ever come from ESPN -- nflverse reports
#: just Out, Doubtful and Questionable.
EXCLUDED = frozenset({"OUT", "IR", "SUSPENDED", "NA"})

#: Statuses that shade the projection instead of benching outright. A doubtful
#: star still deserves to beat a healthy WR4 -- hard-benching every tag is how
#: an AI ends up starting nobody in November.
HAIRCUTS = {
    "QUESTIONABLE": 0.85,
    "DOUBTFUL": 0.50,
}

#: Practice participation, used only until the Friday game designation lands.
#: A full non-participant three days out is a real signal; a limited one is
#: barely one, which is why only the former carries a haircut.
PRACTICE_HAIRCUTS = {"DID NOT PARTICIPATE IN PRACTICE": 0.85}


@dataclass(frozen=True)
class InjuryVerdict:
    """What is known about one player's availability this week."""

    status: Optional[str]        # OUT | DOUBTFUL | QUESTIONABLE | None
    detail: Optional[str]        # the injury, when reported
    multiplier: float            # 1.0 when nothing applies
    excluded: bool
    #: True when this came from a week-scoped report rather than the undated
    #: legacy column. Callers surface it so a viewer can tell a verified
    #: designation from a possibly stale flag.
    dated: bool

    @property
    def reason(self) -> str:
        if not self.status:
            return ""
        label = self.status.lower()
        if self.excluded:
            return f"ruled {label}" if self.dated else f"flagged {label} (unverified)"
        if self.dated:
            return f"{label} — projection discounted"
        return f"{label} (unverified) — projection discounted"


_CLEAR = InjuryVerdict(None, None, 1.0, False, True)


class InjuryIndex:
    """Availability for one ``(year, week)``, loaded once.

    Falls back to ``DBPlayer.injury_status`` **only when no report exists for
    that week at all** — i.e. the import has never been run. Once week-scoped
    data is present it is used exclusively, including for players it does not
    mention: absence from an injury report is itself the report saying the
    player is healthy, and letting a stale flag survive that would defeat the
    point of importing it.
    """

    def __init__(self, db: Session, year: int, week: int) -> None:
        self.year = year
        self.week = week

        rows = (
            db.query(DBPlayerInjury)
            .filter(DBPlayerInjury.year == year, DBPlayerInjury.week == week)
            .all()
        )
        self.has_reports = bool(rows)
        self._reports: Dict[int, DBPlayerInjury] = {
            row.player_id: row for row in rows
        }

        self._legacy: Dict[int, str] = {}
        if not self.has_reports:
            self._legacy = {
                row[0]: row[1]
                for row in db.query(DBPlayer.id, DBPlayer.injury_status)
                .filter(DBPlayer.injury_status.isnot(None)).all()
                if row[1]
            }

    def verdict(self, player_id: int) -> InjuryVerdict:
        report = self._reports.get(player_id)
        if report is not None:
            return _from_report(report)
        if self.has_reports:
            # Reported week, player not on it: healthy.
            return _CLEAR
        return _from_legacy(self._legacy.get(player_id))


def _from_report(report: DBPlayerInjury) -> InjuryVerdict:
    status = (report.report_status or "").strip().upper() or None
    if status:
        return InjuryVerdict(
            status=status,
            detail=report.primary_injury,
            multiplier=HAIRCUTS.get(status, 1.0),
            excluded=status in EXCLUDED,
            dated=True,
        )

    # No game designation yet — fall back to practice participation.
    practice = (report.practice_status or "").strip().upper()
    multiplier = PRACTICE_HAIRCUTS.get(practice, 1.0)
    if multiplier == 1.0:
        return _CLEAR
    return InjuryVerdict(
        status="QUESTIONABLE",
        detail=report.primary_injury or "did not practice",
        multiplier=multiplier,
        excluded=False,
        dated=True,
    )


def _from_legacy(value: Optional[str]) -> InjuryVerdict:
    status = (value or "").strip().upper()
    if status in ("", "ACTIVE", "NORMAL"):
        return _CLEAR
    return InjuryVerdict(
        status=status,
        detail=None,
        multiplier=HAIRCUTS.get(status, 1.0),
        excluded=status in EXCLUDED,
        dated=False,
    )
