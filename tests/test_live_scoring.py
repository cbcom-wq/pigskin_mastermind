"""Live scoring and settlement.

This runs every 60 seconds during games, so idempotence is the normal path.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBLineupSlot, DBMatchup, DBPlayer, DBRosterSpot, DBTeam,
)
from pigskin_mastermind.services.live_scoring import (
    recompute_standings, refresh_week,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
WEEK = 1


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


class FakeClient:
    """Stands in for ESPN. ``status`` drives finalization."""

    def __init__(self, status="post", stats=None):
        self.status = status
        self.stats = stats or {}
        self.summary_calls = 0

    def week_events(self, year, week):
        return [{"event_id": "1", "status": self.status,
                 "home_team": "ATL", "away_team": "NO"}]

    def event_summary(self, event_id):
        self.summary_calls += 1
        return {"_fake": True}


@pytest.fixture
def scored_league(db, monkeypatch):
    """Two teams, one starter each, with known stat lines."""
    league = DBLeague(league_id="s1", name="S", year=YEAR, kind="season",
                      current_week=WEEK, regular_season_weeks=1,
                      playoff_teams=0, roster_slots={"RB": 1, "BENCH": 0})
    db.add(league)
    db.commit()

    teams = []
    for slot in (1, 2):
        team = DBTeam(team_id=f"s1-{slot}", name=f"T{slot}", owner="o",
                      league_id="s1", manager_type="ai", draft_slot=slot)
        db.add(team)
        db.commit()
        player = DBPlayer(player_id=f"espn_{slot}", name=f"P{slot}",
                          position="RB", nfl_team="ATL", espn_id=str(slot))
        db.add(player)
        db.commit()
        db.add(DBRosterSpot(league_id=league.id, team_id=team.id,
                            player_id=player.id, acquired_via="draft"))
        db.add(DBLineupSlot(team_id=team.id, year=YEAR, week=WEEK,
                            player_id=player.id, slot="RB"))
        db.commit()
        teams.append(team)

    db.add(DBMatchup(league_id=league.id, year=YEAR, week=WEEK, bracket_slot=0,
                     home_team_id=teams[0].id, away_team_id=teams[1].id))
    db.commit()

    # Team 1's back runs for 100 and a TD (16.0); team 2's for 30 (3.0).
    def fake_parse_players(_summary):
        return [
            {"espn_id": "1", "name": "P1", "team": "ATL",
             "stats": {"rush_yd": 100, "rush_td": 1}},
            {"espn_id": "2", "name": "P2", "team": "ATL",
             "stats": {"rush_yd": 30}},
        ]

    monkeypatch.setattr(
        "pigskin_mastermind.services.live_scoring.parse_player_stats",
        fake_parse_players,
    )
    monkeypatch.setattr(
        "pigskin_mastermind.services.live_scoring.parse_team_defense_stats",
        lambda _s: [],
    )
    return league, teams


class TestScoring:
    def test_writes_actual_points_onto_lineup_slots(self, db, scored_league):
        league, teams = scored_league
        result = refresh_week(db, league, WEEK, client=FakeClient())
        assert result["scored"] == 2
        rows = {r.team_id: r.actual_points
                for r in db.query(DBLineupSlot).filter_by(week=WEEK)}
        assert rows[teams[0].id] == pytest.approx(16.0)
        assert rows[teams[1].id] == pytest.approx(3.0)

    def test_matchup_totals_come_from_starters(self, db, scored_league):
        league, _teams = scored_league
        refresh_week(db, league, WEEK, client=FakeClient())
        matchup = db.query(DBMatchup).one()
        assert matchup.home_points == pytest.approx(16.0)
        assert matchup.away_points == pytest.approx(3.0)

    def test_bench_points_do_not_count(self, db, scored_league):
        league, teams = scored_league
        row = db.query(DBLineupSlot).filter_by(team_id=teams[1].id).one()
        row.slot = "BENCH"
        db.commit()
        refresh_week(db, league, WEEK, client=FakeClient())
        matchup = db.query(DBMatchup).one()
        assert matchup.away_points == 0.0

    def test_rerunning_updates_in_place(self, db, scored_league):
        league, _teams = scored_league
        refresh_week(db, league, WEEK, client=FakeClient())
        refresh_week(db, league, WEEK, client=FakeClient())
        assert db.query(DBLineupSlot).filter_by(week=WEEK).count() == 2
        assert db.query(DBMatchup).one().home_points == pytest.approx(16.0)

    def test_an_unmatched_player_is_counted_not_guessed(self, db, scored_league,
                                                        monkeypatch):
        """Crediting points to the wrong roster is worse than crediting none."""
        league, _teams = scored_league
        monkeypatch.setattr(
            "pigskin_mastermind.services.live_scoring.parse_player_stats",
            lambda _s: [{"espn_id": "9999", "name": "Ghost", "team": "ATL",
                         "stats": {"rush_yd": 100}}],
        )
        result = refresh_week(db, league, WEEK, client=FakeClient())
        assert result["unmatched"] == 1
        assert result["scored"] == 0


class TestFinalization:
    def test_an_in_progress_week_is_not_finalized(self, db, scored_league):
        league, _teams = scored_league
        result = refresh_week(db, league, WEEK, client=FakeClient(status="in"))
        assert result["finalized"] is False
        assert db.query(DBMatchup).one().status == "in_progress"
        assert league.current_week == WEEK

    def test_a_completed_week_finalizes_and_advances(self, db, scored_league):
        league, teams = scored_league
        result = refresh_week(db, league, WEEK, client=FakeClient(status="post"))
        assert result["finalized"] is True
        matchup = db.query(DBMatchup).one()
        assert matchup.status == "final"
        assert matchup.winner_team_id == teams[0].id
        assert league.current_week == WEEK + 1

    def test_finalizing_twice_does_not_advance_twice(self, db, scored_league):
        league, _teams = scored_league
        refresh_week(db, league, WEEK, client=FakeClient(status="post"))
        refresh_week(db, league, WEEK, client=FakeClient(status="post"))
        assert league.current_week == WEEK + 1

    def test_a_tie_has_no_winner(self, db, scored_league, monkeypatch):
        league, _teams = scored_league
        monkeypatch.setattr(
            "pigskin_mastermind.services.live_scoring.parse_player_stats",
            lambda _s: [
                {"espn_id": "1", "name": "P1", "team": "ATL",
                 "stats": {"rush_yd": 100}},
                {"espn_id": "2", "name": "P2", "team": "ATL",
                 "stats": {"rush_yd": 100}},
            ],
        )
        refresh_week(db, league, WEEK, client=FakeClient(status="post"))
        assert db.query(DBMatchup).one().winner_team_id is None


class TestStandings:
    def test_records_mirror_final_matchups(self, db, scored_league):
        league, teams = scored_league
        refresh_week(db, league, WEEK, client=FakeClient(status="post"))
        recompute_standings(db, league)
        winner = db.query(DBTeam).filter_by(id=teams[0].id).one()
        loser = db.query(DBTeam).filter_by(id=teams[1].id).one()
        assert (winner.wins, winner.losses) == (1, 0)
        assert (loser.wins, loser.losses) == (0, 1)
        assert winner.total_points == pytest.approx(16.0)

    def test_recomputing_is_idempotent(self, db, scored_league):
        league, teams = scored_league
        refresh_week(db, league, WEEK, client=FakeClient(status="post"))
        recompute_standings(db, league)
        recompute_standings(db, league)
        assert db.query(DBTeam).filter_by(id=teams[0].id).one().wins == 1

    def test_unplayed_weeks_do_not_count(self, db, scored_league):
        league, teams = scored_league
        db.add(DBMatchup(league_id=league.id, year=YEAR, week=2, bracket_slot=0,
                         home_team_id=teams[0].id, away_team_id=teams[1].id))
        db.commit()
        refresh_week(db, league, WEEK, client=FakeClient(status="post"))
        recompute_standings(db, league)
        assert db.query(DBTeam).filter_by(id=teams[0].id).one().wins == 1


class TestBracket:
    def test_six_team_bracket_seeds_three_v_six_and_four_v_five(self, db):
        from pigskin_mastermind.services.live_scoring import seed_playoffs

        league = DBLeague(league_id="s2", name="S", year=YEAR, kind="season",
                          regular_season_weeks=1, playoff_teams=6,
                          playoff_start_week=15)
        db.add(league)
        db.commit()
        teams = []
        for i in range(6):
            team = DBTeam(team_id=f"s2-{i}", name=f"T{i}", owner="o",
                          league_id="s2", wins=6 - i, total_points=100.0 - i)
            db.add(team)
            teams.append(team)
        db.commit()
        for slot in (0, 1):
            db.add(DBMatchup(league_id=league.id, year=YEAR, week=15,
                             bracket_slot=slot, is_playoff=True,
                             round_name="quarterfinal"))
        db.commit()

        assert seed_playoffs(db, league) == 2
        games = db.query(DBMatchup).filter_by(week=15).order_by(
            DBMatchup.bracket_slot,
        ).all()
        assert (games[0].home_team_id, games[0].away_team_id) == \
               (teams[2].id, teams[5].id)
        assert (games[1].home_team_id, games[1].away_team_id) == \
               (teams[3].id, teams[4].id)

    def test_seeding_breaks_ties_on_points_for(self, db):
        from pigskin_mastermind.services.live_scoring import seed_playoffs

        league = DBLeague(league_id="s3", name="S", year=YEAR, kind="season",
                          regular_season_weeks=1, playoff_teams=2,
                          playoff_start_week=15)
        db.add(league)
        db.commit()
        low = DBTeam(team_id="s3-a", name="Low", owner="o", league_id="s3",
                     wins=5, total_points=900.0)
        high = DBTeam(team_id="s3-b", name="High", owner="o", league_id="s3",
                      wins=5, total_points=1100.0)
        db.add_all([low, high])
        db.add(DBMatchup(league_id=league.id, year=YEAR, week=17,
                         bracket_slot=0, is_playoff=True, round_name="final"))
        db.commit()

        seed_playoffs(db, league)
        final = db.query(DBMatchup).filter_by(week=17).one()
        assert final.home_team_id == high.id


class TestRegularSeasonOnly:
    """Standings are a regular-season record; playoff games must not count.

    Folding playoff results into wins/points-for corrupts the visible standings
    and, worse, re-orders ``_seeded_teams`` — which ``_advance_bracket`` reads
    to place the bye teams into the semifinal. A team that lost every regular
    season game could be seeded first once it wins a playoff game.
    """

    @staticmethod
    def _two_team_league(db, key):
        league = DBLeague(league_id=key, name="S", year=YEAR, kind="season",
                          regular_season_weeks=1, playoff_teams=2,
                          playoff_start_week=15)
        db.add(league)
        db.commit()
        a = DBTeam(team_id=f"{key}-a", name="A", owner="o", league_id=key)
        b = DBTeam(team_id=f"{key}-b", name="B", owner="o", league_id=key)
        db.add_all([a, b])
        db.commit()
        return league, a, b

    def test_playoff_results_do_not_count_toward_the_record(self, db):
        league, a, b = self._two_team_league(db, "r1")
        # Regular season: A beats B 100-50.
        db.add(DBMatchup(league_id=league.id, year=YEAR, week=1, bracket_slot=0,
                         is_playoff=False, status="final",
                         home_team_id=a.id, away_team_id=b.id,
                         home_points=100.0, away_points=50.0,
                         winner_team_id=a.id))
        # Playoffs: B blows A out 200-10.
        db.add(DBMatchup(league_id=league.id, year=YEAR, week=15, bracket_slot=0,
                         is_playoff=True, status="final",
                         home_team_id=b.id, away_team_id=a.id,
                         home_points=200.0, away_points=10.0,
                         winner_team_id=b.id))
        db.commit()

        recompute_standings(db, league)

        assert (a.wins, a.losses) == (1, 0)
        assert (b.wins, b.losses) == (0, 1)
        assert a.total_points == pytest.approx(100.0)
        assert b.total_points == pytest.approx(50.0)

    def test_playoff_wins_do_not_reorder_the_seeds(self, db):
        """The bye teams _advance_bracket places must stay the regular-season 1/2."""
        from pigskin_mastermind.services.live_scoring import _seeded_teams

        league, a, b = self._two_team_league(db, "r2")
        db.add(DBMatchup(league_id=league.id, year=YEAR, week=1, bracket_slot=0,
                         is_playoff=False, status="final",
                         home_team_id=a.id, away_team_id=b.id,
                         home_points=100.0, away_points=50.0,
                         winner_team_id=a.id))
        db.add(DBMatchup(league_id=league.id, year=YEAR, week=15, bracket_slot=0,
                         is_playoff=True, status="final",
                         home_team_id=b.id, away_team_id=a.id,
                         home_points=200.0, away_points=10.0,
                         winner_team_id=b.id))
        db.commit()

        recompute_standings(db, league)

        assert [t.id for t in _seeded_teams(db, league)] == [a.id, b.id]


class TestWeekNotStarted:
    def test_a_week_before_kickoff_is_not_marked_in_progress(self, db,
                                                             scored_league):
        league, _teams = scored_league
        result = refresh_week(db, league, WEEK, client=FakeClient(status="pre"))
        assert result["finalized"] is False
        assert db.query(DBMatchup).one().status == "scheduled"
