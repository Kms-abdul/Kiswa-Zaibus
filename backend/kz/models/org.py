"""Organisation: companies, regions, zones, locations, branches (company x location), number series, settings."""
import datetime as dt
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import DT6, Base


class Company(Base):
    __tablename__ = 'companies'
    code: Mapped[str] = mapped_column(String(4), primary_key=True)          # KG, ZE
    name: Mapped[str] = mapped_column(String(120))
    short_name: Mapped[str] = mapped_column(String(40))
    gstin: Mapped[Optional[str]] = mapped_column(String(15))
    state_code: Mapped[str] = mapped_column(String(2), server_default='36')
    address: Mapped[Optional[str]] = mapped_column(String(300))
    phone: Mapped[Optional[str]] = mapped_column(String(30))
    email: Mapped[Optional[str]] = mapped_column(String(120))
    online_prefix: Mapped[Optional[str]] = mapped_column(String(10))
    books_locked_to: Mapped[Optional[dt.date]]                                # no posting on or before this date
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class Region(Base):
    __tablename__ = 'regions'
    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))


class Zone(Base):
    __tablename__ = 'zones'
    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    region_id: Mapped[str] = mapped_column(ForeignKey('regions.id'))


class Location(Base):
    """A physical place (store, school counter, warehouse). Shared by both companies."""
    __tablename__ = 'locations'
    code: Mapped[str] = mapped_column(String(10), primary_key=True)          # ABD, KPH, MSA, MYJ, WH
    name: Mapped[str] = mapped_column(String(120))
    public_name: Mapped[Optional[str]] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20))                            # Store | Counter | Warehouse
    zone_id: Mapped[Optional[str]] = mapped_column(ForeignKey('zones.id'))
    address: Mapped[Optional[str]] = mapped_column(String(300))
    contact: Mapped[Optional[str]] = mapped_column(String(120))
    is_central_wh: Mapped[bool] = mapped_column(server_default=text('0'))
    active: Mapped[bool] = mapped_column(server_default=text('1'))
    closed_on: Mapped[Optional[dt.date]]
    closed_reason: Mapped[Optional[str]] = mapped_column(String(300))
    stock_moved_to: Mapped[Optional[str]] = mapped_column(String(10))


class Branch(Base):
    """One company at one location, e.g. KG-ABD. Stock, invoices and journal lines carry a branch."""
    __tablename__ = 'branches'
    __table_args__ = (UniqueConstraint('company', 'location'),)
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    company: Mapped[str] = mapped_column(ForeignKey('companies.code'))
    location: Mapped[str] = mapped_column(ForeignKey('locations.code'))
    code: Mapped[str] = mapped_column(String(10))                            # invoice series code (ABD, MSC ...)
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class NumberSeries(Base):
    __tablename__ = 'number_series'
    company: Mapped[str] = mapped_column(String(4), primary_key=True)        # company code or HO
    series: Mapped[str] = mapped_column(String(20), primary_key=True)        # INV:ABD, RV, JV, PO ...
    fy: Mapped[str] = mapped_column(String(4), primary_key=True)             # 2627 = FY 2026-27
    last_no: Mapped[int] = mapped_column(Integer, server_default='0')


class Setting(Base):
    __tablename__ = 'settings'
    skey: Mapped[str] = mapped_column(String(60), primary_key=True)
    svalue: Mapped[dict] = mapped_column(JSON)
    updated_by: Mapped[Optional[str]] = mapped_column(String(16))
    updated_at: Mapped[dt.datetime] = mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'))
