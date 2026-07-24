"""meetings + campaign scheduling links (M4.3, G8)

meetings: booked-meeting outcomes learned from signed calendar webhooks;
UNIQUE(provider_event_id) makes redelivery an update. campaigns gain
scheduling_url / info_doc_url — static links embedded in reply drafts (campaign
config, never LLM output).

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = '0013'
down_revision: Union[str, None] = '0012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'meetings',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('enrollment_id', UUID(as_uuid=True), sa.ForeignKey('enrollments.id'), nullable=True),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('provider_event_id', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('start_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('booked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('provider_event_id', name='uq_meeting_provider_event'),
    )
    op.create_index('ix_meetings_enrollment_id', 'meetings', ['enrollment_id'])
    op.add_column('campaigns', sa.Column('scheduling_url', sa.Text(), nullable=True))
    op.add_column('campaigns', sa.Column('info_doc_url', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('campaigns', 'info_doc_url')
    op.drop_column('campaigns', 'scheduling_url')
    op.drop_index('ix_meetings_enrollment_id', table_name='meetings')
    op.drop_table('meetings')
