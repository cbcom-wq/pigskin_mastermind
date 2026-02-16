from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, JSON, Boolean, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class DBPlayer(Base):
    __tablename__ = "players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    position = Column(String, nullable=False)
    nfl_team = Column(String, nullable=False)
    projected_points = Column(Float, default=0.0)
    actual_points = Column(Float, default=0.0)
    stats = Column(JSON, default=dict)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = relationship("DBTeam", back_populates="players")
    weekly_stats = relationship("DBWeeklyPlayerStats", back_populates="player", cascade="all, delete-orphan")


class DBTeam(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    owner = Column(String, nullable=False)
    league_id = Column(String, nullable=True)
    wins = Column(Integer, default=0)
    losses = Column(Integer, default=0)
    ties = Column(Integer, default=0)
    total_points = Column(Float, default=0.0)
    espn_team_id = Column(String, nullable=True)
    is_user_team = Column(Boolean, default=False)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    players = relationship("DBPlayer", back_populates="team")
    weekly_stats = relationship("DBWeeklyTeamStats", back_populates="team", cascade="all, delete-orphan")


class DBWeeklyTeamStats(Base):
    __tablename__ = "weekly_team_stats"
    __table_args__ = (
        UniqueConstraint('team_id', 'week', name='uq_team_week'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    week = Column(Integer, nullable=False)
    points_for = Column(Float, default=0.0)
    points_against = Column(Float, default=0.0)
    projected_points = Column(Float, default=0.0)
    opponent_name = Column(String, nullable=True)
    result = Column(String, nullable=True)  # W, L, T, U
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = relationship("DBTeam", back_populates="weekly_stats")
    player_stats = relationship("DBWeeklyPlayerStats", back_populates="weekly_team_stats", cascade="all, delete-orphan")


class DBWeeklyPlayerStats(Base):
    __tablename__ = "weekly_player_stats"
    __table_args__ = (
        UniqueConstraint('player_id', 'weekly_team_stats_id', name='uq_player_weekly_team'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False)
    weekly_team_stats_id = Column(Integer, ForeignKey("weekly_team_stats.id"), nullable=False)
    week = Column(Integer, nullable=False)
    slot_position = Column(String, nullable=True)  # Starting slot (QB, RB, FLEX, BE, IR, etc.)
    projected_points = Column(Float, default=0.0)
    actual_points = Column(Float, default=0.0)
    stats = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("DBPlayer", back_populates="weekly_stats")
    weekly_team_stats = relationship("DBWeeklyTeamStats", back_populates="player_stats")


class DBLeague(Base):
    __tablename__ = "leagues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    league_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    espn_s2 = Column(String, nullable=True)
    swid = Column(String, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
