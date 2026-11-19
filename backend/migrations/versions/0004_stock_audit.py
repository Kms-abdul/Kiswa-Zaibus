"""Release 2.2: stock audit module — stock_audits, stock_audit_lines, stock_audit_events; items.uom;
stock_adjustments.audit_no / approved_by; permission grants for screen 'saudit'; default settings 'stock_audit'.

Revision ID: 0004
Revises: 0003
Create Date: (release 2.2) 2026-11-10 15:46:01.994262+05:30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

DT6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql', 'mariadb')
LONG_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), 'mysql', 'mariadb')

SETTINGS = '{"high_value": 25000, "photo_above": 2000, "due_days": 3, "spot_size": 20, "max_lines": 5000}'
# grants for existing databases (same as services/stock_audit.PERMS; fresh installs get them from the seed)
PERMS = {
    'Director': 'view add edit submit verify review approve post restore cancel print pdf export upload fin cost',
    'Owner': 'view add edit submit verify review approve post restore cancel print pdf export upload fin cost',
    'Chairman': 'view add verify review approve post restore print pdf export upload fin cost',
    'Super Admin': 'view restore print pdf export',
    'CFO': 'view review approve post print pdf export upload fin cost',
    'Finance Head': 'view review approve post print pdf export upload fin cost',
    'Accounts Manager': 'view review approve post print pdf export upload fin cost',
    'Finance': 'view review print pdf export fin cost',
    'Accountant': 'view print pdf export fin cost',
    'Finance User': 'view print pdf export fin',
    'Regional Director': 'view add verify review approve cancel print pdf export upload fin cost',
    'Zonal Director': 'view add verify review approve cancel print pdf export upload fin cost',
    'Company Manager': 'view add verify review approve cancel print pdf export upload fin cost',
    'Inventory Manager': 'view add edit submit review post cancel print pdf export upload fin cost',
    'Warehouse Manager': 'view add edit submit verify review post cancel print pdf export upload cost',
    'Branch Manager': 'view add edit submit verify cancel print pdf export upload cost',
    'Store In-Charge': 'view add edit submit verify cancel print pdf export upload cost',
    'Branch In-Charge': 'view add edit submit verify cancel print pdf export upload',
    'Store Manager': 'view add edit submit verify cancel print pdf export upload',
    'Inventory User': 'view edit submit upload print',
    'Warehouse Picker': 'view edit submit upload',
    'Warehouse Quality Checker': 'view edit submit upload',
    'Sales Executive': 'view edit submit upload',
    'Store Cashier': 'view edit submit upload',
    'Auditor': 'view print pdf export fin cost',
    'Read-only Viewer': 'view',
}

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('stock_audits',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('location', sa.String(length=10), nullable=False),
    sa.Column('location_type', sa.String(length=12), nullable=False),
    sa.Column('audit_date', sa.Date(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('audit_type', sa.String(length=10), nullable=False),
    sa.Column('scope', sa.JSON(), nullable=True),
    sa.Column('stock_area', sa.String(length=60), nullable=True),
    sa.Column('auditors', sa.JSON(), nullable=False),
    sa.Column('supervisor_id', sa.String(length=16), nullable=False),
    sa.Column('instructions', sa.String(length=1000), nullable=True),
    sa.Column('status', sa.String(length=14), server_default='Draft', nullable=False),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('started_at', DT6, nullable=True),
    sa.Column('submitted_by', sa.String(length=16), nullable=True),
    sa.Column('submitted_at', DT6, nullable=True),
    sa.Column('verified_by', sa.String(length=16), nullable=True),
    sa.Column('verified_at', DT6, nullable=True),
    sa.Column('reviewed_by', sa.String(length=16), nullable=True),
    sa.Column('reviewed_at', DT6, nullable=True),
    sa.Column('review_note', sa.String(length=500), nullable=True),
    sa.Column('approval_no', sa.String(length=30), nullable=True),
    sa.Column('approved_by', sa.String(length=16), nullable=True),
    sa.Column('approved_at', DT6, nullable=True),
    sa.Column('rejected_reason', sa.String(length=500), nullable=True),
    sa.Column('escalated', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('adjustment_no', sa.String(length=30), nullable=True),
    sa.Column('posted_by', sa.String(length=16), nullable=True),
    sa.Column('posted_at', DT6, nullable=True),
    sa.Column('closed_at', DT6, nullable=True),
    sa.Column('reopen_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('lines_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('counted_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('matched_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('system_value', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('physical_value', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('shortage_value', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('excess_value', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('damaged_value', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['company'], ['companies.code'], name=op.f('fk_stock_audits_company_companies')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_audits')),
    sa.UniqueConstraint('no', name=op.f('uq_stock_audits_no'))
    )
    op.create_index('ix_stock_audits_branch', 'stock_audits', ['branch_id'], unique=False)
    op.create_index('ix_stock_audits_company_date', 'stock_audits', ['company', 'audit_date'], unique=False)
    op.create_index('ix_stock_audits_status', 'stock_audits', ['status'], unique=False)
    op.create_table('stock_audit_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('audit_id', sa.BigInteger(), nullable=False),
    sa.Column('at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('user_id', sa.String(length=16), nullable=True),
    sa.Column('user_name', sa.String(length=120), nullable=True),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['audit_id'], ['stock_audits.id'], name=op.f('fk_stock_audit_events_audit_id_stock_audits'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_audit_events'))
    )
    op.create_index(op.f('ix_stock_audit_events_audit_id'), 'stock_audit_events', ['audit_id'], unique=False)
    op.create_table('stock_audit_lines',
    sa.Column('audit_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('batch_no', sa.String(length=40), nullable=True),
    sa.Column('system_qty', sa.Integer(), nullable=True),
    sa.Column('physical_qty', sa.Integer(), nullable=True),
    sa.Column('unit_cost', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column('condition', sa.String(length=10), server_default='Good', nullable=False),
    sa.Column('remarks', sa.String(length=300), nullable=True),
    sa.Column('added_in_count', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('counted_by', sa.String(length=16), nullable=True),
    sa.Column('counted_at', DT6, nullable=True),
    sa.ForeignKeyConstraint(['audit_id'], ['stock_audits.id'], name=op.f('fk_stock_audit_lines_audit_id_stock_audits'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('audit_id', 'line_no', name=op.f('pk_stock_audit_lines'))
    )
    op.create_index('ix_stock_audit_lines_item', 'stock_audit_lines', ['item_id'], unique=False)
    op.add_column('items', sa.Column('uom', sa.String(length=10), server_default='Nos', nullable=False))
    op.add_column('stock_adjustments', sa.Column('audit_no', sa.String(length=30), nullable=True))
    op.add_column('stock_adjustments', sa.Column('approved_by', sa.String(length=16), nullable=True))
    op.create_index(op.f('ix_stock_adjustments_audit_no'), 'stock_adjustments', ['audit_no'], unique=False)
    # --- data
    op.execute(f"INSERT IGNORE INTO settings (skey, svalue) VALUES ('stock_audit', '{SETTINGS}')")
    op.execute("UPDATE items SET uom = CASE WHEN category IN ('Shoes','Socks') THEN 'Pair' WHEN company='ZE' THEN 'Book' ELSE 'Nos' END")
    for role, acts in PERMS.items():   # only roles that exist (none on a brand-new database: the seed adds them there)
        for a in acts.split():
            op.execute(f"INSERT IGNORE INTO permissions (role, screen_key, action) SELECT name, 'saudit', '{a}' FROM roles WHERE name='{role}'")


def downgrade() -> None:
    op.execute("DELETE FROM permissions WHERE screen_key='saudit'")
    op.execute("DELETE FROM settings WHERE skey='stock_audit'")
    op.drop_index(op.f('ix_stock_adjustments_audit_no'), table_name='stock_adjustments')
    op.drop_column('stock_adjustments', 'approved_by')
    op.drop_column('stock_adjustments', 'audit_no')
    op.drop_column('items', 'uom')
    op.drop_table('stock_audit_lines')
    op.drop_table('stock_audit_events')
    op.drop_table('stock_audits')
