"""Add cross-source identity columns to players

Revision ID: b8e3f1a6c2d4
Revises: a7d2c9e4f8b1
Create Date: 2026-07-31 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b8e3f1a6c2d4'
down_revision: Union[str, Sequence[str], None] = 'a7d2c9e4f8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add per-source player IDs so ESPN / nfl_data_py / FFC rows can be joined."""
    op.add_column('players', sa.Column('espn_id', sa.String(), nullable=True))
    op.add_column('players', sa.Column('gsis_id', sa.String(), nullable=True))
    op.add_column('players', sa.Column('pfr_id', sa.String(), nullable=True))
    op.create_index('ix_players_espn_id', 'players', ['espn_id'])
    op.create_index('ix_players_gsis_id', 'players', ['gsis_id'])
    op.create_index('ix_players_pfr_id', 'players', ['pfr_id'])

    # Backfill from the existing prefixed player_id values.
    op.execute(
        "UPDATE players SET espn_id = substr(player_id, 6) "
        "WHERE player_id LIKE 'espn\\_%' ESCAPE '\\'"
    )
    op.execute(
        "UPDATE players SET gsis_id = substr(player_id, 5) "
        "WHERE player_id LIKE 'nfl\\_%' ESCAPE '\\'"
    )


def downgrade() -> None:
    """Remove per-source player ID columns."""
    op.drop_index('ix_players_pfr_id', table_name='players')
    op.drop_index('ix_players_gsis_id', table_name='players')
    op.drop_index('ix_players_espn_id', table_name='players')
    op.drop_column('players', 'pfr_id')
    op.drop_column('players', 'gsis_id')
    op.drop_column('players', 'espn_id')
