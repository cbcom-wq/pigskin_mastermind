"""Advanced metrics: import, trends, and the usage-vs-production scan.

The behaviours worth protecting are the ones that produce a *plausible but
wrong* number — a share against the wrong team's total, a metric whose
direction is inverted, or a "buy" that is really just a player getting worse.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBNFLGame, DBPlayer, DBPlayerAdvancedMetric, DBPlayerGameLog,
)
from pigskin_mastermind.services.advanced_metrics import (
    METRICS, for_position, format_value,
)
from pigskin_mastermind.services.advanced_metrics_import import (
    compute_share_metrics, team_by_opponent,
)
from pigskin_mastermind.services.metric_trends import (
    hot_movers, percentile, player_series,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2025


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=test_engine)
    Base.metadata.create_all(bind=test_engine)
    yield


@pytest.fixture
def db():
    session = TestSessionLocal()
    yield session
    session.close()


def make_player(db, name, position="RB", team="KC"):
    player = DBPlayer(
        player_id=f"t_{name.replace(' ', '_').lower()}",
        name=name, position=position, nfl_team=team,
    )
    db.add(player)
    db.flush()
    return player


def add_game(db, week, home, away, year=YEAR):
    db.add(DBNFLGame(year=year, week=week, home_team=home, away_team=away))
    db.flush()


def add_log(db, player, week, opponent, targets=0, carries=0, points=0.0):
    db.add(DBPlayerGameLog(
        player_id=player.id, year=YEAR, week=week, opponent=opponent,
        targets=targets, rush_att=carries, fantasy_points=points,
    ))


def add_metric(db, player, week, metric, value, year=YEAR):
    db.add(DBPlayerAdvancedMetric(
        player_id=player.id, year=year, week=week, metric=metric, value=value,
    ))


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_every_metric_declares_a_kind_and_direction():
    """The scan reads only these; a wrong one silently inverts its metric."""
    for key, entry in METRICS.items():
        assert entry.kind in ("usage", "production"), key
        assert isinstance(entry.higher_is_better, bool), key
        assert entry.positions, key


def test_metrics_where_lower_is_better_are_marked():
    """Cushion, time to throw and stacked box all improve as they fall."""
    for key in ("ngs_cushion", "ngs_time_to_throw", "ngs_stacked_box_pct",
                "ngs_rush_efficiency"):
        assert METRICS[key].higher_is_better is False, key


def test_for_position_filters(db):
    keys = {m.key for m in for_position("QB")}
    assert "ngs_time_to_throw" in keys
    assert "ngs_separation" not in keys


def test_format_respects_the_unit():
    assert format_value("snap_pct", 65.0) == "65.0%"
    assert format_value("ngs_time_to_throw", 2.744) == "2.74s"
    assert format_value("snap_pct", None) == "—"


# ---------------------------------------------------------------------------
# Team resolution and share denominators
# ---------------------------------------------------------------------------


def test_team_comes_from_the_schedule_not_the_player_column(db):
    """A share needs the right denominator, so it needs the right team.

    Game logs carry an opponent but no team, and DBPlayer.nfl_team is the
    player's *current* club — using it would put every player who has since
    moved into the wrong team's total, silently.
    """
    add_game(db, 5, home="KC", away="JAX")
    add_game(db, 5, home="BAL", away="CIN")
    db.commit()

    index = team_by_opponent(db, YEAR)
    assert index[(5, "JAX")] == "KC"
    assert index[(5, "KC")] == "JAX"
    assert index[(5, "CIN")] == "BAL"


def test_share_uses_the_team_he_played_for_not_his_current_one(db):
    """The case a backfill is full of: a player who has since changed teams."""
    add_game(db, 5, home="KC", away="JAX")
    db.commit()

    # nfl_team says JAX — where he plays *now*. In week 5 he faced JAX, so he
    # was on KC, and his share belongs against KC's total.
    moved = make_player(db, "Traded Guy", team="JAX")
    team_mate = make_player(db, "Team Mate", team="KC")
    add_log(db, moved, 5, opponent="JAX", targets=6)
    add_log(db, team_mate, 5, opponent="JAX", targets=4)
    db.commit()

    compute_share_metrics(db, [YEAR])

    share = (
        db.query(DBPlayerAdvancedMetric)
        .filter_by(player_id=moved.id, week=5, metric="target_share")
        .one()
    )
    # 6 of the 10 targets thrown by the team that played JAX.
    assert share.value == pytest.approx(60.0)


def test_touch_share_counts_carries_and_targets(db):
    add_game(db, 5, home="KC", away="JAX")
    db.commit()
    back = make_player(db, "Dual Threat", team="KC")
    other = make_player(db, "Other Guy", team="KC")
    add_log(db, back, 5, opponent="JAX", targets=3, carries=7)
    add_log(db, other, 5, opponent="JAX", targets=7, carries=3)
    db.commit()

    compute_share_metrics(db, [YEAR])
    values = {
        row.metric: row.value
        for row in db.query(DBPlayerAdvancedMetric).filter_by(player_id=back.id)
    }
    assert values["target_share"] == pytest.approx(30.0)
    assert values["rush_share"] == pytest.approx(70.0)
    assert values["touch_share"] == pytest.approx(50.0)


def test_a_week_with_no_schedule_produces_no_shares(db):
    """Without a game there is no team, and a share with a guessed denominator
    is worse than no share."""
    player = make_player(db, "Unscheduled Guy")
    add_log(db, player, 5, opponent="JAX", targets=6)
    db.commit()

    compute_share_metrics(db, [YEAR])
    assert db.query(DBPlayerAdvancedMetric).count() == 0


# ---------------------------------------------------------------------------
# Trends and percentiles
# ---------------------------------------------------------------------------


def test_series_reports_the_weeks_it_actually_covers(db):
    player = make_player(db, "Sparse Guy")
    for week in (3, 4, 7):
        add_metric(db, player, week, "snap_pct", 50.0 + week)
    db.commit()

    series = player_series(db, player.id, YEAR)[0]
    assert series.weeks_covered == 3
    assert series.points[0][0] == 3 and series.points[-1][0] == 7
    assert series.latest == pytest.approx(57.0)


def test_a_single_week_draws_no_sparkline(db):
    """One point is not a trend, and a flat line would imply stability."""
    player = make_player(db, "One Game Guy")
    add_metric(db, player, 3, "snap_pct", 50.0)
    db.commit()

    assert player_series(db, player.id, YEAR)[0].sparkline() == ""


def test_percentile_is_direction_corrected(db):
    """A high percentile must always be good, including where low is better."""
    players = [make_player(db, f"P{i}") for i in range(10)]
    for index, player in enumerate(players):
        add_metric(db, player, 5, "ngs_time_to_throw", 2.0 + index * 0.1)
    db.commit()

    # The fastest release is the best; it must land near the top.
    fastest = percentile(db, "ngs_time_to_throw", YEAR, 5, 2.0)
    slowest = percentile(db, "ngs_time_to_throw", YEAR, 5, 2.9)
    assert fastest > slowest
    assert fastest >= 90


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def _population(db, week, *, usage_start, usage_end, points_start, points_end,
                count=16):
    """A cohort that actually *moves*, so the z-scale has a real distribution.

    Every metric is scaled by how much it varies across the league, so a
    cohort holding perfectly still gives it a near-zero spread — and then any
    move at all becomes an enormous z-score. A flat population does not test
    the scan, it tests a division by almost zero.
    """
    for index in range(count):
        player = make_player(db, f"Filler {index}", position="WR")
        # Deterministic but genuinely varied: each filler drifts a different
        # amount in each direction across the two windows.
        usage_shift = (index % 7) - 3          # -3 .. +3
        points_shift = ((index * 3) % 9) - 4   # -4 .. +4
        for offset, w in enumerate(range(week - 5, week + 1)):
            recent = offset >= 3
            add_metric(db, player, w, "snap_pct",
                       50.0 + index * 0.7 + (usage_shift if recent else 0))
            add_metric(db, player, w, "fantasy_points",
                       8.0 + index * 0.3 + (points_shift if recent else 0))

    subject = make_player(db, "Subject", position="WR")
    for offset, w in enumerate(range(week - 5, week + 1)):
        recent = offset >= 3
        add_metric(db, subject, w, "snap_pct",
                   usage_end if recent else usage_start)
        add_metric(db, subject, w, "fantasy_points",
                   points_end if recent else points_start)
    db.commit()
    return subject


def test_usage_rising_ahead_of_points_is_a_buy(db):
    subject = _population(
        db, 8, usage_start=40.0, usage_end=75.0,
        points_start=9.0, points_end=9.2,
    )
    movers = hot_movers(db, YEAR, 8)
    hit = next((m for m in movers if m.player_id == subject.id), None)

    assert hit is not None, "a large usage move with flat points must surface"
    assert hit.verdict == "buy"
    assert hit.usage_delta > 0


def test_points_collapsing_on_flat_usage_is_not_a_buy(db):
    """The failure this scan was built wrong for the first time.

    `usage - production` scores a player whose scoring fell exactly like one
    whose role grew. The first is not undervalued, he is worse — so a buy
    requires the opportunity side to have actually moved.
    """
    subject = _population(
        db, 8, usage_start=50.0, usage_end=50.0,
        points_start=18.0, points_end=2.0,
    )
    movers = hot_movers(db, YEAR, 8)
    hit = next((m for m in movers if m.player_id == subject.id), None)

    assert hit is None or hit.verdict != "buy"


def test_usage_falling_while_points_hold_is_a_sell(db):
    subject = _population(
        db, 8, usage_start=80.0, usage_end=35.0,
        points_start=11.0, points_end=11.4,
    )
    movers = hot_movers(db, YEAR, 8)
    hit = next((m for m in movers if m.player_id == subject.id), None)

    assert hit is not None
    assert hit.verdict == "sell"
    assert hit.usage_delta < 0


def test_a_player_without_enough_history_is_excluded(db):
    """One game masquerading as a trend is the easiest way to be wrong."""
    _population(db, 8, usage_start=40.0, usage_end=75.0,
                points_start=9.0, points_end=9.2)
    newcomer = make_player(db, "Just Arrived", position="WR")
    add_metric(db, newcomer, 8, "snap_pct", 95.0)
    add_metric(db, newcomer, 8, "fantasy_points", 1.0)
    db.commit()

    movers = hot_movers(db, YEAR, 8)
    assert all(m.player_id != newcomer.id for m in movers)


def test_a_player_with_only_usage_is_excluded(db):
    """Without production there is no divergence, only a mover list."""
    _population(db, 8, usage_start=40.0, usage_end=75.0,
                points_start=9.0, points_end=9.2)
    usage_only = make_player(db, "Usage Only", position="WR")
    for w in range(3, 9):
        add_metric(db, usage_only, w, "snap_pct", 30.0 if w < 6 else 90.0)
    db.commit()

    movers = hot_movers(db, YEAR, 8)
    assert all(m.player_id != usage_only.id for m in movers)


def test_scan_is_empty_without_a_baseline_window(db):
    """Early weeks have nothing to compare against; inventing one is worse."""
    _population(db, 8, usage_start=40.0, usage_end=75.0,
                points_start=9.0, points_end=9.2)
    assert hot_movers(db, YEAR, 2) == []


# ---------------------------------------------------------------------------
# Rookies
# ---------------------------------------------------------------------------


def test_rookie_is_judged_against_the_season_being_scanned(db):
    """`rookie_season` is a fixed fact; an experience count would age.

    Scanning 2025 must call a 2025 rookie a rookie, and must not call a 2026
    one a rookie in 2025 — which a "years of experience" number stamped from a
    current snapshot could not get right.
    """
    subject = _population(
        db, 8, usage_start=40.0, usage_end=75.0,
        points_start=9.0, points_end=9.2,
    )
    subject.rookie_season = YEAR
    subject.draft_round = 2
    db.commit()

    hit = next(
        m for m in hot_movers(db, YEAR, 8) if m.player_id == subject.id
    )
    assert hit.is_rookie is True
    assert hit.draft_label == "R2"
    assert hit.rookie_rising is True


def test_a_later_rookie_is_not_a_rookie_in_an_earlier_season(db):
    subject = _population(
        db, 8, usage_start=40.0, usage_end=75.0,
        points_start=9.0, points_end=9.2,
    )
    subject.rookie_season = YEAR + 1
    db.commit()

    hit = next(
        m for m in hot_movers(db, YEAR, 8) if m.player_id == subject.id
    )
    assert hit.is_rookie is False


def test_an_undrafted_rookie_is_labelled_udfa(db):
    subject = _population(
        db, 8, usage_start=40.0, usage_end=75.0,
        points_start=9.0, points_end=9.2,
    )
    subject.rookie_season = YEAR
    subject.draft_round = None
    db.commit()

    hit = next(
        m for m in hot_movers(db, YEAR, 8) if m.player_id == subject.id
    )
    assert hit.draft_label == "UDFA"


def test_a_rookie_losing_opportunity_is_not_rising(db):
    """The strip is for opportunity *gained*; a fading rookie is not that."""
    subject = _population(
        db, 8, usage_start=80.0, usage_end=35.0,
        points_start=11.0, points_end=11.4,
    )
    subject.rookie_season = YEAR
    db.commit()

    hit = next(
        m for m in hot_movers(db, YEAR, 8) if m.player_id == subject.id
    )
    assert hit.is_rookie is True
    assert hit.rookie_rising is False


def test_rookie_status_does_not_change_the_ranking(db):
    """Badging is presentation; the gap score stays a measurement.

    Weighting one group would make the number mean something other than what
    the column header claims.
    """
    subject = _population(
        db, 8, usage_start=40.0, usage_end=75.0,
        points_start=9.0, points_end=9.2,
    )
    before = [
        (m.player_id, m.divergence) for m in hot_movers(db, YEAR, 8, limit=80)
    ]

    subject.rookie_season = YEAR
    subject.draft_round = 1
    db.commit()

    after = [
        (m.player_id, m.divergence) for m in hot_movers(db, YEAR, 8, limit=80)
    ]
    assert before == after


# ---------------------------------------------------------------------------
# Shared season/week resolution -- moved here from api/routes/metrics.py so
# the dashboard and /metrics/hot cannot disagree about which week has usable
# coverage.
# ---------------------------------------------------------------------------


class TestSharedWeekHelpers:
    def test_latest_season_prefers_the_requested_year(self, db):
        from pigskin_mastermind.services.metric_trends import (
            latest_season_with_metrics,
        )

        subject = make_player(db, "Helper Subject 1")
        add_metric(db, subject, 1, "snap_pct", 50.0, year=2025)
        db.commit()

        assert latest_season_with_metrics(db, 2025) == 2025

    def test_latest_season_falls_back_to_the_newest_stored(self, db):
        from pigskin_mastermind.services.metric_trends import (
            latest_season_with_metrics,
        )

        subject = make_player(db, "Helper Subject 2")
        add_metric(db, subject, 1, "snap_pct", 50.0, year=2025)
        db.commit()

        assert latest_season_with_metrics(db, 2030) == 2025

    def test_latest_season_is_none_with_no_metrics_at_all(self, db):
        from pigskin_mastermind.models.database import DBPlayerAdvancedMetric
        from pigskin_mastermind.services.metric_trends import (
            latest_season_with_metrics,
        )

        db.query(DBPlayerAdvancedMetric).delete()
        db.commit()

        assert latest_season_with_metrics(db, 2025) is None

    def test_last_full_week_is_not_simply_the_max_week(self, db):
        """A season's last stored weeks are the playoffs -- a handful of
        teams remain, so anchoring on ``max(week)`` finds almost nobody with
        continuous coverage and the page would render empty.
        """
        from pigskin_mastermind.services.metric_trends import last_full_week

        players = [make_player(db, f"Helper Subject {i}") for i in range(4)]
        for week in (1, 2, 3):
            for player in players:
                add_metric(db, player, week, "snap_pct", 50.0)
        # Week 4: playoff-style thin coverage -- one player only.
        add_metric(db, players[0], 4, "snap_pct", 55.0)
        db.commit()

        assert last_full_week(db, YEAR) == 3
