"""platform operations: outbound webhooks (M5.4)

`webhook_endpoints` (org-scoped subscriptions: https-only SSRF-guarded url,
Fernet-encrypted secret, JSONB event mask) and `webhook_deliveries` (one row
per event x endpoint: payload, pending|delivered|failed status, attempt count).
Composite index on (endpoint_id, created_at) backs the recent-deliveries view.

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '0018'
down_revision: Union[str, None] = '0017'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'webhook_endpoints',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), nullable=False),
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('secret_enc', sa.Text(), nullable=False),
        sa.Column('event_mask', JSONB(), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_webhook_endpoints_org_id'), 'webhook_endpoints', ['org_id'], unique=False)

    op.create_table(
        'webhook_deliveries',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), nullable=False),
        sa.Column('endpoint_id', UUID(as_uuid=True), nullable=False),
        sa.Column('event_type', sa.Text(), nullable=False),
        sa.Column('payload', JSONB(), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('attempts', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['endpoint_id'], ['webhook_endpoints.id']),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_webhook_deliveries_org_id'), 'webhook_deliveries', ['org_id'], unique=False)
    op.create_index(
        'ix_webhook_deliveries_endpoint_created',
        'webhook_deliveries',
        ['endpoint_id', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_webhook_deliveries_endpoint_created', table_name='webhook_deliveries')
    op.drop_index(op.f('ix_webhook_deliveries_org_id'), table_name='webhook_deliveries')
    op.drop_table('webhook_deliveries')
    op.drop_index(op.f('ix_webhook_endpoints_org_id'), table_name='webhook_endpoints')
    op.drop_table('webhook_endpoints')
