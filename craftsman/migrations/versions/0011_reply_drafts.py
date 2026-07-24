"""reply drafts (M4.1 Copilot)

reply_drafts: one validated, skeleton-rendered draft per inbound reply, waiting for a
human decision. UNIQUE(inbound_message_id) is the generation idempotency claim. Rows
hold lead-personalized content quoting prospect text (person PII) — they join the
erase cascade; no FK cascade per M0.4 doctrine.

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '0011'
down_revision: Union[str, None] = '0010'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'reply_drafts',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('inbound_message_id', UUID(as_uuid=True), sa.ForeignKey('messages.id'), nullable=False),
        sa.Column('enrollment_id', UUID(as_uuid=True), sa.ForeignKey('enrollments.id'), nullable=True),
        sa.Column('skeleton_key', sa.Text(), nullable=True),
        sa.Column('slots', JSONB(), nullable=True),
        sa.Column('body', sa.Text(), nullable=True),
        sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column('auto_sent', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('detail', JSONB(), nullable=True),
        sa.Column('sent_message_id', UUID(as_uuid=True), sa.ForeignKey('messages.id'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('inbound_message_id', name='uq_reply_draft_inbound'),
    )
    op.create_index('ix_reply_drafts_enrollment_id', 'reply_drafts', ['enrollment_id'])


def downgrade() -> None:
    op.drop_index('ix_reply_drafts_enrollment_id', table_name='reply_drafts')
    op.drop_table('reply_drafts')
