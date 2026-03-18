"""add_roster_slots_to_leagues

Revision ID: e1f2a3b4c5d6
Revises: b2c3d4e5f6a7
Create Date: 2026-03-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add roster_slots JSON column to leagues table."""
    op.add_column('leagues', sa.Column('roster_slots', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Remove roster_slots column from leagues table."""
    op.drop_column('leagues', 'roster_slots')
