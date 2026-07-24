"""guarded autopilot flag (M4.4, ⛔ Gate M4 Option B)

campaigns.autopilot_enabled — opt-in, default false. Enable is an admin-scoped API
call (deliberate friction); disable is the operate-scoped kill switch. The README
guarantee amendment ships in the same commit as this flag.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0014'
down_revision: Union[str, None] = '0013'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'campaigns',
        sa.Column('autopilot_enabled', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )


def downgrade() -> None:
    op.drop_column('campaigns', 'autopilot_enabled')
