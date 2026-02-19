"""Add adp columns to player_season_stats

Revision ID: c4f1a3b2e9d0
Revises: ea608024ec07
Create Date: 2026-02-19 20:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4f1a3b2e9d0'
down_revision: Union[str, Sequence[str], None] = 'ea608024ec07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add adp and adp_source columns to player_season_stats."""
    op.add_column('player_season_stats', sa.Column('adp', sa.Float(), nullable=True))
    op.add_column('player_season_stats', sa.Column('adp_source', sa.String(), nullable=True))


def downgrade() -> None:
    """Remove adp and adp_source columns from player_season_stats."""
    op.drop_column('player_season_stats', 'adp_source')
    op.drop_column('player_season_stats', 'adp')
