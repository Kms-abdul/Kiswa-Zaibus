"""Release 2.1: discount schemes (+ online_order_lines.disc_pct), custom fields, scheduled report e-mails, payment links (invoice / receipt linkage),
report attachments in the outbox, and the default receipt-printing settings ('pos').

Revision ID: 0003
Revises: 0002
Create Date: (release 2.1) 2026-11-05 09:16:22.346575+05:30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

DT6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql', 'mariadb')
LONG_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), 'mysql', 'mariadb')

POS_DEFAULT = ('{"printer": "80", "thanks": "Thank you. Keep this bill for exchanges.", '
               '"policyKG": "Exchange within 7 days with the bill and tags intact. Worn or washed uniforms are not taken back.", '
               '"policyZE": "Books are exchanged within 7 days if unmarked and in the original wrapping."}')

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('custom_fields',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('entity', sa.String(length=12), nullable=False),
    sa.Column('label', sa.String(length=60), nullable=False),
    sa.Column('type', sa.String(length=8), server_default='text', nullable=False),
    sa.Column('options', sa.JSON(), nullable=True),
    sa.Column('required', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('sort', sa.Integer(), server_default='0', nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_custom_fields'))
    )
    op.create_table('report_schedules',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('report', sa.String(length=40), nullable=False),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('frequency', sa.String(length=8), nullable=False),
    sa.Column('run_at', sa.String(length=5), server_default='21:30', nullable=False),
    sa.Column('weekday', sa.Integer(), nullable=True),
    sa.Column('month_day', sa.Integer(), nullable=True),
    sa.Column('recipients', sa.JSON(), nullable=False),
    sa.Column('owner_id', sa.String(length=16), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('last_run_at', DT6, nullable=True),
    sa.Column('last_result', sa.String(length=300), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_report_schedules'))
    )
    op.create_table('custom_values',
    sa.Column('field_id', sa.Integer(), nullable=False),
    sa.Column('ref', sa.String(length=30), nullable=False),
    sa.Column('value', sa.String(length=300), nullable=True),
    sa.Column('updated_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['field_id'], ['custom_fields.id'], name=op.f('fk_custom_values_field_id_custom_fields'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('field_id', 'ref', name=op.f('pk_custom_values'))
    )
    op.create_table('discount_schemes',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('scope', sa.String(length=10), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=True),
    sa.Column('school_id', sa.String(length=10), nullable=True),
    sa.Column('item_id', sa.String(length=24), nullable=True),
    sa.Column('pct', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('online', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_by', sa.String(length=16), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['company'], ['companies.code'], name=op.f('fk_discount_schemes_company_companies')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_discount_schemes')),
    sa.UniqueConstraint('name', name=op.f('uq_discount_schemes_name'))
    )
    op.create_index('ix_discount_schemes_company_active', 'discount_schemes', ['company', 'active'], unique=False)
    op.add_column('outbox', sa.Column('attachment_name', sa.String(length=120), nullable=True))
    op.add_column('outbox', sa.Column('attachment', LONG_TEXT, nullable=True))
    op.add_column('online_order_lines', sa.Column('disc_pct', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False))
    op.add_column('payment_links', sa.Column('adm_no', sa.String(length=20), nullable=True))
    op.add_column('payment_links', sa.Column('invoice_no', sa.String(length=40), nullable=True))
    op.add_column('payment_links', sa.Column('mobile', sa.String(length=20), nullable=True))
    op.add_column('payment_links', sa.Column('payment_id', sa.String(length=40), nullable=True))
    op.add_column('payment_links', sa.Column('receipt_no', sa.String(length=30), nullable=True))
    op.add_column('payment_links', sa.Column('paid_at', DT6, nullable=True))
    op.add_column('payment_links', sa.Column('env', sa.String(length=10), server_default='Simulated', nullable=False))
    op.execute(f"INSERT IGNORE INTO settings (skey, svalue) VALUES ('pos', '{POS_DEFAULT}')")


def downgrade() -> None:
    op.execute("DELETE FROM settings WHERE skey='pos'")
    op.drop_column('payment_links', 'env')
    op.drop_column('online_order_lines', 'disc_pct')
    op.drop_column('payment_links', 'paid_at')
    op.drop_column('payment_links', 'receipt_no')
    op.drop_column('payment_links', 'payment_id')
    op.drop_column('payment_links', 'mobile')
    op.drop_column('payment_links', 'invoice_no')
    op.drop_column('payment_links', 'adm_no')
    op.drop_column('outbox', 'attachment')
    op.drop_column('outbox', 'attachment_name')
    op.drop_table('discount_schemes')
    op.drop_table('custom_values')
    op.drop_table('report_schedules')
    op.drop_table('custom_fields')
