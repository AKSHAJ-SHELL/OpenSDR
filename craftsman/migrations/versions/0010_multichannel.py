"""multi-channel sequences (M3.1)

sequence_steps.channel (email | linkedin_task | call_task; default email — existing
campaigns unaffected), sequence_steps.skip_on_expire (⛔ Gate M3: default false = an
undone task holds the sequence), touch_tasks (validated human-touch tasks; person PII —
joins the erase cascade; no FK cascade per M0.4 doctrine), and idx_enroll_due recreated
to include the new awaiting_human_touch state.

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = '0010'
down_revision: Union[str, None] = '0009'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DUE_STATES_OLD = "state IN ('queued','ready','waiting','ooo_rescheduled')"
_DUE_STATES_NEW = (
    "state IN ('queued','ready','waiting','ooo_rescheduled','awaiting_human_touch')"
)


def upgrade() -> None:
    op.add_column(
        'sequence_steps',
        sa.Column('channel', sa.Text(), nullable=False, server_default=sa.text("'email'")),
    )
    op.add_column(
        'sequence_steps',
        sa.Column('skip_on_expire', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )

    op.create_table(
        'touch_tasks',
        sa.Column('id', UUID(as_uuid=True), primary_key=True),
        sa.Column('enrollment_id', UUID(as_uuid=True), sa.ForeignKey('enrollments.id'), nullable=False),
        sa.Column('step_order', sa.Integer(), nullable=False),
        sa.Column('channel', sa.Text(), nullable=False),
        sa.Column('variant_id', UUID(as_uuid=True), sa.ForeignKey('variants.id'), nullable=True),
        sa.Column('payload', JSONB(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column('outcome', sa.Text(), nullable=True),
        sa.Column('due_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('enrollment_id', 'step_order', name='uq_touch_task_step'),
    )
    op.create_index('ix_touch_tasks_enrollment_id', 'touch_tasks', ['enrollment_id'])

    op.drop_index('idx_enroll_due', table_name='enrollments')
    op.create_index(
        'idx_enroll_due', 'enrollments', ['next_action_at'],
        postgresql_where=sa.text(_DUE_STATES_NEW),
    )


def downgrade() -> None:
    op.drop_index('idx_enroll_due', table_name='enrollments')
    op.create_index(
        'idx_enroll_due', 'enrollments', ['next_action_at'],
        postgresql_where=sa.text(_DUE_STATES_OLD),
    )
    op.drop_index('ix_touch_tasks_enrollment_id', table_name='touch_tasks')
    op.drop_table('touch_tasks')
    op.drop_column('sequence_steps', 'skip_on_expire')
    op.drop_column('sequence_steps', 'channel')
