"""Finance & tax (release 2.3): financial years, cost centres, GST and TDS masters, finance vouchers, GST and TDS compliance
records, banking, fixed assets, budgets, recurring journals and the compliance calendar.

Every voucher still posts one balanced journal through services/ledger.post(); these tables hold the documents, tax
detail and compliance status around it. Nothing here is ever deleted once posted or filed."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import DT6, Base, created_at

RATE = Numeric(7, 3)


class FinYear(Base):
    __tablename__ = 'fin_years'
    company: Mapped[str] = mapped_column(String(4), primary_key=True)
    code: Mapped[str] = mapped_column(String(7), primary_key=True)          # 2026-27
    from_date: Mapped[dt.date]
    to_date: Mapped[dt.date]
    status: Mapped[str] = mapped_column(String(8), server_default='Open')   # Open Closed
    closed_by: Mapped[Optional[str]] = mapped_column(String(16))
    closed_at: Mapped[Optional[dt.datetime]] = mapped_column(DT6)
    closing_journal: Mapped[Optional[str]] = mapped_column(String(30))     # 2.6: journal moving the year's profit to capital / reserves
    net_profit: Mapped[Optional[Decimal]] = mapped_column(Numeric(16, 2))
    close_reason: Mapped[Optional[str]] = mapped_column(String(300))
    close_summary: Mapped[Optional[dict]] = mapped_column(JSON)             # checklist, appropriation and figures at closing
    reopened_by: Mapped[Optional[str]] = mapped_column(String(16))
    reopened_at: Mapped[Optional[dt.datetime]] = mapped_column(DT6)
    reopen_reason: Mapped[Optional[str]] = mapped_column(String(300))


class CostCentre(Base):
    __tablename__ = 'cost_centres'
    id: Mapped[str] = mapped_column(String(12), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(14))                           # Cost centre | Profit centre | Department
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class GstRegistration(Base):
    __tablename__ = 'gst_registrations'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    state: Mapped[str] = mapped_column(String(2))
    gstin: Mapped[str] = mapped_column(String(15), unique=True)
    from_date: Mapped[dt.date]
    kind: Mapped[str] = mapped_column(String(12), server_default='Regular')
    einvoice: Mapped[bool] = mapped_column(server_default=text('0'))


class HsnCode(Base):
    __tablename__ = 'hsn_codes'
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    kind: Mapped[str] = mapped_column(String(3))                            # HSN | SAC
    description: Mapped[str] = mapped_column(String(160))
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    uqc: Mapped[str] = mapped_column(String(4), server_default='NOS')


class TdsSection(Base):
    __tablename__ = 'tds_sections'
    code: Mapped[str] = mapped_column(String(10), primary_key=True)         # 194C, 194J(b), 192 ...
    label: Mapped[str] = mapped_column(String(80))
    rate_ind: Mapped[Decimal] = mapped_column(RATE)                         # individual / HUF
    rate_other: Mapped[Decimal] = mapped_column(RATE)
    single_limit: Mapped[Decimal] = mapped_column(server_default='0')       # per bill
    year_limit: Mapped[Decimal] = mapped_column(server_default='0')         # aggregate in the financial year
    monthly: Mapped[bool] = mapped_column(server_default=text('0'))         # limit tested per month (rent)
    slab: Mapped[bool] = mapped_column(server_default=text('0'))            # salary
    non_resident: Mapped[bool] = mapped_column(server_default=text('0'))
    tcs: Mapped[bool] = mapped_column(server_default=text('0'))
    no_pan_rate: Mapped[Optional[Decimal]] = mapped_column(RATE)
    stage: Mapped[str] = mapped_column(String(8), server_default='invoice') # invoice | payment
    form: Mapped[str] = mapped_column(String(5))                            # 24Q 26Q 27Q 27EQ (old names; new forms 138/140/144/143)
    new_ref: Mapped[Optional[str]] = mapped_column(String(30))              # Income-tax Act 2025 reference
    effective_from: Mapped[dt.date]
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class PartyTax(Base):
    """TDS / related-party profile of a supplier, customer or employee."""
    __tablename__ = 'party_tax'
    party: Mapped[str] = mapped_column(String(30), primary_key=True)
    company: Mapped[str] = mapped_column(String(4))
    pan: Mapped[Optional[str]] = mapped_column(String(10))
    resident: Mapped[bool] = mapped_column(server_default=text('1'))
    trc: Mapped[bool] = mapped_column(server_default=text('0'))             # tax residency certificate on file (rule 37BC)
    section: Mapped[Optional[str]] = mapped_column(String(10))
    ldc: Mapped[Optional[dict]] = mapped_column(JSON)                       # {no, section, rate, limit, from, to}
    priority: Mapped[str] = mapped_column(String(6), server_default='Normal')
    related: Mapped[bool] = mapped_column(server_default=text('0'))


class Employee(Base):
    __tablename__ = 'employees'
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    company: Mapped[str] = mapped_column(String(4))
    name: Mapped[str] = mapped_column(String(120))
    branch_id: Mapped[str] = mapped_column(String(16))
    pan: Mapped[Optional[str]] = mapped_column(String(10))
    gross: Mapped[Decimal]
    pf: Mapped[bool] = mapped_column(server_default=text('1'))
    esi: Mapped[bool] = mapped_column(server_default=text('0'))
    joined: Mapped[dt.date]
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class FinVoucher(Base):
    """A finance voucher of one of 21 types. `entered` keeps what the maker typed; `built` the full journal (with GST, TDS and
    the balancing party line) that posts on approval. Posted vouchers are never edited; corrections are reversal vouchers."""
    __tablename__ = 'fin_vouchers'
    __table_args__ = (UniqueConstraint('company', 'no'), Index('ix_fin_vouchers_company_date', 'company', 'vdate'))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30))
    company: Mapped[str] = mapped_column(String(4))
    vtype: Mapped[str] = mapped_column(String(3))
    vdate: Mapped[dt.date]
    branch_id: Mapped[str] = mapped_column(String(16))
    cost_centre: Mapped[Optional[str]] = mapped_column(String(12))
    dept: Mapped[Optional[str]] = mapped_column(String(12))
    ref: Mapped[Optional[str]] = mapped_column(String(60), index=True)
    party_kind: Mapped[Optional[str]] = mapped_column(String(4))            # cust sup emp
    party_id: Mapped[Optional[str]] = mapped_column(String(30), index=True)
    party_name: Mapped[Optional[str]] = mapped_column(String(160))
    gstin: Mapped[Optional[str]] = mapped_column(String(15))
    narration: Mapped[str] = mapped_column(String(500))
    entered: Mapped[list] = mapped_column(JSON)
    gst: Mapped[Optional[dict]] = mapped_column(JSON)
    tds: Mapped[Optional[dict]] = mapped_column(JSON)
    built: Mapped[list] = mapped_column(JSON)
    options: Mapped[Optional[dict]] = mapped_column(JSON)                   # advance, allocation, cheque, po, links …
    taxable: Mapped[Decimal] = mapped_column(server_default='0')
    tax: Mapped[Decimal] = mapped_column(server_default='0')
    tds_amount: Mapped[Decimal] = mapped_column(server_default='0')
    total: Mapped[Decimal] = mapped_column(server_default='0')
    paid: Mapped[Decimal] = mapped_column(server_default='0')               # settled against this bill / invoice
    status: Mapped[str] = mapped_column(String(18), server_default='Draft') # Draft Pending approval Posted Rejected Cancelled Reversed
    maker: Mapped[str] = mapped_column(String(16))
    maker_role: Mapped[Optional[str]] = mapped_column(String(60))
    approval_no: Mapped[Optional[str]] = mapped_column(String(20))
    approver_role: Mapped[Optional[str]] = mapped_column(String(60))
    approved_by: Mapped[Optional[str]] = mapped_column(String(16))
    approved_at: Mapped[Optional[dt.datetime]] = mapped_column(DT6)
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    posted_at: Mapped[Optional[dt.datetime]] = mapped_column(DT6)
    reversal_of: Mapped[Optional[str]] = mapped_column(String(30))
    reversed_by: Mapped[Optional[str]] = mapped_column(String(30))
    reason: Mapped[Optional[str]] = mapped_column(String(500))              # cancel / reject reason
    created_at: Mapped[dt.datetime] = created_at()


class TdsEntry(Base):
    __tablename__ = 'tds_entries'
    __table_args__ = (Index('ix_tds_entries_company_date', 'company', 'ded_date'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    voucher_no: Mapped[str] = mapped_column(String(30), index=True)
    ded_date: Mapped[dt.date]
    doc_date: Mapped[dt.date]
    party: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(160))
    pan: Mapped[Optional[str]] = mapped_column(String(10))
    section: Mapped[str] = mapped_column(String(10))
    base: Mapped[Decimal]
    rate: Mapped[Decimal] = mapped_column(RATE)
    amount: Mapped[Decimal]
    kind: Mapped[str] = mapped_column(String(10))                           # Payable | Receivable
    challan_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(14))                         # Deducted | No deduction | Deposited | Reversed
    branch_id: Mapped[str] = mapped_column(String(16))
    note: Mapped[Optional[str]] = mapped_column(String(200))


class TdsChallan(Base):
    __tablename__ = 'tds_challans'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    bsr: Mapped[str] = mapped_column(String(7))
    serial: Mapped[str] = mapped_column(String(5))
    cdate: Mapped[dt.date]
    cin: Mapped[str] = mapped_column(String(25))
    amount: Mapped[Decimal]
    interest: Mapped[Decimal] = mapped_column(server_default='0')
    sections: Mapped[list] = mapped_column(JSON)
    voucher_no: Mapped[Optional[str]] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10))                         # Pending Paid Cancelled


class TdsReturn(Base):
    __tablename__ = 'tds_returns'
    company: Mapped[str] = mapped_column(String(4), primary_key=True)
    fy: Mapped[str] = mapped_column(String(7), primary_key=True)
    form: Mapped[str] = mapped_column(String(5), primary_key=True)
    quarter: Mapped[str] = mapped_column(String(2), primary_key=True)
    filed_on: Mapped[dt.date]
    token: Mapped[str] = mapped_column(String(15))
    fee: Mapped[Decimal] = mapped_column(server_default='0')
    filed_by: Mapped[str] = mapped_column(String(16))


class TdsCertificate(Base):
    __tablename__ = 'tds_certificates'
    company: Mapped[str] = mapped_column(String(4), primary_key=True)
    fy: Mapped[str] = mapped_column(String(7), primary_key=True)
    quarter: Mapped[str] = mapped_column(String(2), primary_key=True)
    party: Mapped[str] = mapped_column(String(30), primary_key=True)
    cert_no: Mapped[str] = mapped_column(String(20))
    issued_on: Mapped[dt.date]
    amount: Mapped[Decimal]
    issued_by: Mapped[str] = mapped_column(String(16))


class GstChallan(Base):
    __tablename__ = 'gst_challans'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    period: Mapped[str] = mapped_column(String(7))                          # YYYY-MM
    cpin: Mapped[Optional[str]] = mapped_column(String(14))
    cin: Mapped[Optional[str]] = mapped_column(String(25))
    cdate: Mapped[dt.date]
    bank_account: Mapped[str] = mapped_column(String(10))
    cash: Mapped[dict] = mapped_column(JSON)
    used: Mapped[dict] = mapped_column(JSON)
    interest: Mapped[Decimal] = mapped_column(server_default='0')
    late_fee: Mapped[Decimal] = mapped_column(server_default='0')
    voucher_no: Mapped[Optional[str]] = mapped_column(String(30))
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(10))


class GstFiling(Base):
    __tablename__ = 'gst_filings'
    company: Mapped[str] = mapped_column(String(4), primary_key=True)
    ret: Mapped[str] = mapped_column(String(3), primary_key=True)          # 1 3B 9 9C
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    filed_on: Mapped[dt.date]
    arn: Mapped[str] = mapped_column(String(20))
    filed_by: Mapped[str] = mapped_column(String(16))


class EInvoice(Base):
    __tablename__ = 'einvoices'
    doc_no: Mapped[str] = mapped_column(String(30), primary_key=True)
    company: Mapped[str] = mapped_column(String(4))
    irn: Mapped[Optional[str]] = mapped_column(String(64))
    ack_no: Mapped[Optional[str]] = mapped_column(String(20))
    ack_at: Mapped[Optional[dt.datetime]] = mapped_column(DT6)
    status: Mapped[str] = mapped_column(String(12), server_default='Pending')   # Pending Generated Cancelled Not required
    signed_qr: Mapped[Optional[str]] = mapped_column(Text)
    cancel_reason: Mapped[Optional[str]] = mapped_column(String(120))
    ewb: Mapped[Optional[dict]] = mapped_column(JSON)                       # {no, date, vehicle, km, valid}
    local: Mapped[bool] = mapped_column(server_default=text('1'))           # generated without the IRP / NIC portal


class GstPortalRow(Base):
    """Rows of GSTR-2B / 2A imported from the portal, for reconciliation with the purchase register."""
    __tablename__ = 'gst_portal_rows'
    __table_args__ = (Index('ix_gst_portal_rows_key', 'company', 'kind', 'period'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    kind: Mapped[str] = mapped_column(String(2))                            # 2B 2A
    period: Mapped[str] = mapped_column(String(7))
    gstin: Mapped[str] = mapped_column(String(15))
    name: Mapped[Optional[str]] = mapped_column(String(160))
    inv_no: Mapped[str] = mapped_column(String(40))
    inv_date: Mapped[Optional[dt.date]]
    taxable: Mapped[Decimal]
    igst: Mapped[Decimal] = mapped_column(server_default='0')
    cgst: Mapped[Decimal] = mapped_column(server_default='0')
    sgst: Mapped[Decimal] = mapped_column(server_default='0')
    cess: Mapped[Decimal] = mapped_column(server_default='0')
    source: Mapped[str] = mapped_column(String(60))
    loaded_at: Mapped[dt.datetime] = created_at()


class BankAccount(Base):
    __tablename__ = 'bank_accounts'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    account: Mapped[str] = mapped_column(String(10))                        # ledger code
    name: Mapped[str] = mapped_column(String(120))
    ifsc: Mapped[Optional[str]] = mapped_column(String(11))
    branch_id: Mapped[str] = mapped_column(String(16))


class BankStatementLine(Base):
    __tablename__ = 'bank_statement_lines'
    __table_args__ = (UniqueConstraint('uniq'), Index('ix_bank_statement_lines_acct', 'company', 'account', 'sdate'))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    account: Mapped[str] = mapped_column(String(10))
    sdate: Mapped[dt.date]
    narration: Mapped[str] = mapped_column(String(300))
    ref: Mapped[Optional[str]] = mapped_column(String(60))
    amount: Mapped[Decimal]                                                 # + deposit, - withdrawal
    balance: Mapped[Optional[Decimal]]
    match_journal: Mapped[Optional[str]] = mapped_column(String(30))        # journal no
    match_line: Mapped[Optional[int]] = mapped_column(Integer)
    how: Mapped[Optional[str]] = mapped_column(String(60))
    source: Mapped[str] = mapped_column(String(80))
    uniq: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = created_at()


class Cheque(Base):
    __tablename__ = 'cheques'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    account: Mapped[str] = mapped_column(String(10))
    cheque_no: Mapped[str] = mapped_column(String(12))
    cdate: Mapped[dt.date]
    party: Mapped[Optional[str]] = mapped_column(String(160))
    amount: Mapped[Decimal]
    kind: Mapped[str] = mapped_column(String(8))                            # Issued | Received
    status: Mapped[str] = mapped_column(String(14))                         # Issued Deposited Cleared Dishonoured Stop payment
    voucher_no: Mapped[str] = mapped_column(String(30))
    log: Mapped[list] = mapped_column(JSON)


class AssetCategory(Base):
    __tablename__ = 'asset_categories'
    id: Mapped[str] = mapped_column(String(6), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    account: Mapped[str] = mapped_column(String(10))
    life_years: Mapped[Decimal] = mapped_column(Numeric(5, 2))              # Companies Act Schedule II
    it_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2))                 # Income-tax block rate


class FixedAsset(Base):
    __tablename__ = 'fixed_assets'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(20), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    branch_id: Mapped[str] = mapped_column(String(16))
    category: Mapped[str] = mapped_column(String(6))
    name: Mapped[str] = mapped_column(String(160))
    tag: Mapped[str] = mapped_column(String(20))
    put_to_use: Mapped[dt.date]
    cost: Mapped[Decimal]
    residual_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default='5')
    method: Mapped[str] = mapped_column(String(4), server_default='SLM')
    life_years: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    status: Mapped[str] = mapped_column(String(24))                         # Pending capitalisation Active Disposed
    acc_dep: Mapped[Decimal] = mapped_column(server_default='0')
    impairment: Mapped[Decimal] = mapped_column(server_default='0')
    disposed_on: Mapped[Optional[dt.date]]
    voucher_no: Mapped[Optional[str]] = mapped_column(String(30))
    moves: Mapped[list] = mapped_column(JSON)
    verifications: Mapped[list] = mapped_column(JSON)
    dep_log: Mapped[list] = mapped_column(JSON)


class DepreciationRun(Base):
    __tablename__ = 'depreciation_runs'
    company: Mapped[str] = mapped_column(String(4), primary_key=True)
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    voucher_no: Mapped[Optional[str]] = mapped_column(String(30))
    amount: Mapped[Decimal] = mapped_column(server_default='0')


class Budget(Base):
    __tablename__ = 'budgets'
    company: Mapped[str] = mapped_column(String(4), primary_key=True)
    fy: Mapped[str] = mapped_column(String(7), primary_key=True)
    account: Mapped[str] = mapped_column(String(10), primary_key=True)
    amount: Mapped[Decimal]


class RecurringJournal(Base):
    __tablename__ = 'recurring_journals'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(24))                           # Rent Provision Prepaid amortisation Accrual Amortisation
    vtype: Mapped[str] = mapped_column(String(3))
    day: Mapped[int] = mapped_column(Integer)
    next_date: Mapped[dt.date]
    end_date: Mapped[dt.date]
    narration: Mapped[str] = mapped_column(String(300))
    lines: Mapped[list] = mapped_column(JSON)
    party_id: Mapped[Optional[str]] = mapped_column(String(30))
    tds_section: Mapped[Optional[str]] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(server_default=text('1'))
    last_run: Mapped[Optional[dt.date]]


class FinApprovalRule(Base):
    __tablename__ = 'fin_approval_rules'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    vtype: Mapped[str] = mapped_column(String(3), server_default='*')
    account: Mapped[Optional[str]] = mapped_column(String(10))
    branch_id: Mapped[Optional[str]] = mapped_column(String(16))
    above: Mapped[Decimal] = mapped_column(server_default='0')
    role: Mapped[str] = mapped_column(String(60))


class ComplianceStatus(Base):
    __tablename__ = 'compliance_status'
    key: Mapped[str] = mapped_column(String(80), primary_key=True)          # KG|gstr1|2026-10
    company: Mapped[str] = mapped_column(String(4))
    status: Mapped[str] = mapped_column(String(16))
    ref: Mapped[Optional[str]] = mapped_column(String(80))
    note: Mapped[Optional[str]] = mapped_column(String(300))
    done_on: Mapped[Optional[dt.date]]
    owner: Mapped[Optional[str]] = mapped_column(String(16))
    updated_by: Mapped[Optional[str]] = mapped_column(String(16))


class StockAuditDecision(Base):
    """Permanent record of each Director / CFO decision on a stock audit, with the item-wise snapshot that was posted."""
    __tablename__ = 'stock_audit_decisions'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    audit_id: Mapped[int] = mapped_column(BigInteger, index=True)
    approval_no: Mapped[Optional[str]] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(40))                           # Full approval | Partial approval | Returned … | Rejected
    decided_by: Mapped[str] = mapped_column(String(16))
    decided_role: Mapped[str] = mapped_column(String(60))
    decided_at: Mapped[dt.datetime] = mapped_column(DT6)
    remarks: Mapped[Optional[str]] = mapped_column(String(500))
    adjustment_no: Mapped[Optional[str]] = mapped_column(String(30))
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    shortage: Mapped[Decimal] = mapped_column(server_default='0')
    damaged: Mapped[Decimal] = mapped_column(server_default='0')
    excess: Mapped[Decimal] = mapped_column(server_default='0')
    net: Mapped[Decimal] = mapped_column(server_default='0')
    approved: Mapped[list] = mapped_column(JSON)                            # [{line_no, item_id, name, sys, sys_value, phys, phys_value, var, var_value, dmg, dmg_value, before, after}]
    rejected: Mapped[list] = mapped_column(JSON)
    recount: Mapped[list] = mapped_column(JSON)
    held: Mapped[list] = mapped_column(JSON)
