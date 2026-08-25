"""Committing a finished draft into a persisted league.

The commit is one transaction with invariants asserted before it lands, because
a malformed league is far worse to discover in week 6 than at creation.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBLeague, DBMatchup, DBPlayer, DBRosterSpot, DBTeam,
)
from pigskin_mastermind.services.mock_draft import draft_engine
from pigskin_mastermind.services.season_league import (
    DraftCommitError, SeasonLeagueService,
)

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026


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


@pytest.fixture
def pool(db):
    """Four teams x two rounds = 8 players, all resolvable by db_id."""
    positions = ["QB", "RB", "WR", "TE", "QB", "RB", "WR", "TE"]
    players = []
    for i, pos in enumerate(positions):
        p = DBPlayer(
            player_id=f"ffc_{i}", name=f"Player {i}", position=pos, nfl_team="ATL",
        )
        db.add(p)
        players.append(p)
    db.commit()
    return [
        {
            "id": p.player_id, "db_id": p.id, "name": p.name,
            "position": p.position, "nfl_team": p.nfl_team,
            "projected_points": 100.0 - i, "adp_rank": float(i + 1),
        }
        for i, p in enumerate(players)
    ]


@pytest.fixture
def finished_draft(pool):
    """A complete 4-team, 2-round draft with the user at slot 1."""
    state = draft_engine.create_draft(
        num_teams=4, num_rounds=2, user_pick_position=1,
        player_pool=pool,
        lineup_slots={"QB": 1, "RB": 1, "FLEX": 0, "BENCH": 1},
    )
    draft_id = state["draft_id"]
    while draft_engine.get_draft(draft_id)["status"] == "in_progress":
        current = draft_engine.get_draft(draft_id)
        if current["current_slot"] == 1:
            available = current["available_players"]
            draft_engine.make_user_pick(draft_id, available[0]["id"])
        else:
            draft_engine.advance_one_ai_pick(draft_id)
    return draft_id


class TestCommitSucceeds:
    def test_creates_a_season_league(self, db, finished_draft):
        service = SeasonLeagueService(db)
        league = service.create_from_draft(
            finished_draft, name="My League", user_team_name="Mine",
            owner="Brandon", year=YEAR,
        )
        assert league.kind == "season"
        assert league.status == "in_season"
        assert league.current_week == 1
        assert league.year == YEAR

    def test_creates_one_team_per_draft_slot(self, db, finished_draft):
        SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        teams = db.query(DBTeam).all()
        assert len(teams) == 4

    def test_the_user_team_is_human_and_claimed(self, db, finished_draft):
        SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        mine = db.query(DBTeam).filter_by(name="Mine").one()
        assert mine.manager_type == "human"
        assert mine.is_user_team is True
        assert mine.draft_slot == 1

    def test_other_teams_are_ai_and_unclaimed(self, db, finished_draft):
        SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        bots = db.query(DBTeam).filter(DBTeam.manager_type == "ai").all()
        assert len(bots) == 3
        assert all(b.is_user_team is False for b in bots)
        assert all(b.ai_strategy for b in bots)

    def test_every_team_gets_every_pick_as_a_roster_spot(self, db, finished_draft):
        SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        assert db.query(DBRosterSpot).count() == 8
        for team in db.query(DBTeam).all():
            spots = db.query(DBRosterSpot).filter_by(team_id=team.id).count()
            assert spots == 2

    def test_no_player_lands_on_two_teams(self, db, finished_draft):
        SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        ids = [s.player_id for s in db.query(DBRosterSpot).all()]
        assert len(ids) == len(set(ids))

    def test_the_draft_snapshot_is_stored(self, db, finished_draft):
        league = SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        assert len(league.draft_snapshot) == 8

    def test_espn_leagues_are_untouched_by_the_commit(self, db, finished_draft):
        """DBPlayer.team_id is the ESPN path and must stay clear."""
        SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        assert db.query(DBPlayer).filter(DBPlayer.team_id.isnot(None)).count() == 0

    def test_team_names_are_unique(self, db, finished_draft):
        """TeamNameGenerator draws from a small word pool and repeats readily."""
        SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        names = [t.name for t in db.query(DBTeam).all()]
        assert len(names) == len(set(names))


class TestSchedule:
    def test_every_team_plays_once_per_regular_week(self, db, finished_draft):
        league = SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        for week in range(1, league.regular_season_weeks + 1):
            games = db.query(DBMatchup).filter_by(league_id=league.id, week=week).all()
            ids = [g.home_team_id for g in games] + [g.away_team_id for g in games]
            assert sorted(ids) == sorted(t.id for t in db.query(DBTeam).all())

    def test_nobody_plays_themselves(self, db, finished_draft):
        league = SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        for game in db.query(DBMatchup).filter_by(league_id=league.id).all():
            if game.home_team_id and game.away_team_id:
                assert game.home_team_id != game.away_team_id

    def test_playoff_rows_exist_unseeded(self, db, finished_draft):
        league = SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        playoffs = db.query(DBMatchup).filter_by(
            league_id=league.id, is_playoff=True,
        ).all()
        assert playoffs
        assert all(p.home_team_id is None for p in playoffs)

    def test_a_four_team_league_gets_a_four_team_bracket(self, db, finished_draft):
        league = SeasonLeagueService(db).create_from_draft(
            finished_draft, name="L", user_team_name="Mine", owner="B", year=YEAR,
        )
        assert league.playoff_teams == 4
        # 4-team bracket runs weeks 16-17, so week 15 stays regular season
        assert league.regular_season_weeks == 15


class TestCommitRefuses:
    def test_an_unfinished_draft_is_rejected(self, db, pool):
        state = draft_engine.create_draft(
            num_teams=4, num_rounds=2, user_pick_position=1, player_pool=pool,
        )
        with pytest.raises(DraftCommitError, match="not complete"):
            SeasonLeagueService(db).create_from_draft(
                state["draft_id"], name="L", user_team_name="M", owner="B", year=YEAR,
            )

    def test_an_unknown_draft_is_rejected(self, db):
        with pytest.raises(DraftCommitError, match="not found"):
            SeasonLeagueService(db).create_from_draft(
                "no-such-draft", name="L", user_team_name="M", owner="B", year=YEAR,
            )

    def test_an_odd_team_count_is_rejected(self, db, pool):
        """A round robin over an odd count leaves a team idle every week."""
        state = draft_engine.create_draft(
            num_teams=3, num_rounds=2, user_pick_position=1, player_pool=pool,
        )
        draft_id = state["draft_id"]
        while draft_engine.get_draft(draft_id)["status"] == "in_progress":
            current = draft_engine.get_draft(draft_id)
            if current["current_slot"] == 1:
                draft_engine.make_user_pick(
                    draft_id, current["available_players"][0]["id"],
                )
            else:
                draft_engine.advance_one_ai_pick(draft_id)
        with pytest.raises(DraftCommitError, match="even"):
            SeasonLeagueService(db).create_from_draft(
                draft_id, name="L", user_team_name="M", owner="B", year=YEAR,
            )

    def test_unresolvable_players_are_all_reported_at_once(self, db, pool):
        """Not one at a time — fixing them one per run is unusable."""
        ghosts = [
            {
                "id": f"espn_ghost_{i}", "name": f"Ghost {i}", "position": "WR",
                "nfl_team": "ZZZ", "projected_points": 10.0, "adp_rank": float(i),
            }
            for i in range(8)
        ]
        state = draft_engine.create_draft(
            num_teams=4, num_rounds=2, user_pick_position=1, player_pool=ghosts,
        )
        draft_id = state["draft_id"]
        while draft_engine.get_draft(draft_id)["status"] == "in_progress":
            current = draft_engine.get_draft(draft_id)
            if current["current_slot"] == 1:
                draft_engine.make_user_pick(
                    draft_id, current["available_players"][0]["id"],
                )
            else:
                draft_engine.advance_one_ai_pick(draft_id)

        with pytest.raises(DraftCommitError) as exc:
            SeasonLeagueService(db).create_from_draft(
                draft_id, name="L", user_team_name="M", owner="B", year=YEAR,
            )
        assert len(exc.value.unresolved) == 8

    def test_a_rejected_commit_leaves_no_partial_league(self, db, pool):
        state = draft_engine.create_draft(
            num_teams=4, num_rounds=2, user_pick_position=1, player_pool=pool,
        )
        with pytest.raises(DraftCommitError):
            SeasonLeagueService(db).create_from_draft(
                state["draft_id"], name="L", user_team_name="M", owner="B", year=YEAR,
            )
        assert db.query(DBLeague).count() == 0
        assert db.query(DBTeam).count() == 0
