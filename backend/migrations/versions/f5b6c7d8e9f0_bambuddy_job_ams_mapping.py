"""bambuddy_job ams_mapping

Revision ID: f5b6c7d8e9f0
Revises: d3a4b5c6d7e8
Create Date: 2026-07-12 14:00:00.000000

Phase 4 下发重构：bambuddy_job 持久化 ams_mapping（admin 手选或自动匹配的 AMS 料盘映射）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f5b6c7d8e9f0'
down_revision = 'd3a4b5c6d7e8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('bambuddy_job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ams_mapping', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('bambuddy_job', schema=None) as batch_op:
        batch_op.drop_column('ams_mapping')
