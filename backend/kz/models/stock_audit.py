"""Stock audits: compare system stock with physically counted stock per company and location, review and approve the
variance, then post one stock adjustment for it. Added in migration 0004 (see services/stock_audit.py)."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import COST, DT6, Base, created_at

MONEY = Numeric(16, 2)


class StockAudit(Base):
    __tablename__ = 'stock_audits'
    __table_args__ = (Index('ix_stock_audits_company_date', 'company', 'audit_date'), Index('ix_stock_audits_status', 'status'),
                      Index('ix_stock_audits_branch', 'branch_id'))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)                  # KG/SA/2627/0001
    company: Mapped[str] = mapped_column(ForeignKey('companies.code'))
    branch_id: Mapped[str] = mapped_column(String(16))
    location: Mapped[str] = mapped_column(String(10))
    location_type: Mapped[str] = mapped_column(String(12))                    # Warehouse Branch Store Godown
    audit_date: Mapped[dt.date]
    due_date: Mapped[dt.date]
    audit_type: Mapped[str] = mapped_column(String(10))                       # Full Category Item Spot
    scope: Mapped[Optional[dict]] = mapped_column(JSON)                       # {categories: [...], items: [...], spot_size: n}
    stock_area: Mapped[Optional[str]] = mapped_column(String(60))             # department / rack / bin prefix
    auditors: Mapped[list] = mapped_column(JSON)                              # user ids who count
    supervisor_id: Mapped[str] = mapped_column(String(16))                    # location in-charge who verifies
    instructions: Mapped[Optional[str]] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(14), server_default='Draft')   # Draft In Progress Submitted Under Review Approved Rejected Closed Cancelled
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
    started_at: Mapped[Optional[dt.datetime]]
    submitted_by: Mapped[Optional[str]] = mapped_column(String(16))
    submitted_at: Mapped[Optional[dt.datetime]]
    verified_by: Mapped[Optional[str]] = mapped_column(String(16))
    verified_at: Mapped[Optional[dt.datetime]]
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(16))
    reviewed_at: Mapped[Optional[dt.datetime]]
    review_note: Mapped[Optional[str]] = mapped_column(String(500))
    approval_no: Mapped[Optional[str]] = mapped_column(String(30))
    approved_by: Mapped[Optional[str]] = mapped_column(String(16))
    approved_at: Mapped[Optional[dt.datetime]]
    rejected_reason: Mapped[Optional[str]] = mapped_column(String(500))
    escalated: Mapped[bool] = mapped_column(server_default=text('0'))
    adjustment_no: Mapped[Optional[str]] = mapped_column(String(30))
    posted_by: Mapped[Optional[str]] = mapped_column(String(16))
    posted_at: Mapped[Optional[dt.datetime]]
    closed_at: Mapped[Optional[dt.datetime]]
    reopen_count: Mapped[int] = mapped_column(Integer, server_default='0')
    # summary kept up to date on every count save (fast lists, filters and dashboards)
    lines_count: Mapped[int] = mapped_column(Integer, server_default='0')
    counted_count: Mapped[int] = mapped_column(Integer, server_default='0')
    matched_count: Mapped[int] = mapped_column(Integer, server_default='0')
    system_value: Mapped[Decimal] = mapped_column(MONEY, server_default='0')
    physical_value: Mapped[Decimal] = mapped_column(MONEY, server_default='0')
    shortage_value: Mapped[Decimal] = mapped_column(MONEY, server_default='0')
    excess_value: Mapped[Decimal] = mapped_column(MONEY, server_default='0')
    damaged_value: Mapped[Decimal] = mapped_column(MONEY, server_default='0')
    lines: Mapped[list['StockAuditLine']] = relationship(order_by='StockAuditLine.line_no', cascade='all, delete-orphan')


class StockAuditLine(Base):
    """One item at the audited location. system_qty and unit_cost are captured at the moment the count is saved, so sales
    and receipts that happen while counting do not create false variances. Variance = physical - system."""
    __tablename__ = 'stock_audit_lines'
    __table_args__ = (Index('ix_stock_audit_lines_item', 'item_id'),)
    audit_id: Mapped[int] = mapped_column(ForeignKey('stock_audits.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    item_id: Mapped[str] = mapped_column(String(24))
    batch_no: Mapped[Optional[str]] = mapped_column(String(40))
    system_qty: Mapped[Optional[int]] = mapped_column(Integer)
    physical_qty: Mapped[Optional[int]] = mapped_column(Integer)
    unit_cost: Mapped[Optional[Decimal]] = mapped_column(COST)
    condition: Mapped[str] = mapped_column(String(10), server_default='Good')   # Good Damaged Expired Missing Excess Returned
    remarks: Mapped[Optional[str]] = mapped_column(String(300))
    added_in_count: Mapped[bool] = mapped_column(server_default=text('0'))    # found during the count, not in the planned list
    counted_by: Mapped[Optional[str]] = mapped_column(String(16))
    counted_at: Mapped[Optional[dt.datetime]]
    dmg_qty: Mapped[Optional[int]] = mapped_column(Integer)                     # damaged / expired part of the physical count
    decision: Mapped[Optional[str]] = mapped_column(String(10))                 # Approved Rejected Matched (set by the Director / CFO)
    decided_by: Mapped[Optional[str]] = mapped_column(String(16))
    decided_at: Mapped[Optional[dt.datetime]]
    recount: Mapped[bool] = mapped_column(server_default=text('0'))             # sent back for recount at the last decision
    adjustment_no: Mapped[Optional[str]] = mapped_column(String(30))            # stock adjustment that posted this line


class StockAuditEvent(Base):
    """User-wise activity timeline of an audit (create, count saves, submit, verify, review, approve, reject, post, reopen)."""
    __tablename__ = 'stock_audit_events'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    audit_id: Mapped[int] = mapped_column(ForeignKey('stock_audits.id', ondelete='CASCADE'), index=True)
    at: Mapped[dt.datetime] = mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'))
    user_id: Mapped[Optional[str]] = mapped_column(String(16))
    user_name: Mapped[Optional[str]] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(40))
    detail: Mapped[Optional[str]] = mapped_column(Text)


__all__ = ['StockAudit', 'StockAuditLine', 'StockAuditEvent']
