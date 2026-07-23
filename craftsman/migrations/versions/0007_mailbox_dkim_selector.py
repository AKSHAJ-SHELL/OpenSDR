"""mailbox dkim selector (M1.4)

Optional DKIM selector per mailbox. When set it's the authoritative selector for the
deliverability DKIM check; otherwise the checker probes a list of common selectors.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('mailboxes', sa.Column('dkim_selector', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('mailboxes', 'dkim_selector')
