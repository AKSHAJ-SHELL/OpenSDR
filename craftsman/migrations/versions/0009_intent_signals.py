"""intent signals engine (M2.3)

signals (company-level intent observations), signal_rules (per-campaign boost/enroll/
notify policy), collector_state (per-company diff fingerprints), and leads.icp_signal
(the third score-provenance component; NULL = 2-way blend, value = 3-way blend). No FK
cascades (M0.4 doctrine). Signals are company-level, not person PII.

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '0009'
down_revision: Union[str, None] = '0008'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('icp_signal', sa.Float(), nullable=True))

    op.create_table(
        'signals',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('company_id', UUID(as_uuid=True), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('type', sa.Text(), nullable=False),
        sa.Column('payload', JSONB(), nullable=True),
        sa.Column('observed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('source', sa.Text(), nullable=True),
    )
    op.create_index('ix_signals_company_type_observed', 'signals', ['company_id', 'type', 'observed_at'])

    op.create_table(
        'signal_rules',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('campaign_id', UUID(as_uuid=True), sa.ForeignKey('campaigns.id'), nullable=False),
        sa.Column('signal_type', sa.Text(), nullable=False),
        sa.Column('action', sa.Text(), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.UniqueConstraint('campaign_id', 'signal_type', 'action', name='uq_signal_rule'),
    )

    op.create_table(
        'collector_state',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('company_id', UUID(as_uuid=True), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('collector', sa.Text(), nullable=False),
        sa.Column('fingerprint', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.UniqueConstraint('company_id', 'collector', name='uq_collector_state'),
    )


def downgrade() -> None:
    op.drop_table('collector_state')
    op.drop_table('signal_rules')
    op.drop_index('ix_signals_company_type_observed', table_name='signals')
    op.drop_table('signals')
    op.drop_column('leads', 'icp_signal')
