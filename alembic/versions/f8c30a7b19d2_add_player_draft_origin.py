"""add player draft origin

Revision ID: f8c30a7b19d2
Revises: e2b71d449c8a
Create Date: 2026-09-07 19:08:33.204119

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8c30a7b19d2'
down_revision: Union[str, Sequence[str], None] = 'e2b71d449c8a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('players', sa.Column('rookie_season', sa.Integer(), nullable=True))
    op.add_column('players', sa.Column('draft_round', sa.Integer(), nullable=True))
    op.add_column('players', sa.Column('draft_pick', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('players', 'draft_pick')
    op.drop_column('players', 'draft_round')
    op.drop_column('players', 'rookie_season')
