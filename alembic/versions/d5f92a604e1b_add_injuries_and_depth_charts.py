"""add player injuries and depth chart standing

Revision ID: d5f92a604e1b
Revises: c3e8a41b62d7
Create Date: 2026-09-07 15:20:11.447301

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f92a604e1b'
down_revision: Union[str, Sequence[str], None] = 'c3e8a41b62d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'player_injuries',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('player_id', sa.Integer(), nullable=False),
        sa.Column('year', sa.Integer(), nullable=False),
        sa.Column('week', sa.Integer(), nullable=False),
        sa.Column('report_status', sa.String(), nullable=True),
        sa.Column('practice_status', sa.String(), nullable=True),
        sa.Column('primary_injury', sa.String(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['player_id'], ['players.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('player_id', 'year', 'week', name='uq_player_injury'),
    )
    op.create_index(
        op.f('ix_player_injuries_player_id'),
        'player_injuries', ['player_id'], unique=False,
    )
    op.create_index(
        op.f('ix_player_injuries_year'),
        'player_injuries', ['year'], unique=False,
    )

    op.add_column('players', sa.Column('depth_chart_rank', sa.Integer(), nullable=True))
    op.add_column('players', sa.Column('depth_chart_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('players', 'depth_chart_at')
    op.drop_column('players', 'depth_chart_rank')
    op.drop_index(op.f('ix_player_injuries_year'), table_name='player_injuries')
    op.drop_index(op.f('ix_player_injuries_player_id'), table_name='player_injuries')
    op.drop_table('player_injuries')
