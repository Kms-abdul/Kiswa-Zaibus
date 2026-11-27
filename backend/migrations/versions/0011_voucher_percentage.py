"""MSEA vouchers: MSEA pays a percentage of each bill (the parent pays the rest), with an optional amount limit. Only the amount
(or the valid-to date) closes a voucher; quantities are informational. Vouchers closed early by quantity with money left are reopened.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-27
"""
from alembic import op
import sqlalchemy as sa

revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('msea_vouchers', sa.Column('pay_pct', sa.Numeric(5, 2), nullable=True))          # NULL = 100 %
    op.alter_column('msea_vouchers', 'max_value', existing_type=sa.Numeric(12, 2), nullable=True)   # NULL = no amount limit
    op.execute("""UPDATE msea_vouchers SET status='Partly used'
                  WHERE status='Fully used' AND max_value IS NOT NULL AND used_value < max_value AND valid_to >= CURDATE()""")


def downgrade() -> None:
    op.execute("UPDATE msea_vouchers SET max_value=used_value WHERE max_value IS NULL")
    op.alter_column('msea_vouchers', 'max_value', existing_type=sa.Numeric(12, 2), nullable=False)
    op.drop_column('msea_vouchers', 'pay_pct')
