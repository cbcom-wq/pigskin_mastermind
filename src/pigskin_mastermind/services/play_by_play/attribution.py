"""Resolving the actor in a play's text to an athlete in that game.

ESPN's plays carry no ``participants`` array, so the actor has to come out of
the description: ``"T.Etienne left tackle to CIN 23 for 3 yards (L.Wilson)"``.

That sounds like general name matching and is not.  The candidate set is only
the ~65 players in this one game, and the payload's own boxscore hands them
over with ESPN athlete ids -- the same ids ``DBPlayer.espn_id`` stores.
"""

import re
from typing import Any, Dict, List, Optional, Sequence

# ESPN writes first-initial.Lastname, occasionally with two initial letters
# ("Ja.Phillips") to separate teammates who would otherwise collide.
_NAME = re.compile(r"\b([A-Z][a-z]?\.[A-Z][A-Za-z’'\-]+)")
_PARENTHETICAL = re.compile(r"\([^)]*\)")

_SUFFIXES = (" jr.", " sr.", " ii", " iii", " iv", " v")

# Which boxscore category proves a candidate can have played this part.
_ROLE_CATEGORY = {
    "passer": "passing",
    "rusher": "rushing",
    "receiver": "receiving",
}


def _abbreviations(full_name: str) -> List[str]:
    """The forms ESPN might use for one athlete's name."""
    parts = full_name.split()
    if len(parts) < 2:
        return []
    first, last = parts[0], " ".join(parts[1:])
    forms = ["%s.%s" % (first[0], last)]
    low = last.lower()
    for suffix in _SUFFIXES:
        if low.endswith(suffix):
            forms.append("%s.%s" % (first[0], " ".join(last.split()[:-1])))
            break
    return [f.lower() for f in forms]


class RosterIndex:
    """The athletes in one game, indexed by the abbreviations ESPN uses."""

    def __init__(self) -> None:
        self.names: Dict[str, str] = {}
        self.categories: Dict[str, set] = {}
        self._by_token: Dict[str, List[str]] = {}

    @classmethod
    def from_summary(cls, summary: Dict[str, Any]) -> "RosterIndex":
        index = cls()
        teams = (summary.get("boxscore") or {}).get("players") or []
        for team in teams:
            for category in team.get("statistics") or []:
                name = (category.get("name") or "").lower()
                for entry in category.get("athletes") or []:
                    athlete = entry.get("athlete") or {}
                    index._add(athlete.get("id"), athlete.get("displayName"), name)
        return index

    def _add(self, athlete_id: Any, display_name: Any, category: str) -> None:
        if not athlete_id or not display_name:
            return
        athlete_id = str(athlete_id)
        if athlete_id not in self.names:
            self.names[athlete_id] = display_name
            self.categories[athlete_id] = set()
            for token in _abbreviations(display_name):
                self._by_token.setdefault(token, []).append(athlete_id)
        self.categories[athlete_id].add(category)

    def resolve(self, token: str, role: Optional[str] = None) -> Optional[str]:
        """Athlete id for *token*, or None when it cannot be settled.

        A token that matches more than one athlete is settled by *role*: the
        passer on a pass play is the one who appears in the boxscore's passing
        category.  ``c.williams`` is both Caleb Williams and Chris Williams,
        and only one of them threw a pass.

        Returning None is deliberate and is not a failure mode to paper over.
        A play drawn without a name is recoverable; a play drawn under the
        wrong name is not.
        """
        candidates = self._by_token.get(token.lower(), [])
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            return None

        category = _ROLE_CATEGORY.get(role or "")
        if not category:
            return None
        narrowed = [c for c in candidates if category in self.categories.get(c, ())]
        return narrowed[0] if len(narrowed) == 1 else None


def name_tokens(description: str) -> List[str]:
    """Athlete tokens in a play's text, in order, deduplicated.

    The tackler sits in parentheses and is not the actor, so parentheticals go
    first.  A fumble names the same player twice ("C.Williams FUMBLES ... and
    recovers ... C.Williams to NO 29"), so order-preserving dedupe keeps the
    passer/receiver positions meaningful.
    """
    main = _PARENTHETICAL.sub("", description or "")
    seen, ordered = set(), []
    for token in _NAME.findall(main):
        key = token.lower()
        if key not in seen:
            seen.add(key)
            ordered.append(token)
    return ordered


def resolve_actors(
    description: str, role: str, index: RosterIndex
) -> "tuple[List[str], List[str]]":
    """Actor ids and names for one play, in role order.

    A pass credits the passer first and the receiver second; a rush credits
    the ball carrier alone.  Positions are held: if the passer cannot be
    resolved the receiver keeps its slot rather than sliding forward, because
    a receiver silently promoted into the passer's position is how one
    ambiguous name corrupts a whole stat line.
    """
    tokens: Sequence[str] = name_tokens(description)
    if not tokens:
        return [], []

    if role == "pass":
        roles = ["passer", "receiver"]
    elif role == "rush":
        roles = ["rusher"]
    else:
        return [], []

    ids: List[str] = []
    names: List[str] = []
    for token, token_role in zip(tokens, roles):
        resolved = index.resolve(token, token_role)
        if resolved:
            ids.append(resolved)
            names.append(index.names[resolved])
    return ids, names
