"""escalation rules (M4.2, G9)

escalation_rules: per-campaign (or global, campaign_id NULL) rules deciding when a
human is pulled in — match JSONB (classifications/confidence bounds/keywords, AND-ed)
→ actions JSONB (notify/urgent_notify/suppress/review_queue/block_draft/
block_autopilot). DB rules add to the built-in defaults; the legal-threat tripwire
lives in code and cannot be disabled from data.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '0012'
down_revision: Union[str, None] = '0011'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'escalation_rules',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('campaign_id', UUID(as_uuid=True), sa.ForeignKey('campaigns.id'), nullable=True),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('priority', sa.Integer(), nullable=False, server_default=sa.text('100')),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('match', JSONB(), nullable=False),
        sa.Column('actions', JSONB(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    )
    op.create_index('ix_escalation_rules_campaign_id', 'escalation_rules', ['campaign_id'])


def downgrade() -> None:
    op.drop_index('ix_escalation_rules_campaign_id', table_name='escalation_rules')
    op.drop_table('escalation_rules')
