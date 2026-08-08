"""Tests for ProjectionCriteriaBuilder auto-derivation from stats."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base,
    DBPlayer,
    DBTeam,
    DBPlayerSeasonStats,
    DBPlayerGameLog,
    DBNFLTeamStats,
    DBNFLGame,
)
from pigskin_mastermind.models.projection_criteria import (
    WeeklyProjectionCriteria,
    YearlyProjectionCriteria,
)
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder,
)

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


@pytest.fixture
def sample_data(db):
    team = DBTeam(team_id="t1", name="Test Team", owner="Owner")
    db.add(team)
    db.flush()

    player = DBPlayer(
        player_id="p1",
        name="Test QB",
        position="QB",
        nfl_team="KC",
        team_id=team.id,
        stats={"age": 28, "injuryStatus": ""},
    )
    db.add(player)
    db.flush()

    # Season stats
    _fpts_total = 350.0
    _pass_att = 500
    _rush_att = 50
    _rec = 0
    season = DBPlayerSeasonStats(
        player_id=player.id,
        year=2024,
        games_played=16,
        pass_att=_pass_att,
        pass_cmp=340,
        pass_yd=4500,
        pass_td=35,
        pass_int=10,
        rush_att=_rush_att,
        rush_yd=200,
        rush_td=3,
        rec=_rec,
        rec_yd=0,
        rec_td=0,
        targets=0,
        fantasy_points_total=_fpts_total,
        fantasy_points_avg=21.875,
        fantasy_points_per_touch=round(_fpts_total / (_pass_att + _rush_att + _rec), 6),
        snap_pct=0.95,
    )
    db.add(season)

    # Game logs for trend calculation
    for week in range(1, 17):
        pts = 20.0 + (week - 8)  # trending up over season
        db.add(
            DBPlayerGameLog(
                player_id=player.id,
                year=2024,
                week=week,
                opponent=f"OPP{week}",
                pass_yd=280,
                pass_td=2,
                pass_int=1,
                fantasy_points=pts,
            )
        )

    # Team defense stats
    db.add(
        DBNFLTeamStats(
            nfl_team="KC",
            year=2024,
            week=None,
            total_yards=6000,
            pass_yards=4000,
            rush_yards=2000,
            points_scored=450,
        )
    )

    # Opponent defense stats
    db.add(
        DBNFLTeamStats(
            nfl_team="OPP10",
            year=2024,
            week=None,
            def_rank_vs_qb=5,
        )
    )

    db.commit()
    return player


class TestBuildWeeklyCriteria:
    def test_builds_valid_criteria(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)

        assert isinstance(criteria, WeeklyProjectionCriteria)
        # Shrinkage pulls a short sample toward the positional prior, so the
        # anchor sits between the raw average and that prior rather than on
        # either. The old assertion of 21.875 was the raw full-season average
        # *including week 10* — the look-ahead this module exists to remove.
        assert 0 < criteria.historical_average_points < 21.875
        assert 0 <= criteria.player_skill_level <= 100
        assert 0 <= criteria.opponent_defense_level <= 100
        assert 1 <= criteria.opposing_defense_vs_position_rank <= 32

    def test_opponent_defense_rank(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)

        # Week 10 opponent is "OPP10" which has def_rank_vs_qb=5
        assert criteria.opposing_defense_vs_position_rank == 5

    def test_default_defense_rank_when_no_data(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        # Week 1's opponent "OPP1" has no defense data
        criteria = builder.build_weekly_criteria(sample_data.id, week=1, year=2024)
        assert criteria.opposing_defense_vs_position_rank == 16  # default

    def test_trend_score_positive_for_improving(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=16, year=2024)

        # Last 4 weeks (13-16) average higher than season avg
        assert criteria.recent_trend_score > 0

    def test_overrides_applied(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(
            sample_data.id,
            week=10,
            year=2024,
            overrides={"weather_impact_score": -50.0, "injury_risk_score": 80.0},
        )
        assert criteria.weather_impact_score == -50.0
        assert criteria.injury_risk_score == 80.0

    def test_player_not_found(self, db):
        builder = ProjectionCriteriaBuilder(db)
        with pytest.raises(ValueError, match="Player 999 not found"):
            builder.build_weekly_criteria(999, week=1, year=2024)

    def test_touch_share_as_touch_percentage(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)
        # QB with 500 pass_att — only QB on team so 100% of pass attempts
        assert criteria.positional_touch_percentage == pytest.approx(100.0, rel=0.01)


class TestBuildYearlyCriteria:
    def test_builds_valid_criteria(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        # Build for 2025 using 2024 data
        criteria = builder.build_yearly_criteria(sample_data.id, year=2025)

        assert isinstance(criteria, YearlyProjectionCriteria)
        assert criteria.historical_average_points == 21.875

    def test_age_deviation(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_yearly_criteria(sample_data.id, year=2025)

        # Player age 28, QB peak 29, deviation = -1
        assert criteria.age_deviation_from_optimum == -1.0

    def test_coaching_stability_default(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_yearly_criteria(sample_data.id, year=2025)
        # Manual override only, default 50
        assert criteria.coaching_stability_score == 50.0

    def test_overrides_applied(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_yearly_criteria(
            sample_data.id, year=2025, overrides={"coaching_stability_score": 80.0}
        )
        assert criteria.coaching_stability_score == 80.0


class TestOpponentDefenseLevel:
    def test_worst_defense_rank_32_gives_level_near_100(self, db, sample_data):
        """Rank 32 (worst defense) should give opponent_defense_level near 100."""
        builder = ProjectionCriteriaBuilder(db)
        # Override def_rank by using a weak opponent in week 10 (has def_rank_vs_qb=5)
        # We test via overrides to isolate the formula
        criteria = builder.build_weekly_criteria(
            sample_data.id,
            week=10,
            year=2024,
            overrides={"opponent_defense_level": ((32 - 1) / 31) * 100},
        )
        assert criteria.opponent_defense_level == pytest.approx(100.0, rel=0.01)

    def test_best_defense_rank_1_gives_level_near_0(self, db, sample_data):
        """Rank 1 (best defense) should give opponent_defense_level near 0."""
        criteria_level = ((1 - 1) / 31) * 100
        assert criteria_level == pytest.approx(0.0, abs=0.01)

    def test_middle_defense_rank_16_gives_level_near_48(self, db, sample_data):
        """Rank 16 (middle defense) should give opponent_defense_level near 48.4."""
        criteria_level = ((16 - 1) / 31) * 100
        assert criteria_level == pytest.approx(48.4, rel=0.01)

    def test_weekly_criteria_opponent_level_uses_def_rank(self, db, sample_data):
        """A known def_rank should set opponent_defense_level correctly."""
        # Week 10 opponent "OPP10" has def_rank_vs_qb=5
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)
        expected = ((5 - 1) / 31) * 100
        assert criteria.opponent_defense_level == pytest.approx(expected, rel=0.01)

    def test_yearly_criteria_uses_schedule_defense(self, db, sample_data):
        """build_yearly_criteria should use schedule-averaged opponent defense level."""
        # _compute_schedule_defense_level was rewritten to read the *upcoming*
        # schedule (DBNFLGame) instead of averaging opponents the player had
        # already faced (the game-log `opponent` field) — that old approach
        # described a season already over, not the one being projected. A
        # schedule row is now required for this to resolve to anything but
        # the neutral 50.0 default.
        db.add(DBNFLGame(year=2025, week=1, home_team="KC", away_team="OPP10"))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_yearly_criteria(sample_data.id, year=2025)
        # OPP10 has no 2025 defense row yet, so this falls back to its 2024
        # def_rank_vs_qb=5 → level = ((5-1)/31)*100 ≈ 12.9
        expected = ((5 - 1) / 31) * 100
        assert criteria.opponent_defense_level == pytest.approx(expected, rel=0.01)


class TestTrendScoreConfidence:
    def test_small_sample_dampens_score(self, db):
        """A 1-game sample (confidence=0.25) should give at most 25% of the raw
        deviation."""
        team = DBTeam(team_id="t2", name="Team2", owner="Owner2")
        db.add(team)
        db.flush()

        player = DBPlayer(
            player_id="trend1",
            name="Trend Player",
            position="RB",
            nfl_team="SF",
            team_id=team.id,
            stats={},
        )
        db.add(player)
        db.flush()

        # Season average = 5 pts over 4 games; only 1 recent game scoring 25 pts
        # Raw deviation = ((25 - 5) / 5) * 100 = 400 → capped at 100
        # With confidence = 1/4 = 0.25: result = 100 * 0.25 = 25
        season = DBPlayerSeasonStats(
            player_id=player.id,
            year=2024,
            games_played=4,
            fantasy_points_total=20.0,
            fantasy_points_avg=5.0,
        )
        db.add(season)

        # Only 1 game log (small sample)
        db.add(
            DBPlayerGameLog(
                player_id=player.id,
                year=2024,
                week=16,
                fantasy_points=25.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        score = builder._compute_trend_score(player.id, year=2024, num_weeks=4)
        assert (
            score <= 25
        ), f"Expected trend score ≤ 25 with 1/4 confidence, got {score}"


class TestInjuryRisk:
    def test_out_player(self, db):
        player = DBPlayer(
            player_id="inj1",
            name="Injured",
            position="RB",
            nfl_team="SF",
            stats={"injuryStatus": "OUT"},
        )
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player)
        assert risk == 90.0

    def test_questionable_player(self, db):
        player = DBPlayer(
            player_id="inj2",
            name="Questionable",
            position="WR",
            nfl_team="GB",
            stats={"injuryStatus": "QUESTIONABLE"},
        )
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player)
        assert risk == 40.0

    def test_healthy_player(self, db):
        player = DBPlayer(
            player_id="inj3", name="Healthy", position="TE", nfl_team="KC", stats={}
        )
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player)
        assert risk == 5.0


class TestTouchShare:
    def test_qb_touch_share(self, db, sample_data):
        """QB touch share = pass_att / team_total_pass_att."""
        builder = ProjectionCriteriaBuilder(db)
        share = builder._compute_touch_share(sample_data.id, "QB", "KC", 2024)
        # Only QB on team with 500 pass_att → 100%
        assert share == pytest.approx(100.0, rel=0.01)

    def test_wr_touch_share(self, db):
        """WR touch share = targets / team_total_targets."""
        team = DBTeam(team_id="t3", name="Team3", owner="Owner3")
        db.add(team)
        db.flush()

        wr1 = DBPlayer(
            player_id="wr1",
            name="WR1",
            position="WR",
            nfl_team="BUF",
            team_id=team.id,
            stats={},
        )
        wr2 = DBPlayer(
            player_id="wr2",
            name="WR2",
            position="WR",
            nfl_team="BUF",
            team_id=team.id,
            stats={},
        )
        db.add_all([wr1, wr2])
        db.flush()

        db.add(
            DBPlayerSeasonStats(
                player_id=wr1.id,
                year=2024,
                games_played=16,
                targets=120,
                fantasy_points_total=200.0,
                fantasy_points_avg=12.5,
            )
        )
        db.add(
            DBPlayerSeasonStats(
                player_id=wr2.id,
                year=2024,
                games_played=16,
                targets=80,
                fantasy_points_total=120.0,
                fantasy_points_avg=7.5,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        share = builder._compute_touch_share(wr1.id, "WR", "BUF", 2024)
        # 120 / (120 + 80) = 60%
        assert share == pytest.approx(60.0, rel=0.01)

    def test_fallback_to_snap_pct(self, db):
        """Falls back to snap_pct when no team data available.

        ``snap_pct`` is stored 0-100 (the importer scales nflverse's 0-1
        ``offense_pct``), so 85% snaps is 85.0, not 0.85.
        """
        team = DBTeam(team_id="t4", name="Team4", owner="Owner4")
        db.add(team)
        db.flush()

        player = DBPlayer(
            player_id="fb1",
            name="Fallback",
            position="K",
            nfl_team="NYJ",
            team_id=team.id,
            stats={},
        )
        db.add(player)
        db.flush()

        db.add(
            DBPlayerSeasonStats(
                player_id=player.id,
                year=2024,
                games_played=16,
                snap_pct=85.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        share = builder._compute_touch_share(player.id, "K", "NYJ", 2024)
        assert share == pytest.approx(85.0, rel=0.01)


class TestSkillComposite:
    def test_composite_considers_multiple_factors(self, db, sample_data):
        """Skill composite should use points, efficiency, consistency, volume."""
        builder = ProjectionCriteriaBuilder(db)
        skill = builder._compute_skill_composite(sample_data.id, "QB", 2024)
        # Single player = 0th percentile for all rank-based metrics (no one below),
        # but consistency should contribute positively
        assert 0 <= skill <= 100

    def test_composite_higher_for_better_player(self, db):
        """Better player should have higher composite skill."""
        team = DBTeam(team_id="t5", name="Team5", owner="Owner5")
        db.add(team)
        db.flush()

        p1 = DBPlayer(
            player_id="sk1",
            name="Star",
            position="RB",
            nfl_team="DAL",
            team_id=team.id,
            stats={},
        )
        p2 = DBPlayer(
            player_id="sk2",
            name="Backup",
            position="RB",
            nfl_team="DAL",
            team_id=team.id,
            stats={},
        )
        db.add_all([p1, p2])
        db.flush()

        db.add(
            DBPlayerSeasonStats(
                player_id=p1.id,
                year=2024,
                games_played=16,
                rush_att=250,
                targets=60,
                fantasy_points_total=280.0,
                fantasy_points_avg=17.5,
                fantasy_points_per_touch=0.9,
            )
        )
        db.add(
            DBPlayerSeasonStats(
                player_id=p2.id,
                year=2024,
                games_played=16,
                rush_att=80,
                targets=20,
                fantasy_points_total=80.0,
                fantasy_points_avg=5.0,
                fantasy_points_per_touch=0.8,
            )
        )
        for w in range(1, 17):
            db.add(
                DBPlayerGameLog(player_id=p1.id, year=2024, week=w, fantasy_points=17.5)
            )
            db.add(
                DBPlayerGameLog(player_id=p2.id, year=2024, week=w, fantasy_points=5.0)
            )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        skill_star = builder._compute_skill_composite(p1.id, "RB", 2024)
        skill_backup = builder._compute_skill_composite(p2.id, "RB", 2024)
        assert skill_star > skill_backup


class TestPositionEfficiency:
    def test_qb_efficiency(self, db, sample_data):
        """QB efficiency uses pass_att + rush_att as denominator."""
        builder = ProjectionCriteriaBuilder(db)
        eff = builder._compute_position_efficiency(sample_data.id, "QB", 2024)
        # 350 / (500 + 50) = 0.636
        assert eff == pytest.approx(0.636, rel=0.01)

    def test_wr_efficiency(self, db):
        """WR efficiency uses targets as denominator."""
        team = DBTeam(team_id="t6", name="Team6", owner="Owner6")
        db.add(team)
        db.flush()

        wr = DBPlayer(
            player_id="we1",
            name="WR Eff",
            position="WR",
            nfl_team="MIA",
            team_id=team.id,
            stats={},
        )
        db.add(wr)
        db.flush()

        db.add(
            DBPlayerSeasonStats(
                player_id=wr.id,
                year=2024,
                games_played=16,
                targets=150,
                rec=100,
                fantasy_points_total=240.0,
                fantasy_points_avg=15.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        eff = builder._compute_position_efficiency(wr.id, "WR", 2024)
        # 240 / 150 = 1.6
        assert eff == pytest.approx(1.6, rel=0.01)

    def test_wr_efficiency_fallback_to_rec_when_no_targets(self, db):
        """WR efficiency falls back to rec when targets is 0 (ESPN often omits
        targets)."""
        team = DBTeam(team_id="teff1", name="EffTeam1", owner="Owner")
        db.add(team)
        db.flush()

        wr = DBPlayer(
            player_id="we2",
            name="WR NoTargets",
            position="WR",
            nfl_team="DEN",
            team_id=team.id,
            stats={},
        )
        db.add(wr)
        db.flush()

        db.add(
            DBPlayerSeasonStats(
                player_id=wr.id,
                year=2024,
                games_played=16,
                targets=0,
                rec=80,
                fantasy_points_total=160.0,
                fantasy_points_avg=10.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        eff = builder._compute_position_efficiency(wr.id, "WR", 2024)
        # Falls back to rec: 160 / 80 = 2.0  (not 0.0)
        assert eff == pytest.approx(2.0, rel=0.01)

    def test_rb_efficiency_fallback_to_rec_when_no_targets(self, db):
        """RB efficiency falls back to rec for receiving component when targets is 0."""
        team = DBTeam(team_id="teff2", name="EffTeam2", owner="Owner")
        db.add(team)
        db.flush()

        rb = DBPlayer(
            player_id="rbe1",
            name="RB NoTargets",
            position="RB",
            nfl_team="CLE",
            team_id=team.id,
            stats={},
        )
        db.add(rb)
        db.flush()

        db.add(
            DBPlayerSeasonStats(
                player_id=rb.id,
                year=2024,
                games_played=16,
                rush_att=200,
                targets=0,
                rec=40,
                fantasy_points_total=240.0,
                fantasy_points_avg=15.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        eff = builder._compute_position_efficiency(rb.id, "RB", 2024)
        # Falls back to rec: 240 / (200 + 40) = 1.0  (not 0.0)
        assert eff == pytest.approx(1.0, rel=0.01)


class TestTouchSharePositionFiltering:
    """Verify that _compute_touch_share denominators are position-scoped."""

    def test_rb_denominator_excludes_wr_targets(self, db):
        """RB touch share should not be diluted by WR targets in the denominator."""
        team = DBTeam(team_id="tts1", name="TSTeam1", owner="Owner")
        db.add(team)
        db.flush()

        rb = DBPlayer(
            player_id="ts_rb1",
            name="RB1",
            position="RB",
            nfl_team="PIT",
            team_id=team.id,
            stats={},
        )
        wr = DBPlayer(
            player_id="ts_wr1",
            name="WR1",
            position="WR",
            nfl_team="PIT",
            team_id=team.id,
            stats={},
        )
        db.add_all([rb, wr])
        db.flush()

        # RB: 200 rush + 50 targets = 250 opportunities
        db.add(
            DBPlayerSeasonStats(
                player_id=rb.id,
                year=2024,
                games_played=16,
                rush_att=200,
                targets=50,
                fantasy_points_total=200.0,
            )
        )
        # WR: 150 targets — should NOT appear in RB denominator
        db.add(
            DBPlayerSeasonStats(
                player_id=wr.id,
                year=2024,
                games_played=16,
                targets=150,
                fantasy_points_total=120.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        share = builder._compute_touch_share(rb.id, "RB", "PIT", 2024)
        # Only RBs: 250 / 250 = 100% (WR's 150 targets excluded)
        assert share == pytest.approx(100.0, rel=0.01)

    def test_wr_touch_share_fallback_to_rec_when_no_targets(self, db):
        """WR touch share falls back to rec when targets is 0."""
        team = DBTeam(team_id="tts2", name="TSTeam2", owner="Owner")
        db.add(team)
        db.flush()

        wr1 = DBPlayer(
            player_id="ts_wr2",
            name="WR2",
            position="WR",
            nfl_team="SEA",
            team_id=team.id,
            stats={},
        )
        wr2 = DBPlayer(
            player_id="ts_wr3",
            name="WR3",
            position="WR",
            nfl_team="SEA",
            team_id=team.id,
            stats={},
        )
        db.add_all([wr1, wr2])
        db.flush()

        # Both WRs have targets=0 but have receptions (ESPN didn't export targets)
        db.add(
            DBPlayerSeasonStats(
                player_id=wr1.id,
                year=2024,
                games_played=16,
                targets=0,
                rec=80,
                fantasy_points_total=160.0,
            )
        )
        db.add(
            DBPlayerSeasonStats(
                player_id=wr2.id,
                year=2024,
                games_played=16,
                targets=0,
                rec=40,
                fantasy_points_total=80.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        share = builder._compute_touch_share(wr1.id, "WR", "SEA", 2024)
        # Falls back to rec: 80 / (80 + 40) ≈ 66.7%  (not 0.0)
        assert share == pytest.approx(66.7, rel=0.01)


class TestMultiSeasonAvg:
    def test_weighted_average_across_seasons(self, db):
        """Multi-season average should weight recent seasons more."""
        team = DBTeam(team_id="t7", name="Team7", owner="Owner7")
        db.add(team)
        db.flush()

        player = DBPlayer(
            player_id="ms1",
            name="Multi",
            position="QB",
            nfl_team="LAR",
            team_id=team.id,
            stats={},
        )
        db.add(player)
        db.flush()

        # 3 seasons of data
        db.add(
            DBPlayerSeasonStats(
                player_id=player.id,
                year=2024,
                games_played=17,
                fantasy_points_total=340.0,
                fantasy_points_avg=20.0,
            )
        )
        db.add(
            DBPlayerSeasonStats(
                player_id=player.id,
                year=2023,
                games_played=16,
                fantasy_points_total=256.0,
                fantasy_points_avg=16.0,
            )
        )
        db.add(
            DBPlayerSeasonStats(
                player_id=player.id,
                year=2022,
                games_played=17,
                fantasy_points_total=170.0,
                fantasy_points_avg=10.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        avg = builder._compute_weighted_historical_avg(player.id, target_year=2025)
        # 20.0 * 0.6 + 16.0 * 0.3 + 10.0 * 0.1 = 12.0 + 4.8 + 1.0 = 17.8
        assert avg == pytest.approx(17.8, rel=0.01)

    def test_skips_seasons_with_few_games(self, db):
        """Seasons with < 6 games should be skipped."""
        team = DBTeam(team_id="t8", name="Team8", owner="Owner8")
        db.add(team)
        db.flush()

        player = DBPlayer(
            player_id="ms2",
            name="Injured",
            position="RB",
            nfl_team="CHI",
            team_id=team.id,
            stats={},
        )
        db.add(player)
        db.flush()

        db.add(
            DBPlayerSeasonStats(
                player_id=player.id,
                year=2024,
                games_played=17,
                fantasy_points_total=340.0,
                fantasy_points_avg=20.0,
            )
        )
        db.add(
            DBPlayerSeasonStats(
                player_id=player.id,
                year=2023,
                games_played=3,  # too few
                fantasy_points_total=60.0,
                fantasy_points_avg=20.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        avg = builder._compute_weighted_historical_avg(player.id, target_year=2025)
        # Only 2024 counts (weight=0.6), normalized: 20.0 * 0.6 / 0.6 = 20.0
        assert avg == pytest.approx(20.0, rel=0.01)


class TestInjuryHistory:
    def test_healthy_with_good_history(self, db):
        """Healthy player with full seasons should have low risk."""
        team = DBTeam(team_id="t9", name="Team9", owner="Owner9")
        db.add(team)
        db.flush()

        player = DBPlayer(
            player_id="ih1",
            name="Durable",
            position="WR",
            nfl_team="PHI",
            team_id=team.id,
            stats={},
        )
        db.add(player)
        db.flush()

        for yr in [2022, 2023, 2024]:
            db.add(
                DBPlayerSeasonStats(
                    player_id=player.id,
                    year=yr,
                    games_played=17,
                )
            )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player, player.id)
        # status=5.0 * 0.7 + history=(1-17/17)*100*0.3 = 3.5 + 0 = 3.5
        assert risk == pytest.approx(3.5, rel=0.01)

    def test_healthy_with_injury_history(self, db):
        """Healthy player with missed games should have elevated risk."""
        team = DBTeam(team_id="t10", name="Team10", owner="Owner10")
        db.add(team)
        db.flush()

        player = DBPlayer(
            player_id="ih2",
            name="Fragile",
            position="RB",
            nfl_team="SEA",
            team_id=team.id,
            stats={},
        )
        db.add(player)
        db.flush()

        # Average 10 games per season
        for yr in [2022, 2023, 2024]:
            db.add(
                DBPlayerSeasonStats(
                    player_id=player.id,
                    year=yr,
                    games_played=10,
                )
            )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player, player.id)
        # status=5 * 0.7 + (1-10/17)*100 * 0.3 = 3.5 + 12.35 ≈ 15.85
        expected = 5.0 * 0.7 + (1 - 10 / 17) * 100 * 0.3
        assert risk == pytest.approx(expected, rel=0.01)


class TestScheduleDefenseLevel:
    def test_schedule_avg_with_mixed_opponents(self, db, sample_data):
        """Schedule defense should average available opponent data."""
        # _compute_schedule_defense_level's first argument changed from a
        # player_id to an nfl_team (it now reads DBNFLGame, the actual
        # schedule, instead of averaging opponents already faced) — see the
        # comment on test_yearly_criteria_uses_schedule_defense above.
        db.add(DBNFLGame(year=2024, week=10, home_team="KC", away_team="OPP10"))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        level = builder._compute_schedule_defense_level(
            sample_data.nfl_team, "QB", 2024
        )
        # Only OPP10 has def_rank_vs_qb=5 → ((5-1)/31)*100 ≈ 12.9
        expected = ((5 - 1) / 31) * 100
        assert level == pytest.approx(expected, rel=0.01)

    def test_no_opponent_data_returns_neutral(self, db):
        """No game logs → neutral 50.0."""
        team = DBTeam(team_id="t11", name="Team11", owner="Owner11")
        db.add(team)
        db.flush()

        player = DBPlayer(
            player_id="sd1",
            name="NoGames",
            position="WR",
            nfl_team="ATL",
            team_id=team.id,
            stats={},
        )
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        level = builder._compute_schedule_defense_level(player.id, "WR", 2024)
        assert level == 50.0


class TestMomentumComposite:
    def test_momentum_blends_points_and_yards(self, db):
        """Momentum should blend points (60%) and yards (40%) deviations."""
        # Set up team with weekly stats where recent weeks are above average.
        # _compute_momentum now runs the team through normalize_team() before
        # querying, so the lookup key must be a real NFL abbreviation — the
        # old placeholder "MOM" normalizes to None and short-circuits to 0.0.
        for wk in range(1, 9):
            pts = 20 if wk <= 4 else 30
            yds = 300 if wk <= 4 else 400
            db.add(
                DBNFLTeamStats(
                    nfl_team="MIA",
                    year=2024,
                    week=wk,
                    points_scored=pts,
                    total_yards=yds,
                )
            )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        momentum = builder._compute_momentum("MIA", 2024, num_weeks=4)

        # Recent 4 weeks (5-8): pts=30, yds=400
        # Season avg: pts=25, yds=350
        # pts_dev = ((30-25)/25)*100 = 20%, yds_dev = ((400-350)/350)*100 ≈ 14.3%
        # blended = 20*0.6 + 14.3*0.4 = 12 + 5.71 ≈ 17.7
        assert momentum > 0
        assert momentum == pytest.approx(17.7, rel=0.05)


# ── Tests for auto-computed team stats from game logs ───────────────────


class TestPopulateTeamOffenseStats:
    """Test _populate_team_offense_stats aggregation."""

    def test_weekly_rows_created(self, db):
        """Each team-week should get a DBNFLTeamStats row."""
        team = DBTeam(team_id="toff1", name="OffTeam", owner="O")
        db.add(team)
        db.flush()

        qb = DBPlayer(
            player_id="offqb",
            name="QB1",
            position="QB",
            nfl_team="BAL",
            team_id=team.id,
            stats={},
        )
        rb = DBPlayer(
            player_id="offrb",
            name="RB1",
            position="RB",
            nfl_team="BAL",
            team_id=team.id,
            stats={},
        )
        db.add_all([qb, rb])
        db.flush()

        # Week 1 game logs
        db.add(
            DBPlayerGameLog(
                player_id=qb.id,
                year=2024,
                week=1,
                opponent="PIT",
                pass_yd=250,
                pass_td=2,
                rush_yd=30,
                rush_td=0,
                fantasy_points=22.0,
            )
        )
        db.add(
            DBPlayerGameLog(
                player_id=rb.id,
                year=2024,
                week=1,
                opponent="PIT",
                pass_yd=0,
                pass_td=0,
                rush_yd=100,
                rush_td=1,
                rec_yd=40,
                rec_td=0,
                fantasy_points=18.0,
            )
        )
        # Week 2 game logs
        db.add(
            DBPlayerGameLog(
                player_id=qb.id,
                year=2024,
                week=2,
                opponent="CIN",
                pass_yd=300,
                pass_td=3,
                rush_yd=15,
                rush_td=1,
                fantasy_points=30.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        builder._populate_team_offense_stats(2024)
        db.flush()

        # Week 1: pass_yards=250+0=250, rush_yards=30+100=130
        w1 = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="BAL", year=2024, week=1)
            .first()
        )
        assert w1 is not None
        assert w1.pass_yards == 250
        assert w1.rush_yards == 130
        assert w1.total_yards == 380
        # points: max(pass_td=2, rec_td=0)=2, rush_td=0+1=1 → (2+1)*7=21
        assert w1.points_scored == 21

        # Week 2: only QB
        w2 = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="BAL", year=2024, week=2)
            .first()
        )
        assert w2 is not None
        assert w2.pass_yards == 300
        assert w2.rush_yards == 15
        # points: max(pass_td=3, rec_td=0)=3, rush_td=1 → (3+1)*7=28
        assert w2.points_scored == 28

    def test_season_aggregate_created(self, db):
        """Season aggregate row (week=None) should sum across all weeks."""
        team = DBTeam(team_id="toff2", name="OffTeam2", owner="O")
        db.add(team)
        db.flush()

        qb = DBPlayer(
            player_id="offqb2",
            name="QB2",
            position="QB",
            nfl_team="KC",
            team_id=team.id,
            stats={},
        )
        db.add(qb)
        db.flush()

        for wk in range(1, 4):
            db.add(
                DBPlayerGameLog(
                    player_id=qb.id,
                    year=2024,
                    week=wk,
                    opponent=f"OPP{wk}",
                    pass_yd=200 + wk * 10,
                    pass_td=2,
                    rush_yd=20,
                    fantasy_points=20.0,
                )
            )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        builder._populate_team_offense_stats(2024)
        db.flush()

        season = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="KC", year=2024, week=None)
            .first()
        )
        assert season is not None
        # pass_yards = 210 + 220 + 230 = 660
        assert season.pass_yards == 660
        # rush_yards = 20 * 3 = 60
        assert season.rush_yards == 60
        assert season.total_yards == 720
        assert season.source == "computed_from_game_logs"


class TestPopulateDefenseAllowed:
    """Test _populate_defense_allowed aggregation."""

    def test_defense_allowed_from_opponent_field(self, db):
        """Defense allowed should aggregate stats against each opponent."""
        team = DBTeam(team_id="tdef1", name="DefTeam", owner="O")
        db.add(team)
        db.flush()

        qb = DBPlayer(
            player_id="defqb",
            name="QB",
            position="QB",
            nfl_team="BAL",
            team_id=team.id,
            stats={},
        )
        db.add(qb)
        db.flush()

        # Two games against PIT defense
        db.add(
            DBPlayerGameLog(
                player_id=qb.id,
                year=2024,
                week=1,
                opponent="PIT",
                pass_yd=250,
                pass_td=2,
                rush_yd=30,
                rush_td=0,
                fantasy_points=22.0,
            )
        )
        db.add(
            DBPlayerGameLog(
                player_id=qb.id,
                year=2024,
                week=10,
                opponent="PIT",
                pass_yd=200,
                pass_td=1,
                rush_yd=40,
                rush_td=1,
                fantasy_points=18.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        builder._populate_defense_allowed(2024)
        db.flush()

        # Season aggregate for PIT defense
        pit_season = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="PIT", year=2024, week=None)
            .first()
        )
        assert pit_season is not None
        assert pit_season.pass_yards_allowed == 450  # 250 + 200
        assert pit_season.rush_yards_allowed == 70  # 30 + 40

    def test_weekly_defense_rows(self, db):
        """Each week should have a defense-allowed row for the opponent."""
        team = DBTeam(team_id="tdef2", name="DefTeam2", owner="O")
        db.add(team)
        db.flush()

        rb = DBPlayer(
            player_id="defrb",
            name="RB",
            position="RB",
            nfl_team="MIN",
            team_id=team.id,
            stats={},
        )
        db.add(rb)
        db.flush()

        db.add(
            DBPlayerGameLog(
                player_id=rb.id,
                year=2024,
                week=5,
                opponent="GB",
                rush_yd=120,
                rush_td=2,
                fantasy_points=25.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        builder._populate_defense_allowed(2024)
        db.flush()

        gb_wk5 = (
            db.query(DBNFLTeamStats).filter_by(nfl_team="GB", year=2024, week=5).first()
        )
        assert gb_wk5 is not None
        assert gb_wk5.rush_yards_allowed == 120
        assert gb_wk5.points_allowed == 14  # 2 rush_td * 7


class TestPopulateDefenseRankings:
    """Test _populate_defense_rankings positional ranking computation."""

    def test_rankings_ordered_correctly(self, db):
        """Teams allowing fewer fantasy points should rank lower (better defense)."""
        team = DBTeam(team_id="trank1", name="RankTeam", owner="O")
        db.add(team)
        db.flush()

        qb1 = DBPlayer(
            player_id="rqb1",
            name="QB1",
            position="QB",
            nfl_team="BAL",
            team_id=team.id,
            stats={},
        )
        qb2 = DBPlayer(
            player_id="rqb2",
            name="QB2",
            position="QB",
            nfl_team="KC",
            team_id=team.id,
            stats={},
        )
        db.add_all([qb1, qb2])
        db.flush()

        # QB1 (BAL) scores big against CIN, less against PIT
        for wk in range(1, 9):
            db.add(
                DBPlayerGameLog(
                    player_id=qb1.id,
                    year=2024,
                    week=wk,
                    opponent="CIN" if wk <= 4 else "PIT",
                    pass_yd=300,
                    pass_td=3,
                    fantasy_points=30.0 if wk <= 4 else 15.0,
                )
            )
        # QB2 (KC) scores medium against CIN and PIT
        for wk in range(1, 5):
            db.add(
                DBPlayerGameLog(
                    player_id=qb2.id,
                    year=2024,
                    week=wk,
                    opponent="CIN" if wk <= 2 else "PIT",
                    pass_yd=250,
                    pass_td=2,
                    fantasy_points=20.0,
                )
            )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        builder._populate_defense_rankings(2024)
        db.flush()

        # Total fantasy points allowed to QBs:
        # CIN: 30*4 + 20*2 = 160
        # PIT: 15*4 + 20*2 = 100
        # PIT allows fewer → rank 1 (better defense), CIN → rank 2
        pit = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="PIT", year=2024, week=None)
            .first()
        )
        cin = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="CIN", year=2024, week=None)
            .first()
        )

        assert pit is not None
        assert cin is not None
        assert pit.def_rank_vs_qb == 1
        assert cin.def_rank_vs_qb == 2

    def test_multiple_position_rankings(self, db):
        """Rankings should be independent per position."""
        team = DBTeam(team_id="trank2", name="RankTeam2", owner="O")
        db.add(team)
        db.flush()

        qb = DBPlayer(
            player_id="mrqb",
            name="QB",
            position="QB",
            nfl_team="BAL",
            team_id=team.id,
            stats={},
        )
        rb = DBPlayer(
            player_id="mrrb",
            name="RB",
            position="RB",
            nfl_team="BAL",
            team_id=team.id,
            stats={},
        )
        db.add_all([qb, rb])
        db.flush()

        # vs DEN: QB does well, RB does poorly
        db.add(
            DBPlayerGameLog(
                player_id=qb.id,
                year=2024,
                week=1,
                opponent="DEN",
                pass_yd=350,
                pass_td=4,
                fantasy_points=35.0,
            )
        )
        db.add(
            DBPlayerGameLog(
                player_id=rb.id,
                year=2024,
                week=1,
                opponent="DEN",
                rush_yd=40,
                rush_td=0,
                fantasy_points=6.0,
            )
        )
        # vs LV: QB does poorly, RB does well
        db.add(
            DBPlayerGameLog(
                player_id=qb.id,
                year=2024,
                week=2,
                opponent="LV",
                pass_yd=150,
                pass_td=1,
                fantasy_points=12.0,
            )
        )
        db.add(
            DBPlayerGameLog(
                player_id=rb.id,
                year=2024,
                week=2,
                opponent="LV",
                rush_yd=150,
                rush_td=2,
                fantasy_points=28.0,
            )
        )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        builder._populate_defense_rankings(2024)
        db.flush()

        den = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="DEN", year=2024, week=None)
            .first()
        )
        lv = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="LV", year=2024, week=None)
            .first()
        )

        # QB: DEN allowed 35, LV allowed 12 → LV rank 1 (better), DEN rank 2
        assert lv.def_rank_vs_qb == 1
        assert den.def_rank_vs_qb == 2
        # RB: DEN allowed 6, LV allowed 28 → DEN rank 1 (better), LV rank 2
        assert den.def_rank_vs_rb == 1
        assert lv.def_rank_vs_rb == 2


class TestEnsureTeamStats:
    """Test the lazy _ensure_team_stats trigger."""

    def test_skips_when_data_exists(self, db, sample_data):
        """Should not re-compute if DBNFLTeamStats already has data for the year."""
        builder = ProjectionCriteriaBuilder(db)
        # sample_data already creates DBNFLTeamStats for KC 2024
        existing = db.query(DBNFLTeamStats).filter_by(year=2024).count()
        builder._ensure_team_stats(2024)
        after = db.query(DBNFLTeamStats).filter_by(year=2024).count()
        # Should not create additional rows
        assert after == existing

    def test_populates_when_empty(self, db):
        """Should auto-populate when no DBNFLTeamStats exist for the year."""
        team = DBTeam(team_id="tens1", name="EnsTeam", owner="O")
        db.add(team)
        db.flush()

        qb = DBPlayer(
            player_id="ensqb",
            name="QB",
            position="QB",
            nfl_team="SF",
            team_id=team.id,
            stats={},
        )
        db.add(qb)
        db.flush()

        db.add(
            DBPlayerGameLog(
                player_id=qb.id,
                year=2024,
                week=1,
                opponent="SEA",
                pass_yd=300,
                pass_td=3,
                rush_yd=20,
                fantasy_points=28.0,
            )
        )
        db.add(
            DBPlayerGameLog(
                player_id=qb.id,
                year=2024,
                week=2,
                opponent="ARI",
                pass_yd=280,
                pass_td=2,
                rush_yd=15,
                fantasy_points=22.0,
            )
        )
        db.commit()

        # Verify no team stats exist
        assert db.query(DBNFLTeamStats).filter_by(year=2024).count() == 0

        builder = ProjectionCriteriaBuilder(db)
        builder._ensure_team_stats(2024)
        db.flush()

        # Should now have team stats
        count = db.query(DBNFLTeamStats).filter_by(year=2024).count()
        assert count > 0

        # SF should have offense stats
        sf_season = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="SF", year=2024, week=None)
            .first()
        )
        assert sf_season is not None
        assert sf_season.pass_yards == 580  # 300 + 280
        assert sf_season.total_yards > 0

        # SEA and ARI should have defense data
        sea = (
            db.query(DBNFLTeamStats)
            .filter_by(nfl_team="SEA", year=2024, week=None)
            .first()
        )
        assert sea is not None
        assert sea.def_rank_vs_qb is not None

    def test_does_nothing_with_no_game_logs(self, db):
        """Should not create any rows if there are no game logs."""
        builder = ProjectionCriteriaBuilder(db)
        builder._ensure_team_stats(2024)
        assert db.query(DBNFLTeamStats).filter_by(year=2024).count() == 0


class TestBuilderUsesComputedTeamStats:
    """Integration test: builder criteria reflect computed team stats."""

    def test_weekly_team_offense_not_default(self, db):
        """team_offense_level should not be 50.0 when game logs exist."""
        team = DBTeam(team_id="tint1", name="IntTeam", owner="O")
        db.add(team)
        db.flush()

        qb = DBPlayer(
            player_id="intqb",
            name="IntQB",
            position="QB",
            nfl_team="DAL",
            team_id=team.id,
            stats={"age": 27},
        )
        db.add(qb)
        db.flush()

        # Create a second team to establish ranking context
        qb2 = DBPlayer(
            player_id="intqb2",
            name="IntQB2",
            position="QB",
            nfl_team="NYG",
            team_id=team.id,
            stats={"age": 25},
        )
        db.add(qb2)
        db.flush()

        # Season stats for both
        db.add(
            DBPlayerSeasonStats(
                player_id=qb.id,
                year=2024,
                games_played=16,
                pass_att=500,
                fantasy_points_total=350.0,
                fantasy_points_avg=21.875,
            )
        )
        db.add(
            DBPlayerSeasonStats(
                player_id=qb2.id,
                year=2024,
                games_played=16,
                pass_att=450,
                fantasy_points_total=200.0,
                fantasy_points_avg=12.5,
            )
        )

        # Game logs — DAL QB scores more than NYG QB
        for wk in range(1, 9):
            db.add(
                DBPlayerGameLog(
                    player_id=qb.id,
                    year=2024,
                    week=wk,
                    opponent="PHI" if wk <= 4 else "WAS",
                    pass_yd=300,
                    pass_td=3,
                    rush_yd=20,
                    fantasy_points=28.0,
                )
            )
            db.add(
                DBPlayerGameLog(
                    player_id=qb2.id,
                    year=2024,
                    week=wk,
                    opponent="PHI" if wk <= 4 else "WAS",
                    pass_yd=200,
                    pass_td=1,
                    rush_yd=10,
                    fantasy_points=14.0,
                )
            )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(qb.id, week=7, year=2024)

        # DAL has more yards/points than NYG → team_offense_level > 50
        assert criteria.team_offense_level > 50.0

    def test_weekly_opponent_defense_from_computed(self, db):
        """opponent_defense_level and def_rank should use computed rankings."""
        team = DBTeam(team_id="tint2", name="IntTeam2", owner="O")
        db.add(team)
        db.flush()

        qb = DBPlayer(
            player_id="intqb3",
            name="QB3",
            position="QB",
            nfl_team="MIA",
            team_id=team.id,
            stats={"age": 26},
        )
        db.add(qb)
        db.flush()

        db.add(
            DBPlayerSeasonStats(
                player_id=qb.id,
                year=2024,
                games_played=16,
                pass_att=500,
                fantasy_points_total=300.0,
                fantasy_points_avg=18.75,
            )
        )

        # Game logs vs multiple opponents with different results
        opponents = ["BUF", "NYJ", "NE"]
        fpts = [30.0, 15.0, 22.0]
        for wk, (opp, fp) in enumerate(zip(opponents, fpts), 1):
            db.add(
                DBPlayerGameLog(
                    player_id=qb.id,
                    year=2024,
                    week=wk,
                    opponent=opp,
                    pass_yd=250,
                    pass_td=2,
                    rush_yd=20,
                    fantasy_points=fp,
                )
            )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        # Week 2 opponent is NYJ (allowed 15.0 fpts — best defense)
        criteria = builder.build_weekly_criteria(qb.id, week=2, year=2024)

        # NYJ allowed least fpts → rank 1 → opponent_defense_level near 0
        assert criteria.opposing_defense_vs_position_rank == 1
        assert criteria.opponent_defense_level == pytest.approx(0.0, abs=1.0)

    def test_momentum_works_with_computed_weekly_stats(self, db):
        """Offensive momentum should work when weekly team stats are computed."""
        team = DBTeam(team_id="tint3", name="IntTeam3", owner="O")
        db.add(team)
        db.flush()

        qb = DBPlayer(
            player_id="intqb4",
            name="QB4",
            position="QB",
            nfl_team="HOU",
            team_id=team.id,
            stats={"age": 24},
        )
        db.add(qb)
        db.flush()

        db.add(
            DBPlayerSeasonStats(
                player_id=qb.id,
                year=2024,
                games_played=8,
                pass_att=250,
                fantasy_points_total=160.0,
                fantasy_points_avg=20.0,
            )
        )

        # First 4 weeks: low scoring; Last 4 weeks: high scoring
        for wk in range(1, 9):
            fpts = 10.0 if wk <= 4 else 30.0
            yards = 150 if wk <= 4 else 350
            db.add(
                DBPlayerGameLog(
                    player_id=qb.id,
                    year=2024,
                    week=wk,
                    opponent=f"T{wk}",
                    pass_yd=yards,
                    pass_td=2 if wk <= 4 else 4,
                    rush_yd=20,
                    fantasy_points=fpts,
                )
            )
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(qb.id, week=8, year=2024)

        # Recent weeks have higher scoring → positive momentum
        assert criteria.offensive_momentum_score > 0


class TestNoLookAheadLeakage:
    """A projection must not move when the future changes."""

    def test_week_8_projection_ignores_later_weeks(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        before = builder.build_weekly_criteria(sample_data.id, week=8, year=2024)

        # Insert weeks 9-18 with wildly different production. `sample_data`
        # already has logs for weeks 9-16 (from its `range(1, 17)` setup), so
        # those are overwritten in place rather than re-inserted — the model
        # enforces a (player_id, year, week) unique constraint. The brief's
        # `receptions=`/`receiving_yards=` kwargs also don't exist on
        # DBPlayerGameLog; the real columns are `rec`/`rec_yd`.
        for wk in range(9, 19):
            existing = (
                db.query(DBPlayerGameLog)
                .filter_by(player_id=sample_data.id, year=2024, week=wk)
                .first()
            )
            if existing is not None:
                existing.fantasy_points = 99.0
                existing.rec = 20
                existing.rec_yd = 400
            else:
                db.add(
                    DBPlayerGameLog(
                        player_id=sample_data.id,
                        year=2024,
                        week=wk,
                        fantasy_points=99.0,
                        rec=20,
                        rec_yd=400,
                    )
                )
        db.commit()

        after = ProjectionCriteriaBuilder(db).build_weekly_criteria(
            sample_data.id,
            week=8,
            year=2024,
        )
        assert after.historical_average_points == before.historical_average_points
        assert after.player_skill_level == before.player_skill_level
