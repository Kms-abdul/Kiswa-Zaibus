"""Discount schemes, custom fields, scheduled report deliveries (added in migration 0003)."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import DT6, Base, created_at


class DiscountScheme(Base):
    """Pre-approved discount applied automatically at the counter (best matching scheme wins).
    scope: all | category | school | item."""
    __tablename__ = 'discount_schemes'
    __table_args__ = (Index('ix_discount_schemes_company_active', 'company', 'active'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[str] = mapped_column(ForeignKey('companies.code'))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    scope: Mapped[str] = mapped_column(String(10))
    category: Mapped[Optional[str]] = mapped_column(String(60))
    school_id: Mapped[Optional[str]] = mapped_column(String(10))
    item_id: Mapped[Optional[str]] = mapped_column(String(24))
    pct: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    valid_from: Mapped[Optional[dt.date]]
    valid_to: Mapped[Optional[dt.date]]
    online: Mapped[bool] = mapped_column(server_default=text('1'))        # also applies in the parent store
    active: Mapped[bool] = mapped_column(server_default=text('1'))
    created_by: Mapped[Optional[str]] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()


class CustomField(Base):
    """Extra field shown on an entity's form. entity: item | customer | supplier | student. type: text | number | date | select."""
    __tablename__ = 'custom_fields'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity: Mapped[str] = mapped_column(String(12))
    label: Mapped[str] = mapped_column(String(60))
    type: Mapped[str] = mapped_column(String(8), server_default='text')
    options: Mapped[Optional[list]] = mapped_column(JSON)
    required: Mapped[bool] = mapped_column(server_default=text('0'))
    sort: Mapped[int] = mapped_column(Integer, server_default='0')
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class CustomValue(Base):
    __tablename__ = 'custom_values'
    field_id: Mapped[int] = mapped_column(ForeignKey('custom_fields.id', ondelete='CASCADE'), primary_key=True)
    ref: Mapped[str] = mapped_column(String(30), primary_key=True)       # item id, customer id, supplier id or admission no.
    value: Mapped[Optional[str]] = mapped_column(String(300))
    updated_at: Mapped[dt.datetime] = mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'))


class ReportSchedule(Base):
    """Report e-mailed as CSV on a schedule; runs with the permissions of the user who created it."""
    __tablename__ = 'report_schedules'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120))
    report: Mapped[str] = mapped_column(String(40))                      # e.g. sales-register (path under /api/reports/)
    params: Mapped[dict] = mapped_column(JSON)                           # {company, by, account, period: today|yesterday|mtd|last_month}
    frequency: Mapped[str] = mapped_column(String(8))                    # daily weekly monthly
    run_at: Mapped[str] = mapped_column(String(5), server_default='21:30')
    weekday: Mapped[Optional[int]] = mapped_column(Integer)              # 0 = Monday (weekly)
    month_day: Mapped[Optional[int]] = mapped_column(Integer)            # 1-28 (monthly)
    recipients: Mapped[list] = mapped_column(JSON)
    owner_id: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(server_default=text('1'))
    last_run_at: Mapped[Optional[dt.datetime]]
    last_result: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = created_at()


__all__ = ['DiscountScheme', 'CustomField', 'CustomValue', 'ReportSchedule']
