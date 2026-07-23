"""enrichment framework (M2.1)

lead_enrichments provenance table (append-only: field/value/source/confidence/
fetched_at) plus the canonical columns enrichment may fill when empty:
leads.seniority/.phone, companies.industry/.size/.description. The lead FK does
NOT cascade (M0.4 doctrine: only erase_lead deletes; constraints block accidents).

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = '0008'
down_revision: Union[str, None] = '0007'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('seniority', sa.Text(), nullable=True))
    op.add_column('leads', sa.Column('phone', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('industry', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('size', sa.Text(), nullable=True))
    op.add_column('companies', sa.Column('description', sa.Text(), nullable=True))
    op.create_table(
        'lead_enrichments',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column(
            'lead_id',
            UUID(as_uuid=True),
            sa.ForeignKey('leads.id'),
            nullable=False,
        ),
        sa.Column('field', sa.Text(), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('source', sa.Text(), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column(
            'fetched_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
    )
    op.create_index('ix_lead_enrichments_lead_id', 'lead_enrichments', ['lead_id'])


def downgrade() -> None:
    op.drop_index('ix_lead_enrichments_lead_id', table_name='lead_enrichments')
    op.drop_table('lead_enrichments')
    op.drop_column('companies', 'description')
    op.drop_column('companies', 'size')
    op.drop_column('companies', 'industry')
    op.drop_column('leads', 'phone')
    op.drop_column('leads', 'seniority')
