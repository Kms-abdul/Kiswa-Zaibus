"""Accounts: double-entry journals, customer receipts, inter-company transfers."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, CheckConstraint, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, created_at


class Journal(Base):
    """Every money movement is a balanced journal (sum debit = sum credit), posted by services/ledger.py."""
    __tablename__ = 'journals'
    __table_args__ = (UniqueConstraint('company', 'no'), Index('ix_journals_company_date', 'company', 'txn_date'))
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30))
    company: Mapped[str] = mapped_column(String(4))
    txn_date: Mapped[dt.date]
    posted_on: Mapped[dt.date]
    type: Mapped[str] = mapped_column(String(30))             # Sale Credit note Receipt Payment Expense Contra Journal GRN Purchase invoice Debit note Stock transfer Stock adjustment Cash difference Settlement Inter-company Reversal Opening
    ref: Mapped[Optional[str]] = mapped_column(String(60), index=True)
    narration: Mapped[str] = mapped_column(String(500))
    created_by: Mapped[Optional[str]] = mapped_column(String(16))
    approval_no: Mapped[Optional[str]] = mapped_column(String(20))
    reverses_no: Mapped[Optional[str]] = mapped_column(String(30))
    reversed_by_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_at: Mapped[dt.datetime] = created_at()
    lines: Mapped[list['JournalLine']] = relationship(order_by='JournalLine.line_no', cascade='all, delete-orphan')


class JournalLine(Base):
    __tablename__ = 'journal_lines'
    __table_args__ = (
        CheckConstraint('debit >= 0 AND credit >= 0 AND NOT (debit > 0 AND credit > 0)', name='one_side'),
        Index('ix_journal_lines_account_branch', 'account', 'branch_id'),
        Index('ix_journal_lines_party', 'party'),
    )
    journal_id: Mapped[int] = mapped_column(ForeignKey('journals.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    account: Mapped[str] = mapped_column(String(10))
    branch_id: Mapped[str] = mapped_column(String(16))        # branch id or <CO>-HO for Head Office
    party: Mapped[Optional[str]] = mapped_column(String(30))  # customer / supplier / company for sub-ledgers
    debit: Mapped[Decimal] = mapped_column(server_default='0')
    credit: Mapped[Decimal] = mapped_column(server_default='0')
    cost_centre: Mapped[Optional[str]] = mapped_column(String(12))           # cost / profit centre (default by branch type)
    dept: Mapped[Optional[str]] = mapped_column(String(12))


class Receipt(Base):
    __tablename__ = 'receipts'
    __table_args__ = (UniqueConstraint('company', 'no'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30))
    company: Mapped[str] = mapped_column(String(4))
    branch_id: Mapped[str] = mapped_column(String(16))
    customer_id: Mapped[str] = mapped_column(String(30), index=True)
    rcpt_date: Mapped[dt.date]
    amount: Mapped[Decimal]
    mode: Mapped[str] = mapped_column(String(10))
    reference: Mapped[Optional[str]] = mapped_column(String(60))
    utr: Mapped[Optional[str]] = mapped_column(String(60))
    applied: Mapped[list] = mapped_column(JSON)               # [[invoice_no, amount]]
    to_wallet: Mapped[Decimal] = mapped_column(server_default='0')
    shift_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()


class InterCompany(Base):
    """Fund transfer between Kiswa and Zaibus: each company books it in its own ledger (1400 due from / 2400 due to)."""
    __tablename__ = 'inter_company'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    from_company: Mapped[str] = mapped_column(String(4))
    to_company: Mapped[str] = mapped_column(String(4))
    amount: Mapped[Decimal]
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))           # Pending approval Posted Rejected
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
