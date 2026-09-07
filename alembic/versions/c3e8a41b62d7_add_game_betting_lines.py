"""add game betting lines

Revision ID: c3e8a41b62d7
Revises: b7d1c93e50fa
Create Date: 2026-09-07 11:02:47.913204

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3e8a41b62d7'
down_revision: Union[str, Sequence[str], None] = 'b7d1c93e50fa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('nfl_games', sa.Column('spread_line', sa.Float(), nullable=True))
    op.add_column('nfl_games', sa.Column('total_line', sa.Float(), nullable=True))
    op.add_column('nfl_games', sa.Column('home_moneyline', sa.Float(), nullable=True))
    op.add_column('nfl_games', sa.Column('away_moneyline', sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('nfl_games', 'away_moneyline')
    op.drop_column('nfl_games', 'home_moneyline')
    op.drop_column('nfl_games', 'total_line')
    op.drop_column('nfl_games', 'spread_line')
