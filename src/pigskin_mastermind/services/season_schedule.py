"""Round-robin and playoff bracket generation. No database, no side effects.

Kept pure and separate from SeasonLeagueService because the invariants that
matter — everyone plays once a week, nobody plays themselves, venues balance —
are cheap to assert against plain tuples and miserable to debug against
committed rows in October.
"""

from typing import Any, Dict, List, Tuple

#: Bracket sizes the seeding logic supports, largest first.
SUPPORTED_BRACKETS: Tuple[int, ...] = (6, 4, 2)

#: 6 teams needs 3 rounds, 4 needs 2, 2 needs 1.
_BRACKET_ROUNDS: Dict[int, List[Tuple[str, int]]] = {
    6: [("quarterfinal", 2), ("semifinal", 2), ("final", 1)],
    4: [("semifinal", 2), ("final", 1)],
    2: [("final", 1)],
}


def round_robin_pairings(num_teams: int) -> List[List[Tuple[int, int]]]:
    """One full rotation by the circle method: ``num_teams - 1`` rounds.

    Team 0 stays fixed while the rest rotate, which is what guarantees every
    pair meets exactly once. Home and away alternate by round and by position
    so no team accumulates home games.

    Raises:
        ValueError: for an odd *num_teams*. A round robin over an odd count
            leaves one team idle each round, and a fantasy league has no
            meaning for an idle week — it is neither a win, a loss, nor a bye.
    """
    if num_teams < 2:
        raise ValueError("num_teams must be at least 2")
    if num_teams % 2 != 0:
        raise ValueError(
            f"num_teams must be even to build a round robin; got {num_teams}"
        )

    order = list(range(num_teams))
    rounds: List[List[Tuple[int, int]]] = []

    for round_index in range(num_teams - 1):
        pairs: List[Tuple[int, int]] = []
        for i in range(num_teams // 2):
            a, b = order[i], order[num_teams - 1 - i]
            # The fixed team sits at position 0 every round, so its venue can
            # only be balanced by alternating on round parity. Every other
            # pairing rotates through positions, so position parity balances
            # it. Deciding both with one combined `(round + i) % 2` test looks
            # tidier and is badly wrong: it leaves one team in a 10-team
            # league with zero home games across a full rotation.
            home_first = (round_index % 2 == 0) if i == 0 else (i % 2 == 1)
            pairs.append((a, b) if home_first else (b, a))
        rounds.append(pairs)
        # Rotate everything except the fixed first position.
        order = [order[0], order[-1]] + order[1:-1]

    return rounds


def regular_season_schedule(
    num_teams: int, weeks: int,
) -> List[List[Tuple[int, int]]]:
    """*weeks* weeks of pairings, repeating the rotation as needed.

    A 12-team league has an 11-round rotation and a 14-week regular season, so
    weeks 12-14 replay rounds 1-3 with the venues flipped.
    """
    rotation = round_robin_pairings(num_teams)
    schedule: List[List[Tuple[int, int]]] = []

    for week_index in range(weeks):
        pairs = rotation[week_index % len(rotation)]
        if (week_index // len(rotation)) % 2 == 1:
            pairs = [(away, home) for home, away in pairs]
        schedule.append(list(pairs))

    return schedule


def clamp_playoff_teams(num_teams: int, requested: int) -> int:
    """Largest supported bracket that fits both the request and the league."""
    for size in SUPPORTED_BRACKETS:
        if size <= requested and size <= num_teams:
            return size
    return 0


def playoff_rounds(
    playoff_teams: int, playoff_start_week: int,
) -> List[Dict[str, Any]]:
    """Bracket rounds, ending at ``playoff_start_week + 2``.

    The championship week is fixed and rounds are counted backwards from it, so
    a smaller bracket gives its unused early weeks back to the regular season
    rather than finishing early.
    """
    rounds = _BRACKET_ROUNDS.get(playoff_teams)
    if not rounds:
        return []

    championship_week = playoff_start_week + 2
    first_week = championship_week - (len(rounds) - 1)

    return [
        {"week": first_week + i, "round_name": name, "games": games}
        for i, (name, games) in enumerate(rounds)
    ]
