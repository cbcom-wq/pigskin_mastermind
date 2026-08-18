"""Schedule-backed bye-week detection.

``DBPlayerGameLog`` documents itself as "one row per player per game", but both
ESPN import paths write a row for every week a player is *rostered* — including
the week their team is on bye. Those rows carry an empty stat line and 0.0
fantasy points, and the two aggregation sites
(:meth:`NFLDataService.compute_season_stats_from_game_logs` and
:meth:`ESPNSyncService._aggregate_season_stats`) counted them with ``len(logs)``.
That inflated ``games_played`` by one and deflated ``fantasy_points_avg`` by
~5% for every player with a bye — and that average is the input to
``ProjectionBaselines`` and ``_compute_expected_games``, so the error is
inherited by every projection built on it.

A bye cannot be identified from the score: a K or DEF can genuinely put up
0.0 in a game that really happened. It has to come from the schedule, which is
what ``DBNFLGame`` is for. This module holds that single definition so the
importers, the aggregation, and the criteria builder cannot drift apart on what
"on bye" means.
"""

from typing import Dict, Optional, Set

from sqlalchemy.orm import Session

from pigskin_mastermind.models.database import DBNFLGame, DBPlayer, DBPlayerGameLog
from pigskin_mastermind.utils.nfl_teams import normalize_team

#: Every counting stat on a game log. A row where all of these are zero and
#: fantasy points are zero carries no evidence the player took the field.
_STAT_FIELDS = (
    'pass_att', 'pass_cmp', 'pass_yd', 'pass_td', 'pass_int',
    'rush_att', 'rush_yd', 'rush_td',
    'targets', 'rec', 'rec_yd', 'rec_td',
    'fumbles', 'fumbles_lost', 'two_pt_conversions',
)


class ScheduleIndex:
    """Answers "did this team play in this week?" from :class:`DBNFLGame`.

    Loads one set of ``(team, week)`` pairs per year and caches it, because the
    aggregation asks the same question once per game log — tens of thousands of
    times per import.
    """

    def __init__(self, db: Session):
        self.db = db
        self._weeks_by_year: Dict[int, Set[tuple]] = {}

    def _load(self, year: int) -> Set[tuple]:
        if year not in self._weeks_by_year:
            pairs: Set[tuple] = set()
            for game in self.db.query(
                DBNFLGame.week, DBNFLGame.home_team, DBNFLGame.away_team,
            ).filter(DBNFLGame.year == year):
                week, home, away = game
                pairs.add((home, week))
                pairs.add((away, week))
            self._weeks_by_year[year] = pairs
        return self._weeks_by_year[year]

    def has_schedule(self, year: int) -> bool:
        """True when any game is stored for *year*."""
        return bool(self._load(year))

    def played(self, team: Optional[str], year: int, week: int) -> bool:
        """True when *team* has a game stored in *week*."""
        canonical = normalize_team(team)
        if not canonical:
            return False
        return (canonical, week) in self._load(year)

    def is_bye(self, team: Optional[str], year: int, week: int) -> bool:
        """True only when the schedule is loaded and genuinely has no game.

        Returns False for an unknown team and for a year whose schedule was
        never imported — in both cases every week looks missing, and guessing
        would discard real games.
        """
        if not normalize_team(team):
            return False
        if not self.has_schedule(year):
            return False
        return not self.played(team, year, week)


class PlayerTeams:
    """Caches ``player_id -> DBPlayer.nfl_team``.

    The bye check runs once per game log — tens of thousands of times per
    import — and the team is the only player field it needs.
    """

    def __init__(self, db: Session):
        self.db = db
        self._teams: Dict[int, Optional[str]] = {}

    def get(self, player_id: Optional[int]) -> Optional[str]:
        if player_id is None:
            return None
        if player_id not in self._teams:
            row = (
                self.db.query(DBPlayer.nfl_team)
                .filter(DBPlayer.id == player_id)
                .first()
            )
            self._teams[player_id] = row[0] if row else None
        return self._teams[player_id]


def is_bye_stat_line(
    index: ScheduleIndex,
    team: Optional[str],
    year: int,
    week: int,
    fantasy_points: Optional[float],
    stats: Dict[str, object],
) -> bool:
    """True when these numbers record a bye rather than a game.

    Requires **both** that the team had no game that week and that the line
    is empty. The second condition is what makes this safe against a stale
    ``DBPlayer.nfl_team``: game logs have no team column, so the team is read
    from the player's *current* roster spot, which for a past season may be
    the wrong franchise after free agency. If that misidentifies the team, a
    real game still has a stat line and is left alone.
    """
    if fantasy_points:
        return False
    if any(stats.get(field) for field in _STAT_FIELDS):
        return False
    return index.is_bye(team, year, week)


def is_bye_row(
    index: ScheduleIndex,
    team: Optional[str],
    log: DBPlayerGameLog,
) -> bool:
    """:func:`is_bye_stat_line` for a stored ``DBPlayerGameLog`` row."""
    return is_bye_stat_line(
        index, team, log.year, log.week, log.fantasy_points,
        {field: getattr(log, field, 0) for field in _STAT_FIELDS},
    )


def drop_bye_weeks(
    index: ScheduleIndex,
    team: Optional[str],
    logs,
) -> list:
    """Return *logs* without the rows that represent byes."""
    return [log for log in logs if not is_bye_row(index, team, log)]


def purge_bye_week_game_logs(
    db: Session,
    year: int,
    dry_run: bool = True,
) -> int:
    """Delete stored bye-week rows from ``player_game_logs`` for *year*.

    The importers no longer create these, but rows written before that fix
    remain, and they skew more than ``games_played``: a 0.0 bye inside the
    last-3-games window drags down ``_calculate_momentum`` and inflates the
    standard deviation behind the consistency score.

    Defaults to ``dry_run=True``, matching
    ``PlayerIdentityService.merge_duplicates``. Returns the number of rows
    removed (or that would be removed).
    """
    index = ScheduleIndex(db)
    if not index.has_schedule(year):
        return 0

    rows = (
        db.query(DBPlayerGameLog, DBPlayer.nfl_team)
        .join(DBPlayer, DBPlayer.id == DBPlayerGameLog.player_id)
        .filter(DBPlayerGameLog.year == year)
        .all()
    )

    removed = 0
    for log, nfl_team in rows:
        if not is_bye_row(index, nfl_team, log):
            continue
        removed += 1
        if not dry_run:
            db.delete(log)

    if not dry_run and removed:
        db.commit()
    return removed
