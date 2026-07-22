"""dry runs (M1.2 preflight)

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('dry_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('campaign_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('requested_n', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('dry_run_items',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('dry_run_id', sa.UUID(), nullable=False),
    sa.Column('lead_id', sa.UUID(), nullable=True),
    sa.Column('lead_email', sa.Text(), nullable=False),
    sa.Column('lead_name', sa.Text(), nullable=True),
    sa.Column('icp_score', sa.Float(), nullable=True),
    sa.Column('variant_id', sa.UUID(), nullable=True),
    sa.Column('variant_name', sa.Text(), nullable=True),
    sa.Column('subject', sa.Text(), nullable=True),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('validator_ok', sa.Boolean(), nullable=True),
    sa.Column('validator_errors', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('delivered', sa.Boolean(), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['dry_run_id'], ['dry_runs.id'], ),
    sa.ForeignKeyConstraint(['lead_id'], ['leads.id'], ),
    sa.ForeignKeyConstraint(['variant_id'], ['variants.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_dry_run_items_lead_id'), 'dry_run_items', ['lead_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_dry_run_items_lead_id'), table_name='dry_run_items')
    op.drop_table('dry_run_items')
    op.drop_table('dry_runs')
