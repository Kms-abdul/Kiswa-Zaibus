"""Online store: parent orders, central-warehouse fulfilment (pick, QC, pack, dispatch), branch collection, requests, refunds."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at

ORDER_FLOW = ['Order Placed', 'Payment Confirmed', 'Picking', 'Quality Check', 'Packed', 'Dispatched to Branch',
              'Received at Branch', 'Ready for Collection', 'Collected']


class OnlineOrder(Base):
    __tablename__ = 'online_orders'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    grp: Mapped[str] = mapped_column(String(32), index=True)          # one checkout may create one order per company
    company: Mapped[str] = mapped_column(String(4))
    adm_no: Mapped[str] = mapped_column(String(20), index=True)
    mobile: Mapped[str] = mapped_column(String(20))
    collect_at: Mapped[str] = mapped_column(String(10))               # location code where the parent collects
    warehouse_branch: Mapped[str] = mapped_column(String(16))         # fulfilling central warehouse branch
    subtotal: Mapped[Decimal]
    total: Mapped[Decimal]
    status: Mapped[str] = mapped_column(String(30))                   # see ORDER_FLOW, plus Cancelled / Payment failed / Returned
    invoice_no: Mapped[Optional[str]] = mapped_column(String(30))
    payment_id: Mapped[Optional[str]] = mapped_column(String(40))
    rzp_order_id: Mapped[Optional[str]] = mapped_column(String(40))
    collect_otp_hash: Mapped[Optional[str]] = mapped_column(String(64))
    events: Mapped[list] = mapped_column(JSON)                         # [[timestamp, user, text]]
    placed_at: Mapped[dt.datetime] = created_at()
    lines: Mapped[list['OnlineOrderLine']] = relationship(order_by='OnlineOrderLine.line_no', cascade='all, delete-orphan')


class OnlineOrderLine(Base):
    __tablename__ = 'online_order_lines'
    order_id: Mapped[int] = mapped_column(ForeignKey('online_orders.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    item_id: Mapped[str] = mapped_column(String(24))
    qty: Mapped[int] = mapped_column(Integer)
    rate: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    disc_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default='0')    # discount scheme at order time
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    picked_qty: Mapped[int] = mapped_column(Integer, server_default='0')
    collected_qty: Mapped[int] = mapped_column(Integer, server_default='0')
    returned_qty: Mapped[int] = mapped_column(Integer, server_default='0')


class OrderRequest(Base):
    """Parent's request after ordering: cancellation (before dispatch) or return (within the return window)."""
    __tablename__ = 'order_requests'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('online_orders.id'), index=True)
    type: Mapped[str] = mapped_column(String(12))                     # Cancellation Return
    line_items: Mapped[list] = mapped_column(JSON)                     # [{line_no, qty}]
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))                   # Requested Approved Rejected Completed
    decided_by: Mapped[Optional[str]] = mapped_column(String(16))
    decision_note: Mapped[Optional[str]] = mapped_column(String(300))
    credit_note_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_at: Mapped[dt.datetime] = created_at()


class Refund(Base):
    __tablename__ = 'refunds'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    order_id: Mapped[int] = mapped_column(BigInteger, index=True)
    request_no: Mapped[Optional[str]] = mapped_column(String(30))
    credit_note_no: Mapped[Optional[str]] = mapped_column(String(30))
    amount: Mapped[Decimal]
    original_payment: Mapped[Optional[str]] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), server_default='Pending')   # Pending Refunded Failed
    gateway_refund_id: Mapped[Optional[str]] = mapped_column(String(40))
    created_at: Mapped[dt.datetime] = created_at()
    completed_at: Mapped[Optional[dt.datetime]]
