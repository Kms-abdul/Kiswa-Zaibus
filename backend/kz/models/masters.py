"""Masters: chart of accounts, schools, item categories, suppliers, items, packages, students, customers, MSEA vouchers."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import COST, Base, created_at


class Account(Base):
    """Chart of accounts per company. grp: A asset, L liability, E equity, I income, X expense."""
    __tablename__ = 'accounts'
    company: Mapped[str] = mapped_column(String(4), primary_key=True)
    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    grp: Mapped[str] = mapped_column(String(1))
    is_control: Mapped[bool] = mapped_column(server_default=text('0'))   # posted only by its sub-ledger
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class School(Base):
    __tablename__ = 'schools'
    id: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    board: Mapped[Optional[str]] = mapped_column(String(40))
    area: Mapped[Optional[str]] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class ItemCategory(Base):
    __tablename__ = 'item_categories'
    company: Mapped[str] = mapped_column(String(4), primary_key=True)
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    hsn: Mapped[Optional[str]] = mapped_column(String(10))
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default='0')
    max_disc: Mapped[Optional[Decimal]] = mapped_column(Numeric(5, 2))
    isbn_required: Mapped[bool] = mapped_column(server_default=text('0'))
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class Supplier(Base):
    __tablename__ = 'suppliers'
    __table_args__ = (UniqueConstraint('company', 'name'),)
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    company: Mapped[str] = mapped_column(ForeignKey('companies.code'))
    name: Mapped[str] = mapped_column(String(160))
    gstin: Mapped[Optional[str]] = mapped_column(String(15))
    city: Mapped[Optional[str]] = mapped_column(String(80))
    terms_days: Mapped[int] = mapped_column(Integer, server_default='30')
    contact: Mapped[Optional[str]] = mapped_column(String(160))
    bank_name: Mapped[Optional[str]] = mapped_column(String(120))
    bank_account: Mapped[Optional[str]] = mapped_column(String(40))
    bank_ifsc: Mapped[Optional[str]] = mapped_column(String(11))
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class Item(Base):
    __tablename__ = 'items'
    __table_args__ = (UniqueConstraint('company', 'isbn13'), UniqueConstraint('company', 'barcode'), Index('ix_items_company_name', 'company', 'name'))
    id: Mapped[str] = mapped_column(String(24), primary_key=True)         # KG-MSA-SHT-24, ZE-0410
    company: Mapped[str] = mapped_column(ForeignKey('companies.code'))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(60))
    size: Mapped[Optional[str]] = mapped_column(String(20))
    colour: Mapped[Optional[str]] = mapped_column(String(30))
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    class_name: Mapped[Optional[str]] = mapped_column(String(60))
    term: Mapped[Optional[str]] = mapped_column(String(20))
    academic_year: Mapped[Optional[str]] = mapped_column(String(10))
    school_id: Mapped[Optional[str]] = mapped_column(String(10))
    hsn: Mapped[Optional[str]] = mapped_column(String(10))
    gst_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default='0')
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default='0')       # GST-inclusive selling price
    cost: Mapped[Decimal] = mapped_column(COST, server_default='0')                  # moving-average cost, excl. GST
    barcode: Mapped[Optional[str]] = mapped_column(String(30))
    isbn: Mapped[Optional[str]] = mapped_column(String(13))
    isbn13: Mapped[Optional[str]] = mapped_column(String(13))
    isbn_na_reason: Mapped[Optional[str]] = mapped_column(String(60))
    supplier_id: Mapped[Optional[str]] = mapped_column(String(16))
    reorder_level: Mapped[int] = mapped_column(Integer, server_default='0')
    bin_location: Mapped[Optional[str]] = mapped_column(String(20))
    uom: Mapped[str] = mapped_column(String(10), server_default='Nos')        # unit of measure: Nos, Pair, Set, Pack, Book
    unpriced: Mapped[bool] = mapped_column(server_default=text('0'))
    active: Mapped[bool] = mapped_column(server_default=text('1'))
    created_at: Mapped[dt.datetime] = created_at()


class Package(Base):
    """School uniform / book set sold as one choice at the counter (sizes chosen per category)."""
    __tablename__ = 'packages'
    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    company: Mapped[str] = mapped_column(String(4))
    name: Mapped[str] = mapped_column(String(160))
    school_id: Mapped[Optional[str]] = mapped_column(String(10))
    class_name: Mapped[Optional[str]] = mapped_column(String(60))
    gender: Mapped[Optional[str]] = mapped_column(String(10))
    disc_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), server_default='0')
    active: Mapped[bool] = mapped_column(server_default=text('1'))
    lines: Mapped[list['PackageLine']] = relationship(order_by='PackageLine.line_no', cascade='all, delete-orphan')


class PackageLine(Base):
    __tablename__ = 'package_lines'
    package_id: Mapped[str] = mapped_column(ForeignKey('packages.id', ondelete='CASCADE'), primary_key=True)
    line_no: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    item_id: Mapped[Optional[str]] = mapped_column(String(24))     # fixed item (books) ...
    category: Mapped[Optional[str]] = mapped_column(String(60))    # ... or a category whose size is chosen (uniforms)
    qty: Mapped[int] = mapped_column(Integer)


class Student(Base):
    """Cached from the MSEA ERP (ERP sync). The counter bills every sale against an admission number."""
    __tablename__ = 'students'
    adm_no: Mapped[str] = mapped_column(String(20), primary_key=True)
    erp_ref: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    gender: Mapped[Optional[str]] = mapped_column(String(1))
    class_name: Mapped[str] = mapped_column(String(60))
    section: Mapped[Optional[str]] = mapped_column(String(30))
    parent_mobile: Mapped[Optional[str]] = mapped_column(String(20), index=True)
    parent_email: Mapped[Optional[str]] = mapped_column(String(120))
    category: Mapped[str] = mapped_column(String(40), server_default='Regular')   # Regular, MSEA Sponsored, ...
    status: Mapped[str] = mapped_column(String(20), server_default='Active')      # Active, Transferred, Left
    school_id: Mapped[Optional[str]] = mapped_column(String(10))
    campus: Mapped[Optional[str]] = mapped_column(String(30))
    location: Mapped[Optional[str]] = mapped_column(String(60))       # city / state, e.g. Hyderabad, Maharashtra
    academic_year: Mapped[Optional[str]] = mapped_column(String(10))
    synced_at: Mapped[Optional[dt.datetime]]


class Customer(Base):
    __tablename__ = 'customers'
    __table_args__ = (Index('ix_customers_company_adm', 'company', 'adm_no'),)
    id: Mapped[str] = mapped_column(String(30), primary_key=True)        # KG-STU-MSA260101, KG-WALKIN, KG-MSEA
    company: Mapped[str] = mapped_column(String(4))
    type: Mapped[str] = mapped_column(String(12))                        # Student Walk-in Institution Staff
    name: Mapped[str] = mapped_column(String(160))
    mobile: Mapped[Optional[str]] = mapped_column(String(20))
    adm_no: Mapped[Optional[str]] = mapped_column(String(20))
    gstin: Mapped[Optional[str]] = mapped_column(String(15))
    receivable_account: Mapped[str] = mapped_column(String(10), server_default='1200')   # 1210 for MSEA
    credit_limit: Mapped[Decimal] = mapped_column(server_default='0')


class MseaVoucher(Base):
    """Sponsorship voucher: MSEA pays for a sponsored student's items up to an entitlement."""
    __tablename__ = 'msea_vouchers'
    no: Mapped[str] = mapped_column(String(30), primary_key=True)
    adm_no: Mapped[str] = mapped_column(String(20), index=True)
    company: Mapped[str] = mapped_column(String(4))
    scope: Mapped[str] = mapped_column(String(60))                        # e.g. "Full uniform set", "Books Term 1"
    entitlement: Mapped[dict] = mapped_column(JSON)                       # {category: qty}
    used_qty: Mapped[dict] = mapped_column(JSON)                          # {category: qty issued}
    max_value: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    used_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default='0')
    valid_to: Mapped[dt.date]
    status: Mapped[str] = mapped_column(String(20))                       # Pending approval Approved Partly used Fully used Cancelled Expired Rejected
    approved_by: Mapped[Optional[str]] = mapped_column(String(120))
    remarks: Mapped[Optional[str]] = mapped_column(String(300))
    created_by: Mapped[Optional[str]] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
