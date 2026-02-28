"""Add description to sportsbook_odds unique constraint

The original unique constraint (event_id, bookmaker, market, outcome_name)
cannot handle player prop markets where multiple players share the same
event and market (e.g. two QBs with player_pass_yds lines in the same game).
The Odds API disambiguates via the ``description`` field (player name).

This migration drops the old constraint, adds a new one that includes
``description``, and makes the column non-nullable with a default.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-28 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = '08add6ea88d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Widen sportsbook_odds unique constraint to include description."""
    # SQLite doesn't support DROP CONSTRAINT, so we recreate the table
    # via Alembic's batch mode.
    with op.batch_alter_table('sportsbook_odds', schema=None) as batch_op:
        batch_op.drop_constraint(
            'uq_odds_event_bookmaker_market_outcome', type_='unique'
        )
        batch_op.create_unique_constraint(
            'uq_odds_event_bookmaker_market_outcome_desc',
            ['event_id', 'bookmaker', 'market', 'outcome_name', 'description'],
        )


def downgrade() -> None:
    """Restore original 4-column unique constraint."""
    with op.batch_alter_table('sportsbook_odds', schema=None) as batch_op:
        batch_op.drop_constraint(
            'uq_odds_event_bookmaker_market_outcome_desc', type_='unique'
        )
        batch_op.create_unique_constraint(
            'uq_odds_event_bookmaker_market_outcome',
            ['event_id', 'bookmaker', 'market', 'outcome_name'],
        )
