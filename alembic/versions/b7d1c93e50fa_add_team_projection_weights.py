"""add team projection weights

Revision ID: b7d1c93e50fa
Revises: a9c4e2b7d310
Create Date: 2026-09-07 09:41:18.204553

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7d1c93e50fa'
down_revision: Union[str, Sequence[str], None] = 'a9c4e2b7d310'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'teams',
        sa.Column('projection_weights', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('teams', 'projection_weights')
