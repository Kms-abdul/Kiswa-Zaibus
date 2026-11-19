"""Release 2.3: Finance & tax module (financial years, cost centres, GST / TDS masters, finance vouchers, GST and TDS compliance,
banking, fixed assets, budgets, recurring journals, compliance calendar) and stock-audit approval by the Director / CFO with
impact review, partial approval and automatic posting (stock_audit_decisions; decision fields on stock_audit_lines);
cost centre and department on journal lines; finance accounts, roles, permissions and settings (services/fin_bootstrap.py)

Revision ID: 0005
Revises: 0004
Create Date: (release 2.3) 2026-11-10 17:45:42.452860+05:30
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from kz.services.fin_bootstrap import bootstrap, unbootstrap

DT6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql', 'mariadb')

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Tables generated from kz/models/finance.py with `alembic revision --autogenerate`, then reviewed.
    op.create_table('asset_categories',
    sa.Column('id', sa.String(length=6), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('account', sa.String(length=10), nullable=False),
    sa.Column('life_years', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('it_rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_asset_categories'))
    )
    op.create_table('bank_accounts',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('account', sa.String(length=10), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('ifsc', sa.String(length=11), nullable=True),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_accounts'))
    )
    op.create_table('bank_statement_lines',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('account', sa.String(length=10), nullable=False),
    sa.Column('sdate', sa.Date(), nullable=False),
    sa.Column('narration', sa.String(length=300), nullable=False),
    sa.Column('ref', sa.String(length=60), nullable=True),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('balance', sa.Numeric(precision=16, scale=2), nullable=True),
    sa.Column('match_journal', sa.String(length=30), nullable=True),
    sa.Column('match_line', sa.Integer(), nullable=True),
    sa.Column('how', sa.String(length=60), nullable=True),
    sa.Column('source', sa.String(length=80), nullable=False),
    sa.Column('uniq', sa.String(length=64), nullable=False),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_bank_statement_lines')),
    sa.UniqueConstraint('uniq', name=op.f('uq_bank_statement_lines_uniq'))
    )
    op.create_index('ix_bank_statement_lines_acct', 'bank_statement_lines', ['company', 'account', 'sdate'], unique=False)
    op.create_table('budgets',
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('fy', sa.String(length=7), nullable=False),
    sa.Column('account', sa.String(length=10), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.PrimaryKeyConstraint('company', 'fy', 'account', name=op.f('pk_budgets'))
    )
    op.create_table('cheques',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('account', sa.String(length=10), nullable=False),
    sa.Column('cheque_no', sa.String(length=12), nullable=False),
    sa.Column('cdate', sa.Date(), nullable=False),
    sa.Column('party', sa.String(length=160), nullable=True),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('status', sa.String(length=14), nullable=False),
    sa.Column('voucher_no', sa.String(length=30), nullable=False),
    sa.Column('log', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cheques'))
    )
    op.create_table('compliance_status',
    sa.Column('key', sa.String(length=80), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('ref', sa.String(length=80), nullable=True),
    sa.Column('note', sa.String(length=300), nullable=True),
    sa.Column('done_on', sa.Date(), nullable=True),
    sa.Column('owner', sa.String(length=16), nullable=True),
    sa.Column('updated_by', sa.String(length=16), nullable=True),
    sa.PrimaryKeyConstraint('key', name=op.f('pk_compliance_status'))
    )
    op.create_table('cost_centres',
    sa.Column('id', sa.String(length=12), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('kind', sa.String(length=14), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_cost_centres'))
    )
    op.create_table('depreciation_runs',
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('period', sa.String(length=7), nullable=False),
    sa.Column('voucher_no', sa.String(length=30), nullable=True),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.PrimaryKeyConstraint('company', 'period', name=op.f('pk_depreciation_runs'))
    )
    op.create_table('einvoices',
    sa.Column('doc_no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('irn', sa.String(length=64), nullable=True),
    sa.Column('ack_no', sa.String(length=20), nullable=True),
    sa.Column('ack_at', DT6, nullable=True),
    sa.Column('status', sa.String(length=12), server_default='Pending', nullable=False),
    sa.Column('signed_qr', sa.Text(), nullable=True),
    sa.Column('cancel_reason', sa.String(length=120), nullable=True),
    sa.Column('ewb', sa.JSON(), nullable=True),
    sa.Column('local', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('doc_no', name=op.f('pk_einvoices'))
    )
    op.create_table('employees',
    sa.Column('id', sa.String(length=16), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('pan', sa.String(length=10), nullable=True),
    sa.Column('gross', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('pf', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('esi', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('joined', sa.Date(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_employees'))
    )
    op.create_table('fin_approval_rules',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('vtype', sa.String(length=3), server_default='*', nullable=False),
    sa.Column('account', sa.String(length=10), nullable=True),
    sa.Column('branch_id', sa.String(length=16), nullable=True),
    sa.Column('above', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('role', sa.String(length=60), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fin_approval_rules'))
    )
    op.create_table('fin_vouchers',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('vtype', sa.String(length=3), nullable=False),
    sa.Column('vdate', sa.Date(), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('cost_centre', sa.String(length=12), nullable=True),
    sa.Column('dept', sa.String(length=12), nullable=True),
    sa.Column('ref', sa.String(length=60), nullable=True),
    sa.Column('party_kind', sa.String(length=4), nullable=True),
    sa.Column('party_id', sa.String(length=30), nullable=True),
    sa.Column('party_name', sa.String(length=160), nullable=True),
    sa.Column('gstin', sa.String(length=15), nullable=True),
    sa.Column('narration', sa.String(length=500), nullable=False),
    sa.Column('entered', sa.JSON(), nullable=False),
    sa.Column('gst', sa.JSON(), nullable=True),
    sa.Column('tds', sa.JSON(), nullable=True),
    sa.Column('built', sa.JSON(), nullable=False),
    sa.Column('options', sa.JSON(), nullable=True),
    sa.Column('taxable', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('tax', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('tds_amount', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('total', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('paid', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=18), server_default='Draft', nullable=False),
    sa.Column('maker', sa.String(length=16), nullable=False),
    sa.Column('maker_role', sa.String(length=60), nullable=True),
    sa.Column('approval_no', sa.String(length=20), nullable=True),
    sa.Column('approver_role', sa.String(length=60), nullable=True),
    sa.Column('approved_by', sa.String(length=16), nullable=True),
    sa.Column('approved_at', DT6, nullable=True),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('posted_at', DT6, nullable=True),
    sa.Column('reversal_of', sa.String(length=30), nullable=True),
    sa.Column('reversed_by', sa.String(length=30), nullable=True),
    sa.Column('reason', sa.String(length=500), nullable=True),
    sa.Column('created_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fin_vouchers')),
    sa.UniqueConstraint('company', 'no', name=op.f('uq_fin_vouchers_company_no'))
    )
    op.create_index('ix_fin_vouchers_company_date', 'fin_vouchers', ['company', 'vdate'], unique=False)
    op.create_index(op.f('ix_fin_vouchers_party_id'), 'fin_vouchers', ['party_id'], unique=False)
    op.create_index(op.f('ix_fin_vouchers_ref'), 'fin_vouchers', ['ref'], unique=False)
    op.create_table('fin_years',
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('code', sa.String(length=7), nullable=False),
    sa.Column('from_date', sa.Date(), nullable=False),
    sa.Column('to_date', sa.Date(), nullable=False),
    sa.Column('status', sa.String(length=8), server_default='Open', nullable=False),
    sa.Column('closed_by', sa.String(length=16), nullable=True),
    sa.Column('closed_at', DT6, nullable=True),
    sa.PrimaryKeyConstraint('company', 'code', name=op.f('pk_fin_years'))
    )
    op.create_table('fixed_assets',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('no', sa.String(length=20), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('category', sa.String(length=6), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('tag', sa.String(length=20), nullable=False),
    sa.Column('put_to_use', sa.Date(), nullable=False),
    sa.Column('cost', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('residual_pct', sa.Numeric(precision=5, scale=2), server_default='5', nullable=False),
    sa.Column('method', sa.String(length=4), server_default='SLM', nullable=False),
    sa.Column('life_years', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('acc_dep', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('impairment', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('disposed_on', sa.Date(), nullable=True),
    sa.Column('voucher_no', sa.String(length=30), nullable=True),
    sa.Column('moves', sa.JSON(), nullable=False),
    sa.Column('verifications', sa.JSON(), nullable=False),
    sa.Column('dep_log', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fixed_assets')),
    sa.UniqueConstraint('no', name=op.f('uq_fixed_assets_no'))
    )
    op.create_table('gst_challans',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('period', sa.String(length=7), nullable=False),
    sa.Column('cpin', sa.String(length=14), nullable=True),
    sa.Column('cin', sa.String(length=25), nullable=True),
    sa.Column('cdate', sa.Date(), nullable=False),
    sa.Column('bank_account', sa.String(length=10), nullable=False),
    sa.Column('cash', sa.JSON(), nullable=False),
    sa.Column('used', sa.JSON(), nullable=False),
    sa.Column('interest', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('late_fee', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('voucher_no', sa.String(length=30), nullable=True),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_gst_challans'))
    )
    op.create_table('gst_filings',
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('ret', sa.String(length=3), nullable=False),
    sa.Column('period', sa.String(length=7), nullable=False),
    sa.Column('filed_on', sa.Date(), nullable=False),
    sa.Column('arn', sa.String(length=20), nullable=False),
    sa.Column('filed_by', sa.String(length=16), nullable=False),
    sa.PrimaryKeyConstraint('company', 'ret', 'period', name=op.f('pk_gst_filings'))
    )
    op.create_table('gst_portal_rows',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('kind', sa.String(length=2), nullable=False),
    sa.Column('period', sa.String(length=7), nullable=False),
    sa.Column('gstin', sa.String(length=15), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=True),
    sa.Column('inv_no', sa.String(length=40), nullable=False),
    sa.Column('inv_date', sa.Date(), nullable=True),
    sa.Column('taxable', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('igst', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('cgst', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('sgst', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('cess', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('source', sa.String(length=60), nullable=False),
    sa.Column('loaded_at', DT6, server_default=sa.text('CURRENT_TIMESTAMP(6)'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_gst_portal_rows'))
    )
    op.create_index('ix_gst_portal_rows_key', 'gst_portal_rows', ['company', 'kind', 'period'], unique=False)
    op.create_table('gst_registrations',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('state', sa.String(length=2), nullable=False),
    sa.Column('gstin', sa.String(length=15), nullable=False),
    sa.Column('from_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.String(length=12), server_default='Regular', nullable=False),
    sa.Column('einvoice', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_gst_registrations')),
    sa.UniqueConstraint('gstin', name=op.f('uq_gst_registrations_gstin'))
    )
    op.create_table('hsn_codes',
    sa.Column('code', sa.String(length=8), nullable=False),
    sa.Column('kind', sa.String(length=3), nullable=False),
    sa.Column('description', sa.String(length=160), nullable=False),
    sa.Column('rate', sa.Numeric(precision=5, scale=2), nullable=False),
    sa.Column('uqc', sa.String(length=4), server_default='NOS', nullable=False),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_hsn_codes'))
    )
    op.create_table('party_tax',
    sa.Column('party', sa.String(length=30), nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('pan', sa.String(length=10), nullable=True),
    sa.Column('resident', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('trc', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('section', sa.String(length=10), nullable=True),
    sa.Column('ldc', sa.JSON(), nullable=True),
    sa.Column('priority', sa.String(length=6), server_default='Normal', nullable=False),
    sa.Column('related', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.PrimaryKeyConstraint('party', name=op.f('pk_party_tax'))
    )
    op.create_table('recurring_journals',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('vtype', sa.String(length=3), nullable=False),
    sa.Column('day', sa.Integer(), nullable=False),
    sa.Column('next_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=False),
    sa.Column('narration', sa.String(length=300), nullable=False),
    sa.Column('lines', sa.JSON(), nullable=False),
    sa.Column('party_id', sa.String(length=30), nullable=True),
    sa.Column('tds_section', sa.String(length=10), nullable=True),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.Column('last_run', sa.Date(), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recurring_journals'))
    )
    op.create_table('stock_audit_decisions',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('audit_id', sa.BigInteger(), nullable=False),
    sa.Column('approval_no', sa.String(length=20), nullable=True),
    sa.Column('mode', sa.String(length=40), nullable=False),
    sa.Column('decided_by', sa.String(length=16), nullable=False),
    sa.Column('decided_role', sa.String(length=60), nullable=False),
    sa.Column('decided_at', DT6, nullable=False),
    sa.Column('remarks', sa.String(length=500), nullable=True),
    sa.Column('adjustment_no', sa.String(length=30), nullable=True),
    sa.Column('journal_no', sa.String(length=30), nullable=True),
    sa.Column('shortage', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('damaged', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('excess', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('net', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('approved', sa.JSON(), nullable=False),
    sa.Column('rejected', sa.JSON(), nullable=False),
    sa.Column('recount', sa.JSON(), nullable=False),
    sa.Column('held', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_stock_audit_decisions'))
    )
    op.create_index(op.f('ix_stock_audit_decisions_audit_id'), 'stock_audit_decisions', ['audit_id'], unique=False)
    op.create_table('tds_certificates',
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('fy', sa.String(length=7), nullable=False),
    sa.Column('quarter', sa.String(length=2), nullable=False),
    sa.Column('party', sa.String(length=30), nullable=False),
    sa.Column('cert_no', sa.String(length=20), nullable=False),
    sa.Column('issued_on', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('issued_by', sa.String(length=16), nullable=False),
    sa.PrimaryKeyConstraint('company', 'fy', 'quarter', 'party', name=op.f('pk_tds_certificates'))
    )
    op.create_table('tds_challans',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('bsr', sa.String(length=7), nullable=False),
    sa.Column('serial', sa.String(length=5), nullable=False),
    sa.Column('cdate', sa.Date(), nullable=False),
    sa.Column('cin', sa.String(length=25), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('interest', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('sections', sa.JSON(), nullable=False),
    sa.Column('voucher_no', sa.String(length=30), nullable=True),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tds_challans'))
    )
    op.create_table('tds_entries',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('voucher_no', sa.String(length=30), nullable=False),
    sa.Column('ded_date', sa.Date(), nullable=False),
    sa.Column('doc_date', sa.Date(), nullable=False),
    sa.Column('party', sa.String(length=30), nullable=False),
    sa.Column('name', sa.String(length=160), nullable=False),
    sa.Column('pan', sa.String(length=10), nullable=True),
    sa.Column('section', sa.String(length=10), nullable=False),
    sa.Column('base', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('rate', sa.Numeric(precision=7, scale=3), nullable=False),
    sa.Column('amount', sa.Numeric(precision=16, scale=2), nullable=False),
    sa.Column('kind', sa.String(length=10), nullable=False),
    sa.Column('challan_id', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.String(length=14), nullable=False),
    sa.Column('branch_id', sa.String(length=16), nullable=False),
    sa.Column('note', sa.String(length=200), nullable=True),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_tds_entries'))
    )
    op.create_index('ix_tds_entries_company_date', 'tds_entries', ['company', 'ded_date'], unique=False)
    op.create_index(op.f('ix_tds_entries_party'), 'tds_entries', ['party'], unique=False)
    op.create_index(op.f('ix_tds_entries_voucher_no'), 'tds_entries', ['voucher_no'], unique=False)
    op.create_table('tds_returns',
    sa.Column('company', sa.String(length=4), nullable=False),
    sa.Column('fy', sa.String(length=7), nullable=False),
    sa.Column('form', sa.String(length=5), nullable=False),
    sa.Column('quarter', sa.String(length=2), nullable=False),
    sa.Column('filed_on', sa.Date(), nullable=False),
    sa.Column('token', sa.String(length=15), nullable=False),
    sa.Column('fee', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('filed_by', sa.String(length=16), nullable=False),
    sa.PrimaryKeyConstraint('company', 'fy', 'form', 'quarter', name=op.f('pk_tds_returns'))
    )
    op.create_table('tds_sections',
    sa.Column('code', sa.String(length=10), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('rate_ind', sa.Numeric(precision=7, scale=3), nullable=False),
    sa.Column('rate_other', sa.Numeric(precision=7, scale=3), nullable=False),
    sa.Column('single_limit', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('year_limit', sa.Numeric(precision=16, scale=2), server_default='0', nullable=False),
    sa.Column('monthly', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('slab', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('non_resident', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('tcs', sa.Boolean(), server_default=sa.text('0'), nullable=False),
    sa.Column('no_pan_rate', sa.Numeric(precision=7, scale=3), nullable=True),
    sa.Column('stage', sa.String(length=8), server_default='invoice', nullable=False),
    sa.Column('form', sa.String(length=5), nullable=False),
    sa.Column('new_ref', sa.String(length=30), nullable=True),
    sa.Column('effective_from', sa.Date(), nullable=False),
    sa.Column('active', sa.Boolean(), server_default=sa.text('1'), nullable=False),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_tds_sections'))
    )
    op.add_column('journal_lines', sa.Column('cost_centre', sa.String(length=12), nullable=True))
    op.add_column('journal_lines', sa.Column('dept', sa.String(length=12), nullable=True))
    op.add_column('stock_audit_lines', sa.Column('dmg_qty', sa.Integer(), nullable=True))
    op.add_column('stock_audit_lines', sa.Column('decision', sa.String(length=10), nullable=True))
    op.add_column('stock_audit_lines', sa.Column('decided_by', sa.String(length=16), nullable=True))
    op.add_column('stock_audit_lines', sa.Column('decided_at', DT6, nullable=True))
    op.add_column('stock_audit_lines', sa.Column('recount', sa.Boolean(), server_default=sa.text('0'), nullable=False))
    op.add_column('stock_audit_lines', sa.Column('adjustment_no', sa.String(length=30), nullable=True))
    bootstrap(op.get_bind())   # reference data for existing databases (fresh installs get it from the seed)


def downgrade() -> None:
    unbootstrap(op.get_bind())
    op.drop_column('stock_audit_lines', 'adjustment_no')
    op.drop_column('stock_audit_lines', 'recount')
    op.drop_column('stock_audit_lines', 'decided_at')
    op.drop_column('stock_audit_lines', 'decided_by')
    op.drop_column('stock_audit_lines', 'decision')
    op.drop_column('stock_audit_lines', 'dmg_qty')
    op.drop_column('journal_lines', 'dept')
    op.drop_column('journal_lines', 'cost_centre')
    op.drop_table('tds_sections')
    op.drop_table('tds_returns')
    op.drop_table('tds_entries')
    op.drop_table('tds_challans')
    op.drop_table('tds_certificates')
    op.drop_table('stock_audit_decisions')
    op.drop_table('recurring_journals')
    op.drop_table('party_tax')
    op.drop_table('hsn_codes')
    op.drop_table('gst_registrations')
    op.drop_table('gst_portal_rows')
    op.drop_table('gst_filings')
    op.drop_table('gst_challans')
    op.drop_table('fixed_assets')
    op.drop_table('fin_years')
    op.drop_table('fin_vouchers')
    op.drop_table('fin_approval_rules')
    op.drop_table('employees')
    op.drop_table('einvoices')
    op.drop_table('depreciation_runs')
    op.drop_table('cost_centres')
    op.drop_table('compliance_status')
    op.drop_table('cheques')
    op.drop_table('budgets')
    op.drop_table('bank_statement_lines')
    op.drop_table('bank_accounts')
    op.drop_table('asset_categories')
    # ### end Alembic commands ###
