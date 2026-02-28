"""Add sportsbook_odds table

Revision ID: a1b2c3d4e5f6
Revises: 32173dd2ad32
Create Date: 2026-02-28 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '32173dd2ad32'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create sportsbook_odds table for storing betting line data."""
    op.create_table(
        'sportsbook_odds',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('event_id', sa.String(), nullable=False, index=True),
        sa.Column('sport', sa.String(), nullable=False, server_default='americanfootball_nfl'),
        sa.Column('home_team', sa.String(), nullable=False),
        sa.Column('away_team', sa.String(), nullable=False),
        sa.Column('commence_time', sa.DateTime(), nullable=True),
        sa.Column('market', sa.String(), nullable=False),
        sa.Column('bookmaker', sa.String(), nullable=False),
        sa.Column('outcome_name', sa.String(), nullable=True),
        sa.Column('price', sa.Integer(), nullable=True),
        sa.Column('point', sa.Float(), nullable=True),
        sa.Column('player_name', sa.String(), nullable=True, index=True),
        sa.Column('source', sa.String(), server_default='the_odds_api'),
        sa.Column('updated_at', sa.DateTime()),
    )


def downgrade() -> None:
    """Drop sportsbook_odds table."""
    op.drop_table('sportsbook_odds')
