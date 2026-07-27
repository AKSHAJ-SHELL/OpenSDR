"""CRM sync (M5.2, G10)

`crm_connections` (org-scoped: provider, Fernet-encrypted credential blob,
JSONB field-map overlay, outbound watermark), `crm_links` (lead ↔ remote
contact identity; unique both ways per connection so re-import updates),
`crm_sync_runs` (auditable record of every import / activity push).

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '0019'
down_revision: Union[str, None] = '0018'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'crm_connections',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), nullable=False),
        sa.Column('provider', sa.Text(), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('credentials_enc', sa.Text(), nullable=False),
        sa.Column('field_map', JSONB(), nullable=False),
        sa.Column('active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('outbound_watermark', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_crm_connections_org_id'), 'crm_connections', ['org_id'], unique=False)

    op.create_table(
        'crm_links',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', UUID(as_uuid=True), nullable=False),
        sa.Column('lead_id', UUID(as_uuid=True), nullable=False),
        sa.Column('remote_id', sa.Text(), nullable=False),
        sa.Column('remote_type', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['connection_id'], ['crm_connections.id']),
        sa.ForeignKeyConstraint(['lead_id'], ['leads.id']),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('connection_id', 'lead_id', name='uq_crm_link_lead'),
        sa.UniqueConstraint('connection_id', 'remote_id', name='uq_crm_link_remote'),
    )
    op.create_index(op.f('ix_crm_links_org_id'), 'crm_links', ['org_id'], unique=False)
    op.create_index(op.f('ix_crm_links_connection_id'), 'crm_links', ['connection_id'], unique=False)
    op.create_index(op.f('ix_crm_links_lead_id'), 'crm_links', ['lead_id'], unique=False)

    op.create_table(
        'crm_sync_runs',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', UUID(as_uuid=True), nullable=False),
        sa.Column('direction', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False),
        sa.Column('stats', JSONB(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['connection_id'], ['crm_connections.id']),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_crm_sync_runs_org_id'), 'crm_sync_runs', ['org_id'], unique=False)
    op.create_index(
        'ix_crm_sync_runs_connection_created',
        'crm_sync_runs',
        ['connection_id', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_crm_sync_runs_connection_created', table_name='crm_sync_runs')
    op.drop_index(op.f('ix_crm_sync_runs_org_id'), table_name='crm_sync_runs')
    op.drop_table('crm_sync_runs')
    op.drop_index(op.f('ix_crm_links_lead_id'), table_name='crm_links')
    op.drop_index(op.f('ix_crm_links_connection_id'), table_name='crm_links')
    op.drop_index(op.f('ix_crm_links_org_id'), table_name='crm_links')
    op.drop_table('crm_links')
    op.drop_index(op.f('ix_crm_connections_org_id'), table_name='crm_connections')
    op.drop_table('crm_connections')
