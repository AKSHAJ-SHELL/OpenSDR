"""multi-tenancy: orgs, users, org_id on every tenant table (M5.1)

Creates `orgs` and `users`, inserts the default org, stamps every existing row
into it, then tightens org_id to NOT NULL — a single-tenant install upgrades
without noticing. Rewrites the global unique constraints that stop being true
under tenancy (lead email, company domain, mailbox email), rebuilds the
suppression PK around a surrogate id with UNIQUE(org_id, email), and creates
the optional cross-org `global_suppression` overlay (⛔ Gate M5 Q1a).

dead_letters.org_id stays nullable on purpose: the task_failure handler may
fire with no org context and must never lose a record over stamping.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '0016'
down_revision: Union[str, None] = '0015'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEFAULT_ORG_ID = '00000000-0000-0000-0000-000000000001'

# every OrgScoped table (craftsman/core/models.py) — org_id NOT NULL after backfill
ORG_TABLES = [
    'companies', 'leads', 'lead_enrichments', 'signals', 'signal_rules',
    'collector_state', 'campaigns', 'sequence_steps', 'variants', 'enrollments',
    'messages', 'touch_tasks', 'meetings', 'escalation_rules', 'reply_drafts',
    'mailboxes', 'suppression_list', 'audit_log', 'review_queue',
    'unsubscribe_tokens', 'dry_runs', 'dry_run_items', 'api_keys',
]


def upgrade() -> None:
    op.create_table(
        'orgs',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.Text(), nullable=False),
        sa.Column('slug', sa.Text(), nullable=False),
        sa.Column('daily_send_cap', sa.Integer(), nullable=True),
        sa.Column('sent_today', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('max_mailboxes', sa.Integer(), nullable=True),
        sa.Column('enrichment_daily_budget', sa.Integer(), nullable=True),
        sa.Column('enrichment_calls_today', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('slug'),
    )
    op.execute(
        f"INSERT INTO orgs (id, name, slug) VALUES ('{DEFAULT_ORG_ID}', 'Default', 'default')"
    )

    op.create_table(
        'users',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('org_id', UUID(as_uuid=True), nullable=False),
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('display_name', sa.Text(), nullable=True),
        sa.Column('role', sa.Text(), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=True),
        sa.Column('oidc_issuer', sa.Text(), nullable=True),
        sa.Column('oidc_sub', sa.Text(), nullable=True),
        sa.Column('disabled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('org_id', 'email', name='uq_user_org_email'),
        sa.UniqueConstraint('oidc_issuer', 'oidc_sub', name='uq_user_oidc_identity'),
    )
    op.create_index(op.f('ix_users_org_id'), 'users', ['org_id'], unique=False)

    op.create_table(
        'global_suppression',
        sa.Column('email', sa.Text(), nullable=False),
        sa.Column('reason', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('email'),
    )

    # org_id on every tenant table: add nullable → backfill → NOT NULL → FK + index
    for table in ORG_TABLES:
        if table == 'users':
            continue  # created above with org_id inline
        op.add_column(table, sa.Column('org_id', UUID(as_uuid=True), nullable=True))
        op.execute(f"UPDATE {table} SET org_id = '{DEFAULT_ORG_ID}'")
        op.alter_column(table, 'org_id', nullable=False)
        op.create_foreign_key(None, table, 'orgs', ['org_id'], ['id'])
        op.create_index(op.f(f'ix_{table}_org_id'), table, ['org_id'], unique=False)

    # opportunistic attribution only — nullable, no FK (a dead letter must
    # survive even org deletion tooling), indexed for the ops view filter
    op.add_column('dead_letters', sa.Column('org_id', UUID(as_uuid=True), nullable=True))
    op.create_index(op.f('ix_dead_letters_org_id'), 'dead_letters', ['org_id'], unique=False)

    # global unique constraints that stop being true under tenancy
    op.drop_constraint('companies_domain_key', 'companies', type_='unique')
    op.create_unique_constraint('uq_company_org_domain', 'companies', ['org_id', 'domain'])
    op.drop_constraint('leads_email_key', 'leads', type_='unique')
    op.create_unique_constraint('uq_lead_org_email', 'leads', ['org_id', 'email'])
    op.drop_constraint('mailboxes_email_key', 'mailboxes', type_='unique')
    op.create_unique_constraint('uq_mailbox_org_email', 'mailboxes', ['org_id', 'email'])

    # suppression: email PK → surrogate id + UNIQUE(org_id, email)
    op.add_column(
        'suppression_list',
        sa.Column('id', UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
    )
    op.drop_constraint('suppression_list_pkey', 'suppression_list', type_='primary')
    op.create_primary_key('suppression_list_pkey', 'suppression_list', ['id'])
    op.alter_column('suppression_list', 'id', server_default=None)
    op.create_unique_constraint(
        'uq_suppression_org_email', 'suppression_list', ['org_id', 'email']
    )


def downgrade() -> None:
    # collapse to single-tenant: rows outside the default org would collide on
    # the restored global uniques — fail loudly rather than merge tenants
    op.drop_constraint('uq_suppression_org_email', 'suppression_list', type_='unique')
    op.drop_constraint('suppression_list_pkey', 'suppression_list', type_='primary')
    op.drop_column('suppression_list', 'id')
    op.create_primary_key('suppression_list_pkey', 'suppression_list', ['email'])

    op.drop_constraint('uq_mailbox_org_email', 'mailboxes', type_='unique')
    op.create_unique_constraint('mailboxes_email_key', 'mailboxes', ['email'])
    op.drop_constraint('uq_lead_org_email', 'leads', type_='unique')
    op.create_unique_constraint('leads_email_key', 'leads', ['email'])
    op.drop_constraint('uq_company_org_domain', 'companies', type_='unique')
    op.create_unique_constraint('companies_domain_key', 'companies', ['domain'])

    op.drop_index(op.f('ix_dead_letters_org_id'), table_name='dead_letters')
    op.drop_column('dead_letters', 'org_id')

    for table in reversed(ORG_TABLES):
        if table == 'users':
            continue
        op.drop_index(op.f(f'ix_{table}_org_id'), table_name=table)
        op.drop_column(table, 'org_id')

    op.drop_index(op.f('ix_users_org_id'), table_name='users')
    op.drop_table('users')
    op.drop_table('global_suppression')
    op.drop_table('orgs')
