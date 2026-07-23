"""icp score provenance (M1.3)

Records the parts that produced a lead's ICP score, and which campaign's ICP it was
scored against. Existing rows keep NULL components — the dashboard says "not tracked"
rather than inventing a breakdown for a score computed before this existed.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('leads', sa.Column('icp_cosine', sa.Float(), nullable=True))
    op.add_column('leads', sa.Column('icp_rule', sa.Float(), nullable=True))
    op.add_column('leads', sa.Column('icp_scored_campaign_id', sa.UUID(), nullable=True))
    op.add_column('leads', sa.Column('icp_scored_at', sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        'fk_leads_icp_scored_campaign_id', 'leads', 'campaigns',
        ['icp_scored_campaign_id'], ['id'],
    )


def downgrade() -> None:
    op.drop_constraint('fk_leads_icp_scored_campaign_id', 'leads', type_='foreignkey')
    op.drop_column('leads', 'icp_scored_at')
    op.drop_column('leads', 'icp_scored_campaign_id')
    op.drop_column('leads', 'icp_rule')
    op.drop_column('leads', 'icp_cosine')
