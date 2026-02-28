"""Seed realistic sportsbook player-prop data for offline / offseason testing.

This module inserts historically-plausible prop lines into ``sportsbook_odds``
so the sportsbook projection pipeline can be exercised without a live API.
The data mirrors a typical NFL Week 10, 2025 slate.

Usage (CLI):  ``pigskin-mastermind odds seed-props``
Usage (API):  ``POST /api/odds/seed``
"""

from datetime import datetime, timedelta
from typing import Dict, List

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBPlayer, DBSportsbookOdds

# ---------------------------------------------------------------------------
# Realistic prop lines by position
# ---------------------------------------------------------------------------
# Each entry: (market, line, over_price, under_price)
# Prices in American odds format.

_QB_PROPS = [
    ("player_pass_yds", 265.5, -115, -105),
    ("player_pass_tds", 1.5, -140, +120),
    ("player_rush_yds", 18.5, -110, -110),
]

_RB_PROPS = [
    ("player_rush_yds", 62.5, -115, -105),
    ("player_rush_tds", 0.5, -110, -110),
    ("player_receptions", 2.5, -120, +100),
    ("player_reception_yds", 18.5, -110, -110),
]

_WR_PROPS = [
    ("player_receptions", 4.5, -115, -105),
    ("player_reception_yds", 58.5, -110, -110),
]

_TE_PROPS = [
    ("player_receptions", 3.5, -115, -105),
    ("player_reception_yds", 38.5, -110, -110),
]

# Per-player overrides – more realistic lines for well-known players
_STAR_OVERRIDES: Dict[str, List[tuple]] = {
    # QBs
    "Patrick Mahomes":   [("player_pass_yds", 279.5, -115, -105), ("player_pass_tds", 2.5, +100, -120), ("player_rush_yds", 22.5, -110, -110)],
    "Josh Allen":        [("player_pass_yds", 255.5, -110, -110), ("player_pass_tds", 2.5, +105, -125), ("player_rush_yds", 36.5, -115, -105)],
    "Lamar Jackson":     [("player_pass_yds", 218.5, -115, -105), ("player_pass_tds", 1.5, -130, +110), ("player_rush_yds", 62.5, -110, -110), ("player_rush_tds", 0.5, -105, -115)],
    "Jalen Hurts":       [("player_pass_yds", 232.5, -110, -110), ("player_pass_tds", 1.5, -135, +115), ("player_rush_yds", 38.5, -115, -105), ("player_rush_tds", 0.5, -125, +105)],
    "Joe Burrow":        [("player_pass_yds", 271.5, -115, -105), ("player_pass_tds", 2.5, +110, -130)],
    "C.J. Stroud":       [("player_pass_yds", 248.5, -110, -110), ("player_pass_tds", 1.5, -120, +100)],
    "Dak Prescott":      [("player_pass_yds", 258.5, -115, -105), ("player_pass_tds", 2.5, +120, -140)],
    "Tua Tagovailoa":    [("player_pass_yds", 242.5, -110, -110), ("player_pass_tds", 1.5, -110, -110)],
    "Jayden Daniels":    [("player_pass_yds", 212.5, -110, -110), ("player_pass_tds", 1.5, -110, -110), ("player_rush_yds", 42.5, -115, -105)],
    "Anthony Richardson":[("player_pass_yds", 195.5, -110, -110), ("player_pass_tds", 1.5, +110, -130), ("player_rush_yds", 38.5, -115, -105)],
    "Brock Purdy":       [("player_pass_yds", 245.5, -115, -105), ("player_pass_tds", 1.5, -135, +115)],
    # RBs
    "Derrick Henry":     [("player_rush_yds", 89.5, -115, -105), ("player_rush_tds", 0.5, -140, +120), ("player_receptions", 1.5, -105, -115), ("player_reception_yds", 12.5, -110, -110)],
    "Saquon Barkley":    [("player_rush_yds", 82.5, -110, -110), ("player_rush_tds", 0.5, -130, +110), ("player_receptions", 3.5, -115, -105), ("player_reception_yds", 28.5, -110, -110)],
    "Bijan Robinson":    [("player_rush_yds", 78.5, -115, -105), ("player_rush_tds", 0.5, -125, +105), ("player_receptions", 3.5, -110, -110), ("player_reception_yds", 25.5, -110, -110)],
    "Breece Hall":       [("player_rush_yds", 65.5, -110, -110), ("player_rush_tds", 0.5, -110, -110), ("player_receptions", 3.5, -120, +100), ("player_reception_yds", 22.5, -110, -110)],
    "Jonathan Taylor":   [("player_rush_yds", 72.5, -115, -105), ("player_rush_tds", 0.5, -120, +100), ("player_receptions", 2.5, -110, -110), ("player_reception_yds", 18.5, -110, -110)],
    "Jahmyr Gibbs":      [("player_rush_yds", 62.5, -110, -110), ("player_rush_tds", 0.5, -110, -110), ("player_receptions", 3.5, -115, -105), ("player_reception_yds", 25.5, -110, -110)],
    "De'Von Achane":     [("player_rush_yds", 55.5, -110, -110), ("player_rush_tds", 0.5, +100, -120), ("player_receptions", 3.5, -115, -105), ("player_reception_yds", 28.5, -110, -110)],
    "Josh Jacobs":       [("player_rush_yds", 68.5, -115, -105), ("player_rush_tds", 0.5, -115, -105), ("player_receptions", 2.5, -110, -110), ("player_reception_yds", 15.5, -110, -110)],
    "Kyren Williams":    [("player_rush_yds", 62.5, -110, -110), ("player_rush_tds", 0.5, -110, -110), ("player_receptions", 2.5, -110, -110), ("player_reception_yds", 15.5, -110, -110)],
    "Travis Etienne":    [("player_rush_yds", 58.5, -110, -110), ("player_rush_tds", 0.5, +105, -125), ("player_receptions", 2.5, -115, -105), ("player_reception_yds", 18.5, -110, -110)],
    "Kenneth Walker III":[("player_rush_yds", 65.5, -115, -105), ("player_rush_tds", 0.5, -115, -105), ("player_receptions", 1.5, -105, -115), ("player_reception_yds", 10.5, -110, -110)],
    "Isiah Pacheco":     [("player_rush_yds", 58.5, -110, -110), ("player_rush_tds", 0.5, -110, -110), ("player_receptions", 1.5, -110, -110), ("player_reception_yds", 8.5, -110, -110)],
    # WRs
    "Ja'Marr Chase":     [("player_receptions", 6.5, -105, -115), ("player_reception_yds", 82.5, -115, -105)],
    "CeeDee Lamb":       [("player_receptions", 6.5, -110, -110), ("player_reception_yds", 78.5, -110, -110)],
    "Tyreek Hill":       [("player_receptions", 5.5, -110, -110), ("player_reception_yds", 72.5, -115, -105)],
    "Amon-Ra St. Brown": [("player_receptions", 6.5, -115, -105), ("player_reception_yds", 68.5, -110, -110)],
    "A.J. Brown":        [("player_receptions", 5.5, -115, -105), ("player_reception_yds", 72.5, -110, -110)],
    "Nico Collins":      [("player_receptions", 5.5, -110, -110), ("player_reception_yds", 75.5, -115, -105)],
    "Davante Adams":     [("player_receptions", 6.5, -110, -110), ("player_reception_yds", 68.5, -110, -110)],
    "Malik Nabers":      [("player_receptions", 5.5, -110, -110), ("player_reception_yds", 65.5, -110, -110)],
    "Puka Nacua":        [("player_receptions", 6.5, -110, -110), ("player_reception_yds", 72.5, -115, -105)],
    "DeVonta Smith":     [("player_receptions", 5.5, -110, -110), ("player_reception_yds", 58.5, -110, -110)],
    "Drake London":      [("player_receptions", 5.5, -115, -105), ("player_reception_yds", 62.5, -110, -110)],
    "Garrett Wilson":    [("player_receptions", 5.5, -110, -110), ("player_reception_yds", 58.5, -110, -110)],
    "Chris Olave":       [("player_receptions", 5.5, -110, -110), ("player_reception_yds", 62.5, -110, -110)],
    "Brandon Aiyuk":     [("player_receptions", 4.5, -105, -115), ("player_reception_yds", 58.5, -110, -110)],
    "DK Metcalf":        [("player_receptions", 4.5, -110, -110), ("player_reception_yds", 62.5, -115, -105)],
    "Stefon Diggs":      [("player_receptions", 5.5, -110, -110), ("player_reception_yds", 65.5, -110, -110)],
    "Mike Evans":        [("player_receptions", 4.5, -110, -110), ("player_reception_yds", 62.5, -110, -110)],
    "Terry McLaurin":    [("player_receptions", 4.5, -110, -110), ("player_reception_yds", 58.5, -110, -110)],
    # TEs
    "Travis Kelce":      [("player_receptions", 5.5, -115, -105), ("player_reception_yds", 52.5, -110, -110)],
    "Mark Andrews":      [("player_receptions", 4.5, -110, -110), ("player_reception_yds", 42.5, -110, -110)],
    "George Kittle":     [("player_receptions", 4.5, -110, -110), ("player_reception_yds", 48.5, -115, -105)],
    "T.J. Hockenson":    [("player_receptions", 4.5, -110, -110), ("player_reception_yds", 42.5, -110, -110)],
    "Dallas Goedert":    [("player_receptions", 4.5, -110, -110), ("player_reception_yds", 38.5, -110, -110)],
    "Sam LaPorta":       [("player_receptions", 4.5, -115, -105), ("player_reception_yds", 42.5, -110, -110)],
    "Dalton Kincaid":    [("player_receptions", 4.5, -110, -110), ("player_reception_yds", 42.5, -110, -110)],
    "Kyle Pitts":        [("player_receptions", 3.5, -110, -110), ("player_reception_yds", 35.5, -110, -110)],
    "Evan Engram":       [("player_receptions", 4.5, -110, -110), ("player_reception_yds", 42.5, -110, -110)],
    "Pat Freiermuth":    [("player_receptions", 3.5, -110, -110), ("player_reception_yds", 35.5, -110, -110)],
    "David Njoku":       [("player_receptions", 4.5, -115, -105), ("player_reception_yds", 42.5, -110, -110)],
}

# Fake NFL Week 10 matchups (event_id → (home, away, kickoff_offset_hours))
_SAMPLE_GAMES = [
    ("seed_evt_1",  "Kansas City Chiefs",     "Denver Broncos",         0),
    ("seed_evt_2",  "Philadelphia Eagles",    "Dallas Cowboys",         0),
    ("seed_evt_3",  "Baltimore Ravens",       "Cincinnati Bengals",     3),
    ("seed_evt_4",  "San Francisco 49ers",    "Los Angeles Rams",       7),
    ("seed_evt_5",  "Buffalo Bills",          "Miami Dolphins",         0),
    ("seed_evt_6",  "Detroit Lions",          "Green Bay Packers",      0),
    ("seed_evt_7",  "New York Jets",          "New England Patriots",   0),
    ("seed_evt_8",  "Houston Texans",         "Jacksonville Jaguars",   3),
    ("seed_evt_9",  "Atlanta Falcons",        "New Orleans Saints",     0),
    ("seed_evt_10", "Seattle Seahawks",       "Arizona Cardinals",      7),
    ("seed_evt_11", "Indianapolis Colts",     "Minnesota Vikings",      0),
    ("seed_evt_12", "Los Angeles Chargers",   "Cleveland Browns",       0),
    ("seed_evt_13", "Pittsburgh Steelers",    "Washington Commanders",  0),
    ("seed_evt_14", "Chicago Bears",          "Carolina Panthers",      0),
    ("seed_evt_15", "Tampa Bay Buccaneers",   "New York Giants",        0),
    ("seed_evt_16", "Tennessee Titans",       "Las Vegas Raiders",      0),
]

# NFL team → event_id (auto-built from _SAMPLE_GAMES)
_TEAM_TO_EVENT: Dict[str, str] = {}
for _evt_id, _home, _away, _ in _SAMPLE_GAMES:
    _TEAM_TO_EVENT[_home] = _evt_id
    _TEAM_TO_EVENT[_away] = _evt_id

# Common NFL team abbreviations → full names
_ABBREV_TO_FULL: Dict[str, str] = {
    "KC": "Kansas City Chiefs", "DEN": "Denver Broncos",
    "PHI": "Philadelphia Eagles", "DAL": "Dallas Cowboys",
    "BAL": "Baltimore Ravens", "CIN": "Cincinnati Bengals",
    "SF": "San Francisco 49ers", "LAR": "Los Angeles Rams",
    "BUF": "Buffalo Bills", "MIA": "Miami Dolphins",
    "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "NYJ": "New York Jets", "NE": "New England Patriots",
    "HOU": "Houston Texans", "JAX": "Jacksonville Jaguars",
    "ATL": "Atlanta Falcons", "NO": "New Orleans Saints",
    "SEA": "Seattle Seahawks", "ARI": "Arizona Cardinals",
    "IND": "Indianapolis Colts", "MIN": "Minnesota Vikings",
    "LAC": "Los Angeles Chargers", "CLE": "Cleveland Browns",
    "PIT": "Pittsburgh Steelers", "WAS": "Washington Commanders",
    "CHI": "Chicago Bears", "CAR": "Carolina Panthers",
    "TB": "Tampa Bay Buccaneers", "NYG": "New York Giants",
    "TEN": "Tennessee Titans", "LV": "Las Vegas Raiders",
}

_BOOKMAKERS = ["draftkings", "fanduel", "betmgm"]


def _resolve_team(nfl_team: str) -> str:
    """Turn an abbreviation like 'KC' into 'Kansas City Chiefs'."""
    return _ABBREV_TO_FULL.get(nfl_team.upper(), nfl_team)


def _find_event_for_team(nfl_team: str) -> tuple:
    """Return (event_id, home, away) for a team, or None."""
    full = _resolve_team(nfl_team)
    for evt_id, home, away, _ in _SAMPLE_GAMES:
        if full in (home, away):
            return evt_id, home, away
    return None, None, None


def _default_props_for_position(position: str) -> list:
    """Return generic prop lines based on position."""
    mapping = {"QB": _QB_PROPS, "RB": _RB_PROPS, "WR": _WR_PROPS, "TE": _TE_PROPS}
    return list(mapping.get(position, []))


def _jitter(value: float, pct: float = 0.02) -> float:
    """Apply a tiny spread to differentiate bookmaker lines."""
    import random
    # Round to nearest 0.5 to stay realistic
    raw = value * (1 + random.uniform(-pct, pct))
    return round(raw * 2) / 2


def seed_sample_props(
    db: Session,
    *,
    roster_only: bool = True,
    clear_existing: bool = False,
) -> int:
    """Insert realistic player-prop lines into ``sportsbook_odds``.

    Args:
        db: Active SQLAlchemy session.
        roster_only: If True, only seed props for players already in the
            database (on any team). If False, seed *all* players in the
            ``_STAR_OVERRIDES`` table whether or not they're on a roster.
        clear_existing: If True, delete any rows whose ``event_id`` starts
            with ``seed_evt_`` before inserting.

    Returns:
        Number of rows inserted.
    """
    import random
    random.seed(42)  # reproducible

    if clear_existing:
        db.query(DBSportsbookOdds).filter(
            DBSportsbookOdds.event_id.like("seed_evt_%")
        ).delete(synchronize_session="fetch")
        db.flush()

    base_time = datetime(2025, 11, 9, 13, 0, 0)  # Week 10 Sunday 1PM ET
    now = datetime.utcnow()

    # Collect target players
    if roster_only:
        db_players = db.query(DBPlayer).all()
    else:
        db_players = []

    # Build set of player names to seed
    players_to_seed: list[dict] = []

    for p in db_players:
        if p.position in ("QB", "RB", "WR", "TE"):
            evt_id, home, away = _find_event_for_team(p.nfl_team)
            if evt_id is None:
                # Fallback: assign to the first event
                evt_id, home, away = _SAMPLE_GAMES[0][0], _SAMPLE_GAMES[0][1], _SAMPLE_GAMES[0][2]
            players_to_seed.append({
                "name": p.name,
                "position": p.position,
                "event_id": evt_id,
                "home": home,
                "away": away,
            })

    if not roster_only:
        seen_names = {p["name"] for p in players_to_seed}
        for name, props in _STAR_OVERRIDES.items():
            if name not in seen_names:
                # Guess position from props
                has_pass = any(m.startswith("player_pass") for m, _, _, _ in props)
                has_rush_only = any(m.startswith("player_rush") for m, _, _, _ in props) and not has_pass
                has_rec = any(m.startswith("player_rec") for m, _, _, _ in props)
                if has_pass:
                    pos = "QB"
                elif has_rush_only and has_rec:
                    pos = "RB"
                elif has_rec:
                    pos = "WR"
                else:
                    pos = "RB"
                # Pick a random event
                evt = random.choice(_SAMPLE_GAMES)
                players_to_seed.append({
                    "name": name,
                    "position": pos,
                    "event_id": evt[0],
                    "home": evt[1],
                    "away": evt[2],
                })

    count = 0
    for info in players_to_seed:
        name = info["name"]
        position = info["position"]
        evt_id = info["event_id"]
        home = info["home"]
        away = info["away"]

        # Determine game time offset from event list
        offset = 0
        for g in _SAMPLE_GAMES:
            if g[0] == evt_id:
                offset = g[3]
                break
        commence = base_time + timedelta(hours=offset)

        # Pick prop lines: star overrides first, then position defaults
        if name in _STAR_OVERRIDES:
            prop_lines = _STAR_OVERRIDES[name]
        else:
            prop_lines = _default_props_for_position(position)

        for bk in _BOOKMAKERS:
            for market, line, over_price, under_price in prop_lines:
                # Add a tiny jitter per bookmaker so medians are interesting
                adj_line = _jitter(line) if bk != "draftkings" else line

                for outcome, price in [("Over", over_price), ("Under", under_price)]:
                    row = DBSportsbookOdds(
                        event_id=evt_id,
                        sport_key="americanfootball_nfl",
                        sport_title="NFL",
                        commence_time=commence,
                        home_team=home,
                        away_team=away,
                        bookmaker=bk,
                        market=market,
                        outcome_name=outcome,
                        price=float(price),
                        point=adj_line,
                        description=name,
                        fetched_at=now,
                        updated_at=now,
                    )
                    db.add(row)
                    count += 1

    db.commit()
    return count
