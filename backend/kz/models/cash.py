"""Cash custody: counter shifts (open / close with denominations), handovers to the Outdoor Clerk, owner instructions."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at


class CashShift(Base):
    __tablename__ = 'cash_shifts'
    __table_args__ = (Index('ix_cash_shifts_user_status', 'user_id', 'status'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    location: Mapped[str] = mapped_column(String(10))
    counter: Mapped[Optional[str]] = mapped_column(String(30))
    user_id: Mapped[str] = mapped_column(String(16))
    shift_date: Mapped[dt.date]
    opened_at: Mapped[dt.datetime]
    closed_at: Mapped[Optional[dt.datetime]]
    status: Mapped[str] = mapped_column(String(20), server_default='Open')   # Open Closed Pending approval
    opening: Mapped[dict] = mapped_column(JSON)               # {company: float float}
    figures: Mapped[Optional[dict]] = mapped_column(JSON)     # per company: expected, counted, difference, sales split
    denominations: Mapped[Optional[dict]] = mapped_column(JSON)
    difference_reason: Mapped[Optional[str]] = mapped_column(String(300))
    approval_no: Mapped[Optional[str]] = mapped_column(String(20))


class CashHandover(Base):
    """Counter cash handed to the Outdoor Clerk (company cash custody)."""
    __tablename__ = 'cash_handovers'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    branch_id: Mapped[str] = mapped_column(String(16))
    txn_date: Mapped[dt.date]
    clerk_id: Mapped[str] = mapped_column(String(16), index=True)
    from_user: Mapped[str] = mapped_column(String(16))
    amount: Mapped[Decimal]
    counted: Mapped[Optional[Decimal]]
    difference: Mapped[Decimal] = mapped_column(server_default='0')
    reason: Mapped[Optional[str]] = mapped_column(String(300))
    denominations: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), server_default='Awaiting clerk')   # Awaiting clerk Accepted Disputed Rejected
    accepted_at: Mapped[Optional[dt.datetime]]
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_at: Mapped[dt.datetime] = created_at()


class OwnerInstruction(Base):
    """Owner's instruction to the Outdoor Clerk: deposit to bank, pay someone, or hand cash to the owner.
    Draft -> Owner Approved -> Executed -> Finance Verified -> Closed."""
    __tablename__ = 'owner_instructions'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(30), unique=True)
    company: Mapped[str] = mapped_column(String(4))
    clerk_id: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(20))             # Bank deposit Cash payment Transfer to owner
    amount: Mapped[Decimal]
    recipient: Mapped[Optional[str]] = mapped_column(String(160))
    purpose: Mapped[str] = mapped_column(String(300))
    expense_account: Mapped[Optional[str]] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))
    approval_no: Mapped[Optional[str]] = mapped_column(String(20))
    executed_at: Mapped[Optional[dt.datetime]]
    execution: Mapped[Optional[dict]] = mapped_column(JSON)   # bank slip no, paid to, notes
    verified_by: Mapped[Optional[str]] = mapped_column(String(16))
    verified_at: Mapped[Optional[dt.datetime]]
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
