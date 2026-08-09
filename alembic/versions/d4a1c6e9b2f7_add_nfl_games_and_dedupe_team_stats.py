"""Add nfl_games schedule table and de-duplicate nfl_team_stats season rows

The season rows on ``nfl_team_stats`` use ``week IS NULL``, and SQL treats NULL
as distinct from NULL — so ``uq_nfl_team_year_week`` never constrained them and
repeated imports appended a new row each time. This collapses the duplicates
(keeping the most recently updated row per team/year) and adds a partial unique
index that actually holds.

Revision ID: d4a1c6e9b2f7
Revises: c9f4a2b7d3e5
Create Date: 2026-08-06 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd4a1c6e9b2f7'
down_revision: Union[str, Sequence[str], None] = 'c9f4a2b7d3e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'nfl_games',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('game_id', sa.String(), nullable=True),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('week', sa.Integer(), nullable=False),
        sa.Column('game_type', sa.String(), nullable=True),
        sa.Column('home_team', sa.String(), nullable=False),
        sa.Column('away_team', sa.String(), nullable=False),
        sa.Column('home_score', sa.Integer(), nullable=True),
        sa.Column('away_score', sa.Integer(), nullable=True),
        sa.Column('kickoff_at', sa.DateTime(), nullable=True),
        sa.Column('roof', sa.String(), nullable=True),
        sa.Column('surface', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'year', 'week', 'home_team', 'away_team', name='uq_nfl_game',
        ),
    )
    op.create_index('ix_nfl_games_game_id', 'nfl_games', ['game_id'])
    op.create_index('ix_nfl_games_year', 'nfl_games', ['year'])
    op.create_index('ix_nfl_games_home_team', 'nfl_games', ['home_team'])
    op.create_index('ix_nfl_games_away_team', 'nfl_games', ['away_team'])

    # Canonicalize team abbreviations before de-duplicating, or the same
    # franchise survives twice under two spellings. nflverse writes LA for the
    # Rams and ESPN writes WSH for Washington; the game-log aggregation path
    # also stored the literal string 'None'.
    op.execute(
        "DELETE FROM nfl_team_stats WHERE nfl_team IS NULL "
        "OR nfl_team IN ('None', 'FA', '')"
    )
    for stored, canonical in (
        ('LA', 'LAR'), ('WSH', 'WAS'), ('GNB', 'GB'), ('KAN', 'KC'),
        ('SFO', 'SF'), ('TAM', 'TB'), ('NOR', 'NO'), ('NWE', 'NE'),
        ('LVR', 'LV'), ('JAC', 'JAX'),
    ):
        # Drop the alias row wherever the canonical spelling already covers
        # that (year, week); renaming it would just collide. Then rename the
        # rest, which now have no twin.
        op.execute(
            f"""
            DELETE FROM nfl_team_stats
            WHERE nfl_team = '{stored}'
              AND EXISTS (
                  SELECT 1 FROM nfl_team_stats c
                  WHERE c.nfl_team = '{canonical}'
                    AND c.year = nfl_team_stats.year
                    AND (c.week = nfl_team_stats.week
                         OR (c.week IS NULL AND nfl_team_stats.week IS NULL))
              )
            """
        )
        op.execute(
            f"UPDATE nfl_team_stats SET nfl_team = '{canonical}' "
            f"WHERE nfl_team = '{stored}'"
        )

    # Collapse duplicate season rows, keeping the highest id per (team, year).
    # Ordering by id rather than updated_at because updated_at is NULL on some
    # historical rows, and NULL sorts unpredictably across backends.
    op.execute(
        """
        DELETE FROM nfl_team_stats
        WHERE week IS NULL
          AND id NOT IN (
              SELECT MAX(id) FROM nfl_team_stats
              WHERE week IS NULL
              GROUP BY nfl_team, year
          )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_nfl_team_season
        ON nfl_team_stats (nfl_team, year)
        WHERE week IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_nfl_team_season")
    op.drop_index('ix_nfl_games_away_team', table_name='nfl_games')
    op.drop_index('ix_nfl_games_home_team', table_name='nfl_games')
    op.drop_index('ix_nfl_games_year', table_name='nfl_games')
    op.drop_index('ix_nfl_games_game_id', table_name='nfl_games')
    op.drop_table('nfl_games')
