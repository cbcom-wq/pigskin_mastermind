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
    # stored blend_multi row above: model .25 and espn .20 renormalize to
    # (10*.25 + 20*.20) / .45. The stored 15.0 is deliberately ignored so a
    # viewer's reweighting and the default view go through one code path.
    assert row.consensus == pytest.approx(14.44, abs=0.01)


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
    projected = make_player(db, "Projected Guy")
    unprojected = make_player(db, "Bench Guy")

    db.add(DBWeeklyPlayerStats(
        player_id=projected.id, weekly_team_stats_id=1, week=WEEK,
        projected_points=13.2,
    ))
    db.add(DBWeeklyPlayerStats(
        player_id=unprojected.id, weekly_team_stats_id=1, week=WEEK,
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
