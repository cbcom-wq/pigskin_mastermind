"""Add adp variance columns to player_season_stats

Revision ID: a7d2c9e4f8b1
Revises: e1f2a3b4c5d6
Create Date: 2026-07-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7d2c9e4f8b1'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add FFC draft-variance columns to player_season_stats."""
    op.add_column('player_season_stats', sa.Column('adp_stdev', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('adp_high', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('adp_low', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('adp_times_drafted', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove FFC draft-variance columns from player_season_stats."""
    op.drop_column('player_season_stats', 'adp_times_drafted')
    op.drop_column('player_season_stats', 'adp_low')
    op.drop_column('player_season_stats', 'adp_high')
    op.drop_column('player_season_stats', 'adp_stdev')
