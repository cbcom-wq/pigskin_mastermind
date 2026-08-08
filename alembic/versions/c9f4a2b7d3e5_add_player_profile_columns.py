"""Add profile / bio columns to players

Revision ID: c9f4a2b7d3e5
Revises: b8e3f1a6c2d4
Create Date: 2026-07-31 00:00:01.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c9f4a2b7d3e5'
down_revision: Union[str, Sequence[str], None] = 'b8e3f1a6c2d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COLUMNS = (
    ('bye_week', sa.Integer()),
    ('injury_status', sa.String()),
    ('injured', sa.Boolean()),
    ('jersey', sa.String()),
    ('age', sa.Integer()),
    ('height', sa.String()),
    ('weight', sa.Integer()),
    ('college', sa.String()),
    ('years_exp', sa.Integer()),
    ('draft_number', sa.Integer()),
    ('pos_rank', sa.Integer()),
    ('percent_owned', sa.Float()),
    ('percent_started', sa.Float()),
    ('profile_updated_at', sa.DateTime()),
)


def upgrade() -> None:
    """Add bio / status columns previously dropped on the floor by the importers."""
    for name, type_ in _COLUMNS:
        op.add_column('players', sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    """Remove bio / status columns."""
    for name, _ in reversed(_COLUMNS):
        op.drop_column('players', name)
