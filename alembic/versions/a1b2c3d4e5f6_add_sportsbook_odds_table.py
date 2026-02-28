"""Add sportsbook_odds table

Revision ID: a1b2c3d4e5f6
Revises: c4f1a3b2e9d0
Create Date: 2026-02-28 02:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'c4f1a3b2e9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create sportsbook_odds table for storing betting lines and player props."""
    op.create_table(
        'sportsbook_odds',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.String(), nullable=False),
        sa.Column('sport_key', sa.String(), nullable=False),
        sa.Column('sport_title', sa.String(), nullable=True),
        sa.Column('commence_time', sa.DateTime(), nullable=True),
        sa.Column('home_team', sa.String(), nullable=False),
        sa.Column('away_team', sa.String(), nullable=False),
        sa.Column('bookmaker', sa.String(), nullable=False),
        sa.Column('market', sa.String(), nullable=False),
        sa.Column('outcome_name', sa.String(), nullable=False),
        sa.Column('price', sa.Float(), nullable=True),
        sa.Column('point', sa.Float(), nullable=True),
        sa.Column('description', sa.String(), nullable=True),
        sa.Column('fetched_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'event_id', 'bookmaker', 'market', 'outcome_name',
            name='uq_odds_event_bookmaker_market_outcome',
        ),
    )
    op.create_index('ix_sportsbook_odds_event_id', 'sportsbook_odds', ['event_id'])
    op.create_index('ix_sportsbook_odds_market', 'sportsbook_odds', ['market'])


def downgrade() -> None:
    """Drop sportsbook_odds table."""
    op.drop_index('ix_sportsbook_odds_market', table_name='sportsbook_odds')
    op.drop_index('ix_sportsbook_odds_event_id', table_name='sportsbook_odds')
    op.drop_table('sportsbook_odds')
