"""Purchasing: requisition -> quotes -> purchase order -> goods receipt -> supplier bill -> payment; debit notes."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import COST, Base, created_at


class PurchaseOrder(Base):
    __tablename__ = 'purchase_orders'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    branch_id: Mapped[str] = mapped_column(String(16))        # deliver to
    supplier_id: Mapped[Optional[str]] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20))           # Requisition Quotes Pending approval PO approved Goods received Invoiced Closed Cancelled Rejected
    value: Mapped[Decimal] = mapped_column(server_default='0')
    freight: Mapped[Decimal] = mapped_column(server_default='0')
    required_by: Mapped[Optional[dt.date]]
    notes: Mapped[Optional[str]] = mapped_column(String(300))
    grn_value: Mapped[Decimal] = mapped_column(server_default='0')
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
    lines: Mapped[list['PurchaseOrderLine']] = relationship(order_by='PurchaseOrderLine.line_no', cascade='all, delete-orphan')
    quotes: Mapped[list['PurchaseQuote']] = relationship(cascade='all, delete-orphan')


class PurchaseOrderLine(Base):
    __tablename__ = 'purchase_order_lines'
    po_id: Mapped[int] = mapped_column(ForeignKey('purchase_orders.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    item_id: Mapped[str] = mapped_column(String(24))
    qty: Mapped[int] = mapped_column(Integer)
    rate: Mapped[Decimal] = mapped_column(server_default='0')         # excl. GST
    received_qty: Mapped[int] = mapped_column(Integer, server_default='0')
    rejected_qty: Mapped[int] = mapped_column(Integer, server_default='0')
    returned_qty: Mapped[int] = mapped_column(Integer, server_default='0')
    landed_rate: Mapped[Optional[Decimal]] = mapped_column(COST)       # rate + share of freight


class PurchaseQuote(Base):
    __tablename__ = 'purchase_quotes'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    po_id: Mapped[int] = mapped_column(ForeignKey('purchase_orders.id', ondelete='CASCADE'), index=True)
    supplier_id: Mapped[str] = mapped_column(String(16))
    rates: Mapped[list] = mapped_column(JSON)                 # one rate per PO line
    lead_days: Mapped[Optional[int]] = mapped_column(Integer)
    selected: Mapped[bool] = mapped_column(server_default=text('0'))


class GoodsReceipt(Base):
    __tablename__ = 'goods_receipts'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    po_id: Mapped[int] = mapped_column(ForeignKey('purchase_orders.id'), index=True)
    txn_date: Mapped[dt.date]
    value: Mapped[Decimal]
    line_items: Mapped[list] = mapped_column(JSON)            # [{line_no, item_id, received, rejected, landed_rate}]
    received_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()


class SupplierBill(Base):
    __tablename__ = 'supplier_bills'
    __table_args__ = (UniqueConstraint('company', 'supplier_id', 'bill_no'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    supplier_id: Mapped[str] = mapped_column(String(16), index=True)
    branch_id: Mapped[str] = mapped_column(String(16))
    po_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    bill_no: Mapped[str] = mapped_column(String(40))          # supplier's own invoice number
    internal_no: Mapped[str] = mapped_column(String(30))      # our PI number
    bill_date: Mapped[dt.date]
    due_date: Mapped[dt.date]
    taxable: Mapped[Decimal]
    gst: Mapped[Decimal]
    amount: Mapped[Decimal]
    paid: Mapped[Decimal] = mapped_column(server_default='0')
    adjusted: Mapped[Decimal] = mapped_column(server_default='0')   # debit notes
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_at: Mapped[dt.datetime] = created_at()


class SupplierPayment(Base):
    __tablename__ = 'supplier_payments'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    supplier_id: Mapped[str] = mapped_column(String(16), index=True)
    pay_date: Mapped[dt.date]
    amount: Mapped[Decimal]
    mode: Mapped[str] = mapped_column(String(10))             # bank cash
    reference: Mapped[Optional[str]] = mapped_column(String(60))
    applied: Mapped[list] = mapped_column(JSON)               # [[bill_id, amount]]
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()


class DebitNote(Base):
    """Purchase return to the supplier."""
    __tablename__ = 'debit_notes'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    po_id: Mapped[int] = mapped_column(BigInteger, index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[str] = mapped_column(String(24))
    qty: Mapped[int] = mapped_column(Integer)
    amount: Mapped[Decimal]
    reason: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
