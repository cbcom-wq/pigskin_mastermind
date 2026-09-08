"""The dashboard view model.

Every rule here is one the page gets wrong silently if it breaks: a score from
the wrong year, an empty roster, a lineup nobody flagged as unset.
"""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBLeague,
    DBMatchup,
    DBNFLGame,
    DBPlayer,
    DBPlayerProjection,
    DBRosterSpot,
    DBTeam,
)
from pigskin_mastermind.services import dashboard

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

YEAR = 2026
WEEK = 1
SUNDAY_EARLY = datetime(2026, 9, 13, 13, 0)
SUNDAY_LATE = datetime(2026, 9, 13, 16, 25)
WEDNESDAY = datetime(2026, 9, 9, 10, 0)
MID_EARLY_GAME = datetime(2026, 9, 13, 14, 30)

SLOTS = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1, "BENCH": 6}


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


def add_schedule(db, year=YEAR, week=WEEK):
    """Two early games and one late game, none played."""
    db.add(
        DBNFLGame(
            year=year,
            week=week,
            home_team="CHI",
            away_team="DET",
            kickoff_at=SUNDAY_EARLY,
            total_line=48.5,
            spread_line=1.5,
        )
    )
    db.add(
        DBNFLGame(
            year=year,
            week=week,
            home_team="MIN",
            away_team="GB",
            kickoff_at=SUNDAY_EARLY,
            total_line=44.5,
            spread_line=-2.5,
        )
    )
    db.add(
        DBNFLGame(
            year=year,
            week=week,
            home_team="KC",
            away_team="LAC",
            kickoff_at=SUNDAY_LATE,
            total_line=45.0,
            spread_line=3.0,
        )
    )
    db.commit()


def add_league(db, league_id, name, kind, year=YEAR, week=WEEK):
    lg = DBLeague(
        league_id=league_id,
        name=name,
        year=year,
        kind=kind,
        current_week=week,
        roster_slots=SLOTS,
    )
    db.add(lg)
    db.commit()
    return lg


def add_team(
    db,
    league,
    name,
    is_user=True,
    espn_team_id=None,
    wins=0,
    losses=0,
    ties=0,
    points=0.0,
):
    t = DBTeam(
        team_id=f"{league.league_id}-{name}",
        name=name,
        owner="Brandon",
        league_id=league.league_id,
        is_user_team=is_user,
        espn_team_id=espn_team_id,
        wins=wins,
        losses=losses,
        ties=ties,
        total_points=points,
    )
    db.add(t)
    db.commit()
    return t


class TestResolveScope:
    def test_uses_the_newest_non_archive_league(self, db):
        add_league(db, "old", "Old", "archive", year=2025)
        add_league(db, "cur", "Current", "season", year=YEAR, week=4)
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 4)

    def test_ignores_an_archive_league_even_when_it_is_newest(self, db):
        add_league(db, "cur", "Current", "season", year=YEAR, week=2)
        add_league(db, "arc", "Archived", "archive", year=2030)
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 2)

    def test_falls_back_to_the_calendar_season_with_no_leagues(self, db):
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 1)

    def test_missing_current_week_reads_as_week_one(self, db):
        lg = add_league(db, "cur", "Current", "season")
        lg.current_week = None
        db.commit()
        assert dashboard.resolve_scope(db, WEDNESDAY) == (YEAR, 1)


class TestWeekContext:
    def test_counts_games(self, db):
        add_schedule(db)
        ctx = dashboard.build_week_context(db, YEAR, WEEK, WEDNESDAY)
        assert ctx.games_total == 3
        assert ctx.games_in_progress == 0
        assert ctx.games_final == 0

    def test_not_live_on_a_wednesday(self, db):
        add_schedule(db)
        ctx = dashboard.build_week_context(db, YEAR, WEEK, WEDNESDAY)
        assert ctx.games_live is False
        assert ctx.next_kickoff == SUNDAY_EARLY

    def test_live_during_the_early_window(self, db):
        add_schedule(db)
        ctx = dashboard.build_week_context(db, YEAR, WEEK, MID_EARLY_GAME)
        assert ctx.games_live is True
        assert ctx.games_in_progress == 2
        assert ctx.next_kickoff == SUNDAY_LATE

    def test_a_scored_game_is_final_not_in_progress(self, db):
        add_schedule(db)
        game = db.query(DBNFLGame).filter_by(home_team="CHI").first()
        game.home_score, game.away_score = 20, 17
        db.commit()
        ctx = dashboard.build_week_context(db, YEAR, WEEK, MID_EARLY_GAME)
        assert ctx.games_final == 1
        assert ctx.games_in_progress == 1

    def test_no_schedule_is_not_live(self, db):
        ctx = dashboard.build_week_context(db, YEAR, WEEK, MID_EARLY_GAME)
        assert ctx.games_total == 0
        assert ctx.games_live is False
        assert ctx.next_kickoff is None


ROSTER = [
    ("QB1", "QB", "CHI", 27.8),
    ("QB2", "QB", "MIN", 17.3),
    ("RB1", "RB", "DET", 18.7),
    ("RB2", "RB", "KC", 13.5),
    ("RB3", "RB", "LAC", 9.5),
    ("WR1", "WR", "GB", 15.3),
    ("WR2", "WR", "LAC", 13.9),
    ("WR3", "WR", "MIN", 10.9),
    ("TE1", "TE", "CHI", 15.6),
    ("K1", "K", "KC", 11.6),
    ("DEF1", "DEF", "DET", 12.1),
]


def add_roster(db, league, team, roster=ROSTER, year=YEAR, week=WEEK):
    """Give *team* a legal roster, stored the way its league kind stores it."""
    players = []
    for name, position, nfl_team, points in roster:
        p = DBPlayer(
            player_id=f"p_{team.id}_{name}",
            name=f"{team.name} {name}",
            position=position,
            nfl_team=nfl_team,
        )
        db.add(p)
        db.commit()
        if league.kind in ("season", "archive"):
            db.add(
                DBRosterSpot(
                    league_id=league.id,
                    team_id=team.id,
                    player_id=p.id,
                    acquired_via="draft",
                )
            )
        else:
            p.team_id = team.id
        db.add(
            DBPlayerProjection(
                player_id=p.id,
                year=year,
                week=week,
                source="model",
                projected_points=points,
            )
        )
        db.commit()
        players.append(p)
    return players


def add_matchup(db, league, home, away, week=WEEK, **kwargs):
    m = DBMatchup(
        league_id=league.id,
        year=league.year,
        week=week,
        bracket_slot=0,
        home_team_id=home.id,
        away_team_id=away.id,
        **kwargs,
    )
    db.add(m)
    db.commit()
    return m


def card_for(cards, team):
    return next(c for c in cards if c.team_id == team.id)


class TestSeasonLeagueCard:
    @pytest.fixture
    def league(self, db):
        return add_league(db, "season-x", "Bird Turds", "season")

    def test_names_the_opponent_from_the_matchup(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        theirs = add_team(db, league, "Touchdown There", is_user=False)
        add_roster(db, league, mine)
        add_roster(db, league, theirs)
        add_matchup(db, league, mine, theirs)

        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        card = card_for(cards, mine)
        assert card.opponent_name == "Touchdown There"
        assert card.kind == "season"
        assert card.empty_reason is None

    def test_projects_both_sides_before_kickoff(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        theirs = add_team(db, league, "Touchdown There", is_user=False)
        add_roster(db, league, mine)
        add_roster(db, league, theirs)
        add_matchup(db, league, mine, theirs)

        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        card = card_for(cards, mine)
        # QB1 27.8 + RB1 18.7 + RB2 13.5 + WR1 15.3 + WR2 13.9 + TE1 15.6
        # + WR3 10.9 (FLEX, beating RB3 9.5) + K1 11.6 + DEF1 12.1 = 139.4
        assert card.is_live is False
        assert card.projected == pytest.approx(139.4, abs=0.1)
        assert card.opponent_projected == pytest.approx(139.4, abs=0.1)

    def test_uses_live_points_once_the_matchup_is_in_progress(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        theirs = add_team(db, league, "Touchdown There", is_user=False)
        add_roster(db, league, mine)
        add_roster(db, league, theirs)
        add_matchup(
            db,
            league,
            mine,
            theirs,
            status="in_progress",
            home_points=61.4,
            away_points=44.9,
        )

        cards, _plans = dashboard.build_league_cards(
            db,
            YEAR,
            WEEK,
            MID_EARLY_GAME,
        )
        card = card_for(cards, mine)
        assert card.is_live is True
        assert card.points == pytest.approx(61.4)
        assert card.opponent_points == pytest.approx(44.9)

    def test_reads_the_roster_from_roster_spots(self, db, league):
        """A season roster lives in DBRosterSpot, never DBPlayer.team_id."""
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        add_roster(db, league, mine)
        _cards, plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert plans[mine.id].projected_total > 0

    def test_no_matchup_this_week_is_an_empty_reason(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        add_roster(db, league, mine)
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        card = card_for(cards, mine)
        assert card.empty_reason == "No week 1 matchup"
        assert card.points is None

    def test_links_to_the_season_team_page_with_a_back_param(self, db, league):
        mine = add_team(db, league, "The Scoobies")
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert card_for(cards, mine).team_url == (
            f"/season/season-x/teams/{mine.id}?back=/"
        )

    def test_only_user_teams_get_cards(self, db, league):
        mine = add_team(db, league, "The Scoobies")
        add_team(db, league, "Someone Else", is_user=False)
        cards, _plans = dashboard.build_league_cards(db, YEAR, WEEK, WEDNESDAY)
        assert [c.team_id for c in cards] == [mine.id]
