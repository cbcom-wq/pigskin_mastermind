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
    headshot_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = relationship("DBTeam", back_populates="players")
    weekly_stats = relationship("DBWeeklyPlayerStats", back_populates="player", cascade="all, delete-orphan")
    season_stats = relationship("DBPlayerSeasonStats", back_populates="player", cascade="all, delete-orphan")
    game_logs = relationship("DBPlayerGameLog", back_populates="player", cascade="all, delete-orphan")


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
    espn_slot_position = Column(String, nullable=True)  # Original slot from ESPN import
    projected_points = Column(Float, default=0.0)
    actual_points = Column(Float, default=0.0)
    stats = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("DBPlayer", back_populates="weekly_stats")
    weekly_team_stats = relationship("DBWeeklyTeamStats", back_populates="player_stats")


class DBPlayerSeasonStats(Base):
    """Aggregated season-level stats for a player (one row per player per year)."""
    __tablename__ = "player_season_stats"
    __table_args__ = (
        UniqueConstraint('player_id', 'year', name='uq_player_season'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    games_played = Column(Integer, default=0)

    # Passing
    pass_att = Column(Integer, default=0)
    pass_cmp = Column(Integer, default=0)
    pass_yd = Column(Integer, default=0)
    pass_td = Column(Integer, default=0)
    pass_int = Column(Integer, default=0)
    pass_rating = Column(Float, default=0.0)

    # Rushing
    rush_att = Column(Integer, default=0)
    rush_yd = Column(Integer, default=0)
    rush_td = Column(Integer, default=0)
    rush_fumbles = Column(Integer, default=0)

    # Receiving
    targets = Column(Integer, default=0)
    rec = Column(Integer, default=0)
    rec_yd = Column(Integer, default=0)
    rec_td = Column(Integer, default=0)

    # Fantasy
    fantasy_points_total = Column(Float, default=0.0)
    fantasy_points_avg = Column(Float, default=0.0)
    fantasy_points_per_touch = Column(Float, default=0.0)

    # Advanced (from nfl_data_py)
    snap_count = Column(Integer, nullable=True)
    snap_pct = Column(Float, nullable=True)
    air_yards = Column(Float, nullable=True)
    yac = Column(Float, nullable=True)
    wopr = Column(Float, nullable=True)

    # ADP (Average Draft Position)
    adp = Column(Float, nullable=True)
    adp_source = Column(String, nullable=True)  # e.g. 'csv', 'espn', 'yahoo'

    # Meta
    source = Column(String, default='espn')  # 'espn', 'nfl_data_py', 'combined'
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("DBPlayer", back_populates="season_stats")


class DBNFLTeamStats(Base):
    """NFL team-level stats per season/week for matchup analysis."""
    __tablename__ = "nfl_team_stats"
    __table_args__ = (
        UniqueConstraint('nfl_team', 'year', 'week', name='uq_nfl_team_year_week'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    nfl_team = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=False)
    week = Column(Integer, nullable=True)  # NULL for season totals

    # Offense
    total_yards = Column(Integer, default=0)
    pass_yards = Column(Integer, default=0)
    rush_yards = Column(Integer, default=0)
    points_scored = Column(Integer, default=0)

    # Defense
    points_allowed = Column(Integer, default=0)
    pass_yards_allowed = Column(Integer, default=0)
    rush_yards_allowed = Column(Integer, default=0)

    # Positional defense rankings (1=best defense, 32=worst)
    def_rank_vs_qb = Column(Integer, nullable=True)
    def_rank_vs_rb = Column(Integer, nullable=True)
    def_rank_vs_wr = Column(Integer, nullable=True)
    def_rank_vs_te = Column(Integer, nullable=True)

    # Meta
    source = Column(String, default='nfl_data_py')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DBPlayerGameLog(Base):
    """Individual game log entries — one row per player per game."""
    __tablename__ = "player_game_logs"
    __table_args__ = (
        UniqueConstraint('player_id', 'year', 'week', name='uq_player_game_log'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    week = Column(Integer, nullable=False)
    opponent = Column(String, nullable=True)

    # Passing
    pass_att = Column(Integer, default=0)
    pass_cmp = Column(Integer, default=0)
    pass_yd = Column(Integer, default=0)
    pass_td = Column(Integer, default=0)
    pass_int = Column(Integer, default=0)

    # Rushing
    rush_att = Column(Integer, default=0)
    rush_yd = Column(Integer, default=0)
    rush_td = Column(Integer, default=0)

    # Receiving
    targets = Column(Integer, default=0)
    rec = Column(Integer, default=0)
    rec_yd = Column(Integer, default=0)
    rec_td = Column(Integer, default=0)

    # Misc
    fumbles = Column(Integer, default=0)
    fumbles_lost = Column(Integer, default=0)
    two_pt_conversions = Column(Integer, default=0)

    # Fantasy
    fantasy_points = Column(Float, default=0.0)

    # Tracking
    is_active_game = Column(Boolean, default=False)

    # Meta
    source = Column(String, default='espn')  # 'espn', 'nfl_data_py'
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("DBPlayer", back_populates="game_logs")


class DBLeague(Base):
    __tablename__ = "leagues"

    id = Column(Integer, primary_key=True, autoincrement=True)
    league_id = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    year = Column(Integer, nullable=False)
    espn_s2 = Column(String, nullable=True)
    swid = Column(String, nullable=True)
    scoring_settings = Column(JSON, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DBSportsbookOdds(Base):
    """Betting line data fetched from The Odds API.

    Stores both game lines (h2h, spreads, totals) and player props.
    One row per unique (event, bookmaker, market, outcome_name) combination.
    """
    __tablename__ = "sportsbook_odds"
    __table_args__ = (
        UniqueConstraint(
            'event_id', 'bookmaker', 'market', 'outcome_name',
            name='uq_odds_event_bookmaker_market_outcome',
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_id = Column(String, nullable=False, index=True)
    sport_key = Column(String, nullable=False)
    sport_title = Column(String, nullable=True)
    commence_time = Column(DateTime, nullable=True)
    home_team = Column(String, nullable=False)
    away_team = Column(String, nullable=False)
    bookmaker = Column(String, nullable=False)
    market = Column(String, nullable=False, index=True)
    outcome_name = Column(String, nullable=False)
    price = Column(Float, nullable=True)
    point = Column(Float, nullable=True)
    description = Column(String, nullable=True)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


DEFAULT_SCORING_SETTINGS = {
    "pass_yd": 0.04,
    "pass_td": 4,
    "pass_int": -2,
    "rush_yd": 0.1,
    "rush_td": 6,
    "rec": 0.5,
    "rec_yd": 0.1,
    "rec_td": 6,
    "fumbles_lost": -2,
    "two_pt": 2,
}


def get_scoring_settings(league: DBLeague = None) -> dict:
    """Return scoring settings from a league, falling back to 0.5 PPR defaults."""
    if league and league.scoring_settings:
        merged = dict(DEFAULT_SCORING_SETTINGS)
        merged.update(league.scoring_settings)
        return merged
    return dict(DEFAULT_SCORING_SETTINGS)
