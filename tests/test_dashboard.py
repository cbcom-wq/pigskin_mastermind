"""The dashboard view model.

Every rule here is one the page gets wrong silently if it breaks: a score from
the wrong year, an empty roster, a lineup nobody flagged as unset.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBLeague,
    DBLineupSlot,
    DBMatchup,
    DBNFLGame,
    DBPlayer,
    DBPlayerInjury,
    DBPlayerProjection,
    DBRosterSpot,
    DBTeam,
    DBWeeklyTeamStats,
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

        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
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

        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
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

        cards, _plans, _rosters = dashboard.build_league_cards(
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
        _cards, plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert plans[mine.id].projected_total > 0

    def test_no_matchup_this_week_is_an_empty_reason(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "The Scoobies")
        add_roster(db, league, mine)
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        card = card_for(cards, mine)
        assert card.empty_reason == "No week 1 matchup"
        assert card.points is None

    def test_links_to_the_season_team_page_with_a_back_param(self, db, league):
        mine = add_team(db, league, "The Scoobies")
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert card_for(cards, mine).team_url == (
            f"/season/season-x/teams/{mine.id}?back=/"
        )

    def test_only_user_teams_get_cards(self, db, league):
        mine = add_team(db, league, "The Scoobies")
        add_team(db, league, "Someone Else", is_user=False)
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert [c.team_id for c in cards] == [mine.id]


def add_week_stats(db, team, week=WEEK, **kwargs):
    row = DBWeeklyTeamStats(team_id=team.id, week=week, **kwargs)
    db.add(row)
    db.commit()
    return row


class TestEspnLeagueCard:
    @pytest.fixture
    def league(self, db):
        return add_league(db, "878627004", "Airframe Engine League", "espn")

    def test_reads_the_synced_week(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "55 burgers", espn_team_id="4")
        add_roster(db, league, mine)
        add_week_stats(
            db,
            mine,
            points_for=61.4,
            points_against=44.9,
            projected_points=118.4,
            opponent_name="The Crushers",
            result="U",
        )

        cards, _plans, _rosters = dashboard.build_league_cards(
            db,
            YEAR,
            WEEK,
            MID_EARLY_GAME,
        )
        card = card_for(cards, mine)
        assert card.opponent_name == "The Crushers"
        assert card.points == pytest.approx(61.4)
        assert card.opponent_points == pytest.approx(44.9)
        assert card.is_live is True
        assert card.empty_reason is None

    def test_a_settled_week_is_not_live(self, db, league):
        add_schedule(db)
        mine = add_team(db, league, "55 burgers")
        add_roster(db, league, mine)
        add_week_stats(
            db,
            mine,
            points_for=61.4,
            points_against=44.9,
            opponent_name="The Crushers",
            result="W",
        )
        cards, _plans, _rosters = dashboard.build_league_cards(
            db,
            YEAR,
            WEEK,
            MID_EARLY_GAME,
        )
        assert card_for(cards, mine).is_live is False

    def test_an_archived_leagues_week_one_is_not_served_as_this_season(self, db):
        """The regression this branch exists for.

        weekly_team_stats has no year column and its unique key is
        (team_id, week), so 2025 week 1 and 2026 week 1 are the same slot. A
        read that does not go through the league's year serves the wrong
        season's score for the right team.
        """
        archived = add_league(
            db, "1977617326-2025", "Throne 2.0 (2025)", "archive", year=2025
        )
        old_team = add_team(
            db, archived, "Stable of Stars", wins=10, losses=4, points=2237.8
        )
        add_week_stats(
            db,
            old_team,
            points_for=151.2,
            points_against=98.0,
            opponent_name="Somebody 2025",
            result="W",
        )

        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        card = card_for(cards, old_team)
        assert card.points is None
        assert card.opponent_name != "Somebody 2025"

    def test_unsynced_league_says_so_and_offers_the_sync(self, db, league):
        mine = add_team(db, league, "55 burgers")
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        card = card_for(cards, mine)
        assert card.empty_reason == "No 2026 weeks synced"
        assert card.empty_action == ("Sync from ESPN", "/settings")

    def test_reads_the_roster_from_the_team_id_column(self, db, league):
        """An ESPN roster lives on DBPlayer.team_id, never DBRosterSpot.

        plan_lineup's own query reads DBRosterSpot, so without the injected
        roster this team projects 0.0 with a full roster.
        """
        add_schedule(db)
        mine = add_team(db, league, "55 burgers")
        add_roster(db, league, mine)
        _cards, plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert plans[mine.id].projected_total > 0

    def test_links_to_the_generic_team_page(self, db, league):
        mine = add_team(db, league, "55 burgers")
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert card_for(cards, mine).team_url == f"/teams/{mine.id}?back=/"

    def test_unsettled_week_with_no_starter_in_window_is_not_live(self, db, league):
        """An unsettled week alone doesn't make a card live.

        Same fixture as test_reads_the_synced_week (synced week, result="U"),
        but evaluated on a Wednesday when no starter is mid-game.
        """
        add_schedule(db)
        mine = add_team(db, league, "55 burgers", espn_team_id="4")
        add_roster(db, league, mine)
        add_week_stats(
            db,
            mine,
            points_for=61.4,
            points_against=44.9,
            projected_points=118.4,
            opponent_name="The Crushers",
            result="U",
        )
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert card_for(cards, mine).is_live is False


class TestArchiveLeagueCard:
    """The archived league's own card, not the footnote it leaves behind on a
    successor (that's TestArchiveFootnote below)."""

    @pytest.fixture
    def league(self, db):
        return add_league(
            db, "1977617326-2025", "Throne 2.0 (2025)", "archive", year=2025
        )

    def test_says_the_season_is_complete(self, db, league):
        mine = add_team(db, league, "Stable of Stars", wins=10, losses=4, points=2237.8)
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        card = card_for(cards, mine)
        assert card.empty_reason == "2025 season complete"
        assert card.empty_action is None
        assert card.footnote == "2025 finish: 10-4, 2237.8 pts"

    def test_has_no_current_week(self, db, league):
        mine = add_team(db, league, "Stable of Stars", wins=10, losses=4, points=2237.8)
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        card = card_for(cards, mine)
        assert card.points is None
        assert card.is_live is False

    def test_a_stored_week_one_row_does_not_leak_onto_the_card(self, db, league):
        mine = add_team(db, league, "Stable of Stars", wins=10, losses=4, points=2237.8)
        add_week_stats(
            db,
            mine,
            points_for=151.2,
            points_against=98.0,
            opponent_name="Somebody 2025",
            result="W",
        )
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert card_for(cards, mine).points is None


class TestArchiveFootnote:
    def test_the_predecessors_record_lands_on_the_live_card(self, db):
        archived = add_league(
            db, "1977617326-2025", "Throne 2.0 (2025)", "archive", year=2025
        )
        add_team(
            db,
            archived,
            "Bozos Dubbed Over",
            is_user=False,
            espn_team_id="7",
            wins=10,
            losses=4,
            points=2237.8,
        )
        live = add_league(db, "1977617326", "Pigskin Throne 2.0", "espn")
        mine = add_team(db, live, "Bozos Dubbed Over", espn_team_id="7")

        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert card_for(cards, mine).footnote == "2025 finish: 10-4, 2237.8 pts"

    def test_no_predecessor_means_no_footnote(self, db):
        live = add_league(db, "878627004", "Airframe Engine League", "espn")
        mine = add_team(db, live, "55 burgers", espn_team_id="4")
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert card_for(cards, mine).footnote is None

    def test_a_tie_is_included_in_the_record(self, db):
        archived = add_league(
            db, "1977617326-2025", "Throne (2025)", "archive", year=2025
        )
        add_team(
            db,
            archived,
            "Bozos Dubbed Over",
            is_user=False,
            espn_team_id="7",
            wins=9,
            losses=4,
            ties=1,
            points=2100.0,
        )
        live = add_league(db, "1977617326", "Pigskin Throne 2.0", "espn")
        mine = add_team(db, live, "Bozos Dubbed Over", espn_team_id="7")
        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert card_for(cards, mine).footnote == "2025 finish: 9-4-1, 2100.0 pts"

    def test_an_archived_team_that_is_not_a_user_team_gets_no_card(self, db):
        """Its history is already the footnote on the successor's card."""
        archived = add_league(
            db, "1977617326-2025", "Throne (2025)", "archive", year=2025
        )
        add_team(
            db,
            archived,
            "Bozos Dubbed Over",
            is_user=False,
            espn_team_id="7",
            wins=10,
            losses=4,
            points=2237.8,
        )
        live = add_league(db, "1977617326", "Pigskin Throne 2.0", "espn")
        mine = add_team(db, live, "Bozos Dubbed Over", espn_team_id="7")

        cards, _plans, _rosters = dashboard.build_league_cards(
            db, YEAR, WEEK, WEDNESDAY
        )
        assert [c.team_id for c in cards] == [mine.id]


class TestBuildView:
    def test_assembles_week_and_leagues(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        theirs = add_team(db, league, "Touchdown There", is_user=False)
        add_roster(db, league, mine)
        add_roster(db, league, theirs)
        add_matchup(db, league, mine, theirs)

        view = dashboard.build_view(db, WEDNESDAY)
        assert view.week.year == YEAR
        assert view.week.week == WEEK
        assert [c.team_name for c in view.leagues] == ["The Scoobies"]

    def test_unrequested_sections_are_empty_lists_not_none(self, db):
        add_schedule(db)
        view = dashboard.build_view(db, WEDNESDAY, sections=frozenset())
        assert view.attention == []
        assert view.players == []
        assert view.slate == []
        assert view.movers == []

    def test_week_and_leagues_are_built_even_with_no_sections(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        add_team(db, league, "The Scoobies")
        view = dashboard.build_view(db, WEDNESDAY, sections=frozenset())
        assert view.week.games_total == 3
        assert len(view.leagues) == 1


def set_lineup(db, team, assignments, year=YEAR, week=WEEK):
    """assignments: {player: slot}"""
    for player, slot in assignments.items():
        db.add(
            DBLineupSlot(
                team_id=team.id, year=year, week=week, player_id=player.id, slot=slot
            )
        )
    db.commit()


def bench_all_but(db, team, players, starters, year=YEAR, week=WEEK):
    """Save a lineup: *starters* is {player: slot}, everyone else benched."""
    assignments = dict(starters)
    for p in players:
        assignments.setdefault(p, "BENCH")
    set_lineup(db, team, assignments, year, week)


def add_injury(db, player, status, year=YEAR, week=WEEK):
    db.add(
        DBPlayerInjury(player_id=player.id, year=year, week=week, report_status=status)
    )
    db.commit()


def optimal_lineup(db, team, league, year=YEAR, week=WEEK, now=WEDNESDAY):
    """The saved lineup plan_lineup would choose, so nothing is 'unset'."""
    from pigskin_mastermind.services.lineup_manager import plan_lineup
    from pigskin_mastermind.services.season_league import roster_players

    plan = plan_lineup(
        db,
        team,
        year,
        week,
        now,
        league=league,
        players=roster_players(db, team, league),
    )
    for decision in plan.decisions:
        db.add(
            DBLineupSlot(
                team_id=team.id,
                year=year,
                week=week,
                player_id=decision.player_id,
                slot=decision.slot,
            )
        )
    db.commit()


def kinds(items):
    return [i.kind for i in items]


class TestAttentionItems:
    @pytest.fixture
    def setup(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        players = add_roster(db, league, mine)
        return league, mine, {p.name.split()[-1]: p for p in players}

    def _build(self, db, now=WEDNESDAY):
        cards, plans, rosters = dashboard.build_league_cards(db, YEAR, WEEK, now)
        return dashboard.build_attention(
            db,
            cards,
            plans,
            rosters,
            YEAR,
            WEEK,
            now,
        )

    def test_no_saved_lineup_is_the_top_item(self, db, setup):
        items = self._build(db)
        assert items[0].kind == "no_lineup"
        assert items[0].severity == "critical"
        assert items[0].team_name == "The Scoobies"

    def test_an_optimal_saved_lineup_raises_nothing(self, db, setup):
        league, mine, _p = setup
        optimal_lineup(db, mine, league)
        assert self._build(db) == []

    def test_an_out_starter_is_critical(self, db, setup):
        league, mine, p = setup
        optimal_lineup(db, mine, league)
        add_injury(db, p["QB1"], "Out")
        items = self._build(db)
        assert "injury_excluded" in kinds(items)
        item = next(i for i in items if i.kind == "injury_excluded")
        assert item.player_name == "The Scoobies QB1"
        assert item.severity == "critical"

    def test_a_questionable_starter_is_a_warning(self, db, setup):
        league, mine, p = setup
        optimal_lineup(db, mine, league)
        add_injury(db, p["QB1"], "Questionable")
        items = self._build(db)
        assert "injury_haircut" in kinds(items)
        assert (
            next(i for i in items if i.kind == "injury_haircut").severity == "warning"
        )

    def test_a_bye_week_starter_is_critical(self, db, setup):
        league, mine, p = setup
        optimal_lineup(db, mine, league)
        # No week-1 game for ARI, so a player on ARI is on bye.
        p["WR1"].nfl_team = "ARI"
        db.commit()
        assert "on_bye" in kinds(self._build(db))

    def test_bench_better_fires_only_against_the_saved_lineup(self, db, setup):
        """A saved lineup that starts RB3 over RB1 is the user's own choice
        gone wrong -- that is news. plan_lineup's own ideal is not."""
        league, mine, p = setup
        starters = {
            p["QB1"]: "QB",
            p["RB3"]: "RB",
            p["RB2"]: "RB",
            p["WR1"]: "WR",
            p["WR2"]: "WR",
            p["TE1"]: "TE",
            p["WR3"]: "FLEX",
            p["K1"]: "K",
            p["DEF1"]: "DEF",
        }
        bench_all_but(db, mine, list(p.values()), starters)
        items = self._build(db)
        assert "bench_better" in kinds(items)
        item = next(i for i in items if i.kind == "bench_better")
        assert "RB1" in item.detail

    def test_ordering_follows_attention_order(self, db, setup):
        league, mine, p = setup
        starters = {
            p["QB1"]: "QB",
            p["RB3"]: "RB",
            p["RB2"]: "RB",
            p["WR1"]: "WR",
            p["WR2"]: "WR",
            p["TE1"]: "TE",
            p["WR3"]: "FLEX",
            p["K1"]: "K",
            p["DEF1"]: "DEF",
        }
        bench_all_but(db, mine, list(p.values()), starters)
        add_injury(db, p["QB1"], "Out")
        add_injury(db, p["WR2"], "Questionable")

        seen = kinds(self._build(db))
        positions = [dashboard.ATTENTION_ORDER.index(k) for k in seen]
        assert positions == sorted(positions)

    def test_a_lock_within_three_hours_is_informational(self, db, setup):
        league, mine, _p = setup
        optimal_lineup(db, mine, league)
        items = self._build(db, now=SUNDAY_EARLY - timedelta(hours=1))
        assert "lock_soon" in kinds(items)
        assert next(i for i in items if i.kind == "lock_soon").severity == "info"

    def test_every_item_links_back_to_the_dashboard(self, db, setup):
        for item in self._build(db):
            assert item.url.endswith("?back=/")


class TestPlayerCells:
    def _build(self, db, now):
        cards, plans, rosters = dashboard.build_league_cards(db, YEAR, WEEK, now)
        return dashboard.build_players(
            db,
            cards,
            plans,
            rosters,
            YEAR,
            WEEK,
            now,
        )

    @pytest.fixture
    def setup(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        players = add_roster(db, league, mine)
        return league, mine, {p.name.split()[-1]: p for p in players}

    def test_one_team_fills_the_strip_exactly(self, db, setup):
        """A legal starting lineup is nine slots, which is the strip."""
        cells, total = self._build(db, WEDNESDAY)
        assert len(cells) == dashboard.PLAYER_STRIP_LIMIT
        assert total == 9

    def test_reports_every_starter_uncapped(self, db, setup):
        """Two teams is eighteen starters. ``build_players`` itself never
        caps -- capping to the strip is ``build_view``'s job, so that the
        hero's uncapped ``players_yet_to_play`` count can be computed from
        the same list a display caller then slices."""
        league, _mine, _players = setup
        second = add_team(db, league, "Second Squad")
        add_roster(db, league, second)

        cells, total = self._build(db, WEDNESDAY)
        assert len(cells) == 18
        assert total == 18

    def test_upcoming_players_sort_by_kickoff_then_projection(self, db, setup):
        cells, _total = self._build(db, WEDNESDAY)
        assert {c.state for c in cells} == {"upcoming"}
        early = [c for c in cells if c.kickoff_at == SUNDAY_EARLY]
        assert early[0].projected >= early[-1].projected

    def test_a_player_mid_game_sorts_first_and_reads_as_playing(self, db, setup):
        cells, _total = self._build(db, MID_EARLY_GAME)
        assert cells[0].state == "playing"

    def test_a_finished_game_reads_as_final(self, db, setup):
        game = db.query(DBNFLGame).filter_by(home_team="CHI").first()
        game.home_score, game.away_score = 20, 17
        db.commit()
        cells, _total = self._build(db, MID_EARLY_GAME)
        assert "final" in {c.state for c in cells}

    def test_an_injured_starter_is_a_concern(self, db, setup):
        _league, _mine, p = setup
        add_injury(db, p["QB1"], "Questionable")
        cells, _total = self._build(db, WEDNESDAY)
        cell = next(c for c in cells if c.player_id == p["QB1"].id)
        assert cell.state == "concern"
        assert "questionable" in cell.note.lower()

    def test_a_player_on_two_teams_appears_once(self, db):
        add_schedule(db)
        espn = add_league(db, "espn-x", "Airframe", "espn")
        season = add_league(db, "season-x", "Bird Turds", "season")
        espn_team = add_team(db, espn, "55 burgers")
        season_team = add_team(db, season, "The Scoobies")
        shared = add_roster(db, espn, espn_team)
        # Put the same DBPlayer rows on the season roster too.
        for p in shared:
            db.add(
                DBRosterSpot(
                    league_id=season.id,
                    team_id=season_team.id,
                    player_id=p.id,
                    acquired_via="draft",
                )
            )
        db.commit()

        cells, _total = self._build(db, WEDNESDAY)
        ids = [c.player_id for c in cells]
        assert len(ids) == len(set(ids))

    def test_every_cell_links_back_to_the_dashboard(self, db, setup):
        cells, _total = self._build(db, WEDNESDAY)
        assert all(c.url.endswith("?back=/") for c in cells)


class TestWeekPlayersYetToPlay:
    """Closes the review finding on Task 8.

    ``build_view(db, now, sections=frozenset())`` is exactly how ``/`` and
    ``/api/dashboard/pulse`` call this -- neither ever requests the
    ``players`` section, so the hero's count must not depend on it, and it
    must not be capped to what the (unrequested, empty) strip would show.
    """

    def test_counts_every_upcoming_starter_not_just_the_shown_nine(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        add_roster(db, league, mine)
        second = add_team(db, league, "Second Squad")
        add_roster(db, league, second)

        view = dashboard.build_view(db, WEDNESDAY, sections=frozenset())
        assert view.players == []
        assert view.week.players_yet_to_play == 18

    def test_players_section_is_still_capped_to_the_strip(self, db):
        """The cap moved from ``build_players`` to ``build_view`` -- this is
        where "N more" coverage now belongs."""
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        add_roster(db, league, mine)
        second = add_team(db, league, "Second Squad")
        add_roster(db, league, second)

        view = dashboard.build_view(db, WEDNESDAY, sections=frozenset({"players"}))
        assert len(view.players) == dashboard.PLAYER_STRIP_LIMIT
        assert view.players_total == 18


class TestSlate:
    @pytest.fixture
    def setup(self, db):
        add_schedule(db)
        league = add_league(db, "season-x", "Bird Turds", "season")
        mine = add_team(db, league, "The Scoobies")
        add_roster(db, league, mine)
        return league, mine

    def _build(self, db, now):
        rosters = {
            t.id: dashboard.roster_players(db, t, lg)
            for t, lg in dashboard.user_team_leagues(db)
        }
        return dashboard.build_slate(db, rosters, YEAR, WEEK, now)

    def test_orders_by_kickoff(self, db, setup):
        slate = self._build(db, WEDNESDAY)
        assert [g.kickoff_at for g in slate] == sorted(g.kickoff_at for g in slate)

    def test_badges_the_users_players(self, db, setup):
        """Counts the whole roster, not just starters — you care that four of
        your players are in one game whichever of them you started."""
        slate = self._build(db, WEDNESDAY)
        chi_det = next(g for g in slate if g.home_team == "CHI")
        # CHI: QB1, TE1.  DET: RB1, DEF1.
        assert chi_det.your_player_count == 4
        assert "The Scoobies QB1" in chi_det.your_player_names

    def test_a_game_with_none_of_your_players_still_renders(self, db, setup):
        db.add(
            DBNFLGame(
                year=YEAR,
                week=WEEK,
                home_team="NYJ",
                away_team="BUF",
                kickoff_at=SUNDAY_EARLY,
            )
        )
        db.commit()
        slate = self._build(db, WEDNESDAY)
        nyj = next(g for g in slate if g.home_team == "NYJ")
        assert nyj.your_player_count == 0

    def test_state_is_derived_from_the_schedule_not_the_score(self, db, setup):
        """A 0-0 game that has kicked off is in progress, not upcoming."""
        slate = self._build(db, MID_EARLY_GAME)
        early = next(g for g in slate if g.home_team == "CHI")
        late = next(g for g in slate if g.home_team == "KC")
        assert early.state == "in_progress"
        assert late.state == "upcoming"

    def test_a_scored_game_is_final(self, db, setup):
        game = db.query(DBNFLGame).filter_by(home_team="CHI").first()
        game.home_score, game.away_score = 20, 17
        db.commit()
        slate = self._build(db, MID_EARLY_GAME)
        assert next(g for g in slate if g.home_team == "CHI").state == "final"

    def test_carries_the_market_lines(self, db, setup):
        slate = self._build(db, WEDNESDAY)
        chi = next(g for g in slate if g.home_team == "CHI")
        assert chi.total_line == pytest.approx(48.5)
        assert chi.spread_line == pytest.approx(1.5)
