from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, JSON, Boolean,
    UniqueConstraint, Index,
)
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

    # Cross-source identity. ``player_id`` is whichever importer created the
    # row first; these are the stable per-source keys used to recognize the
    # same human across ESPN, nfl_data_py, and FantasyFootballCalculator.
    espn_id = Column(String, nullable=True, index=True)
    gsis_id = Column(String, nullable=True, index=True)
    pfr_id = Column(String, nullable=True, index=True)

    # Depth-chart standing, from nflverse's snapshot feed. A single current
    # value rather than a table: a depth chart is state, not history, and the
    # only question a lineup asks is "is he the starter right now". Rank 1 is
    # the starter at his position.
    depth_chart_rank = Column(Integer, nullable=True)
    depth_chart_at = Column(DateTime, nullable=True)

    # Draft origin, from nflverse's player table.
    #
    # ``rookie_season`` rather than a years-of-experience count on purpose: an
    # experience number is a *current* value that ages, so from a 2026 snapshot
    # it cannot answer "was he a rookie in 2025" — which a backfill needs.
    # A season is a fixed fact and stays correct for every year we look at.
    # (The older ``years_exp`` and ``draft_number`` columns below are ESPN
    # profile fields and have never been populated; left alone rather than
    # repurposed, so nothing that reads them changes meaning.)
    rookie_season = Column(Integer, nullable=True)
    draft_round = Column(Integer, nullable=True)
    draft_pick = Column(Integer, nullable=True)

    # Profile / bio. Deliberately real columns rather than keys in ``stats``:
    # ``stats`` holds ESPN's raw scoring-period payload and is replaced
    # wholesale on every sync, so anything stored there does not survive.
    bye_week = Column(Integer, nullable=True)
    injury_status = Column(String, nullable=True)  # ACTIVE, QUESTIONABLE, OUT, ...
    injured = Column(Boolean, default=False)
    jersey = Column(String, nullable=True)
    age = Column(Integer, nullable=True)
    height = Column(String, nullable=True)
    weight = Column(Integer, nullable=True)
    college = Column(String, nullable=True)
    years_exp = Column(Integer, nullable=True)
    draft_number = Column(Integer, nullable=True)
    pos_rank = Column(Integer, nullable=True)  # ESPN positional ranking
    percent_owned = Column(Float, nullable=True)
    percent_started = Column(Float, nullable=True)
    profile_updated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    team = relationship("DBTeam", back_populates="players")
    weekly_stats = relationship("DBWeeklyPlayerStats", back_populates="player", cascade="all, delete-orphan")
    season_stats = relationship("DBPlayerSeasonStats", back_populates="player", cascade="all, delete-orphan")
    game_logs = relationship("DBPlayerGameLog", back_populates="player", cascade="all, delete-orphan")
    news = relationship("DBPlayerNews", back_populates="player", cascade="all, delete-orphan")


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
    # 'human' or 'ai'. An AI team is structurally identical to a human one —
    # this column is the only difference, which is what lets a future human
    # take one over.
    manager_type = Column(String, nullable=False, default='human', server_default='human')
    ai_strategy = Column(String, nullable=True)   # a DraftStrategy value
    ai_profile = Column(JSON, nullable=True)      # an AIProfile dict
    draft_slot = Column(Integer, nullable=True)   # 1-indexed pick slot
    # Forward-compat hook for multiple human users. Nothing reads it yet and
    # there is no users table; it exists so adding one is not a migration of
    # every team row.
    owner_user_id = Column(String, nullable=True)

    # Per-source weighting for the multi-source projections view, as
    # ``{source_key: weight}``. NULL means "never customised" and reads as the
    # tuned defaults — distinct from an all-zero dict, which is a viewer who
    # deliberately unticked everything. Resetting nulls the column rather than
    # writing the defaults into it, so a later change to those defaults still
    # reaches a team that never expressed a preference.
    projection_weights = Column(JSON, nullable=True)

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
    adp_stdev = Column(Float, nullable=True)  # pick-position spread across real drafts
    adp_high = Column(Float, nullable=True)  # earliest pick observed
    adp_low = Column(Float, nullable=True)  # latest pick observed
    adp_times_drafted = Column(Integer, nullable=True)  # sample size behind the ADP

    # Meta
    source = Column(String, default='espn')  # 'espn', 'nfl_data_py', 'combined'
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    player = relationship("DBPlayer", back_populates="season_stats")


class DBNFLTeamStats(Base):
    """NFL team-level stats per season/week for matchup analysis."""
    __tablename__ = "nfl_team_stats"
    __table_args__ = (
        UniqueConstraint('nfl_team', 'year', 'week', name='uq_nfl_team_year_week'),
        # Season rows use week=NULL, and SQL treats NULL as distinct from NULL,
        # so the constraint above never applied to them — every re-import
        # appended another season row (the 2024 data had five per team).
        # A partial index is what actually enforces one season row per team.
        Index(
            'uq_nfl_team_season',
            'nfl_team', 'year',
            unique=True,
            sqlite_where=Column('week').is_(None),
        ),
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


class DBNFLGame(Base):
    """One row per NFL game, from ``nfl_data_py.import_schedules``.

    This is the only forward-looking data in the schema. Game logs exist only
    for games already played, so without a schedule a projection for an
    upcoming week cannot name the opponent — the matchup adjustment silently
    collapses to the neutral rank-16 default for exactly the weeks a user
    cares about.

    It also carries real final scores, which replace the
    ``touchdowns × 7`` approximation that team offense/defense ratings used to
    be derived from.
    """
    __tablename__ = "nfl_games"
    __table_args__ = (
        UniqueConstraint(
            'year', 'week', 'home_team', 'away_team', name='uq_nfl_game',
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(String, nullable=True, index=True)  # nflverse game_id
    year = Column(Integer, nullable=False, index=True)
    week = Column(Integer, nullable=False)
    game_type = Column(String, nullable=True)  # REG, WC, DIV, CON, SB
    home_team = Column(String, nullable=False, index=True)
    away_team = Column(String, nullable=False, index=True)

    # NULL until the game is played — this is how "upcoming" is detected.
    home_score = Column(Integer, nullable=True)
    away_score = Column(Integer, nullable=True)

    kickoff_at = Column(DateTime, nullable=True)
    roof = Column(String, nullable=True)  # dome, outdoors, closed, open
    surface = Column(String, nullable=True)

    # Closing betting lines, shipped free by nfl_data_py.import_schedules().
    # This is the only real market data in the schema — the sportsbook_odds
    # table needs a paid Odds API plan for player props, and what it currently
    # holds is hand-written seed fixtures.
    #
    # ``spread_line`` is from the HOME team's perspective and positive when the
    # home team is favoured (SEA 3.5 with a -180 home moneyline means Seattle
    # laying 3.5). Getting that sign backwards inverts every implied team total
    # derived from it, so it is stored exactly as nflverse publishes it and
    # interpreted at the point of use.
    spread_line = Column(Float, nullable=True)
    total_line = Column(Float, nullable=True)
    home_moneyline = Column(Float, nullable=True)
    away_moneyline = Column(Float, nullable=True)

    source = Column(String, default='nfl_data_py')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DBPlayerProjection(Base):
    """One projection per player per scope per source.

    ``DBPlayer.projected_points`` cannot hold this. Two importers write it in
    two different units -- espn_sync stores a per-game scoring-period value,
    adp_service stores the board's season-scale totalRating -- so the column
    ranks Philip Rivers above Josh Allen. This table keeps each source
    separate and units explicit, which also lets the UI show *why* two
    sources disagree instead of hiding it behind one number.
    """
    __tablename__ = "player_projections"
    __table_args__ = (
        UniqueConstraint(
            'player_id', 'year', 'week', 'source', name='uq_player_projection',
        ),
        # Season rows use week=NULL, and SQL treats NULL as distinct from NULL,
        # so the constraint above never applies to them. A partial index is what
        # actually enforces one season row per player per source.
        Index(
            'uq_player_projection_season',
            'player_id', 'year', 'source',
            unique=True,
            sqlite_where=Column('week').is_(None),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)

    # NULL means the season scope. Season rows store season TOTALS; weekly
    # rows store that week's points.
    week = Column(Integer, nullable=True)

    # blend | model | espn | sportsbook | adp
    source = Column(String, nullable=False)

    projected_points = Column(Float, nullable=False, default=0.0)
    floor = Column(Float, nullable=True)
    ceiling = Column(Float, nullable=True)
    std_dev = Column(Float, nullable=True)

    # A real column, not a components key. This is the games-played divisor
    # projection_service used to turn its per-game rate into the season TOTAL
    # stored in projected_points above — kept here for transparency/debugging,
    # not because a consumer re-derives a rate from it. The draft pool serves
    # projected_points as-is; reaching into JSON for this value is how the
    # mixed-unit bug comes back.
    expected_games = Column(Float, nullable=True)

    components = Column(JSON, default=dict)
    computed_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    roster_slots = Column(JSON, nullable=True)
    # Season-league fields. ``kind`` discriminates: existing ESPN-synced rows
    # default to 'espn' and none of the rest apply to them.
    kind = Column(String, nullable=False, default='espn', server_default='espn')  # espn | season
    status = Column(String, nullable=True)  # drafting | in_season | complete
    current_week = Column(Integer, default=1)
    regular_season_weeks = Column(Integer, default=14)
    playoff_teams = Column(Integer, default=6)
    playoff_start_week = Column(Integer, default=15)
    # The finished picks_log, so a recap survives a server restart — the mock
    # draft engine's state is in-memory and does not.
    draft_snapshot = Column(JSON, nullable=True)
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
            'event_id', 'bookmaker', 'market', 'outcome_name', 'description',
            name='uq_odds_event_bookmaker_market_outcome_desc',
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


class DBPlayerNews(Base):
    """Cached player news articles from ESPN's public API.

    Fetched on-demand when viewing a player detail page, with a TTL-based
    cache so repeated views within ``max_age_minutes`` serve from the DB
    instead of re-hitting the API.
    """
    __tablename__ = "player_news"
    __table_args__ = (
        UniqueConstraint(
            'player_id', 'espn_headline_id',
            name='uq_player_news_headline',
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    espn_headline_id = Column(String, nullable=False)
    headline = Column(String, nullable=False)
    description = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    published_at = Column(DateTime, nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    player = relationship("DBPlayer", back_populates="news")


class DBRosterSpot(Base):
    """Who owns a player, scoped to one league.

    ``DBPlayer.team_id`` is a single global FK — one player, one team, across
    the whole application. That is fine for a single imported ESPN league and
    impossible for a drafted league sharing the same player rows. This table
    carries the assignment instead, so both kinds of league coexist.

    ``dropped_at`` is not used in Cycle 1 (rosters are frozen after the draft)
    but the shape is here so waivers and trades do not require migrating the
    core relationship later.
    """
    __tablename__ = "roster_spots"
    __table_args__ = (
        # SQL cannot express "one team per player per league" with a plain
        # unique constraint once drops exist — a dropped row must not block a
        # re-add. The partial index is what makes it enforceable.
        Index(
            'uq_roster_spot_active',
            'league_id', 'player_id',
            unique=True,
            sqlite_where=Column('dropped_at').is_(None),
        ),
        Index('ix_roster_spot_team', 'team_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    acquired_via = Column(String, nullable=False, default='draft')
    acquired_at = Column(DateTime, default=datetime.utcnow)
    dropped_at = Column(DateTime, nullable=True)


class DBMatchup(Base):
    """One head-to-head game. Source of truth for standings.

    Team ids are nullable because playoff rows are created at league creation,
    before anyone is seeded. That is also why the unique key is
    ``bracket_slot`` rather than ``home_team_id``: SQLite treats every NULL as
    distinct, so a team-keyed constraint would silently allow duplicate
    unseeded rows in the same week.
    """
    __tablename__ = "matchups"
    __table_args__ = (
        UniqueConstraint(
            'league_id', 'year', 'week', 'bracket_slot', name='uq_matchup_slot',
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    week = Column(Integer, nullable=False, index=True)
    bracket_slot = Column(Integer, nullable=False, default=0)

    home_team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    away_team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)
    home_points = Column(Float, default=0.0)
    away_points = Column(Float, default=0.0)
    winner_team_id = Column(Integer, ForeignKey("teams.id"), nullable=True)

    is_playoff = Column(Boolean, default=False)
    round_name = Column(String, nullable=True)
    status = Column(String, nullable=False, default='scheduled')

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DBLineupSlot(Base):
    """One rostered player's slot for one team-week.

    Deliberately not DBWeeklyPlayerStats: that table's parent is keyed
    ``(team_id, week)`` with no year, and espn_sync rewrites those rows
    wholesale on every sync. A season league's lineup history must not be
    destroyable by an unrelated ESPN import.
    """
    __tablename__ = "lineup_slots"
    __table_args__ = (
        UniqueConstraint(
            'team_id', 'year', 'week', 'player_id', name='uq_lineup_slot_player',
        ),
        Index('ix_lineup_slot_team_week', 'team_id', 'year', 'week'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False)
    year = Column(Integer, nullable=False)
    week = Column(Integer, nullable=False)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)

    # QB | RB | WR | TE | FLEX | K | DEF | BENCH — the vocabulary in
    # services/mock_draft.py DEFAULT_LINEUP_SLOTS.
    slot = Column(String, nullable=False)

    # Kickoff of this player's game. NULL until the game starts.
    locked_at = Column(DateTime, nullable=True)
    set_by = Column(String, nullable=False, default='auto')  # user|auto|ai|agent

    projected_points = Column(Float, default=0.0)
    actual_points = Column(Float, default=0.0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DBManagerRun(Base):
    """One invocation of the Claude team-manager agent.

    Exists so a proposal is reviewable and revisitable rather than a transient
    HTTP response, and so a track record can be built over a season the way
    agent_scoring.py does for projections.
    """
    __tablename__ = "manager_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    league_id = Column(Integer, ForeignKey("leagues.id"), nullable=False, index=True)
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    week = Column(Integer, nullable=True)

    kind = Column(String, nullable=False, default='lineup')
    # running | proposed | applied | discarded | failed
    status = Column(String, nullable=False, default='running')

    proposal = Column(JSON, nullable=True)
    rationale = Column(String, nullable=True)
    citations = Column(JSON, nullable=True)

    model = Column(String, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    error = Column(String, nullable=True)


class DBPlayerAdvancedMetric(Base):
    """One advanced metric, for one player, for one week.

    Long rather than wide on purpose. The metrics come from four feeds with
    very uneven coverage — snap counts reach every player, Next Gen Stats only
    a few hundred qualifying ones — so a wide table would be mostly NULL, and
    every new metric would be a migration. Here a new metric is rows.

    The bigger reason is the hot-movers scan: with one row shape, "recent
    window against prior baseline, direction-aware" is a single implementation
    covering every metric, instead of a hand-maintained list of column names
    that goes stale the first time someone adds one and forgets.

    NGS publishes ``week = 0`` rows holding season aggregates. Those are
    skipped at import; season figures are derived from the weekly rows so there
    is one definition, and so a season total can never be read as week zero.
    """
    __tablename__ = "player_advanced_metrics"
    __table_args__ = (
        UniqueConstraint(
            'player_id', 'year', 'week', 'metric', name='uq_player_metric_week',
        ),
        # The league-wide hot scan reads a whole (year, week, metric) slice.
        Index('ix_metric_week_scan', 'year', 'week', 'metric'),
        # One player's trend for one metric.
        Index('ix_metric_player_series', 'player_id', 'metric'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False)
    week = Column(Integer, nullable=False)

    #: A key from ``services/advanced_metrics.py::METRICS``.
    metric = Column(String, nullable=False)
    value = Column(Float, nullable=False)

    source = Column(String, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DBPlayerInjury(Base):
    """One injury-report row per player per week, from nflverse.

    ``DBPlayer.injury_status`` cannot do this job. It is a single undated
    column written by whatever ESPN sync ran last, so an archived league leaves
    a season-old ``QUESTIONABLE`` sitting on a player — and ``plan_lineup``
    discounts projections from it. For a start/sit decision that is worse than
    having no injury data at all, because it looks current.

    ``report_status`` is the official game designation (Out / Doubtful /
    Questionable) and is only published from about Friday. ``practice_status``
    appears from Wednesday and is the earlier signal, which is why both are
    kept rather than collapsing them into one field.
    """
    __tablename__ = "player_injuries"
    __table_args__ = (
        UniqueConstraint('player_id', 'year', 'week', name='uq_player_injury'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    player_id = Column(Integer, ForeignKey("players.id"), nullable=False, index=True)
    year = Column(Integer, nullable=False, index=True)
    week = Column(Integer, nullable=False)

    # Out | Doubtful | Questionable — NULL until the Friday report.
    report_status = Column(String, nullable=True)
    # Did Not Participate / Limited / Full — available from Wednesday.
    practice_status = Column(String, nullable=True)
    primary_injury = Column(String, nullable=True)

    source = Column(String, default='nfl_data_py')
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DBProjectionSourceRun(Base):
    """One refresh attempt for one projection source at one scope.

    The projections view renders six sources whose failure modes have nothing
    in common: one needs an API key, one scrapes HTML, one covers a few dozen
    players because an agent had to run for each. ``player_projections`` cannot
    answer "did sportsbook fail, or does this player simply have no props?" —
    a missing row looks identical either way. This table is what separates the
    two, and it is the only thing the header freshness chip reads.
    """
    __tablename__ = "projection_source_runs"
    __table_args__ = (
        UniqueConstraint(
            'source', 'year', 'week', name='uq_projection_source_run',
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String, nullable=False, index=True)
    year = Column(Integer, nullable=False)

    # NULL is the season scope, matching DBPlayerProjection.week. Weekly
    # refreshes always set it.
    week = Column(Integer, nullable=True)

    # ok | error | skipped
    status = Column(String, nullable=False, default='ok')
    rows_written = Column(Integer, default=0)
    error = Column(String, nullable=True)

    started_at = Column(DateTime, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)


DEFAULT_SCORING_SETTINGS = {
    # Offense — unchanged, 0.5 PPR
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
    "two_pt_conversions": 2,  # ESPN mappers emit this key; both names must exist or 2PT scores zero

    # Kicking. Field goals score by distance, which is why one ``fg`` key
    # would not do — a 52-yarder and a 21-yarder are not worth the same.
    "xp": 1,
    "fg_0_39": 3,
    "fg_40_49": 4,
    "fg_50_plus": 5,
    "fg_miss": -1,

    # Team defense. ``pts_allowed`` is deliberately absent here: it is a tier
    # table, not a multiplier, and lives in services/scoring.py.
    "def_sack": 1,
    "def_int": 2,
    "def_fumble_rec": 2,
    "def_td": 6,
    "def_safety": 2,
}


def get_scoring_settings(league: DBLeague = None) -> dict:
    """Return scoring settings from a league, falling back to 0.5 PPR defaults."""
    if league and league.scoring_settings:
        merged = dict(DEFAULT_SCORING_SETTINGS)
        merged.update(league.scoring_settings)
        return merged
    return dict(DEFAULT_SCORING_SETTINGS)
