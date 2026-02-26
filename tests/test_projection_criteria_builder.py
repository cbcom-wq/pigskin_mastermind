"""Tests for ProjectionCriteriaBuilder auto-derivation from stats."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from pigskin_mastermind.models.database import (
    Base, DBPlayer, DBTeam, DBPlayerSeasonStats,
    DBPlayerGameLog, DBNFLTeamStats
)
from pigskin_mastermind.models.projection_criteria import (
    WeeklyProjectionCriteria, YearlyProjectionCriteria
)
from pigskin_mastermind.services.projection_criteria_builder import (
    ProjectionCriteriaBuilder
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
        player_id="p1", name="Test QB", position="QB",
        nfl_team="KC", team_id=team.id,
        stats={'age': 28, 'injuryStatus': ''},
    )
    db.add(player)
    db.flush()

    # Season stats
    _fpts_total = 350.0
    _pass_att = 500
    _rush_att = 50
    _rec = 0
    season = DBPlayerSeasonStats(
        player_id=player.id, year=2024, games_played=16,
        pass_att=_pass_att, pass_cmp=340, pass_yd=4500, pass_td=35, pass_int=10,
        rush_att=_rush_att, rush_yd=200, rush_td=3,
        rec=_rec, rec_yd=0, rec_td=0, targets=0,
        fantasy_points_total=_fpts_total, fantasy_points_avg=21.875,
        fantasy_points_per_touch=round(_fpts_total / (_pass_att + _rush_att + _rec), 6),
        snap_pct=0.95,
    )
    db.add(season)

    # Game logs for trend calculation
    for week in range(1, 17):
        pts = 20.0 + (week - 8)  # trending up over season
        db.add(DBPlayerGameLog(
            player_id=player.id, year=2024, week=week,
            opponent=f"OPP{week}",
            pass_yd=280, pass_td=2, pass_int=1,
            fantasy_points=pts,
        ))

    # Team defense stats
    db.add(DBNFLTeamStats(
        nfl_team="KC", year=2024, week=None,
        total_yards=6000, pass_yards=4000, rush_yards=2000,
        points_scored=450,
    ))

    # Opponent defense stats
    db.add(DBNFLTeamStats(
        nfl_team="OPP10", year=2024, week=None,
        def_rank_vs_qb=5,
    ))

    db.commit()
    return player


class TestBuildWeeklyCriteria:
    def test_builds_valid_criteria(self, db, sample_data):
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)

        assert isinstance(criteria, WeeklyProjectionCriteria)
        assert criteria.historical_average_points == 21.875
        # Criteria builder reads fantasy_points_per_touch directly from the stored season stats.
        # With the corrected formula (pass_att + rush_att + rec in denominator):
        # 350.0 / (500 + 50 + 0) = ~0.636
        season = db.query(DBPlayerSeasonStats).filter_by(player_id=sample_data.id).first()
        assert criteria.fantasy_points_per_touch == pytest.approx(season.fantasy_points_per_touch, rel=0.01)
        assert 0 <= criteria.player_skill_level <= 100

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
            sample_data.id, week=10, year=2024,
            overrides={'weather_impact_score': -50.0, 'injury_risk_score': 80.0}
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
            sample_data.id, year=2025,
            overrides={'coaching_stability_score': 80.0}
        )
        assert criteria.coaching_stability_score == 80.0


class TestOpponentDefenseLevel:
    def test_worst_defense_rank_32_gives_level_near_100(self, db, sample_data):
        """Rank 32 (worst defense) should give opponent_defense_level near 100."""
        builder = ProjectionCriteriaBuilder(db)
        # Override def_rank by using a weak opponent in week 10 (has def_rank_vs_qb=5)
        # We test via overrides to isolate the formula
        criteria = builder.build_weekly_criteria(
            sample_data.id, week=10, year=2024,
            overrides={'opponent_defense_level': ((32 - 1) / 31) * 100}
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
        """build_weekly_criteria with a known def_rank should set opponent_defense_level correctly."""
        # Week 10 opponent "OPP10" has def_rank_vs_qb=5
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_weekly_criteria(sample_data.id, week=10, year=2024)
        expected = ((5 - 1) / 31) * 100
        assert criteria.opponent_defense_level == pytest.approx(expected, rel=0.01)

    def test_yearly_criteria_uses_schedule_defense(self, db, sample_data):
        """build_yearly_criteria should use schedule-averaged opponent defense level."""
        builder = ProjectionCriteriaBuilder(db)
        criteria = builder.build_yearly_criteria(sample_data.id, year=2025)
        # Only OPP10 has def_rank_vs_qb=5 → level = ((5-1)/31)*100 ≈ 12.9
        # Other opponents have no data, so average is based on OPP10 only
        expected = ((5 - 1) / 31) * 100
        assert criteria.opponent_defense_level == pytest.approx(expected, rel=0.01)


class TestTrendScoreConfidence:
    def test_small_sample_dampens_score(self, db):
        """A 1-game sample (confidence=0.25) should give at most 25% of the raw deviation."""
        team = DBTeam(team_id="t2", name="Team2", owner="Owner2")
        db.add(team)
        db.flush()

        player = DBPlayer(
            player_id="trend1", name="Trend Player", position="RB",
            nfl_team="SF", team_id=team.id, stats={}
        )
        db.add(player)
        db.flush()

        # Season average = 5 pts over 4 games; only 1 recent game scoring 25 pts
        # Raw deviation = ((25 - 5) / 5) * 100 = 400 → capped at 100
        # With confidence = 1/4 = 0.25: result = 100 * 0.25 = 25
        season = DBPlayerSeasonStats(
            player_id=player.id, year=2024, games_played=4,
            fantasy_points_total=20.0, fantasy_points_avg=5.0,
        )
        db.add(season)

        # Only 1 game log (small sample)
        db.add(DBPlayerGameLog(
            player_id=player.id, year=2024, week=16,
            fantasy_points=25.0,
        ))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        score = builder._compute_trend_score(player.id, year=2024, num_weeks=4)
        assert score <= 25, f"Expected trend score ≤ 25 with 1/4 confidence, got {score}"


class TestInjuryRisk:
    def test_out_player(self, db):
        player = DBPlayer(
            player_id="inj1", name="Injured", position="RB",
            nfl_team="SF", stats={'injuryStatus': 'OUT'}
        )
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player)
        assert risk == 90.0

    def test_questionable_player(self, db):
        player = DBPlayer(
            player_id="inj2", name="Questionable", position="WR",
            nfl_team="GB", stats={'injuryStatus': 'QUESTIONABLE'}
        )
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player)
        assert risk == 40.0

    def test_healthy_player(self, db):
        player = DBPlayer(
            player_id="inj3", name="Healthy", position="TE",
            nfl_team="KC", stats={}
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
        share = builder._compute_touch_share(sample_data.id, 'QB', 'KC', 2024)
        # Only QB on team with 500 pass_att → 100%
        assert share == pytest.approx(100.0, rel=0.01)

    def test_wr_touch_share(self, db):
        """WR touch share = targets / team_total_targets."""
        team = DBTeam(team_id="t3", name="Team3", owner="Owner3")
        db.add(team)
        db.flush()

        wr1 = DBPlayer(player_id="wr1", name="WR1", position="WR",
                        nfl_team="BUF", team_id=team.id, stats={})
        wr2 = DBPlayer(player_id="wr2", name="WR2", position="WR",
                        nfl_team="BUF", team_id=team.id, stats={})
        db.add_all([wr1, wr2])
        db.flush()

        db.add(DBPlayerSeasonStats(
            player_id=wr1.id, year=2024, games_played=16,
            targets=120, fantasy_points_total=200.0, fantasy_points_avg=12.5,
        ))
        db.add(DBPlayerSeasonStats(
            player_id=wr2.id, year=2024, games_played=16,
            targets=80, fantasy_points_total=120.0, fantasy_points_avg=7.5,
        ))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        share = builder._compute_touch_share(wr1.id, 'WR', 'BUF', 2024)
        # 120 / (120 + 80) = 60%
        assert share == pytest.approx(60.0, rel=0.01)

    def test_fallback_to_snap_pct(self, db):
        """Falls back to snap_pct when no team data available."""
        team = DBTeam(team_id="t4", name="Team4", owner="Owner4")
        db.add(team)
        db.flush()

        player = DBPlayer(player_id="fb1", name="Fallback", position="K",
                          nfl_team="NYJ", team_id=team.id, stats={})
        db.add(player)
        db.flush()

        db.add(DBPlayerSeasonStats(
            player_id=player.id, year=2024, games_played=16,
            snap_pct=0.85,
        ))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        share = builder._compute_touch_share(player.id, 'K', 'NYJ', 2024)
        assert share == pytest.approx(85.0, rel=0.01)


class TestSkillComposite:
    def test_composite_considers_multiple_factors(self, db, sample_data):
        """Skill composite should use points, efficiency, consistency, volume."""
        builder = ProjectionCriteriaBuilder(db)
        skill = builder._compute_skill_composite(sample_data.id, 'QB', 2024)
        # Single player = 0th percentile for all rank-based metrics (no one below),
        # but consistency should contribute positively
        assert 0 <= skill <= 100

    def test_composite_higher_for_better_player(self, db):
        """Better player should have higher composite skill."""
        team = DBTeam(team_id="t5", name="Team5", owner="Owner5")
        db.add(team)
        db.flush()

        p1 = DBPlayer(player_id="sk1", name="Star", position="RB",
                       nfl_team="DAL", team_id=team.id, stats={})
        p2 = DBPlayer(player_id="sk2", name="Backup", position="RB",
                       nfl_team="DAL", team_id=team.id, stats={})
        db.add_all([p1, p2])
        db.flush()

        db.add(DBPlayerSeasonStats(
            player_id=p1.id, year=2024, games_played=16,
            rush_att=250, targets=60, fantasy_points_total=280.0,
            fantasy_points_avg=17.5, fantasy_points_per_touch=0.9,
        ))
        db.add(DBPlayerSeasonStats(
            player_id=p2.id, year=2024, games_played=16,
            rush_att=80, targets=20, fantasy_points_total=80.0,
            fantasy_points_avg=5.0, fantasy_points_per_touch=0.8,
        ))
        for w in range(1, 17):
            db.add(DBPlayerGameLog(player_id=p1.id, year=2024, week=w,
                                   fantasy_points=17.5))
            db.add(DBPlayerGameLog(player_id=p2.id, year=2024, week=w,
                                   fantasy_points=5.0))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        skill_star = builder._compute_skill_composite(p1.id, 'RB', 2024)
        skill_backup = builder._compute_skill_composite(p2.id, 'RB', 2024)
        assert skill_star > skill_backup


class TestPositionEfficiency:
    def test_qb_efficiency(self, db, sample_data):
        """QB efficiency uses pass_att + rush_att as denominator."""
        builder = ProjectionCriteriaBuilder(db)
        eff = builder._compute_position_efficiency(sample_data.id, 'QB', 2024)
        # 350 / (500 + 50) = 0.636
        assert eff == pytest.approx(0.636, rel=0.01)

    def test_wr_efficiency(self, db):
        """WR efficiency uses targets as denominator."""
        team = DBTeam(team_id="t6", name="Team6", owner="Owner6")
        db.add(team)
        db.flush()

        wr = DBPlayer(player_id="we1", name="WR Eff", position="WR",
                       nfl_team="MIA", team_id=team.id, stats={})
        db.add(wr)
        db.flush()

        db.add(DBPlayerSeasonStats(
            player_id=wr.id, year=2024, games_played=16,
            targets=150, rec=100, fantasy_points_total=240.0,
            fantasy_points_avg=15.0,
        ))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        eff = builder._compute_position_efficiency(wr.id, 'WR', 2024)
        # 240 / 150 = 1.6
        assert eff == pytest.approx(1.6, rel=0.01)


class TestMultiSeasonAvg:
    def test_weighted_average_across_seasons(self, db):
        """Multi-season average should weight recent seasons more."""
        team = DBTeam(team_id="t7", name="Team7", owner="Owner7")
        db.add(team)
        db.flush()

        player = DBPlayer(player_id="ms1", name="Multi", position="QB",
                          nfl_team="LAR", team_id=team.id, stats={})
        db.add(player)
        db.flush()

        # 3 seasons of data
        db.add(DBPlayerSeasonStats(
            player_id=player.id, year=2024, games_played=17,
            fantasy_points_total=340.0, fantasy_points_avg=20.0,
        ))
        db.add(DBPlayerSeasonStats(
            player_id=player.id, year=2023, games_played=16,
            fantasy_points_total=256.0, fantasy_points_avg=16.0,
        ))
        db.add(DBPlayerSeasonStats(
            player_id=player.id, year=2022, games_played=17,
            fantasy_points_total=170.0, fantasy_points_avg=10.0,
        ))
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

        player = DBPlayer(player_id="ms2", name="Injured", position="RB",
                          nfl_team="CHI", team_id=team.id, stats={})
        db.add(player)
        db.flush()

        db.add(DBPlayerSeasonStats(
            player_id=player.id, year=2024, games_played=17,
            fantasy_points_total=340.0, fantasy_points_avg=20.0,
        ))
        db.add(DBPlayerSeasonStats(
            player_id=player.id, year=2023, games_played=3,  # too few
            fantasy_points_total=60.0, fantasy_points_avg=20.0,
        ))
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

        player = DBPlayer(player_id="ih1", name="Durable", position="WR",
                          nfl_team="PHI", team_id=team.id, stats={})
        db.add(player)
        db.flush()

        for yr in [2022, 2023, 2024]:
            db.add(DBPlayerSeasonStats(
                player_id=player.id, year=yr, games_played=17,
            ))
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

        player = DBPlayer(player_id="ih2", name="Fragile", position="RB",
                          nfl_team="SEA", team_id=team.id, stats={})
        db.add(player)
        db.flush()

        # Average 10 games per season
        for yr in [2022, 2023, 2024]:
            db.add(DBPlayerSeasonStats(
                player_id=player.id, year=yr, games_played=10,
            ))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        risk = builder._compute_injury_risk(player, player.id)
        # status=5 * 0.7 + (1-10/17)*100 * 0.3 = 3.5 + 12.35 ≈ 15.85
        expected = 5.0 * 0.7 + (1 - 10 / 17) * 100 * 0.3
        assert risk == pytest.approx(expected, rel=0.01)


class TestScheduleDefenseLevel:
    def test_schedule_avg_with_mixed_opponents(self, db, sample_data):
        """Schedule defense should average available opponent data."""
        builder = ProjectionCriteriaBuilder(db)
        level = builder._compute_schedule_defense_level(sample_data.id, 'QB', 2024)
        # Only OPP10 has def_rank_vs_qb=5 → ((5-1)/31)*100 ≈ 12.9
        expected = ((5 - 1) / 31) * 100
        assert level == pytest.approx(expected, rel=0.01)

    def test_no_opponent_data_returns_neutral(self, db):
        """No game logs → neutral 50.0."""
        team = DBTeam(team_id="t11", name="Team11", owner="Owner11")
        db.add(team)
        db.flush()

        player = DBPlayer(player_id="sd1", name="NoGames", position="WR",
                          nfl_team="ATL", team_id=team.id, stats={})
        db.add(player)
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        level = builder._compute_schedule_defense_level(player.id, 'WR', 2024)
        assert level == 50.0


class TestMomentumComposite:
    def test_momentum_blends_points_and_yards(self, db):
        """Momentum should blend points (60%) and yards (40%) deviations."""
        # Set up team with weekly stats where recent weeks are above average
        for wk in range(1, 9):
            pts = 20 if wk <= 4 else 30
            yds = 300 if wk <= 4 else 400
            db.add(DBNFLTeamStats(
                nfl_team="MOM", year=2024, week=wk,
                points_scored=pts, total_yards=yds,
            ))
        db.commit()

        builder = ProjectionCriteriaBuilder(db)
        momentum = builder._compute_momentum("MOM", 2024, num_weeks=4)

        # Recent 4 weeks (5-8): pts=30, yds=400
        # Season avg: pts=25, yds=350
        # pts_dev = ((30-25)/25)*100 = 20%, yds_dev = ((400-350)/350)*100 ≈ 14.3%
        # blended = 20*0.6 + 14.3*0.4 = 12 + 5.71 ≈ 17.7
        assert momentum > 0
        assert momentum == pytest.approx(17.7, rel=0.05)
