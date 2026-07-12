"""add manual_slice_surcharge pricing config

Revision ID: e1f2a3b4c5d6
Revises: fc6bc712506c
Create Date: 2026-07-12 12:00:00.000000

Phase 2 订单与下发流重构：.3mf 走 admin 手动切片路径，加固定手工费。
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e1f2a3b4c5d6'
down_revision = 'fc6bc712506c'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # 幂等：key 唯一，已存在不报错（与 seed_pricing.py 同源，互不踩）
    bind.execute(sa.text(
        "INSERT INTO pricing_config (`key`, `value`, `category`, `label`, `unit`) "
        "VALUES ('manual_slice_surcharge', 5, 'global', '手工切片费', '次') "
        "ON DUPLICATE KEY UPDATE label = VALUES(label)"
    ))


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text(
        "DELETE FROM pricing_config WHERE `key` = 'manual_slice_surcharge'"
    ))
