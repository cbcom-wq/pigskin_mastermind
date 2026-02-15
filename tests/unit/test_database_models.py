import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from pigskin_mastermind.models.database import Base, DBPlayer, DBTeam


def test_player_model_creation():
    """Test creating a player in the database"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        player = DBPlayer(
            player_id="p1",
            name="Patrick Mahomes",
            position="QB",
            nfl_team="KC",
            projected_points=25.5
        )
        session.add(player)
        session.commit()

        result = session.query(DBPlayer).filter_by(player_id="p1").first()
        assert result is not None
        assert result.name == "Patrick Mahomes"
        assert result.position == "QB"


def test_team_model_creation():
    """Test creating a team in the database"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        team = DBTeam(
            team_id="t1",
            name="Test Team",
            owner="Owner"
        )
        session.add(team)
        session.commit()

        result = session.query(DBTeam).filter_by(team_id="t1").first()
        assert result is not None
        assert result.name == "Test Team"
        assert result.wins == 0


def test_team_player_relationship():
    """Test team-player relationship"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
        session.add(team)
        session.commit()

        player = DBPlayer(
            player_id="p1",
            name="Player 1",
            position="QB",
            nfl_team="KC",
            team_id=team.id
        )
        session.add(player)
        session.commit()

        session.refresh(team)
        assert len(team.players) == 1
        assert team.players[0].name == "Player 1"
