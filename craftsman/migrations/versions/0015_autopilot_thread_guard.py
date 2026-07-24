"""structural ≤1-auto-reply-per-thread guard (F-05, findings/12)

Partial unique index on reply_drafts(enrollment_id) WHERE auto_sent: the auto
dispatch path now stamps auto_sent at its pending→sending claim (before any
SMTP I/O), so two workers that both read a zero prior-auto-reply count can
never both send — the second claim trips this index. If this migration fails
on existing data, that is a real invariant violation and must be investigated,
not worked around.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0015'
down_revision: Union[str, None] = '0014'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'uq_auto_reply_per_thread',
        'reply_drafts',
        ['enrollment_id'],
        unique=True,
        postgresql_where=sa.text('auto_sent AND enrollment_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index('uq_auto_reply_per_thread', table_name='reply_drafts')
