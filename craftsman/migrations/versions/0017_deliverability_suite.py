"""deliverability suite: domain_stats + placement runs/results (M5.3)

Per-domain daily rollups (sends / hard bounces / spam-bounce complaint proxy)
behind the health score and the auto-pause budget, plus the inbox-placement
smoke-test tables. All three are OrgScoped tenant data.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = '0017'
down_revision: Union[str, None] = '0016'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'domain_stats',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), nullable=False),
        sa.Column('domain', sa.Text(), nullable=False),
        sa.Column('day', sa.Date(), nullable=False),
        sa.Column('sends', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('hard_bounces', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('spam_bounces', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'domain', 'day', name='uq_domain_stats_org_domain_day'),
    )
    op.create_index(op.f('ix_domain_stats_org_id'), 'domain_stats', ['org_id'], unique=False)

    op.create_table(
        'placement_runs',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), nullable=False),
        sa.Column('campaign_id', UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.Text(), server_default=sa.text("'running'"), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id']),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_placement_runs_org_id'), 'placement_runs', ['org_id'], unique=False)

    op.create_table(
        'placement_results',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), nullable=False),
        sa.Column('run_id', UUID(as_uuid=True), nullable=False),
        sa.Column('seed_email', sa.Text(), nullable=False),
        sa.Column('mailbox_id', UUID(as_uuid=True), nullable=True),
        sa.Column('verdict', sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column('delivered', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('marked_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['mailbox_id'], ['mailboxes.id']),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['run_id'], ['placement_runs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('run_id', 'seed_email', name='uq_placement_result_seed'),
    )
    op.create_index(op.f('ix_placement_results_org_id'), 'placement_results', ['org_id'], unique=False)
    op.create_index(op.f('ix_placement_results_run_id'), 'placement_results', ['run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_placement_results_run_id'), table_name='placement_results')
    op.drop_index(op.f('ix_placement_results_org_id'), table_name='placement_results')
    op.drop_table('placement_results')
    op.drop_index(op.f('ix_placement_runs_org_id'), table_name='placement_runs')
    op.drop_table('placement_runs')
    op.drop_index(op.f('ix_domain_stats_org_id'), table_name='domain_stats')
    op.drop_table('domain_stats')
