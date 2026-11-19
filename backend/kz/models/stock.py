"""Inventory: on-hand balance, stock ledger (every movement), transfers between branches, adjustments."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import BigInteger, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import COST, Base, created_at


class StockBalance(Base):
    """Current quantity per branch and item. Always equals SUM(stock_ledger.qty) for the pair (see docs/DATABASE.md)."""
    __tablename__ = 'stock_balance'
    branch_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(24), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, server_default='0')
    reserved: Mapped[int] = mapped_column(Integer, server_default='0')   # held for online orders not yet packed


class StockLedger(Base):
    __tablename__ = 'stock_ledger'
    __table_args__ = (Index('ix_stock_ledger_branch_item_date', 'branch_id', 'item_id', 'txn_date'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(String(4))
    branch_id: Mapped[str] = mapped_column(String(16))
    item_id: Mapped[str] = mapped_column(String(24))
    txn_date: Mapped[dt.date]
    qty: Mapped[int] = mapped_column(Integer)                 # + in, - out
    unit_cost: Mapped[Decimal] = mapped_column(COST)
    txn_type: Mapped[str] = mapped_column(String(30))         # Opening stock, GRN, Sale, Sales return, Transfer out/in, Adjustment ...
    ref: Mapped[str] = mapped_column(String(40), index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()


class Transfer(Base):
    __tablename__ = 'transfers'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    from_branch: Mapped[str] = mapped_column(String(16))
    to_branch: Mapped[str] = mapped_column(String(16))
    txn_date: Mapped[dt.date]
    status: Mapped[str] = mapped_column(String(20))           # Pending approval Approved In transit Received Rejected Cancelled
    value: Mapped[Decimal] = mapped_column(server_default='0')
    reason: Mapped[Optional[str]] = mapped_column(String(300))
    requested_by: Mapped[str] = mapped_column(String(16))
    dispatched_by: Mapped[Optional[str]] = mapped_column(String(16))
    dispatched_at: Mapped[Optional[dt.datetime]]
    received_by: Mapped[Optional[str]] = mapped_column(String(16))
    received_at: Mapped[Optional[dt.datetime]]
    receipt_note: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = created_at()
    lines: Mapped[list['TransferLine']] = relationship(order_by='TransferLine.line_no', cascade='all, delete-orphan')


class TransferLine(Base):
    __tablename__ = 'transfer_lines'
    transfer_id: Mapped[int] = mapped_column(ForeignKey('transfers.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    item_id: Mapped[str] = mapped_column(String(24))
    qty: Mapped[int] = mapped_column(Integer)
    received_qty: Mapped[Optional[int]] = mapped_column(Integer)
    damaged_qty: Mapped[Optional[int]] = mapped_column(Integer)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(COST)


class StockAdjustment(Base):
    __tablename__ = 'stock_adjustments'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    branch_id: Mapped[str] = mapped_column(String(16))
    txn_date: Mapped[dt.date]
    adj_type: Mapped[str] = mapped_column(String(20))         # Damaged Write-off Obsolete Lost / missing Found excess Physical count
    value: Mapped[Decimal]
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))           # Pending approval Posted Rejected
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
    audit_no: Mapped[Optional[str]] = mapped_column(String(30), index=True)       # stock audit this adjustment posts
    approved_by: Mapped[Optional[str]] = mapped_column(String(16))
    lines: Mapped[list['StockAdjustmentLine']] = relationship(order_by='StockAdjustmentLine.line_no', cascade='all, delete-orphan')


class StockAdjustmentLine(Base):
    __tablename__ = 'stock_adjustment_lines'
    adjustment_id: Mapped[int] = mapped_column(ForeignKey('stock_adjustments.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    item_id: Mapped[str] = mapped_column(String(24))
    qty: Mapped[int] = mapped_column(Integer)                 # + found, - lost/damaged
    unit_cost: Mapped[Decimal] = mapped_column(COST)


# convenience for type checkers in services
__all__ = ['StockBalance', 'StockLedger', 'Transfer', 'TransferLine', 'StockAdjustment', 'StockAdjustmentLine', 'text']
