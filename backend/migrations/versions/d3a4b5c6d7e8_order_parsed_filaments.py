"""order parsed_filaments/nozzles + is_manual_slice_path

Revision ID: d3a4b5c6d7e8
Revises: e1f2a3b4c5d6
Create Date: 2026-07-12 13:00:00.000000

Phase 3 订单与下发流重构：print_order 存 gcode 解析产物（多色 filament/nozzle）
+ 标记是否走 admin 手动切片路径（.3mf）。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3a4b5c6d7e8'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('print_order', schema=None) as batch_op:
        batch_op.add_column(sa.Column('parsed_filaments', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('parsed_nozzles', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column(
            'is_manual_slice_path', sa.Boolean(),
            server_default='0', nullable=False))


def downgrade():
    with op.batch_alter_table('print_order', schema=None) as batch_op:
        batch_op.drop_column('is_manual_slice_path')
        batch_op.drop_column('parsed_nozzles')
        batch_op.drop_column('parsed_filaments')
