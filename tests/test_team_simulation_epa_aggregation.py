"""Team totals when a play-by-play source publishes no EPA.

``_empty_running_stats`` reports ``total_epa`` as ``None`` when nothing
supplied EPA -- a running total of nothing is not zero.  The team aggregator
summed those snapshots with ``snap.get("total_epa", 0.0)``, and ``dict.get``'s
default applies only when the key is *absent*, never when it is present and
None.  So every ESPN-sourced team simulation raised TypeError.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import Base
from pigskin_mastermind.services.team_game_simulation_service import (
    TeamGameSimulationService,
)

engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    session = Session()
    yield session
    session.close()


def _event(player_id, total_epa, rush_yards=10):
    return {
        "player_id": player_id,
        "stats_snapshot": {
            "total_plays": 3,
            "pass_yards": 0,
            "rush_yards": rush_yards,
            "rec_yards": 0,
            "total_tds": 0,
            "first_downs": 1,
            "total_epa": total_epa,
        },
    }


class TestTeamEPAAggregation:
    def test_a_source_without_epa_does_not_raise(self, db):
        service = TeamGameSimulationService(db)

        totals = service._compute_team_stats([_event(1, None), _event(2, None)], [], {})

        assert totals["total_epa"] is None
        assert totals["total_rush_yards"] == 20

    def test_epa_still_totals_when_supplied(self, db):
        service = TeamGameSimulationService(db)

        totals = service._compute_team_stats([_event(1, 1.5), _event(2, -0.5)], [], {})

        assert totals["total_epa"] == pytest.approx(1.0)

    def test_a_mixed_roster_totals_only_what_was_measured(self, db):
        """One player on an EPA-bearing source and one without."""
        service = TeamGameSimulationService(db)

        totals = service._compute_team_stats([_event(1, 2.0), _event(2, None)], [], {})

        assert totals["total_epa"] == pytest.approx(2.0)
