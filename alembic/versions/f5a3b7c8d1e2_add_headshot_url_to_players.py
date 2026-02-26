"""Add headshot_url to players table

Revision ID: f5a3b7c8d1e2
Revises: c4f1a3b2e9d0
Create Date: 2026-02-24 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f5a3b7c8d1e2'
down_revision: Union[str, Sequence[str], None] = 'c4f1a3b2e9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add headshot_url column to players table."""
    op.add_column('players', sa.Column('headshot_url', sa.String(), nullable=True))


def downgrade() -> None:
    """Remove headshot_url column from players table."""
    op.drop_column('players', 'headshot_url')
