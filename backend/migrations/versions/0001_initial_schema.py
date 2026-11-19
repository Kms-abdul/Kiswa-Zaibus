"""initial schema

Revision ID: 0001
Revises: 
Create Date: (release 2.0) 2026-11-04 17:02:47.967997+05:30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# DATETIME(6) on MySQL / MariaDB (microsecond timestamps); MEDIUMTEXT for raw webhook bodies
DT6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql', 'mariadb')
LONG_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), 'mysql', 'mariadb')

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All tables of release 2.0. Generated from kz/models with `alembic revision --autogenerate`, then reviewed.
    op.create_table('access_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('user_id', sa.String(length=16), nullable=True),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('what', sa.String(length=200), nullable=False),
    sa.Column('detail', sa.String(length=300), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_access_log'))
    )
    op.create_index(op.f('ix_access_log_at'), 'access_log', ['at'], unique=False)
    op.create_table('accounts',
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('code', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('grp', sa.String(length=1), nullable=False),
    sa.Column('is_control', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('company', 'code', name=op.f('pk_accounts'))
    )
    op.create_table('alert_rules',
    sa.Column('alert_type', sa.String(length=60), nullable=False),
    sa.Column('in_app', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('email', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('whatsapp', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('roles', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('alert_type', name=op.f('pk_alert_rules'))
    )
    op.create_table('alerts',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=True),
    sa.Column('branch_id', sa.String(length=16), nullable=True),
    sa.Column('severity', sa.String(length=8), server_default='warn', nullable=False),
    sa.Column('alert_type', sa.String(length=60), nullable=False),
    sa.Column('message', sa.String(length=500), nullable=False),
    sa.Column('ref', sa.String(length=60), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alerts'))
    )
    op.create_index(op.f('ix_alerts_created_at'), 'alerts', ['created_at'], unique=False)
    op.create_table('approvals',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=20), nullable=False),
    sa.Column('kind', sa.String(length=30), nullable=False),
    sa.Column('sub_kind', sa.String(length=30), nullable=True),
    sa.Column('company', sa.String(length=4), nullable=True),
    sa.Column('branch_id', sa.String(length=16), nullable=True),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('detail', sa.String(length=1000), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('ref', sa.String(length=60), nullable=True),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('required_role', sa.String(length=60), nullable=False),
    sa.Column('super_admin_only', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('status', sa.String(length=12), server_default='Pending', nullable=False),
    sa.Column('requested_by', sa.String(length=16), nullable=False),
    sa.Column('requested_role', sa.String(length=60), nullable=True),
    sa.Column('requested_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('decided_by', sa.String(length=16), nullable=True),
    sa.Column('decided_at', DT6, nullable=True),
    sa.Column('result', sa.String(length=500), nullable=True),
    sa.Column('escalated_at', DT6, nullable=True),
    sa.Column('signature', sa.String(length=255), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_approvals')),
    sa.UniqueConstraint('no', name=op.f('uq_approvals_no'))
    )
    op.create_index(op.f('ix_approvals_ref'), 'approvals', ['ref'], unique=False)
    op.create_index(op.f('ix_approvals_signature'), 'approvals', ['signature'], unique=False)
    op.create_index('ix_approvals_status_role', 'approvals', ['status', 'required_role'], unique=False)
    op.create_table('audit_head',
    sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('last_id', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_head'))
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('user_id', sa.String(length=16), nullable=True),
    sa.Column('user_name', sa.String(length=120), nullable=False),
    sa.Column('role', sa.String(length=60), nullable=True),
    sa.Column('company', sa.String(length=8), nullable=True),
    sa.Column('branch_id', sa.String(length=16), nullable=True),
    sa.Column('action', sa.String(length=120), nullable=False),
    sa.Column('ref', sa.String(length=120), nullable=True),
    sa.Column('old_value', sa.Text(), nullable=True),
    sa.Column('new_value', sa.Text(), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('category', sa.String(length=30), nullable=True),
    sa.Column('status', sa.String(length=30), nullable=True),
    sa.Column('device', sa.String(length=160), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('prev_hash', sa.String(length=64), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index(op.f('ix_audit_log_at'), 'audit_log', ['at'], unique=False)
    op.create_index(op.f('ix_audit_log_category'), 'audit_log', ['category'], unique=False)
    op.create_index(op.f('ix_audit_log_ref'), 'audit_log', ['ref'], unique=False)
    op.create_index(op.f('ix_audit_log_user_id'), 'audit_log', ['user_id'], unique=False)
    op.create_table('cash_handovers',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('txn_date', sa.Date(), nullable=False),
    sa.Column('clerk_id', sa.String(length=16), nullable=False),
    sa.Column('from_user', sa.String(length=16), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('counted', sa.Numeric(precision=16, scale=2), nullable=True),
    sa.Column('difference', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('denominations', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='Awaiting clerk', nullable=False),
    sa.Column('accepted_at', DT6, nullable=True),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cash_handovers')),
    sa.UniqueConstraint('no', name=op.f('uq_cash_handovers_no'))
    )
    op.create_index(op.f('ix_cash_handovers_clerk_id'), 'cash_handovers', ['clerk_id'], unique=False)
    op.create_table('cash_shifts',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('location', sa.String(length=10), nullable=False),
    sa.Column('counter', sa.String(length=30), nullable=True),
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('shift_date', sa.Date(), nullable=False),
    sa.Column('opened_at', DT6, nullable=False),
    sa.Column('closed_at', DT6, nullable=True),
    sa.Column('status', sa.String(length=20), server_default='Open', nullable=False),
    sa.Column('opening', sa.JSON(), nullable=False),
    sa.Column('figures', sa.JSON(), nullable=True),
    sa.Column('denominations', sa.JSON(), nullable=True),
    sa.Column('difference_reason', sa.String(length=300), nullable=True),
    sa.Column('approval_no', sa.String(length=20), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cash_shifts')),
    sa.UniqueConstraint('no', name=op.f('uq_cash_shifts_no'))
    )
    op.create_index('ix_cash_shifts_user_status', 'cash_shifts', ['user_id', 'status'], unique=False)
    op.create_table('companies',
    sa.Column('code', sa.String(length=4), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('short_name', sa.String(length=40), nullable=False),
    sa.Column('gstin', sa.String(length=15), nullable=True),
    sa.Column('state_code', sa.String(length=2), server_default='36', nullable=False),
    sa.Column('address', sa.String(length=300), nullable=True),
    sa.Column('phone', sa.String(length=30), nullable=True),
    sa.Column('email', sa.String(length=120), nullable=True),
    sa.Column('online_prefix', sa.String(length=10), nullable=True),
    sa.Column('books_locked_to', sa.Date(), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_companies'))
    )
    op.create_table('customers',
    sa.Column('id', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('type', sa.String(length=12), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('mobile', sa.String(length=20), nullable=True),
    sa.Column('adm_no', sa.String(length=20), nullable=True),
    sa.Column('gstin', sa.String(length=15), nullable=True),
    sa.Column('receivable_account', sa.String(length=10), server_default='1200', nullable=False),
    sa.Column('credit_limit', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_customers'))
    )
    op.create_index('ix_customers_company_adm', 'customers', ['company', 'adm_no'], unique=False)
    op.create_table('debit_notes',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('po_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_debit_notes')),
    sa.UniqueConstraint('no', name=op.f('uq_debit_notes_no'))
    )
    op.create_index(op.f('ix_debit_notes_po_id'), 'debit_notes', ['po_id'], unique=False)
    op.create_table('erp_keys',
    sa.Column('feed', sa.String(length=20), nullable=False),
    sa.Column('source_ref', sa.String(length=60), nullable=False),
    sa.Column('version', sa.String(length=80), nullable=False),
    sa.Column('target_ref', sa.String(length=60), nullable=True),
    sa.Column('synced_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('feed', 'source_ref', name=op.f('pk_erp_keys'))
    )
    op.create_table('erp_sync_runs',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=20), nullable=False),
    sa.Column('started_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('finished_at', DT6, nullable=True),
    sa.Column('triggered_by', sa.String(length=60), nullable=False),
    sa.Column('trigger_type', sa.String(length=20), nullable=False),
    sa.Column('feeds', sa.String(length=200), nullable=False),
    sa.Column('dry_run', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('created', sa.Integer(), server_default='0', nullable=False),
    sa.Column('updated', sa.Integer(), server_default='0', nullable=False),
    sa.Column('skipped', sa.Integer(), server_default='0', nullable=False),
    sa.Column('failed', sa.Integer(), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_erp_sync_runs')),
    sa.UniqueConstraint('no', name=op.f('uq_erp_sync_runs_no'))
    )
    op.create_table('evidence',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('ref', sa.String(length=120), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=True),
    sa.Column('kind', sa.String(length=60), nullable=False),
    sa.Column('file_name', sa.String(length=255), nullable=False),
    sa.Column('mime_type', sa.String(length=100), nullable=True),
    sa.Column('size_bytes', sa.BigInteger(), nullable=False),
    sa.Column('storage_path', sa.String(length=400), nullable=False),
    sa.Column('sha256', sa.String(length=64), nullable=False),
    sa.Column('remarks', sa.String(length=500), nullable=True),
    sa.Column('uploaded_by', sa.String(length=16), nullable=False),
    sa.Column('uploaded_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('status', sa.String(length=12), server_default='Active', nullable=False),
    sa.Column('superseded_by', sa.BigInteger(), nullable=True),
    sa.Column('verify_status', sa.String(length=12), server_default='Submitted', nullable=False),
    sa.Column('verified_by', sa.String(length=16), nullable=True),
    sa.Column('verified_at', DT6, nullable=True),
    sa.Column('verify_remarks', sa.String(length=500), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_evidence'))
    )
    op.create_index(op.f('ix_evidence_ref'), 'evidence', ['ref'], unique=False)
    op.create_table('held_bills',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('draft', sa.JSON(), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('approval_no', sa.String(length=20), nullable=True),
    sa.Column('status', sa.String(length=12), server_default='Held', nullable=False),
    sa.Column('invoice_no', sa.String(length=30), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_held_bills')),
    sa.UniqueConstraint('no', name=op.f('uq_held_bills_no'))
    )
    op.create_table('inter_company',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('from_company', sa.String(length=4), nullable=False),
    sa.Column('to_company', sa.String(length=4), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_inter_company')),
    sa.UniqueConstraint('no', name=op.f('uq_inter_company_no'))
    )
    op.create_table('invoices',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('counter', sa.String(length=30), nullable=True),
    sa.Column('inv_date', sa.Date(), nullable=False),
    sa.Column('inv_time', sa.Time(), nullable=False),
    sa.Column('customer_id', sa.String(length=30), nullable=True),
    sa.Column('adm_no', sa.String(length=20), nullable=True),
    sa.Column('student_snapshot', sa.JSON(), nullable=True),
    sa.Column('cashier_id', sa.String(length=16), nullable=True),
    sa.Column('shift_id', sa.BigInteger(), nullable=True),
    sa.Column('channel', sa.String(length=10), server_default='counter', nullable=False),
    sa.Column('place_of_supply', sa.String(length=2), server_default='36', nullable=False),
    sa.Column('gross', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('discount', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('taxable', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('cgst', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('sgst', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('igst', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('round_off', sa.Numeric(precision=6, scale=2), server_default='0', nullable=False),
    sa.Column('total', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('balance', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=20), server_default='Posted', nullable=False),
    sa.Column('delivery_no', sa.String(length=30), nullable=True),
    sa.Column('pending_reason', sa.String(length=300), nullable=True),
    sa.Column('online_order_no', sa.String(length=30), nullable=True),
    sa.Column('msea_voucher_no', sa.String(length=30), nullable=True),
    sa.Column('issue_no', sa.String(length=30), nullable=True),
    sa.Column('override_by', sa.String(length=120), nullable=True),
    sa.Column('override_reason', sa.String(length=300), nullable=True),
    sa.Column('reprints', sa.Integer(), server_default='0', nullable=False),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_invoices')),
    sa.UniqueConstraint('no', name=op.f('uq_invoices_no'))
    )
    op.create_index(op.f('ix_invoices_adm_no'), 'invoices', ['adm_no'], unique=False)
    op.create_index('ix_invoices_company_branch_date', 'invoices', ['company', 'branch_id', 'inv_date'], unique=False)
    op.create_table('item_categories',
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('hsn', sa.String(length=10), nullable=True),
    sa.Column('gst_rate', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False),
    sa.Column('max_disc', sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column('isbn_required', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('company', 'name', name=op.f('pk_item_categories'))
    )
    op.create_table('journals',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('txn_date', sa.Date(), nullable=False),
    sa.Column('posted_on', sa.Date(), nullable=False),
    sa.Column('type', sa.String(length=30), nullable=False),
    sa.Column('ref', sa.String(length=60), nullable=True),
    sa.Column('narration', sa.String(length=500), nullable=False),
    sa.Column('created_by', sa.String(length=16), nullable=True),
    sa.Column('approval_no', sa.String(length=20), nullable=True),
    sa.Column('reverses_no', sa.String(length=30), nullable=True),
    sa.Column('reversed_by_no', sa.String(length=30), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_journals')),
    sa.UniqueConstraint('company', 'no', name=op.f('uq_journals_company_no'))
    )
    op.create_index('ix_journals_company_date', 'journals', ['company', 'txn_date'], unique=False)
    op.create_index(op.f('ix_journals_ref'), 'journals', ['ref'], unique=False)
    op.create_table('msea_vouchers',
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('adm_no', sa.String(length=20), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('scope', sa.String(length=60), nullable=False),
    sa.Column('entitlement', sa.JSON(), nullable=False),
    sa.Column('used_qty', sa.JSON(), nullable=False),
    sa.Column('max_value', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('used_value', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('approved_by', sa.String(length=120), nullable=True),
    sa.Column('remarks', sa.String(length=300), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('no', name=op.f('pk_msea_vouchers'))
    )
    op.create_index(op.f('ix_msea_vouchers_adm_no'), 'msea_vouchers', ['adm_no'], unique=False)
    op.create_table('number_series',
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('series', sa.String(length=20), nullable=False),
    sa.Column('fy', sa.String(length=4), nullable=False),
    sa.Column('last_no', sa.Integer(), server_default='0', nullable=False),
    sa.PrimaryKeyConstraint('company', 'series', 'fy', name=op.f('pk_number_series'))
    )
    op.create_table('online_orders',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('grp', sa.String(length=32), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('adm_no', sa.String(length=20), nullable=False),
    sa.Column('mobile', sa.String(length=20), nullable=False),
    sa.Column('collect_at', sa.String(length=10), nullable=False),
    sa.Column('warehouse_branch', sa.String(length=16), nullable=False),
    sa.Column('subtotal', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('total', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('invoice_no', sa.String(length=30), nullable=True),
    sa.Column('payment_id', sa.String(length=40), nullable=True),
    sa.Column('rzp_order_id', sa.String(length=40), nullable=True),
    sa.Column('collect_otp_hash', sa.String(length=64), nullable=True),
    sa.Column('events', sa.JSON(), nullable=False),
    sa.Column('placed_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_online_orders')),
    sa.UniqueConstraint('no', name=op.f('uq_online_orders_no'))
    )
    op.create_index(op.f('ix_online_orders_adm_no'), 'online_orders', ['adm_no'], unique=False)
    op.create_index(op.f('ix_online_orders_grp'), 'online_orders', ['grp'], unique=False)
    op.create_table('outbox',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('channel', sa.String(length=10), nullable=False),
    sa.Column('to_name', sa.String(length=120), nullable=True),
    sa.Column('address', sa.String(length=160), nullable=False),
    sa.Column('subject', sa.String(length=200), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('alert_type', sa.String(length=60), nullable=True),
    sa.Column('status', sa.String(length=10), server_default='Queued', nullable=False),
    sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_error', sa.String(length=300), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('sent_at', DT6, nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox'))
    )
    op.create_index('ix_outbox_status_created', 'outbox', ['status', 'created_at'], unique=False)
    op.create_table('owner_instructions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('clerk_id', sa.String(length=16), nullable=False),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('recipient', sa.String(length=160), nullable=True),
    sa.Column('purpose', sa.String(length=300), nullable=False),
    sa.Column('expense_account', sa.String(length=10), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('approval_no', sa.String(length=20), nullable=True),
    sa.Column('executed_at', DT6, nullable=True),
    sa.Column('execution', sa.JSON(), nullable=True),
    sa.Column('verified_by', sa.String(length=16), nullable=True),
    sa.Column('verified_at', DT6, nullable=True),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_owner_instructions')),
    sa.UniqueConstraint('no', name=op.f('uq_owner_instructions_no'))
    )
    op.create_table('packages',
    sa.Column('id', sa.String(length=24), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('school_id', sa.String(length=10), nullable=True),
    sa.Column('class_name', sa.String(length=20), nullable=True),
    sa.Column('gender', sa.String(length=10), nullable=True),
    sa.Column('disc_pct', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_packages'))
    )
    op.create_table('parent_challenges',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('adm_no', sa.String(length=20), nullable=False),
    sa.Column('mobile', sa.String(length=20), nullable=False),
    sa.Column('otp_hash', sa.String(length=64), nullable=False),
    sa.Column('tries', sa.Integer(), server_default='0', nullable=False),
    sa.Column('expires_at', DT6, nullable=False),
    sa.Column('used_at', DT6, nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_parent_challenges'))
    )
    op.create_index(op.f('ix_parent_challenges_adm_no'), 'parent_challenges', ['adm_no'], unique=False)
    op.create_table('parent_sessions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('mobile', sa.String(length=20), nullable=False),
    sa.Column('adm_nos', sa.JSON(), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('last_seen', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('ended_at', DT6, nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_parent_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_parent_sessions_token_hash'))
    )
    op.create_index(op.f('ix_parent_sessions_mobile'), 'parent_sessions', ['mobile'], unique=False)
    op.create_table('payment_links',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('short_url', sa.String(length=160), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('purpose', sa.String(length=20), nullable=False),
    sa.Column('customer_id', sa.String(length=30), nullable=True),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('paid', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('expires_on', sa.Date(), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_payment_links'))
    )
    op.create_table('purchase_orders',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('supplier_id', sa.String(length=16), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('value', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('freight', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('required_by', sa.Date(), nullable=True),
    sa.Column('notes', sa.String(length=300), nullable=True),
    sa.Column('grn_value', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_purchase_orders')),
    sa.UniqueConstraint('no', name=op.f('uq_purchase_orders_no'))
    )
    op.create_table('receipts',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('customer_id', sa.String(length=30), nullable=False),
    sa.Column('rcpt_date', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('mode', sa.String(length=10), nullable=False),
    sa.Column('reference', sa.String(length=60), nullable=True),
    sa.Column('utr', sa.String(length=60), nullable=True),
    sa.Column('applied', sa.JSON(), nullable=False),
    sa.Column('to_wallet', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('shift_id', sa.BigInteger(), nullable=True),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_receipts')),
    sa.UniqueConstraint('company', 'no', name=op.f('uq_receipts_company_no'))
    )
    op.create_index(op.f('ix_receipts_customer_id'), 'receipts', ['customer_id'], unique=False)
    op.create_table('refunds',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('request_no', sa.String(length=30), nullable=True),
    sa.Column('credit_note_no', sa.String(length=30), nullable=True),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('original_payment', sa.String(length=40), nullable=True),
    sa.Column('status', sa.String(length=12), server_default='Pending', nullable=False),
    sa.Column('gateway_refund_id', sa.String(length=40), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('completed_at', DT6, nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refunds')),
    sa.UniqueConstraint('no', name=op.f('uq_refunds_no'))
    )
    op.create_index(op.f('ix_refunds_order_id'), 'refunds', ['order_id'], unique=False)
    op.create_table('regions',
    sa.Column('id', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_regions'))
    )
    op.create_table('roles',
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=True),
    sa.Column('approval_rank', sa.Numeric(precision=4, scale=2), server_default='0', nullable=False),
    sa.Column('read_only', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('is_custom', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('name', name=op.f('pk_roles'))
    )
    op.create_table('rzp_exceptions',
    sa.Column('ex_key', sa.String(length=80), nullable=False),
    sa.Column('ex_type', sa.String(length=20), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=True),
    sa.Column('ref', sa.String(length=200), nullable=True),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('detail', sa.String(length=500), nullable=False),
    sa.Column('ex_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='Open', nullable=False),
    sa.Column('resolution', sa.String(length=300), nullable=True),
    sa.Column('resolved_by', sa.String(length=16), nullable=True),
    sa.Column('resolved_at', DT6, nullable=True),
    sa.PrimaryKeyConstraint('ex_key', name=op.f('pk_rzp_exceptions'))
    )
    op.create_table('rzp_payments',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('order_id', sa.String(length=40), nullable=True),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('ref', sa.String(length=200), nullable=False),
    sa.Column('ref_type', sa.String(length=30), nullable=False),
    sa.Column('adm_no', sa.String(length=20), nullable=True),
    sa.Column('payer_mobile', sa.String(length=20), nullable=True),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('method', sa.String(length=20), nullable=False),
    sa.Column('method_detail', sa.String(length=60), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('bank_ref', sa.String(length=60), nullable=True),
    sa.Column('fee', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('tax', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('refunded', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('settlement_id', sa.String(length=40), nullable=True),
    sa.Column('app_ref', sa.String(length=200), nullable=True),
    sa.Column('webhook_verified', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('error_reason', sa.String(length=300), nullable=True),
    sa.Column('env', sa.String(length=10), nullable=False),
    sa.Column('paid_at', DT6, nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rzp_payments'))
    )
    op.create_index(op.f('ix_rzp_payments_order_id'), 'rzp_payments', ['order_id'], unique=False)
    op.create_index('ix_rzp_payments_paid_at', 'rzp_payments', ['paid_at'], unique=False)
    op.create_index(op.f('ix_rzp_payments_ref'), 'rzp_payments', ['ref'], unique=False)
    op.create_table('rzp_settlements',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('settled_on', sa.Date(), nullable=False),
    sa.Column('gross', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('fee', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('tax', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('net', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('utr', sa.String(length=40), nullable=False),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rzp_settlements'))
    )
    op.create_table('rzp_webhook_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('event_id', sa.String(length=40), nullable=True),
    sa.Column('event', sa.String(length=60), nullable=True),
    sa.Column('company', sa.String(length=4), nullable=True),
    sa.Column('signature_ok', sa.Boolean(), nullable=False),
    sa.Column('duplicate', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('body', LONG_TEXT, nullable=False),
    sa.Column('received_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('processed_at', DT6, nullable=True),
    sa.Column('error', sa.String(length=300), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rzp_webhook_events')),
    sa.UniqueConstraint('event_id', name=op.f('uq_rzp_webhook_events_event_id'))
    )
    op.create_table('schools',
    sa.Column('id', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('board', sa.String(length=40), nullable=True),
    sa.Column('area', sa.String(length=80), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schools'))
    )
    op.create_table('security_events',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('type', sa.String(length=60), nullable=False),
    sa.Column('user_id', sa.String(length=16), nullable=True),
    sa.Column('user_name', sa.String(length=120), nullable=True),
    sa.Column('detail', sa.String(length=500), nullable=True),
    sa.Column('device', sa.String(length=160), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_security_events'))
    )
    op.create_index(op.f('ix_security_events_at'), 'security_events', ['at'], unique=False)
    op.create_index(op.f('ix_security_events_user_id'), 'security_events', ['user_id'], unique=False)
    op.create_table('settings',
    sa.Column('skey', sa.String(length=60), nullable=False),
    sa.Column('svalue', sa.JSON(), nullable=False),
    sa.Column('updated_by', sa.String(length=16), nullable=True),
    sa.Column('updated_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('skey', name=op.f('pk_settings'))
    )
    op.create_table('stock_adjustments',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('txn_date', sa.Date(), nullable=False),
    sa.Column('adj_type', sa.String(length=20), nullable=False),
    sa.Column('value', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_adjustments')),
    sa.UniqueConstraint('no', name=op.f('uq_stock_adjustments_no'))
    )
    op.create_table('stock_balance',
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('qty', sa.Integer(), server_default='0', nullable=False),
    sa.Column('reserved', sa.Integer(), server_default='0', nullable=False),
    sa.PrimaryKeyConstraint('branch_id', 'item_id', name=op.f('pk_stock_balance'))
    )
    op.create_table('stock_ledger',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('txn_date', sa.Date(), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('unit_cost', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('txn_type', sa.String(length=30), nullable=False),
    sa.Column('ref', sa.String(length=40), nullable=False),
    sa.Column('created_by', sa.String(length=16), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_ledger'))
    )
    op.create_index('ix_stock_ledger_branch_item_date', 'stock_ledger', ['branch_id', 'item_id', 'txn_date'], unique=False)
    op.create_index(op.f('ix_stock_ledger_ref'), 'stock_ledger', ['ref'], unique=False)
    op.create_table('students',
    sa.Column('adm_no', sa.String(length=20), nullable=False),
    sa.Column('erp_ref', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('gender', sa.String(length=1), nullable=True),
    sa.Column('class_name', sa.String(length=20), nullable=False),
    sa.Column('section', sa.String(length=5), nullable=True),
    sa.Column('parent_mobile', sa.String(length=20), nullable=True),
    sa.Column('parent_email', sa.String(length=120), nullable=True),
    sa.Column('category', sa.String(length=40), server_default='Regular', nullable=False),
    sa.Column('status', sa.String(length=20), server_default='Active', nullable=False),
    sa.Column('school_id', sa.String(length=10), nullable=True),
    sa.Column('campus', sa.String(length=30), nullable=True),
    sa.Column('academic_year', sa.String(length=10), nullable=True),
    sa.Column('synced_at', DT6, nullable=True),
    sa.PrimaryKeyConstraint('adm_no', name=op.f('pk_students')),
    sa.UniqueConstraint('erp_ref', name=op.f('uq_students_erp_ref'))
    )
    op.create_index(op.f('ix_students_parent_mobile'), 'students', ['parent_mobile'], unique=False)
    op.create_table('supplier_bills',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('supplier_id', sa.String(length=16), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('po_id', sa.BigInteger(), nullable=True),
    sa.Column('bill_no', sa.String(length=40), nullable=False),
    sa.Column('internal_no', sa.String(length=30), nullable=False),
    sa.Column('bill_date', sa.Date(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('taxable', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('gst', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('paid', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('adjusted', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_bills')),
    sa.UniqueConstraint('company', 'supplier_id', 'bill_no', name=op.f('uq_supplier_bills_company_supplier_id_bill_no'))
    )
    op.create_index(op.f('ix_supplier_bills_supplier_id'), 'supplier_bills', ['supplier_id'], unique=False)
    op.create_table('supplier_payments',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('supplier_id', sa.String(length=16), nullable=False),
    sa.Column('pay_date', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('mode', sa.String(length=10), nullable=False),
    sa.Column('reference', sa.String(length=60), nullable=True),
    sa.Column('applied', sa.JSON(), nullable=False),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_supplier_payments')),
    sa.UniqueConstraint('no', name=op.f('uq_supplier_payments_no'))
    )
    op.create_index(op.f('ix_supplier_payments_supplier_id'), 'supplier_payments', ['supplier_id'], unique=False)
    op.create_table('transfers',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('from_branch', sa.String(length=16), nullable=False),
    sa.Column('to_branch', sa.String(length=16), nullable=False),
    sa.Column('txn_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('value', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=True),
    sa.Column('requested_by', sa.String(length=16), nullable=False),
    sa.Column('dispatched_by', sa.String(length=16), nullable=True),
    sa.Column('dispatched_at', DT6, nullable=True),
    sa.Column('received_by', sa.String(length=16), nullable=True),
    sa.Column('received_at', DT6, nullable=True),
    sa.Column('receipt_note', sa.String(length=300), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transfers')),
    sa.UniqueConstraint('no', name=op.f('uq_transfers_no'))
    )
    op.create_table('approval_limits',
    sa.Column('role', sa.String(length=60), nullable=False),
    sa.Column('limit_key', sa.String(length=20), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['role'], ['roles.name'], name=op.f('fk_approval_limits_role_roles'), onupdate='CASCADE'),
    sa.PrimaryKeyConstraint('role', 'limit_key', name=op.f('pk_approval_limits'))
    )
    op.create_table('approval_log',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('approval_id', sa.BigInteger(), nullable=False),
    sa.Column('at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('user_name', sa.String(length=120), nullable=False),
    sa.Column('role', sa.String(length=60), nullable=True),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('remarks', sa.String(length=500), nullable=True),
    sa.ForeignKeyConstraint(['approval_id'], ['approvals.id'], name=op.f('fk_approval_log_approval_id_approvals'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_approval_log'))
    )
    op.create_index(op.f('ix_approval_log_approval_id'), 'approval_log', ['approval_id'], unique=False)
    op.create_table('credit_notes',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('invoice_id', sa.BigInteger(), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('cn_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('condition', sa.String(length=12), server_default='Good', nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('taxable', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('tax', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('refund_mode', sa.String(length=10), nullable=False),
    sa.Column('shift_id', sa.BigInteger(), nullable=True),
    sa.Column('approval_no', sa.String(length=20), nullable=True),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('created_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], name=op.f('fk_credit_notes_invoice_id_invoices')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_credit_notes')),
    sa.UniqueConstraint('no', name=op.f('uq_credit_notes_no'))
    )
    op.create_index(op.f('ix_credit_notes_invoice_id'), 'credit_notes', ['invoice_id'], unique=False)
    op.create_table('erp_sync_records',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('run_id', sa.BigInteger(), nullable=False),
    sa.Column('feed', sa.String(length=20), nullable=False),
    sa.Column('direction', sa.String(length=3), nullable=False),
    sa.Column('source_ref', sa.String(length=60), nullable=False),
    sa.Column('action', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=30), nullable=False),
    sa.Column('error', sa.String(length=500), nullable=True),
    sa.Column('snapshot', sa.JSON(), nullable=True),
    sa.Column('tries', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['run_id'], ['erp_sync_runs.id'], name=op.f('fk_erp_sync_records_run_id_erp_sync_runs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_erp_sync_records'))
    )
    op.create_index('ix_erp_sync_records_feed_src', 'erp_sync_records', ['feed', 'source_ref'], unique=False)
    op.create_index(op.f('ix_erp_sync_records_run_id'), 'erp_sync_records', ['run_id'], unique=False)
    op.create_index(op.f('ix_erp_sync_records_status'), 'erp_sync_records', ['status'], unique=False)
    op.create_table('goods_receipts',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('po_id', sa.BigInteger(), nullable=False),
    sa.Column('txn_date', sa.Date(), nullable=False),
    sa.Column('value', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('line_items', sa.JSON(), nullable=False),
    sa.Column('received_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], name=op.f('fk_goods_receipts_po_id_purchase_orders')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_goods_receipts')),
    sa.UniqueConstraint('no', name=op.f('uq_goods_receipts_no'))
    )
    op.create_index(op.f('ix_goods_receipts_po_id'), 'goods_receipts', ['po_id'], unique=False)
    op.create_table('invoice_lines',
    sa.Column('invoice_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('description', sa.String(length=200), nullable=False),
    sa.Column('hsn', sa.String(length=10), nullable=True),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('rate', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('disc_pct', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False),
    sa.Column('gst_rate', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False),
    sa.Column('taxable', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('tax', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('unit_cost', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column('delivered_qty', sa.Integer(), server_default='0', nullable=False),
    sa.Column('returned_qty', sa.Integer(), server_default='0', nullable=False),
    sa.Column('package_name', sa.String(length=160), nullable=True),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], name=op.f('fk_invoice_lines_invoice_id_invoices'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('invoice_id', 'line_no', name=op.f('pk_invoice_lines'))
    )
    op.create_table('invoice_payments',
    sa.Column('invoice_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('mode', sa.String(length=10), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('ref', sa.String(length=80), nullable=True),
    sa.ForeignKeyConstraint(['invoice_id'], ['invoices.id'], name=op.f('fk_invoice_payments_invoice_id_invoices'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('invoice_id', 'line_no', name=op.f('pk_invoice_payments'))
    )
    op.create_table('items',
    sa.Column('id', sa.String(length=24), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('category', sa.String(length=60), nullable=False),
    sa.Column('size', sa.String(length=20), nullable=True),
    sa.Column('colour', sa.String(length=30), nullable=True),
    sa.Column('gender', sa.String(length=10), nullable=True),
    sa.Column('class_name', sa.String(length=20), nullable=True),
    sa.Column('term', sa.String(length=20), nullable=True),
    sa.Column('academic_year', sa.String(length=10), nullable=True),
    sa.Column('school_id', sa.String(length=10), nullable=True),
    sa.Column('hsn', sa.String(length=10), nullable=True),
    sa.Column('gst_rate', sa.Numeric(precision=5, scale=2), server_default='0', nullable=False),
    sa.Column('price', sa.Numeric(precision=12, scale=2), server_default='0', nullable=False),
    sa.Column('cost', sa.Numeric(precision=12, scale=4), server_default='0', nullable=False),
    sa.Column('barcode', sa.String(length=30), nullable=True),
    sa.Column('isbn', sa.String(length=13), nullable=True),
    sa.Column('isbn13', sa.String(length=13), nullable=True),
    sa.Column('isbn_na_reason', sa.String(length=60), nullable=True),
    sa.Column('supplier_id', sa.String(length=16), nullable=True),
    sa.Column('reorder_level', sa.Integer(), server_default='0', nullable=False),
    sa.Column('bin_location', sa.String(length=20), nullable=True),
    sa.Column('unpriced', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['company'], ['companies.code'], name=op.f('fk_items_company_companies')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_items')),
    sa.UniqueConstraint('company', 'barcode', name=op.f('uq_items_company_barcode')),
    sa.UniqueConstraint('company', 'isbn13', name=op.f('uq_items_company_isbn13'))
    )
    op.create_index('ix_items_company_name', 'items', ['company', 'name'], unique=False)
    op.create_table('journal_lines',
    sa.Column('journal_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('account', sa.String(length=10), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('party', sa.String(length=30), nullable=True),
    sa.Column('debit', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('credit', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.CheckConstraint('debit >= 0 AND credit >= 0 AND NOT (debit > 0 AND credit > 0)', name=op.f('ck_journal_lines_one_side')),
    sa.ForeignKeyConstraint(['journal_id'], ['journals.id'], name=op.f('fk_journal_lines_journal_id_journals'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('journal_id', 'line_no', name=op.f('pk_journal_lines'))
    )
    op.create_index('ix_journal_lines_account_branch', 'journal_lines', ['account', 'branch_id'], unique=False)
    op.create_index('ix_journal_lines_party', 'journal_lines', ['party'], unique=False)
    op.create_table('online_order_lines',
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('rate', sa.Numeric(precision=12, scale=2), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('picked_qty', sa.Integer(), server_default='0', nullable=False),
    sa.Column('collected_qty', sa.Integer(), server_default='0', nullable=False),
    sa.Column('returned_qty', sa.Integer(), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['online_orders.id'], name=op.f('fk_online_order_lines_order_id_online_orders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('order_id', 'line_no', name=op.f('pk_online_order_lines'))
    )
    op.create_table('order_requests',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('order_id', sa.BigInteger(), nullable=False),
    sa.Column('type', sa.String(length=12), nullable=False),
    sa.Column('line_items', sa.JSON(), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('decided_by', sa.String(length=16), nullable=True),
    sa.Column('decision_note', sa.String(length=300), nullable=True),
    sa.Column('credit_note_no', sa.String(length=30), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['order_id'], ['online_orders.id'], name=op.f('fk_order_requests_order_id_online_orders')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_order_requests')),
    sa.UniqueConstraint('no', name=op.f('uq_order_requests_no'))
    )
    op.create_index(op.f('ix_order_requests_order_id'), 'order_requests', ['order_id'], unique=False)
    op.create_table('package_lines',
    sa.Column('package_id', sa.String(length=24), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=True),
    sa.Column('category', sa.String(length=60), nullable=True),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['package_id'], ['packages.id'], name=op.f('fk_package_lines_package_id_packages'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('package_id', 'line_no', name=op.f('pk_package_lines'))
    )
    op.create_table('permissions',
    sa.Column('role', sa.String(length=60), nullable=False),
    sa.Column('screen_key', sa.String(length=40), nullable=False),
    sa.Column('action', sa.String(length=12), nullable=False),
    sa.ForeignKeyConstraint(['role'], ['roles.name'], name=op.f('fk_permissions_role_roles'), onupdate='CASCADE'),
    sa.PrimaryKeyConstraint('role', 'screen_key', 'action', name=op.f('pk_permissions'))
    )
    op.create_table('purchase_order_lines',
    sa.Column('po_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('rate', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('received_qty', sa.Integer(), server_default='0', nullable=False),
    sa.Column('rejected_qty', sa.Integer(), server_default='0', nullable=False),
    sa.Column('returned_qty', sa.Integer(), server_default='0', nullable=False),
    sa.Column('landed_rate', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], name=op.f('fk_purchase_order_lines_po_id_purchase_orders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('po_id', 'line_no', name=op.f('pk_purchase_order_lines'))
    )
    op.create_table('purchase_quotes',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('po_id', sa.BigInteger(), nullable=False),
    sa.Column('supplier_id', sa.String(length=16), nullable=False),
    sa.Column('rates', sa.JSON(), nullable=False),
    sa.Column('lead_days', sa.Integer(), nullable=True),
    sa.Column('selected', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.ForeignKeyConstraint(['po_id'], ['purchase_orders.id'], name=op.f('fk_purchase_quotes_po_id_purchase_orders'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_purchase_quotes'))
    )
    op.create_index(op.f('ix_purchase_quotes_po_id'), 'purchase_quotes', ['po_id'], unique=False)
    op.create_table('routing_rules',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('txn_type', sa.String(length=30), nullable=False),
    sa.Column('location', sa.String(length=10), nullable=True),
    sa.Column('up_to', sa.Numeric(precision=16, scale=2), nullable=True),
    sa.Column('role', sa.String(length=60), nullable=False),
    sa.ForeignKeyConstraint(['role'], ['roles.name'], name=op.f('fk_routing_rules_role_roles'), onupdate='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_routing_rules'))
    )
    op.create_table('rzp_refunds',
    sa.Column('id', sa.String(length=40), nullable=False),
    sa.Column('payment_id', sa.String(length=40), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('requested_by', sa.String(length=16), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['payment_id'], ['rzp_payments.id'], name=op.f('fk_rzp_refunds_payment_id_rzp_payments')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rzp_refunds'))
    )
    op.create_index(op.f('ix_rzp_refunds_payment_id'), 'rzp_refunds', ['payment_id'], unique=False)
    op.create_table('stock_adjustment_lines',
    sa.Column('adjustment_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('unit_cost', sa.Numeric(precision=12, scale=4), nullable=False),
    sa.ForeignKeyConstraint(['adjustment_id'], ['stock_adjustments.id'], name=op.f('fk_stock_adjustment_lines_adjustment_id_stock_adjustments'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('adjustment_id', 'line_no', name=op.f('pk_stock_adjustment_lines'))
    )
    op.create_table('suppliers',
    sa.Column('id', sa.String(length=16), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('gstin', sa.String(length=15), nullable=True),
    sa.Column('city', sa.String(length=80), nullable=True),
    sa.Column('terms_days', sa.Integer(), server_default='30', nullable=False),
    sa.Column('contact', sa.String(length=160), nullable=True),
    sa.Column('bank_name', sa.String(length=120), nullable=True),
    sa.Column('bank_account', sa.String(length=40), nullable=True),
    sa.Column('bank_ifsc', sa.String(length=11), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['company'], ['companies.code'], name=op.f('fk_suppliers_company_companies')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_suppliers')),
    sa.UniqueConstraint('company', 'name', name=op.f('uq_suppliers_company_name'))
    )
    op.create_table('transfer_lines',
    sa.Column('transfer_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('received_qty', sa.Integer(), nullable=True),
    sa.Column('damaged_qty', sa.Integer(), nullable=True),
    sa.Column('unit_cost', sa.Numeric(precision=12, scale=4), nullable=True),
    sa.ForeignKeyConstraint(['transfer_id'], ['transfers.id'], name=op.f('fk_transfer_lines_transfer_id_transfers'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('transfer_id', 'line_no', name=op.f('pk_transfer_lines'))
    )
    op.create_table('users',
    sa.Column('id', sa.String(length=16), nullable=False),
    sa.Column('emp_id', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('mobile', sa.String(length=20), nullable=False),
    sa.Column('email', sa.String(length=120), nullable=True),
    sa.Column('department', sa.String(length=80), nullable=True),
    sa.Column('designation', sa.String(length=120), nullable=True),
    sa.Column('main_role', sa.String(length=60), nullable=False),
    sa.Column('manager_id', sa.String(length=16), nullable=True),
    sa.Column('status', sa.String(length=12), server_default='Active', nullable=False),
    sa.Column('join_date', sa.Date(), nullable=True),
    sa.Column('relieve_date', sa.Date(), nullable=True),
    sa.Column('valid_from', sa.Date(), nullable=True),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('all_branches', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('pw_hash', sa.String(length=100), nullable=True),
    sa.Column('pw_set_at', DT6, nullable=True),
    sa.Column('pw_must_change', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('failed_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('locked_at', DT6, nullable=True),
    sa.Column('lock_reason', sa.String(length=200), nullable=True),
    sa.Column('last_login_at', DT6, nullable=True),
    sa.Column('last_login_device', sa.String(length=160), nullable=True),
    sa.Column('last_login_ip', sa.String(length=45), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['main_role'], ['roles.name'], name=op.f('fk_users_main_role_roles'), onupdate='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('emp_id', name=op.f('uq_users_emp_id')),
    sa.UniqueConstraint('mobile', name=op.f('uq_users_mobile')),
    sa.UniqueConstraint('name', name=op.f('uq_users_name'))
    )
    op.create_table('zones',
    sa.Column('id', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('region_id', sa.String(length=10), nullable=False),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_zones_region_id_regions')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_zones'))
    )
    op.create_table('access_schedule',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('change_type', sa.String(length=12), nullable=False),
    sa.Column('effective_on', sa.Date(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=True),
    sa.Column('reason', sa.String(length=300), nullable=False),
    sa.Column('status', sa.String(length=20), server_default='Scheduled', nullable=False),
    sa.Column('requested_by', sa.String(length=16), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('applied_at', DT6, nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_access_schedule_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_access_schedule'))
    )
    op.create_index('ix_access_schedule_due', 'access_schedule', ['status', 'effective_on'], unique=False)
    op.create_table('credit_note_lines',
    sa.Column('credit_note_id', sa.BigInteger(), nullable=False),
    sa.Column('line_no', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('invoice_line_no', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.String(length=24), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=14, scale=2), nullable=False),
    sa.Column('restocked', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['credit_note_id'], ['credit_notes.id'], name=op.f('fk_credit_note_lines_credit_note_id_credit_notes'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('credit_note_id', 'line_no', name=op.f('pk_credit_note_lines'))
    )
    op.create_table('locations',
    sa.Column('code', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('public_name', sa.String(length=120), nullable=True),
    sa.Column('type', sa.String(length=20), nullable=False),
    sa.Column('zone_id', sa.String(length=10), nullable=True),
    sa.Column('address', sa.String(length=300), nullable=True),
    sa.Column('contact', sa.String(length=120), nullable=True),
    sa.Column('is_central_wh', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('closed_on', sa.Date(), nullable=True),
    sa.Column('closed_reason', sa.String(length=300), nullable=True),
    sa.Column('stock_moved_to', sa.String(length=10), nullable=True),
    sa.ForeignKeyConstraint(['zone_id'], ['zones.id'], name=op.f('fk_locations_zone_id_zones')),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_locations'))
    )
    op.create_table('login_challenges',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('purpose', sa.String(length=10), nullable=False),
    sa.Column('otp_hash', sa.String(length=64), nullable=False),
    sa.Column('tries', sa.Integer(), server_default='0', nullable=False),
    sa.Column('stage', sa.String(length=10), server_default='otp', nullable=False),
    sa.Column('expires_at', DT6, nullable=False),
    sa.Column('used_at', DT6, nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('device', sa.String(length=160), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_login_challenges_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_login_challenges'))
    )
    op.create_index(op.f('ix_login_challenges_user_id'), 'login_challenges', ['user_id'], unique=False)
    op.create_table('password_history',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('pw_hash', sa.String(length=100), nullable=False),
    sa.Column('set_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_password_history_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_password_history'))
    )
    op.create_index(op.f('ix_password_history_user_id'), 'password_history', ['user_id'], unique=False)
    op.create_table('sessions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=True),
    sa.Column('branch_id', sa.String(length=16), nullable=True),
    sa.Column('device', sa.String(length=160), nullable=True),
    sa.Column('device_id', sa.String(length=80), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('last_seen', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.Column('reauth_at', DT6, nullable=True),
    sa.Column('ended_at', DT6, nullable=True),
    sa.Column('end_reason', sa.String(length=120), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_sessions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sessions')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_sessions_token_hash'))
    )
    op.create_index('ix_sessions_user_ended', 'sessions', ['user_id', 'ended_at'], unique=False)
    op.create_table('user_companies',
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.ForeignKeyConstraint(['company'], ['companies.code'], name=op.f('fk_user_companies_company_companies')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_companies_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'company', name=op.f('pk_user_companies'))
    )
    op.create_table('user_devices',
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('device_id', sa.String(length=80), nullable=False),
    sa.Column('first_seen', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_devices_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'device_id', name=op.f('pk_user_devices'))
    )
    op.create_table('user_regions',
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('region_id', sa.String(length=10), nullable=False),
    sa.ForeignKeyConstraint(['region_id'], ['regions.id'], name=op.f('fk_user_regions_region_id_regions')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_regions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'region_id', name=op.f('pk_user_regions'))
    )
    op.create_table('user_roles',
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('role', sa.String(length=60), nullable=False),
    sa.ForeignKeyConstraint(['role'], ['roles.name'], name=op.f('fk_user_roles_role_roles'), onupdate='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_roles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'role', name=op.f('pk_user_roles'))
    )
    op.create_table('user_zones',
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('zone_id', sa.String(length=10), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_zones_user_id_users'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['zone_id'], ['zones.id'], name=op.f('fk_user_zones_zone_id_zones')),
    sa.PrimaryKeyConstraint('user_id', 'zone_id', name=op.f('pk_user_zones'))
    )
    op.create_table('branches',
    sa.Column('id', sa.String(length=16), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('location', sa.String(length=10), nullable=False),
    sa.Column('code', sa.String(length=10), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.ForeignKeyConstraint(['company'], ['companies.code'], name=op.f('fk_branches_company_companies')),
    sa.ForeignKeyConstraint(['location'], ['locations.code'], name=op.f('fk_branches_location_locations')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_branches')),
    sa.UniqueConstraint('company', 'location', name=op.f('uq_branches_company_location'))
    )
    op.create_table('user_branches',
    sa.Column('user_id', sa.String(length=16), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], name=op.f('fk_user_branches_branch_id_branches')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_branches_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'branch_id', name=op.f('pk_user_branches'))
    )
    # ### end Alembic commands ###


def downgrade() -> None:
    # Drop every table (indexes go with their tables), children before parents.
    op.drop_table('user_branches')
    op.drop_table('branches')
    op.drop_table('user_zones')
    op.drop_table('user_roles')
    op.drop_table('user_regions')
    op.drop_table('user_devices')
    op.drop_table('user_companies')
    op.drop_table('sessions')
    op.drop_table('password_history')
    op.drop_table('login_challenges')
    op.drop_table('locations')
    op.drop_table('credit_note_lines')
    op.drop_table('access_schedule')
    op.drop_table('zones')
    op.drop_table('users')
    op.drop_table('transfer_lines')
    op.drop_table('suppliers')
    op.drop_table('stock_adjustment_lines')
    op.drop_table('rzp_refunds')
    op.drop_table('routing_rules')
    op.drop_table('purchase_quotes')
    op.drop_table('purchase_order_lines')
    op.drop_table('permissions')
    op.drop_table('package_lines')
    op.drop_table('order_requests')
    op.drop_table('online_order_lines')
    op.drop_table('journal_lines')
    op.drop_table('items')
    op.drop_table('invoice_payments')
    op.drop_table('invoice_lines')
    op.drop_table('goods_receipts')
    op.drop_table('erp_sync_records')
    op.drop_table('credit_notes')
    op.drop_table('approval_log')
    op.drop_table('approval_limits')
    op.drop_table('transfers')
    op.drop_table('supplier_payments')
    op.drop_table('supplier_bills')
    op.drop_table('students')
    op.drop_table('stock_ledger')
    op.drop_table('stock_balance')
    op.drop_table('stock_adjustments')
    op.drop_table('settings')
    op.drop_table('security_events')
    op.drop_table('schools')
    op.drop_table('rzp_webhook_events')
    op.drop_table('rzp_settlements')
    op.drop_table('rzp_payments')
    op.drop_table('rzp_exceptions')
    op.drop_table('roles')
    op.drop_table('regions')
    op.drop_table('refunds')
    op.drop_table('receipts')
    op.drop_table('purchase_orders')
    op.drop_table('payment_links')
    op.drop_table('parent_sessions')
    op.drop_table('parent_challenges')
    op.drop_table('packages')
    op.drop_table('owner_instructions')
    op.drop_table('outbox')
    op.drop_table('online_orders')
    op.drop_table('number_series')
    op.drop_table('msea_vouchers')
    op.drop_table('journals')
    op.drop_table('item_categories')
    op.drop_table('invoices')
    op.drop_table('inter_company')
    op.drop_table('held_bills')
    op.drop_table('evidence')
    op.drop_table('erp_sync_runs')
    op.drop_table('erp_keys')
    op.drop_table('debit_notes')
    op.drop_table('customers')
    op.drop_table('companies')
    op.drop_table('cash_shifts')
    op.drop_table('cash_handovers')
    op.drop_table('audit_log')
    op.drop_table('audit_head')
    op.drop_table('approvals')
    op.drop_table('alerts')
    op.drop_table('alert_rules')
    op.drop_table('accounts')
    op.drop_table('access_log')
    # ### end Alembic commands ###
