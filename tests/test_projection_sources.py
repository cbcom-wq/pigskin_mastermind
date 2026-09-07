"""The multi-source weekly projection stack.

The behaviours worth protecting here are mostly about *degradation*: six
sources with six unrelated failure modes, where partial coverage is normal and
one broken provider must not cost the other five.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBPlayerProjection, DBProjectionSourceRun,
    DBWeeklyPlayerStats,
)
from pigskin_mastermind.services.projection_blender import (
    WEEKLY_MULTI_WEIGHTS, blend,
)
from pigskin_mastermind.services.projection_rankings import weekly_source_table
from pigskin_mastermind.services.projection_refresh import weekly_projection_map
from pigskin_mastermind.services.projection_sources.base import (
    SOURCE_BLEND_MULTI, SOURCE_ESPN, SOURCE_LLM, SOURCE_MODEL,
    SOURCE_SPORTSBOOK, ProjectionValue,
)
from pigskin_mastermind.services.projection_sources.consensus_source import (
    ConsensusProjectionSource, parse_consensus_html,
)
from pigskin_mastermind.services.projection_sources.espn_source import (
    EspnProjectionSource,
)
from pigskin_mastermind.services.projection_sources.llm_source import (
    LlmProjectionSource,
)
from pigskin_mastermind.services.projection_sources.nflverse_xp_source import _ewma
from pigskin_mastermind.services import weekly_projection_refresh as wpr

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
WEEK = 5


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


def make_player(db, name, position="WR", team="KC"):
    player = DBPlayer(
        player_id=f"test_{name.replace(' ', '_').lower()}",
        name=name,
        position=position,
        nfl_team=team,
    )
    db.add(player)
    db.flush()
    return player


class StubSource:
    """A provider that returns a fixed map, or raises."""

    writes = True

    def __init__(self, key, values=None, error=None, label=None, writes=True):
        self.key = key
        self.label = label or key
        self.writes = writes
        self._values = values or {}
        self._error = error

    def project_week(self, db, year, week, player_ids):
        if self._error:
            raise self._error
        return self._values


# ---------------------------------------------------------------------------
# Blend weights
# ---------------------------------------------------------------------------


def test_blend_renormalizes_over_present_sources():
    """Three of six present must not be diluted by the three that are absent."""
    result = blend(
        {"model": 20.0, "espn": 20.0, "sportsbook": 20.0},
        WEEKLY_MULTI_WEIGHTS,
    )
    assert result is not None
    assert result.points == pytest.approx(20.0)


def test_llm_alone_produces_no_consensus():
    """llm carries zero weight, so it cannot be the whole basis of a consensus."""
    assert blend({"llm": 18.0}, WEEKLY_MULTI_WEIGHTS) is None


def test_llm_does_not_shift_the_consensus():
    """A zero-weight source is displayed, never counted."""
    without = blend({"model": 10.0, "espn": 20.0}, WEEKLY_MULTI_WEIGHTS)
    with_llm = blend(
        {"model": 10.0, "espn": 20.0, "llm": 99.0}, WEEKLY_MULTI_WEIGHTS,
    )
    assert with_llm.points == pytest.approx(without.points)


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def test_ranks_are_global_and_report_their_denominator(db):
    """A roster of two is ranked against everyone, not against itself."""
    roster = [make_player(db, f"Rostered {i}") for i in range(2)]
    others = [make_player(db, f"Other {i}") for i in range(8)]

    for index, player in enumerate(roster + others):
        db.add(DBPlayerProjection(
            player_id=player.id, year=YEAR, week=WEEK,
            source=SOURCE_MODEL, projected_points=100.0 - index,
        ))
    db.commit()

    rows = weekly_source_table(db, [p.id for p in roster], YEAR, WEEK)

    assert len(rows) == 2
    cell = rows[0].cells[SOURCE_MODEL]
    assert cell.rank == 1
    # Ten WRs have a model row, not the two on this roster.
    assert cell.rank_of == 10


def test_rank_denominator_differs_per_source(db):
    """WR7-of-200 and WR7-of-1000 must not both render as WR7."""
    players = [make_player(db, f"P{i}") for i in range(5)]
    for index, player in enumerate(players):
        db.add(DBPlayerProjection(
            player_id=player.id, year=YEAR, week=WEEK,
            source=SOURCE_MODEL, projected_points=50.0 - index,
        ))
    # Sportsbook prices only two of the five.
    for index, player in enumerate(players[:2]):
        db.add(DBPlayerProjection(
            player_id=player.id, year=YEAR, week=WEEK,
            source=SOURCE_SPORTSBOOK, projected_points=40.0 - index,
        ))
    db.commit()

    rows = weekly_source_table(db, [players[0].id], YEAR, WEEK)
    cells = rows[0].cells

    assert cells[SOURCE_MODEL].rank_of == 5
    assert cells[SOURCE_SPORTSBOOK].rank_of == 2


def test_ties_share_a_rank(db):
    """Two players at the same projection are both WR1; the next is WR3."""
    tied_a = make_player(db, "Tied A")
    tied_b = make_player(db, "Tied B")
    third = make_player(db, "Third")

    for player, points in ((tied_a, 20.0), (tied_b, 20.0), (third, 10.0)):
        db.add(DBPlayerProjection(
            player_id=player.id, year=YEAR, week=WEEK,
            source=SOURCE_MODEL, projected_points=points,
        ))
    db.commit()

    rows = weekly_source_table(
        db, [tied_a.id, tied_b.id, third.id], YEAR, WEEK,
    )
    by_name = {r.name: r.cells[SOURCE_MODEL].rank for r in rows}

    assert by_name["Tied A"] == 1
    assert by_name["Tied B"] == 1
    assert by_name["Third"] == 3


def test_ranks_are_scoped_per_position(db):
    """A QB's 25 points does not outrank a WR's 15 in the WR column."""
    qb = make_player(db, "A Quarterback", position="QB")
    wr = make_player(db, "A Receiver", position="WR")

    db.add(DBPlayerProjection(
        player_id=qb.id, year=YEAR, week=WEEK,
        source=SOURCE_MODEL, projected_points=25.0,
    ))
    db.add(DBPlayerProjection(
        player_id=wr.id, year=YEAR, week=WEEK,
        source=SOURCE_MODEL, projected_points=15.0,
    ))
    db.commit()

    rows = weekly_source_table(db, [qb.id, wr.id], YEAR, WEEK)
    for row in rows:
        assert row.cells[SOURCE_MODEL].rank == 1
        assert row.cells[SOURCE_MODEL].rank_of == 1


def test_spread_excludes_the_consensus(db):
    """The consensus is an average of the others; counting it shrinks the spread."""
    player = make_player(db, "Disputed Guy")
    for source, points in (
        (SOURCE_MODEL, 10.0),
        (SOURCE_ESPN, 20.0),
        (SOURCE_BLEND_MULTI, 15.0),
    ):
        db.add(DBPlayerProjection(
            player_id=player.id, year=YEAR, week=WEEK,
            source=source, projected_points=points,
        ))
    db.commit()

    row = weekly_source_table(db, [player.id], YEAR, WEEK)[0]
    assert row.spread == pytest.approx(10.0)

    # The consensus is recomputed from the default weights, not read from the
    # stored blend_multi row above — the stored 15.0 is deliberately ignored so
    # a viewer's reweighting and the default view go through one code path.
    #
    # Derived from the weights rather than pinned to a literal: this test is
    # about *where the number comes from*, and retuning WEEKLY_MULTI_WEIGHTS
    # should not break it.
    wm = WEEKLY_MULTI_WEIGHTS[SOURCE_MODEL]
    we = WEEKLY_MULTI_WEIGHTS[SOURCE_ESPN]
    assert row.consensus == pytest.approx(
        (10.0 * wm + 20.0 * we) / (wm + we), abs=0.01,
    )
    assert row.consensus != 15.0


# ---------------------------------------------------------------------------
# Orchestrator degradation
# ---------------------------------------------------------------------------


def test_one_broken_source_does_not_stop_the_others(db, monkeypatch):
    """The whole point of the registry: partial success is the normal path."""
    player = make_player(db, "Survivor")

    monkeypatch.setattr(wpr, "build_registry", lambda keys=None, league_id=None: [
        StubSource(SOURCE_MODEL, {player.id: ProjectionValue(points=12.0)}),
        StubSource(SOURCE_SPORTSBOOK, error=RuntimeError("odds api down")),
        StubSource(SOURCE_ESPN, {player.id: ProjectionValue(points=14.0)}),
    ])

    result = wpr.refresh_week_all(db, YEAR, WEEK, player_ids=[player.id])

    assert result["sources"][SOURCE_MODEL]["status"] == "ok"
    assert result["sources"][SOURCE_ESPN]["status"] == "ok"
    assert result["sources"][SOURCE_SPORTSBOOK]["status"] == "error"

    stored = {
        row.source: row.projected_points
        for row in db.query(DBPlayerProjection).filter_by(
            player_id=player.id, week=WEEK,
        )
    }
    assert stored[SOURCE_MODEL] == 12.0
    assert stored[SOURCE_ESPN] == 14.0
    assert SOURCE_BLEND_MULTI in stored


def test_failed_source_records_its_error(db, monkeypatch):
    monkeypatch.setattr(wpr, "build_registry", lambda keys=None, league_id=None: [
        StubSource(SOURCE_SPORTSBOOK, error=RuntimeError("odds api down")),
    ])
    player = make_player(db, "Nobody")

    wpr.refresh_week_all(db, YEAR, WEEK, player_ids=[player.id])

    run = db.query(DBProjectionSourceRun).filter_by(source=SOURCE_SPORTSBOOK).one()
    assert run.status == "error"
    assert "odds api down" in run.error
    assert run.finished_at is not None


def test_read_only_source_is_not_written_back(db, monkeypatch):
    """Re-upserting llm rows would strip an agent's citations."""
    player = make_player(db, "Analysed Guy")
    db.add(DBPlayerProjection(
        player_id=player.id, year=YEAR, week=WEEK, source=SOURCE_LLM,
        projected_points=17.0, components={"citations": ["a source"]},
    ))
    db.commit()

    monkeypatch.setattr(wpr, "build_registry", lambda keys=None, league_id=None: [
        StubSource(
            SOURCE_LLM,
            {player.id: ProjectionValue(points=99.0)},
            writes=False,
        ),
    ])
    wpr.refresh_week_all(db, YEAR, WEEK, player_ids=[player.id])

    row = db.query(DBPlayerProjection).filter_by(
        player_id=player.id, source=SOURCE_LLM,
    ).one()
    assert row.projected_points == 17.0
    assert row.components["citations"] == ["a source"]


# ---------------------------------------------------------------------------
# Lineup isolation — the regression this feature is most likely to cause
# ---------------------------------------------------------------------------


def test_blend_multi_is_invisible_to_the_lineup_read_path(db, monkeypatch):
    """plan_lineup() must keep reading `model`, not the new consensus.

    weekly_projection_map's _READ_PRIORITY is (blend, model). Naming the
    consensus `blend` would silently switch every AI manager, the first-kickoff
    auto-fill, and the web auto-set button onto it.
    """
    player = make_player(db, "Starter")

    monkeypatch.setattr(wpr, "build_registry", lambda keys=None, league_id=None: [
        StubSource(SOURCE_MODEL, {player.id: ProjectionValue(points=10.0)}),
        StubSource(SOURCE_ESPN, {player.id: ProjectionValue(points=30.0)}),
    ])
    wpr.refresh_week_all(db, YEAR, WEEK, player_ids=[player.id])

    consensus = db.query(DBPlayerProjection).filter_by(
        player_id=player.id, source=SOURCE_BLEND_MULTI,
    ).one()
    assert consensus.projected_points != 10.0  # it really is a different number

    assert weekly_projection_map(db, [player.id], YEAR, WEEK) == {player.id: 10.0}


# ---------------------------------------------------------------------------
# Individual providers
# ---------------------------------------------------------------------------


def test_espn_source_skips_unprojected_rows(db):
    """0.0 is the column default for a bench row ESPN never projected."""
    from pigskin_mastermind.models.database import (
        DBLeague, DBTeam, DBWeeklyTeamStats,
    )

    projected = make_player(db, "Projected Guy")
    unprojected = make_player(db, "Bench Guy")

    # Real parent rows: the source now reaches through them for the season,
    # because weekly_player_stats has no year column of its own.
    db.add(DBLeague(league_id="lg", name="L", year=YEAR, kind="espn"))
    team = DBTeam(team_id="t", name="T", owner="o", league_id="lg")
    db.add(team)
    db.flush()
    weekly = DBWeeklyTeamStats(team_id=team.id, week=WEEK)
    db.add(weekly)
    db.flush()

    db.add(DBWeeklyPlayerStats(
        player_id=projected.id, weekly_team_stats_id=weekly.id, week=WEEK,
        projected_points=13.2,
    ))
    db.add(DBWeeklyPlayerStats(
        player_id=unprojected.id, weekly_team_stats_id=weekly.id, week=WEEK,
        projected_points=0.0,
    ))
    db.commit()

    values = EspnProjectionSource().project_week(
        db, YEAR, WEEK, [projected.id, unprojected.id],
    )
    assert set(values) == {projected.id}
    assert values[projected.id].points == 13.2


def test_llm_source_preserves_components(db):
    player = make_player(db, "Analysed Guy")
    db.add(DBPlayerProjection(
        player_id=player.id, year=YEAR, week=WEEK, source=SOURCE_LLM,
        projected_points=17.0, components={"citations": ["beat writer"]},
    ))
    db.commit()

    values = LlmProjectionSource().project_week(db, YEAR, WEEK, [player.id])
    assert values[player.id].components["citations"] == ["beat writer"]


def test_llm_source_ignores_other_weeks(db):
    player = make_player(db, "Analysed Guy")
    db.add(DBPlayerProjection(
        player_id=player.id, year=YEAR, week=WEEK + 1, source=SOURCE_LLM,
        projected_points=17.0,
    ))
    db.commit()

    assert LlmProjectionSource().project_week(db, YEAR, WEEK, [player.id]) == {}


def test_consensus_is_skipped_when_unconfigured(db, monkeypatch):
    """No feed configured is 'nothing to do', not a failure."""
    monkeypatch.delenv("PIGSKIN_CONSENSUS_URL", raising=False)
    source = ConsensusProjectionSource()
    assert source.available is False
    assert source.project_week(db, YEAR, WEEK, [1, 2, 3]) == {}


def test_consensus_resolves_published_names(db):
    player = make_player(db, "Justin Jefferson", position="WR", team="MIN")
    source = ConsensusProjectionSource(
        fetch_rows=lambda year, week: [("Justin Jefferson", "WR", 18.4)],
    )
    assert source.available is True

    values = source.project_week(db, YEAR, WEEK, [player.id])
    assert values[player.id].points == 18.4


def test_consensus_html_parsing():
    html = """
    <table><tr><th>Player</th><th>Pos</th><th>Proj</th></tr>
    <tr><td>Justin Jefferson</td><td>WR</td><td>18.4</td></tr>
    <tr><td>Patrick Mahomes</td><td>QB</td><td>21.7</td></tr>
    <tr><td>&nbsp;</td><td></td></tr></table>
    """
    rows = parse_consensus_html(html)
    assert ("Justin Jefferson", "WR", 18.4) in rows
    assert ("Patrick Mahomes", "QB", 21.7) in rows
    assert len(rows) == 2


# ---------------------------------------------------------------------------
# nflverse expected points — the pure half
# ---------------------------------------------------------------------------


def test_ewma_weights_the_most_recent_game_heaviest():
    """Oldest-first input; a changed role should move the number quickly."""
    steady = _ewma([10.0, 10.0, 10.0, 10.0], 0.65)
    assert steady == pytest.approx(10.0)

    # One 20-point game on a 5-point baseline. The unweighted mean would be
    # 8.75; the decay must pull it materially above that without letting a
    # single game become the whole forecast.
    breakout = _ewma([5.0, 5.0, 5.0, 20.0], 0.65)
    assert 8.75 < breakout < 20.0
    assert breakout == pytest.approx(11.39, abs=0.01)


def test_ewma_renormalizes_for_short_histories():
    """Two games must not be penalised against four."""
    assert _ewma([10.0, 10.0], 0.65) == pytest.approx(10.0)
    assert _ewma([], 0.65) == 0.0


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------


def test_freshness_reports_stale_when_nothing_has_run(db):
    result = wpr.freshness(db, YEAR, WEEK)
    assert result["stale"] is True
    assert result["latest_at"] is None


def test_freshness_ignores_failed_runs_when_dating_the_refresh(db, monkeypatch):
    """A source that errored is not evidence the data is fresh."""
    player = make_player(db, "Somebody")
    monkeypatch.setattr(wpr, "build_registry", lambda keys=None, league_id=None: [
        StubSource(SOURCE_SPORTSBOOK, error=RuntimeError("down")),
    ])
    wpr.refresh_week_all(db, YEAR, WEEK, player_ids=[player.id])

    result = wpr.freshness(db, YEAR, WEEK)
    assert result["sources"][SOURCE_SPORTSBOOK]["status"] == "error"
    # blend_multi still ran, so the overall stamp comes from it alone.
    assert result["sources"][SOURCE_BLEND_MULTI]["status"] == "ok"


def test_nflverse_xp_is_silent_in_week_one(db):
    """No completed games means nothing to average — and nothing to download.

    nflverse has not published a season's weekly frame at week 1, so a fetch
    would 404 and be recorded as a broken source rather than the ordinary
    "no history yet" this is.
    """
    from pigskin_mastermind.services.projection_sources.nflverse_xp_source import (
        NflverseExpectedPointsSource,
    )

    player = make_player(db, "Week One Guy")

    def explode(*args, **kwargs):  # pragma: no cover - must never be called
        raise AssertionError("week 1 must not trigger a download")

    source = NflverseExpectedPointsSource()
    source._frame = None
    import pigskin_mastermind.services.projection_sources.nflverse_xp_source as mod
    original, mod.nfl = mod.nfl, type("X", (), {"import_weekly_data": explode})
    try:
        assert source.project_week(db, YEAR, 1, [player.id]) == {}
    finally:
        mod.nfl = original


def test_model_source_skips_the_no_signal_clamp(db):
    """A model 0.0 is `max(0, base_score)` — "couldn't score him", not zero.

    Storing it ranks a player the model could not evaluate below every player
    it actually scored low, as though the model had made that call.
    """
    from pigskin_mastermind.services.projection_sources.model_source import (
        ModelProjectionSource,
    )

    scored = make_player(db, "Scored Guy")
    unscorable = make_player(db, "Unscorable Guy")
    db.commit()

    class StubBuilder:
        def ensure_players_stats(self, ids, year):
            pass

        def build_weekly_criteria(self, player_id, week, year):
            return type("C", (), {
                "opponent_defense_level": 16, "player_skill_level": 50,
            })()

    class StubService:
        def calculate_projection(self, domain, criteria):
            return 0.0 if domain.name == "Unscorable Guy" else 14.0

    source = ModelProjectionSource(builder=StubBuilder(), service=StubService())
    values = source.project_week(db, YEAR, WEEK, [scored.id, unscorable.id])

    assert set(values) == {scored.id}


def test_refresh_prunes_rows_a_source_no_longer_covers(db, monkeypatch):
    """Re-running a week must not leave last run's rows behind.

    The Refresh button re-runs the same week, so an upsert-only pass would let
    a stale number and its stale rank survive indefinitely.
    """
    covered = make_player(db, "Still Covered")
    dropped = make_player(db, "Props Pulled")
    ids = [covered.id, dropped.id]

    monkeypatch.setattr(wpr, "build_registry", lambda keys=None, league_id=None: [
        StubSource(SOURCE_SPORTSBOOK, {
            covered.id: ProjectionValue(points=12.0),
            dropped.id: ProjectionValue(points=9.0),
        }),
    ])
    wpr.refresh_week_all(db, YEAR, WEEK, player_ids=ids)
    assert db.query(DBPlayerProjection).filter_by(
        source=SOURCE_SPORTSBOOK, week=WEEK,
    ).count() == 2

    # Second pass: the book no longer prices the second player.
    monkeypatch.setattr(wpr, "build_registry", lambda keys=None, league_id=None: [
        StubSource(SOURCE_SPORTSBOOK, {covered.id: ProjectionValue(points=12.0)}),
    ])
    wpr.refresh_week_all(db, YEAR, WEEK, player_ids=ids)

    remaining = db.query(DBPlayerProjection).filter_by(
        source=SOURCE_SPORTSBOOK, week=WEEK,
    ).all()
    assert [r.player_id for r in remaining] == [covered.id]


def test_prune_never_touches_players_outside_the_pass(db, monkeypatch):
    """A --sources or partial-roster refresh must not delete what it didn't look at."""
    examined = make_player(db, "Examined")
    untouched = make_player(db, "Untouched")
    db.add(DBPlayerProjection(
        player_id=untouched.id, year=YEAR, week=WEEK,
        source=SOURCE_SPORTSBOOK, projected_points=8.0,
    ))
    db.commit()

    monkeypatch.setattr(wpr, "build_registry", lambda keys=None, league_id=None: [
        StubSource(SOURCE_SPORTSBOOK, {examined.id: ProjectionValue(points=12.0)}),
    ])
    wpr.refresh_week_all(db, YEAR, WEEK, player_ids=[examined.id])

    assert db.query(DBPlayerProjection).filter_by(
        player_id=untouched.id, source=SOURCE_SPORTSBOOK,
    ).count() == 1


# ---------------------------------------------------------------------------
# Viewer-weighted consensus and the optimal lineup
# ---------------------------------------------------------------------------


def test_consensus_follows_the_supplied_weights(db):
    """Excluding a source must change the consensus, not just hide a column."""
    player = make_player(db, "Weighted Guy")
    for source, points in ((SOURCE_MODEL, 10.0), (SOURCE_ESPN, 20.0)):
        db.add(DBPlayerProjection(
            player_id=player.id, year=YEAR, week=WEEK,
            source=source, projected_points=points,
        ))
    db.commit()

    model_only = weekly_source_table(
        db, [player.id], YEAR, WEEK, weights={SOURCE_MODEL: 1.0},
    )[0]
    espn_only = weekly_source_table(
        db, [player.id], YEAR, WEEK, weights={SOURCE_ESPN: 1.0},
    )[0]

    assert model_only.consensus == pytest.approx(10.0)
    assert espn_only.consensus == pytest.approx(20.0)
    # Both columns stay visible either way — excluded is not hidden.
    assert set(model_only.cells) == {SOURCE_MODEL, SOURCE_ESPN}


def test_relative_weights_survive_renormalization(db):
    """Weights need not sum to 1; only their ratio matters."""
    player = make_player(db, "Ratio Guy")
    for source, points in ((SOURCE_MODEL, 10.0), (SOURCE_ESPN, 20.0)):
        db.add(DBPlayerProjection(
            player_id=player.id, year=YEAR, week=WEEK,
            source=source, projected_points=points,
        ))
    db.commit()

    def consensus(weights):
        return weekly_source_table(
            db, [player.id], YEAR, WEEK, weights=weights,
        )[0].consensus

    assert consensus({SOURCE_MODEL: 3.0, SOURCE_ESPN: 1.0}) == pytest.approx(12.5)
    assert consensus({SOURCE_MODEL: 0.3, SOURCE_ESPN: 0.1}) == pytest.approx(12.5)


def test_spread_narrows_to_the_counted_sources(db):
    """A source you excluded should not still contribute its disagreement."""
    player = make_player(db, "Spread Guy")
    for source, points in (
        (SOURCE_MODEL, 10.0), (SOURCE_ESPN, 20.0), (SOURCE_SPORTSBOOK, 14.0),
    ):
        db.add(DBPlayerProjection(
            player_id=player.id, year=YEAR, week=WEEK,
            source=source, projected_points=points,
        ))
    db.commit()

    everything = weekly_source_table(db, [player.id], YEAR, WEEK)[0]
    assert everything.spread == pytest.approx(10.0)

    without_espn = weekly_source_table(
        db, [player.id], YEAR, WEEK,
        weights={SOURCE_MODEL: 1.0, SOURCE_SPORTSBOOK: 1.0},
    )[0]
    assert without_espn.spread == pytest.approx(4.0)


def test_stored_blend_multi_is_never_an_input_to_the_live_consensus(db):
    """Counting it would fold every source in twice — once via its own average."""
    player = make_player(db, "Double Counted")
    db.add(DBPlayerProjection(
        player_id=player.id, year=YEAR, week=WEEK,
        source=SOURCE_MODEL, projected_points=10.0,
    ))
    db.add(DBPlayerProjection(
        player_id=player.id, year=YEAR, week=WEEK,
        source=SOURCE_BLEND_MULTI, projected_points=999.0,
    ))
    db.commit()

    row = weekly_source_table(db, [player.id], YEAR, WEEK)[0]
    assert SOURCE_BLEND_MULTI not in row.cells
    assert row.consensus == pytest.approx(10.0)


def test_parse_weights_defaults_until_the_form_says_otherwise(db):
    """A first load and a cleared form must not look identical."""
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import (
        WEIGHTS_ACTIVE_FIELD, parse_weights,
    )

    assert parse_weights(QueryParams("week=1")) == WEEKLY_MULTI_WEIGHTS

    cleared = parse_weights(QueryParams(f"{WEIGHTS_ACTIVE_FIELD}=1"))
    assert set(cleared.values()) == {0.0}


def test_parse_weights_zeroes_unchecked_and_keeps_checked(db):
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import (
        WEIGHTS_ACTIVE_FIELD, parse_weights,
    )

    weights = parse_weights(QueryParams(
        f"{WEIGHTS_ACTIVE_FIELD}=1&src=model&w_model=0.8&w_espn=0.5",
    ))
    assert weights[SOURCE_MODEL] == pytest.approx(0.8)
    # espn carried a weight but was never checked.
    assert weights[SOURCE_ESPN] == 0.0


def test_parse_weights_falls_back_to_the_tuned_value_on_garbage(db):
    """A checked source with an unparseable weight keeps its default, not zero."""
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import (
        WEIGHTS_ACTIVE_FIELD, parse_weights,
    )

    weights = parse_weights(QueryParams(
        f"{WEIGHTS_ACTIVE_FIELD}=1&src=model&w_model=abc",
    ))
    assert weights[SOURCE_MODEL] == WEEKLY_MULTI_WEIGHTS[SOURCE_MODEL]


def test_consensus_map_omits_players_without_one(db):
    """A missing consensus must not reach plan_lineup as a projected 0.0."""
    from pigskin_mastermind.services.projection_rankings import consensus_map

    covered = make_player(db, "Covered")
    uncovered = make_player(db, "Uncovered")
    db.add(DBPlayerProjection(
        player_id=covered.id, year=YEAR, week=WEEK,
        source=SOURCE_MODEL, projected_points=11.0,
    ))
    db.commit()

    rows = weekly_source_table(db, [covered.id, uncovered.id], YEAR, WEEK)
    assert consensus_map(rows) == {covered.id: 11.0}


def test_injected_plan_lineup_matches_the_uninjected_one(db):
    """Injection must change the inputs and nothing about the decision."""
    from datetime import datetime

    from pigskin_mastermind.models.database import DBLeague, DBRosterSpot, DBTeam
    from pigskin_mastermind.services.lineup_manager import plan_lineup

    league = DBLeague(league_id="inject-test", name="Inject", year=YEAR,
                      kind="season")
    db.add(league)
    team = DBTeam(team_id="inject-team", name="Inject", owner="t",
                  league_id="inject-test")
    db.add(team)
    db.flush()

    roster = [
        make_player(db, "QB One", position="QB"),
        make_player(db, "RB One", position="RB"),
        make_player(db, "RB Two", position="RB"),
        make_player(db, "WR One", position="WR"),
        make_player(db, "WR Two", position="WR"),
        make_player(db, "TE One", position="TE"),
        make_player(db, "K One", position="K"),
        make_player(db, "DEF One", position="DEF"),
    ]
    for index, player in enumerate(roster):
        db.add(DBRosterSpot(league_id=league.id, team_id=team.id,
                            player_id=player.id))
        db.add(DBPlayerProjection(
            player_id=player.id, year=YEAR, week=WEEK,
            source=SOURCE_MODEL, projected_points=20.0 - index,
        ))
    db.commit()

    now = datetime(YEAR, 9, 1, 12, 0)
    natural = plan_lineup(db, team, YEAR, WEEK, now, league=league)
    injected = plan_lineup(
        db, team, YEAR, WEEK, now, league=league,
        players=roster,
        projections={p.id: 20.0 - i for i, p in enumerate(roster)},
    )

    assert [(d.player_id, d.slot) for d in natural.decisions] == \
           [(d.player_id, d.slot) for d in injected.decisions]
    assert natural.projected_total == injected.projected_total


def test_unchecking_a_source_stays_reversible(db):
    """The box shown for an unchecked source must not be its effective 0.

    Rendering 0.00 there means re-ticking the checkbox sends w_<key>=0, the
    source stays silent, and the checkbox bounces straight back off — the
    source becomes impossible to re-enable.
    """
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import (
        WEIGHTS_ACTIVE_FIELD, parse_source_controls,
    )

    # espn unchecked, and the form echoes back the 0.00 it was rendered with.
    weights, checked, shown = parse_source_controls(QueryParams(
        f"{WEIGHTS_ACTIVE_FIELD}=1&src=model&w_model=0.25&w_espn=0.00",
    ))
    assert weights[SOURCE_ESPN] == 0.0          # contributes nothing
    assert SOURCE_ESPN not in checked           # renders unchecked
    assert shown[SOURCE_ESPN] > 0               # but the box keeps a usable value

    # Re-ticking it with that shown value restores a real contribution.
    weights, checked, _ = parse_source_controls(QueryParams(
        f"{WEIGHTS_ACTIVE_FIELD}=1&src=model&src=espn"
        f"&w_model=0.25&w_espn={shown[SOURCE_ESPN]}",
    ))
    assert SOURCE_ESPN in checked
    assert weights[SOURCE_ESPN] > 0


def test_checked_with_an_explicit_zero_is_a_legal_state(db):
    """Typing 0 into a checked source's box is the viewer's own decision."""
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import (
        WEIGHTS_ACTIVE_FIELD, parse_source_controls,
    )

    weights, checked, _ = parse_source_controls(QueryParams(
        f"{WEIGHTS_ACTIVE_FIELD}=1&src=model&src=espn&w_model=1&w_espn=0",
    ))
    assert SOURCE_ESPN in checked
    assert weights[SOURCE_ESPN] == 0.0


# ---------------------------------------------------------------------------
# Per-team persistence of the weighting
# ---------------------------------------------------------------------------


def _demo_team(db):
    from pigskin_mastermind.models.database import DBTeam

    team = DBTeam(team_id="w-test", name="Weights", owner="t")
    db.add(team)
    db.flush()
    return team


def test_saved_weights_are_restored_on_a_plain_load(db):
    """The whole point: reopening the page must not reset the mix."""
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import resolve_controls

    team = _demo_team(db)
    team.projection_weights = {SOURCE_MODEL: 0.9, SOURCE_ESPN: 0.1}
    db.commit()

    weights, checked, _shown = resolve_controls(team, QueryParams("week=1"))
    assert weights[SOURCE_MODEL] == pytest.approx(0.9)
    assert weights[SOURCE_ESPN] == pytest.approx(0.1)
    assert SOURCE_MODEL in checked


def test_a_stored_zero_reads_as_unticked_but_stays_reversible(db):
    """Same reversibility rule as the form: the box keeps a usable number."""
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import resolve_controls

    team = _demo_team(db)
    team.projection_weights = {SOURCE_MODEL: 1.0, SOURCE_ESPN: 0.0}
    db.commit()

    weights, checked, shown = resolve_controls(team, QueryParams("week=1"))
    assert weights[SOURCE_ESPN] == 0.0
    assert SOURCE_ESPN not in checked
    assert shown[SOURCE_ESPN] > 0


def test_an_explicit_form_beats_stored_weights(db):
    """A caller passing its own weighting is honoured, not overridden."""
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import (
        WEIGHTS_ACTIVE_FIELD, resolve_controls,
    )

    team = _demo_team(db)
    team.projection_weights = {SOURCE_MODEL: 0.9, SOURCE_ESPN: 0.1}
    db.commit()

    weights, _checked, _shown = resolve_controls(team, QueryParams(
        f"{WEIGHTS_ACTIVE_FIELD}=1&src=espn&w_espn=0.7",
    ))
    assert weights[SOURCE_ESPN] == pytest.approx(0.7)
    assert weights[SOURCE_MODEL] == 0.0


def test_no_stored_weights_falls_through_to_defaults(db):
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import resolve_controls

    team = _demo_team(db)
    db.commit()

    weights, _checked, _shown = resolve_controls(team, QueryParams("week=1"))
    assert weights == WEEKLY_MULTI_WEIGHTS


def test_reset_nulls_the_column_rather_than_storing_defaults(db):
    """Null and a stored copy of the defaults are different states.

    Null means "never customised" and keeps tracking the tuned defaults if they
    are ever retuned; a stored copy would freeze this team on today's numbers.
    """
    from starlette.datastructures import QueryParams

    from pigskin_mastermind.api.routes.weekly_projections import resolve_controls

    team = _demo_team(db)
    team.projection_weights = {SOURCE_MODEL: 0.9}
    db.commit()

    # What the reset endpoint does.
    team.projection_weights = None
    db.commit()

    assert team.projection_weights is None
    weights, _checked, _shown = resolve_controls(team, QueryParams("week=1"))
    assert weights == WEEKLY_MULTI_WEIGHTS


# ---------------------------------------------------------------------------
# Market-implied source, and props scoping
# ---------------------------------------------------------------------------


def _game(db, week, home, away, spread, total, year=YEAR):
    from pigskin_mastermind.models.database import DBNFLGame

    game = DBNFLGame(
        year=year, week=week, home_team=home, away_team=away,
        spread_line=spread, total_line=total,
    )
    db.add(game)
    db.flush()
    return game


def _log(db, player, year, week, points):
    from pigskin_mastermind.models.database import DBPlayerGameLog

    db.add(DBPlayerGameLog(
        player_id=player.id, year=year, week=week,
        fantasy_points=points, rush_att=10,
    ))


def test_implied_totals_follow_the_home_spread_sign(db):
    """spread_line is positive when the HOME side is favoured.

    Inverting this sign would mark up every underdog and mark down every
    favourite -- a wrong number in the right shape, which is the hardest kind
    to notice.
    """
    from pigskin_mastermind.services.projection_sources.market_source import (
        _implied_totals,
    )

    game = _game(db, WEEK, "SEA", "NE", spread=3.5, total=44.5)
    totals = _implied_totals([game])

    assert totals["SEA"][0] == pytest.approx(24.0)   # home favourite
    assert totals["NE"][0] == pytest.approx(20.5)
    assert sum(t for t, _ in totals.values()) == pytest.approx(44.5)


def test_market_marks_up_a_good_environment_and_down_a_bad_one(db):
    from pigskin_mastermind.services.projection_sources.market_source import (
        MarketImpliedSource,
    )

    _game(db, WEEK, "BAL", "IND", spread=3.0, total=47.5)   # BAL 25.25
    _game(db, WEEK, "ATL", "TB", spread=-3.5, total=42.5)   # ATL 19.5

    good = make_player(db, "Favoured Guy", position="RB", team="BAL")
    bad = make_player(db, "Underdog Guy", position="RB", team="ATL")
    for player in (good, bad):
        for week in range(1, 5):
            _log(db, player, YEAR - 1, week, 15.0)
    db.commit()

    values = MarketImpliedSource().project_week(
        db, YEAR, WEEK, [good.id, bad.id],
    )
    assert values[good.id].points > 15.0
    assert values[bad.id].points < 15.0


def test_market_says_nothing_when_the_week_is_unpriced(db):
    """No line means no implied total. Silence beats a confident zero."""
    from pigskin_mastermind.services.projection_sources.market_source import (
        MarketImpliedSource,
    )

    _game(db, WEEK, "BAL", "IND", spread=None, total=None)
    player = make_player(db, "Unpriced Guy", position="RB", team="BAL")
    _log(db, player, YEAR - 1, 1, 15.0)
    db.commit()

    assert MarketImpliedSource().project_week(db, YEAR, WEEK, [player.id]) == {}


def test_market_baseline_reaches_back_into_last_season(db):
    """Week 1 has no current-season games; refusing to project would make this
    source useless exactly when a lineup is first set."""
    from pigskin_mastermind.services.projection_sources.market_source import (
        MarketImpliedSource,
    )

    _game(db, 1, "BAL", "IND", spread=3.0, total=47.5)
    player = make_player(db, "Week One Back", position="RB", team="BAL")
    for week in range(14, 18):
        _log(db, player, YEAR - 1, week, 12.0)
    db.commit()

    values = MarketImpliedSource().project_week(db, YEAR, 1, [player.id])
    assert player.id in values
    assert values[player.id].points > 0


def test_market_ignores_a_stored_bye_in_the_baseline(db):
    """A 0.0 with no stat line is a bye, not a game the player was bad in."""
    from pigskin_mastermind.models.database import DBPlayerGameLog
    from pigskin_mastermind.services.projection_sources.market_source import (
        _recent_average,
    )

    player = make_player(db, "Bye Guy", position="RB", team="BAL")
    for week in (1, 2, 3):
        _log(db, player, YEAR, week, 12.0)
    db.add(DBPlayerGameLog(
        player_id=player.id, year=YEAR, week=4, fantasy_points=0.0,
    ))
    db.commit()

    assert _recent_average(db, player.id, YEAR, 5) == pytest.approx(12.0)


def test_defense_is_scaled_by_the_opponent_total(db):
    """A defense facing a low-scoring offense is in a good spot, not a bad one."""
    from pigskin_mastermind.services.projection_sources.market_source import (
        MarketImpliedSource,
    )

    # SF hosts a heavy underdog: opponent implied total is low.
    _game(db, WEEK, "SF", "CAR", spread=10.0, total=40.0)   # CAR 15.0
    _game(db, WEEK, "KC", "BUF", spread=0.0, total=50.0)    # both 25.0

    good = make_player(db, "49ers D/ST", position="DEF", team="SF")
    plain = make_player(db, "Chiefs D/ST", position="DEF", team="KC")
    for player in (good, plain):
        for week in range(1, 5):
            _log(db, player, YEAR - 1, week, 8.0)
    db.commit()

    values = MarketImpliedSource().project_week(db, YEAR, WEEK, [good.id, plain.id])
    assert values[good.id].points > values[plain.id].points


def test_props_are_scoped_to_the_week_being_projected(db):
    """The bug that let a seeded 2025 slate produce 2026 week-1 numbers.

    Without an event scope, _fetch_props matches a player's name across every
    odds row ever stored -- no year, no week, no event.
    """
    from pigskin_mastermind.models.database import DBSportsbookOdds
    from pigskin_mastermind.services.projection_sources.sportsbook_source import (
        SportsbookProjectionSource,
    )

    player = make_player(db, "Propped Guy", position="RB", team="BAL")
    # An odds event for a completely different game.
    db.add(DBSportsbookOdds(
        event_id="other_game", sport_key="americanfootball_nfl",
        home_team="Kansas City Chiefs", away_team="Denver Broncos",
        bookmaker="draftkings", market="player_rush_yds",
        outcome_name="Over", point=80.0, description="Propped Guy",
    ))
    # The week being projected is a game those props have nothing to do with.
    _game(db, WEEK, "BAL", "IND", spread=3.0, total=47.5)
    db.commit()

    values = SportsbookProjectionSource().project_week(
        db, YEAR, WEEK, [player.id],
    )
    assert values == {}


def test_props_are_used_when_the_event_matches_the_game(db):
    """Full club names on the odds side must still join to abbreviations."""
    from pigskin_mastermind.models.database import DBSportsbookOdds
    from pigskin_mastermind.services.projection_sources.sportsbook_source import (
        _events_for_week,
    )

    _game(db, WEEK, "KC", "DEN", spread=3.0, total=47.5)
    db.add(DBSportsbookOdds(
        event_id="the_right_game", sport_key="americanfootball_nfl",
        home_team="Kansas City Chiefs", away_team="Denver Broncos",
        bookmaker="draftkings", market="player_rush_yds",
        outcome_name="Over", point=80.0, description="Somebody",
    ))
    db.commit()

    events = _events_for_week(db, YEAR, WEEK)
    assert events["KC"] == "the_right_game"
    assert events["DEN"] == "the_right_game"


def test_espn_source_is_scoped_to_the_season(db):
    """weekly_player_stats has no year column; the league supplies it.

    Its parent's UniqueConstraint is ('team_id', 'week'), so 2025 week 1 and
    2026 week 1 are the same slot. Filtering on week alone served an archived
    league's projections as though they were this season's.
    """
    from pigskin_mastermind.models.database import (
        DBLeague, DBTeam, DBWeeklyTeamStats,
    )

    player = make_player(db, "Two Season Guy")

    for year, points in ((YEAR - 1, 25.0), (YEAR, 12.0)):
        league = DBLeague(
            league_id=f"lg-{year}", name=str(year), year=year, kind="espn",
        )
        db.add(league)
        team = DBTeam(team_id=f"t-{year}", name=str(year), owner="o",
                      league_id=f"lg-{year}")
        db.add(team)
        db.flush()
        weekly = DBWeeklyTeamStats(team_id=team.id, week=WEEK)
        db.add(weekly)
        db.flush()
        db.add(DBWeeklyPlayerStats(
            player_id=player.id, weekly_team_stats_id=weekly.id,
            week=WEEK, projected_points=points,
        ))
    db.commit()

    this_year = EspnProjectionSource().project_week(db, YEAR, WEEK, [player.id])
    last_year = EspnProjectionSource().project_week(
        db, YEAR - 1, WEEK, [player.id],
    )

    assert this_year[player.id].points == 12.0
    assert last_year[player.id].points == 25.0


def test_panel_resolves_a_season_league_roster(db):
    """The panel must work on DBRosterSpot teams, not just ESPN snapshots.

    A season league is the one actually being played; gating the view on
    weekly_team_stats meant it could never render there.
    """
    from pigskin_mastermind.models.database import DBLeague, DBRosterSpot, DBTeam
    from pigskin_mastermind.api.routes.weekly_projections import (
        team_week_player_ids,
    )

    league = DBLeague(
        league_id="season-x", name="Season", year=YEAR, kind="season",
        status="in_season", current_week=WEEK,
    )
    db.add(league)
    team = DBTeam(team_id="season-team", name="Mine", owner="o",
                  league_id="season-x")
    db.add(team)
    db.flush()

    roster = [make_player(db, f"Season Guy {i}") for i in range(3)]
    for player in roster:
        db.add(DBRosterSpot(
            league_id=league.id, team_id=team.id, player_id=player.id,
        ))
    db.commit()

    # No DBWeeklyTeamStats exists for this team at all.
    ids = team_week_player_ids(db, team, WEEK)
    assert sorted(ids) == sorted(p.id for p in roster)


# ---------------------------------------------------------------------------
# Week-scoped injury status
# ---------------------------------------------------------------------------


def _injury(db, player, week, report=None, practice=None, year=YEAR):
    from pigskin_mastermind.models.database import DBPlayerInjury

    db.add(DBPlayerInjury(
        player_id=player.id, year=year, week=week,
        report_status=report, practice_status=practice,
    ))


def test_a_weeks_report_overrides_the_undated_column(db):
    """The whole point: a season-old ESPN flag must not survive a real report.

    Absence from an injury report IS the report saying he is healthy, so a
    player the week does not mention comes back clear even though the legacy
    column still says QUESTIONABLE.
    """
    from pigskin_mastermind.services.injury_status import InjuryIndex

    stale = make_player(db, "Stale Flag Guy")
    stale.injury_status = "QUESTIONABLE"
    reported = make_player(db, "Actually Hurt Guy")
    _injury(db, reported, WEEK, report="Out")
    db.commit()

    index = InjuryIndex(db, YEAR, WEEK)
    assert index.has_reports is True
    assert index.verdict(stale.id).status is None
    assert index.verdict(reported.id).excluded is True


def test_the_legacy_column_is_used_only_when_no_report_exists(db):
    """Without the import, behaviour must not silently become 'nobody is hurt'."""
    from pigskin_mastermind.services.injury_status import InjuryIndex

    player = make_player(db, "Legacy Guy")
    player.injury_status = "QUESTIONABLE"
    db.commit()

    verdict = InjuryIndex(db, YEAR, WEEK).verdict(player.id)
    assert verdict.status == "QUESTIONABLE"
    assert verdict.multiplier == pytest.approx(0.85)
    # Marked so the UI can say it is unverified rather than imply a designation.
    assert verdict.dated is False
    assert "unverified" in verdict.reason


def test_doubtful_is_discounted_not_benched(db):
    """Existing deliberate behaviour: a doubtful star still beats a healthy WR4."""
    from pigskin_mastermind.services.injury_status import InjuryIndex

    player = make_player(db, "Doubtful Star")
    _injury(db, player, WEEK, report="Doubtful")
    db.commit()

    verdict = InjuryIndex(db, YEAR, WEEK).verdict(player.id)
    assert verdict.excluded is False
    assert verdict.multiplier == pytest.approx(0.50)


def test_practice_status_carries_until_the_friday_designation(db):
    """report_status is NULL until ~Friday; a full non-participant still counts."""
    from pigskin_mastermind.services.injury_status import InjuryIndex

    absent = make_player(db, "Did Not Practice")
    limited = make_player(db, "Limited Practice")
    _injury(db, absent, WEEK, practice="Did Not Participate In Practice")
    _injury(db, limited, WEEK, practice="Limited Participation in Practice")
    db.commit()

    index = InjuryIndex(db, YEAR, WEEK)
    assert index.verdict(absent.id).multiplier < 1.0
    assert index.verdict(absent.id).dated is True
    # A limited practice is barely a signal and must not silently discount.
    assert index.verdict(limited.id).multiplier == 1.0


def test_injury_index_is_week_scoped(db):
    """Last week's Out must not bench a player who has since been cleared."""
    from pigskin_mastermind.services.injury_status import InjuryIndex

    player = make_player(db, "Recovered Guy")
    _injury(db, player, WEEK - 1, report="Out")
    _injury(db, player, WEEK, report=None, practice="Full Participation in Practice")
    db.commit()

    assert InjuryIndex(db, YEAR, WEEK - 1).verdict(player.id).excluded is True
    assert InjuryIndex(db, YEAR, WEEK).verdict(player.id).status is None


def test_gsis_index_prefers_the_named_row_over_a_placeholder(db):
    """570 gsis ids here are held by two rows; the real player must win.

    A plain dict comprehension keeps whichever row came last, which sent depth
    ranks and injury reports to nameless stubs while the rostered player got
    nothing.
    """
    from pigskin_mastermind.services.nfl_data_service import NFLDataService

    real = make_player(db, "Real Player")
    real.gsis_id = "00-0012345"
    stub = make_player(db, "Unknown")
    stub.gsis_id = "00-0012345"
    db.commit()

    index = NFLDataService(db)._gsis_index()
    assert index["00-0012345"] == real.id
    assert index["00-0012345"] != stub.id


def test_merging_identities_repoints_injury_rows(db):
    """Every child table needs an explicit mover in merge_duplicates.

    A table added without one does not fail loudly — its rows are left
    pointing at a deleted player id, and the only symptom is an injury
    designation silently vanishing from a lineup.
    """
    from pigskin_mastermind.models.database import DBPlayerInjury
    from pigskin_mastermind.services.player_identity import PlayerIdentityService

    # The id prefixes matter: merge_duplicates only treats `nfl_*` / `ffc_*`
    # rows as duplicates and folds them into an ESPN survivor. A pair built
    # with any other prefix is never considered, and the test passes without
    # exercising a single line of the merge.
    survivor = DBPlayer(
        player_id="espn_9999", name="Real Player", position="RB",
        nfl_team="KC", gsis_id="00-0099887", espn_id="9999",
    )
    duplicate = DBPlayer(
        player_id="nfl_00-0099887", name="Unknown", position="RB",
        nfl_team="KC", gsis_id="00-0099887", espn_id="9999",
    )
    db.add_all([survivor, duplicate])
    db.flush()

    # The report sits on the row that gets deleted — that is the case an
    # explicit mover exists for.
    _injury(db, duplicate, WEEK, report="Out")
    db.commit()

    PlayerIdentityService(db).merge_duplicates(dry_run=False)
    db.commit()

    player_ids = {p.id for p in db.query(DBPlayer).all()}
    rows = db.query(DBPlayerInjury).all()

    assert rows, "the merge dropped the injury report entirely"
    orphans = [row for row in rows if row.player_id not in player_ids]
    assert not orphans, (
        "injury rows left pointing at a deleted player — merge_duplicates "
        "needs an explicit mover for every child table"
    )


def test_an_injury_report_alone_keeps_a_row_from_being_deleted(db):
    """_has_no_data guards a deletion, so every child table must appear in it.

    A placeholder row that looks empty is dropped outright, without any mover
    running — so a table missing from that check is not merely orphaned, its
    rows are destroyed along with the player they described.
    """
    from pigskin_mastermind.models.database import DBPlayerInjury
    from pigskin_mastermind.services.player_identity import PlayerIdentityService

    # No ESPN counterpart, so no survivor: the only question is whether this
    # row counts as empty.
    orphaned = DBPlayer(
        player_id="nfl_00-0055555", name="Unknown", position="RB",
        nfl_team="KC", gsis_id="00-0055555",
    )
    db.add(orphaned)
    db.flush()
    _injury(db, orphaned, WEEK, report="Out")
    db.commit()

    PlayerIdentityService(db).merge_duplicates(dry_run=False)
    db.commit()

    assert db.query(DBPlayerInjury).count() == 1
    assert db.query(DBPlayer).filter_by(id=orphaned.id).first() is not None
