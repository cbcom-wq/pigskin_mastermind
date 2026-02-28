"""merge heads

Revision ID: 984f22746aca
Revises: 32173dd2ad32, a1b2c3d4e5f6
Create Date: 2026-02-28 11:10:04.191673

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '984f22746aca'
down_revision: Union[str, Sequence[str], None] = ('32173dd2ad32', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
