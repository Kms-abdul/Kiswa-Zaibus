"""Sales: invoices (counter, sponsored and online), lines, payments, credit notes (returns / cancellations), held bills."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, Numeric, String, Time, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import COST, Base, created_at


class Invoice(Base):
    __tablename__ = 'invoices'
    __table_args__ = (Index('ix_invoices_company_branch_date', 'company', 'branch_id', 'inv_date'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    branch_id: Mapped[str] = mapped_column(String(16))
    counter: Mapped[Optional[str]] = mapped_column(String(30))
    inv_date: Mapped[dt.date]
    inv_time: Mapped[dt.time] = mapped_column(Time)
    customer_id: Mapped[Optional[str]] = mapped_column(String(30))
    adm_no: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    student_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    cashier_id: Mapped[Optional[str]] = mapped_column(String(16))
    shift_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    channel: Mapped[str] = mapped_column(String(10), server_default='counter')     # counter | sponsored | online
    place_of_supply: Mapped[str] = mapped_column(String(2), server_default='36')
    gross: Mapped[Decimal] = mapped_column(server_default='0')
    discount: Mapped[Decimal] = mapped_column(server_default='0')
    taxable: Mapped[Decimal]
    cgst: Mapped[Decimal] = mapped_column(server_default='0')
    sgst: Mapped[Decimal] = mapped_column(server_default='0')
    igst: Mapped[Decimal] = mapped_column(server_default='0')
    round_off: Mapped[Decimal] = mapped_column(Numeric(6, 2), server_default='0')
    total: Mapped[Decimal]
    balance: Mapped[Decimal] = mapped_column(server_default='0')        # credit still owed
    status: Mapped[str] = mapped_column(String(20), server_default='Posted')       # Posted Partly returned Returned Cancelled
    delivery_no: Mapped[Optional[str]] = mapped_column(String(30))
    pending_reason: Mapped[Optional[str]] = mapped_column(String(300))
    online_order_no: Mapped[Optional[str]] = mapped_column(String(30))
    msea_voucher_no: Mapped[Optional[str]] = mapped_column(String(30))
    issue_no: Mapped[Optional[str]] = mapped_column(String(30))         # sponsored issue voucher (SIV)
    override_by: Mapped[Optional[str]] = mapped_column(String(120))     # stock override approver
    override_reason: Mapped[Optional[str]] = mapped_column(String(300))
    reprints: Mapped[int] = mapped_column(Integer, server_default='0')
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_at: Mapped[dt.datetime] = created_at()
    lines: Mapped[list['InvoiceLine']] = relationship(order_by='InvoiceLine.line_no', cascade='all, delete-orphan')
    payments: Mapped[list['InvoicePayment']] = relationship(order_by='InvoicePayment.line_no', cascade='all, delete-orphan')


class InvoiceLine(Base):
    __tablename__ = 'invoice_lines'
    invoice_id: Mapped[int] = mapped_column(ForeignKey('invoices.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    item_id: Mapped[str] = mapped_column(String(24))
    description: Mapped[str] = mapped_column(String(200))
    hsn: Mapped[Optional[str]] = mapped_column(String(10))
    qty: Mapped[int] = mapped_column(Integer)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))                # GST-inclusive unit price
    disc_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default='0')
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default='0')
    taxable: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    tax: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    unit_cost: Mapped[Decimal] = mapped_column(COST)                     # moving-average cost at sale
    delivered_qty: Mapped[int] = mapped_column(Integer, server_default='0')
    returned_qty: Mapped[int] = mapped_column(Integer, server_default='0')
    package_name: Mapped[Optional[str]] = mapped_column(String(160))


class InvoicePayment(Base):
    __tablename__ = 'invoice_payments'
    invoice_id: Mapped[int] = mapped_column(ForeignKey('invoices.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    mode: Mapped[str] = mapped_column(String(10))             # cash upi card bank online credit wallet
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    ref: Mapped[Optional[str]] = mapped_column(String(80))


class CreditNote(Base):
    __tablename__ = 'credit_notes'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey('invoices.id'), index=True)
    company: Mapped[str] = mapped_column(String(4))
    cn_date: Mapped[dt.date]
    kind: Mapped[str] = mapped_column(String(12))             # Return Cancellation Exchange
    condition: Mapped[str] = mapped_column(String(12), server_default='Good')    # Good Damaged
    reason: Mapped[str] = mapped_column(String(300))
    taxable: Mapped[Decimal] = mapped_column(server_default='0')
    tax: Mapped[Decimal] = mapped_column(server_default='0')
    amount: Mapped[Decimal]
    refund_mode: Mapped[str] = mapped_column(String(10))
    shift_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    approval_no: Mapped[Optional[str]] = mapped_column(String(20))
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
    lines: Mapped[list['CreditNoteLine']] = relationship(order_by='CreditNoteLine.line_no', cascade='all, delete-orphan')


class CreditNoteLine(Base):
    __tablename__ = 'credit_note_lines'
    credit_note_id: Mapped[int] = mapped_column(ForeignKey('credit_notes.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    invoice_line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[str] = mapped_column(String(24))
    qty: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    restocked: Mapped[bool] = mapped_column(server_default=text('1'))


class HeldBill(Base):
    """A bill waiting for approval (discount above the cashier's limit, stock override)."""
    __tablename__ = 'held_bills'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    branch_id: Mapped[str] = mapped_column(String(16))
    draft: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[Optional[str]] = mapped_column(String(300))
    approval_no: Mapped[Optional[str]] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(12), server_default='Held')   # Held Released Discarded
    invoice_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
