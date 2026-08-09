"""Tests for SportsbookProjectionService."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBLeague,
    DBPlayer,
    DBSportsbookOdds,
    DEFAULT_SCORING_SETTINGS,
)
from pigskin_mastermind.services.sportsbook_projection_service import (
    MARKET_TO_SCORING,
    SportsbookProjectionService,
)

# ---------------------------------------------------------------------------
# In-memory test DB
# ---------------------------------------------------------------------------

test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


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


# ---------------------------------------------------------------------------
# Helpers – seed prop data
# ---------------------------------------------------------------------------

def _make_prop(
    description: str,
    market: str,
    point: float,
    outcome_name: str = "Over",
    bookmaker: str = "draftkings",
    event_id: str = "event_1",
    price: int = -110,
) -> DBSportsbookOdds:
    return DBSportsbookOdds(
        event_id=event_id,
        sport_key="americanfootball_nfl",
        sport_title="NFL",
        commence_time=datetime(2025, 9, 7, 20, 0),
        home_team="Kansas City Chiefs",
        away_team="Baltimore Ravens",
        bookmaker=bookmaker,
        market=market,
        outcome_name=outcome_name,
        price=price,
        point=point,
        description=description,
        fetched_at=datetime.utcnow(),
    )


def _seed_mahomes_props(db, bookmakers=("draftkings",)):
    """Seed a realistic set of Mahomes props for a single event."""
    props = []
    lines = {
        "player_pass_yds": 279.5,
        "player_pass_tds": 2.5,
        "player_rush_yds": 19.5,
    }
    for bk in bookmakers:
        for market, point in lines.items():
            # Over row
            props.append(_make_prop("Patrick Mahomes", market, point, "Over", bk))
            # Under row
            props.append(_make_prop("Patrick Mahomes", market, point, "Under", bk))
    db.add_all(props)
    db.commit()
    return props


def _seed_multi_player(db):
    """Seed props for two players in the same event.

    Uses separate bookmakers per player to satisfy the unique constraint
    (event_id, bookmaker, market, outcome_name) on overlapping markets.
    """
    # Mahomes via draftkings (pass-only markets, no overlap with Henry)
    _seed_mahomes_props(db)
    # Henry via fanduel so rush_yds doesn't collide with Mahomes' rush_yds
    props = [
        _make_prop("Derrick Henry", "player_rush_yds", 89.5, "Over", bookmaker="fanduel"),
        _make_prop("Derrick Henry", "player_rush_yds", 89.5, "Under", bookmaker="fanduel"),
        _make_prop("Derrick Henry", "player_rush_tds", 0.5, "Over", bookmaker="fanduel"),
        _make_prop("Derrick Henry", "player_rush_tds", 0.5, "Under", bookmaker="fanduel"),
        _make_prop("Derrick Henry", "player_receptions", 1.5, "Over", bookmaker="fanduel"),
        _make_prop("Derrick Henry", "player_receptions", 1.5, "Under", bookmaker="fanduel"),
        _make_prop("Derrick Henry", "player_reception_yds", 12.5, "Over", bookmaker="fanduel"),
        _make_prop("Derrick Henry", "player_reception_yds", 12.5, "Under", bookmaker="fanduel"),
    ]
    db.add_all(props)
    db.commit()


# ---------------------------------------------------------------------------
# Tests – project_player
# ---------------------------------------------------------------------------

class TestProjectPlayer:

    def test_basic_projection(self, db):
        """Single bookmaker, three markets – verify category math."""
        _seed_mahomes_props(db)
        service = SportsbookProjectionService(db)
        result = service.project_player("Patrick Mahomes")

        assert result["player_name"] == "Patrick Mahomes"
        cats = {c["market"]: c for c in result["categories"]}

        # Passing yards: 279.5 * 0.04 = 11.18
        assert cats["player_pass_yds"]["expected_stat"] == 279.5
        assert cats["player_pass_yds"]["fantasy_points"] == pytest.approx(11.18, abs=0.01)

        # Passing TDs: 2.5 * 4 = 10.0
        assert cats["player_pass_tds"]["expected_stat"] == 2.5
        assert cats["player_pass_tds"]["fantasy_points"] == pytest.approx(10.0, abs=0.01)

        # Rushing yards: 19.5 * 0.1 = 1.95
        assert cats["player_rush_yds"]["expected_stat"] == 19.5
        assert cats["player_rush_yds"]["fantasy_points"] == pytest.approx(1.95, abs=0.01)

        expected_total = 11.18 + 10.0 + 1.95
        assert result["total_projected_points"] == pytest.approx(expected_total, abs=0.02)

    def test_partial_name_match(self, db):
        """Partial name search should still find the player."""
        _seed_mahomes_props(db)
        service = SportsbookProjectionService(db)
        result = service.project_player("Mahomes")

        assert result["player_name"] == "Patrick Mahomes"
        assert len(result["categories"]) == 3

    def test_no_props_returns_zero(self, db):
        """Player with no props → zero total, empty categories."""
        service = SportsbookProjectionService(db)
        result = service.project_player("Nobody")

        assert result["total_projected_points"] == 0.0
        assert result["categories"] == []

    def test_median_across_bookmakers(self, db):
        """Multiple bookmakers → median line used, not sum."""
        # DraftKings: 279.5, FanDuel: 284.5, BetMGM: 275.5
        # Median = 279.5
        props = [
            _make_prop("Patrick Mahomes", "player_pass_yds", 279.5, "Over", "draftkings"),
            _make_prop("Patrick Mahomes", "player_pass_yds", 279.5, "Under", "draftkings"),
            _make_prop("Patrick Mahomes", "player_pass_yds", 284.5, "Over", "fanduel"),
            _make_prop("Patrick Mahomes", "player_pass_yds", 284.5, "Under", "fanduel"),
            _make_prop("Patrick Mahomes", "player_pass_yds", 275.5, "Over", "betmgm"),
            _make_prop("Patrick Mahomes", "player_pass_yds", 275.5, "Under", "betmgm"),
        ]
        db.add_all(props)
        db.commit()

        service = SportsbookProjectionService(db)
        result = service.project_player("Patrick Mahomes")

        cats = {c["market"]: c for c in result["categories"]}
        assert cats["player_pass_yds"]["expected_stat"] == 279.5
        assert cats["player_pass_yds"]["bookmaker_count"] == 3

    def test_event_id_filter(self, db):
        """Only props from the requested event are included."""
        _seed_mahomes_props(db)
        # Add prop from a different event
        db.add(_make_prop("Patrick Mahomes", "player_rush_tds", 0.5, "Over", event_id="event_other"))
        db.commit()

        service = SportsbookProjectionService(db)
        result = service.project_player("Patrick Mahomes", event_id="event_1")

        markets = {c["market"] for c in result["categories"]}
        assert "player_rush_tds" not in markets  # belongs to other event

    def test_bookmaker_filter(self, db):
        """Bookmaker filter restricts which lines are used."""
        _seed_mahomes_props(db, bookmakers=("draftkings", "fanduel"))
        service = SportsbookProjectionService(db)
        result = service.project_player("Patrick Mahomes", bookmaker="draftkings")

        for cat in result["categories"]:
            assert cat["bookmaker_count"] == 1

    def test_custom_league_scoring(self, db):
        """League-specific scoring settings override defaults."""
        _seed_mahomes_props(db)
        # Create a league with 6pt pass TDs
        league = DBLeague(
            league_id="league_1",
            name="Test League",
            year=2025,
            scoring_settings={"pass_td": 6},
        )
        db.add(league)
        db.commit()

        service = SportsbookProjectionService(db)
        result = service.project_player("Patrick Mahomes", league_id="league_1")

        cats = {c["market"]: c for c in result["categories"]}
        # 2.5 * 6 = 15.0 (instead of 2.5 * 4 = 10.0)
        assert cats["player_pass_tds"]["fantasy_points"] == pytest.approx(15.0, abs=0.01)
        assert cats["player_pass_tds"]["multiplier"] == 6

    def test_scoring_settings_included_in_response(self, db):
        """Response includes the scoring settings that were applied."""
        _seed_mahomes_props(db)
        service = SportsbookProjectionService(db)
        result = service.project_player("Patrick Mahomes")

        assert "scoring_settings" in result
        assert result["scoring_settings"]["pass_yd"] == DEFAULT_SCORING_SETTINGS["pass_yd"]


# ---------------------------------------------------------------------------
# Tests – project_event
# ---------------------------------------------------------------------------

class TestProjectEvent:

    def test_projects_all_players_in_event(self, db):
        """All players with props in the event get a projection."""
        _seed_multi_player(db)
        service = SportsbookProjectionService(db)
        results = service.project_event("event_1")

        names = {r["player_name"] for r in results}
        assert "Patrick Mahomes" in names
        assert "Derrick Henry" in names

    def test_sorted_by_total_descending(self, db):
        """Results are sorted highest projected points first."""
        _seed_multi_player(db)
        service = SportsbookProjectionService(db)
        results = service.project_event("event_1")

        totals = [r["total_projected_points"] for r in results]
        assert totals == sorted(totals, reverse=True)

    def test_empty_event_returns_empty(self, db):
        """An event with no props returns an empty list."""
        service = SportsbookProjectionService(db)
        results = service.project_event("nonexistent_event")
        assert results == []


# ---------------------------------------------------------------------------
# Tests – _group_lines_by_market
# ---------------------------------------------------------------------------

class TestGroupLinesByMarket:

    def test_over_only(self, db):
        """Only Over rows contribute to the grouped lines."""
        props = [
            _make_prop("Player A", "player_pass_yds", 250.5, "Over", "dk"),
            _make_prop("Player A", "player_pass_yds", 250.5, "Under", "dk"),
        ]
        grouped = SportsbookProjectionService._group_lines_by_market(props)
        assert grouped == {"player_pass_yds": [250.5]}

    def test_multiple_bookmakers_one_line_each(self, db):
        """Each bookmaker contributes exactly one line per market."""
        props = [
            _make_prop("Player A", "player_pass_yds", 250.5, "Over", "dk"),
            _make_prop("Player A", "player_pass_yds", 255.5, "Over", "fd"),
        ]
        grouped = SportsbookProjectionService._group_lines_by_market(props)
        assert sorted(grouped["player_pass_yds"]) == [250.5, 255.5]

    def test_skip_null_point(self, db):
        """Rows with point=None are skipped."""
        prop = _make_prop("Player A", "player_pass_yds", 0.0, "Over")
        prop.point = None
        grouped = SportsbookProjectionService._group_lines_by_market([prop])
        assert grouped == {}

    def test_deduplicates_bookmaker(self, db):
        """If a bookmaker appears twice for same market, first wins."""
        props = [
            _make_prop("Player A", "player_pass_yds", 250.5, "Over", "dk"),
            _make_prop("Player A", "player_pass_yds", 999.0, "Over", "dk"),
        ]
        grouped = SportsbookProjectionService._group_lines_by_market(props)
        assert grouped["player_pass_yds"] == [250.5]


# ---------------------------------------------------------------------------
# Tests – _resolve_canonical_name
# ---------------------------------------------------------------------------

class TestResolveCanonicalName:

    def test_picks_most_common(self, db):
        props = [
            _make_prop("Patrick Mahomes", "player_pass_yds", 279.5),
            _make_prop("Patrick Mahomes", "player_pass_tds", 2.5),
            _make_prop("Pat Mahomes", "player_rush_yds", 19.5),
        ]
        name = SportsbookProjectionService._resolve_canonical_name(props, "Mahomes")
        assert name == "Patrick Mahomes"

    def test_fallback_on_empty(self, db):
        name = SportsbookProjectionService._resolve_canonical_name([], "Mahomes")
        assert name == "Mahomes"


# ---------------------------------------------------------------------------
# Tests – RB / skill-position projection
# ---------------------------------------------------------------------------

class TestSkillPositionProjections:

    def test_rb_projection_with_receptions(self, db):
        """RB with rush + receiving props maps correctly in 0.5 PPR."""
        props = [
            _make_prop("Derrick Henry", "player_rush_yds", 89.5, "Over"),
            _make_prop("Derrick Henry", "player_rush_yds", 89.5, "Under"),
            _make_prop("Derrick Henry", "player_rush_tds", 0.5, "Over"),
            _make_prop("Derrick Henry", "player_rush_tds", 0.5, "Under"),
            _make_prop("Derrick Henry", "player_receptions", 1.5, "Over"),
            _make_prop("Derrick Henry", "player_receptions", 1.5, "Under"),
            _make_prop("Derrick Henry", "player_reception_yds", 12.5, "Over"),
            _make_prop("Derrick Henry", "player_reception_yds", 12.5, "Under"),
        ]
        db.add_all(props)
        db.commit()

        service = SportsbookProjectionService(db)
        result = service.project_player("Derrick Henry")
        cats = {c["market"]: c for c in result["categories"]}

        # rush_yds: 89.5 * 0.1 = 8.95
        assert cats["player_rush_yds"]["fantasy_points"] == pytest.approx(8.95, abs=0.01)
        # rush_tds: 0.5 * 6 = 3.0
        assert cats["player_rush_tds"]["fantasy_points"] == pytest.approx(3.0, abs=0.01)
        # receptions: 1.5 * 0.5 = 0.75
        assert cats["player_receptions"]["fantasy_points"] == pytest.approx(0.75, abs=0.01)
        # rec_yds: 12.5 * 0.1 = 1.25
        assert cats["player_reception_yds"]["fantasy_points"] == pytest.approx(1.25, abs=0.01)

        expected_total = 8.95 + 3.0 + 0.75 + 1.25
        assert result["total_projected_points"] == pytest.approx(expected_total, abs=0.02)


# ---------------------------------------------------------------------------
# Tests – project_player_by_id
# ---------------------------------------------------------------------------


class TestProjectPlayerById:

    def test_project_player_by_id_resolves_through_identity(self, db):
        """A DB player id, not a name substring, selects the props."""
        _seed_mahomes_props(db)
        p = DBPlayer(player_id="espn_1", name="Patrick Mahomes", position="QB",
                     nfl_team="KC")
        db.add(p)
        db.commit()

        result = SportsbookProjectionService(db).project_player_by_id(p.id)

        assert result["player_name"] == "Patrick Mahomes"
        assert "total_projected_points" in result
        assert result["total_projected_points"] > 0

    def test_project_player_by_id_unknown_player_returns_empty(self, db):
        """Unknown player ID returns empty result with zero points."""
        result = SportsbookProjectionService(db).project_player_by_id(999999)
        assert result["player_name"] is None
        assert result["total_projected_points"] == 0.0
        assert result["categories"] == []

    def test_project_player_by_id_with_league_id(self, db):
        """project_player_by_id respects league scoring settings."""
        _seed_mahomes_props(db)
        p = DBPlayer(player_id="espn_1", name="Patrick Mahomes", position="QB",
                     nfl_team="KC")
        db.add(p)
        # Create a league with 6pt pass TDs
        league = DBLeague(
            league_id="league_1",
            name="Test League",
            year=2025,
            scoring_settings={"pass_td": 6},
        )
        db.add(league)
        db.commit()

        result = SportsbookProjectionService(db).project_player_by_id(
            p.id, league_id="league_1"
        )

        cats = {c["market"]: c for c in result["categories"]}
        # 2.5 * 6 = 15.0 (instead of 2.5 * 4 = 10.0)
        assert cats["player_pass_tds"]["fantasy_points"] == pytest.approx(15.0, abs=0.01)

    def test_project_player_by_id_with_event_filter(self, db):
        """project_player_by_id respects event_id filter."""
        _seed_mahomes_props(db)
        # Add prop from a different event
        db.add(_make_prop("Patrick Mahomes", "player_rush_tds", 0.5, "Over",
                          event_id="event_other"))
        p = DBPlayer(player_id="espn_1", name="Patrick Mahomes", position="QB",
                     nfl_team="KC")
        db.add(p)
        db.commit()

        result = SportsbookProjectionService(db).project_player_by_id(
            p.id, event_id="event_1"
        )

        markets = {c["market"] for c in result["categories"]}
        assert "player_rush_tds" not in markets  # belongs to other event
