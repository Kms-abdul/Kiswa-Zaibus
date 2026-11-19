"""Razorpay (payments, refunds, settlements, webhooks, payment links, reconciliation) and MSEA ERP sync."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at

LONG_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), 'mysql', 'mariadb')


class RzpPayment(Base):
    __tablename__ = 'rzp_payments'
    __table_args__ = (Index('ix_rzp_payments_paid_at', 'paid_at'),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)        # pay_...
    order_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    company: Mapped[str] = mapped_column(String(4))
    ref: Mapped[str] = mapped_column(String(200), index=True)            # online order group / invoice / link
    ref_type: Mapped[str] = mapped_column(String(30))
    adm_no: Mapped[Optional[str]] = mapped_column(String(20))
    payer_mobile: Mapped[Optional[str]] = mapped_column(String(20))
    amount: Mapped[Decimal]
    method: Mapped[str] = mapped_column(String(20))
    method_detail: Mapped[Optional[str]] = mapped_column(String(60))     # never card numbers
    status: Mapped[str] = mapped_column(String(20))                      # Created Authorised Captured Failed Refunded Partially refunded
    bank_ref: Mapped[Optional[str]] = mapped_column(String(60))
    fee: Mapped[Decimal] = mapped_column(server_default='0')
    tax: Mapped[Decimal] = mapped_column(server_default='0')
    refunded: Mapped[Decimal] = mapped_column(server_default='0')
    settlement_id: Mapped[Optional[str]] = mapped_column(String(40))
    app_ref: Mapped[Optional[str]] = mapped_column(String(200))          # receipt/invoice posted in this system
    webhook_verified: Mapped[bool] = mapped_column(server_default=text('0'))
    error_reason: Mapped[Optional[str]] = mapped_column(String(300))
    env: Mapped[str] = mapped_column(String(10))                         # Test Live Simulated
    paid_at: Mapped[dt.datetime]


class RzpRefund(Base):
    __tablename__ = 'rzp_refunds'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    payment_id: Mapped[str] = mapped_column(ForeignKey('rzp_payments.id'), index=True)
    amount: Mapped[Decimal]
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))
    requested_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()


class RzpSettlement(Base):
    __tablename__ = 'rzp_settlements'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    company: Mapped[str] = mapped_column(String(4))
    settled_on: Mapped[dt.date]
    gross: Mapped[Decimal]
    fee: Mapped[Decimal]
    tax: Mapped[Decimal]
    net: Mapped[Decimal]
    utr: Mapped[str] = mapped_column(String(40))
    journal_no: Mapped[Optional[str]] = mapped_column(String(30))


class RzpWebhookEvent(Base):
    __tablename__ = 'rzp_webhook_events'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(40), unique=True)
    event: Mapped[Optional[str]] = mapped_column(String(60))
    company: Mapped[Optional[str]] = mapped_column(String(4))
    signature_ok: Mapped[bool]
    duplicate: Mapped[bool] = mapped_column(server_default=text('0'))
    body: Mapped[str] = mapped_column(LONG_TEXT)
    received_at: Mapped[dt.datetime] = created_at()
    processed_at: Mapped[Optional[dt.datetime]]
    error: Mapped[Optional[str]] = mapped_column(String(300))


class PaymentLink(Base):
    __tablename__ = 'payment_links'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    short_url: Mapped[str] = mapped_column(String(160))
    company: Mapped[str] = mapped_column(String(4))
    purpose: Mapped[str] = mapped_column(String(20))
    customer_id: Mapped[Optional[str]] = mapped_column(String(30))
    amount: Mapped[Decimal]
    paid: Mapped[Decimal] = mapped_column(server_default='0')
    status: Mapped[str] = mapped_column(String(16))           # Issued Paid Expired Cancelled
    expires_on: Mapped[dt.date]
    description: Mapped[Optional[str]] = mapped_column(String(300))
    created_by: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
    adm_no: Mapped[Optional[str]] = mapped_column(String(20))
    invoice_no: Mapped[Optional[str]] = mapped_column(String(40))           # bill the money is applied to (else oldest bills / advance)
    mobile: Mapped[Optional[str]] = mapped_column(String(20))
    payment_id: Mapped[Optional[str]] = mapped_column(String(40))
    receipt_no: Mapped[Optional[str]] = mapped_column(String(30))
    paid_at: Mapped[Optional[dt.datetime]]
    env: Mapped[str] = mapped_column(String(10), server_default='Simulated')


class RzpException(Base):
    """Reconciliation exception found by the daily four-way match (gateway, app, settlement, bank)."""
    __tablename__ = 'rzp_exceptions'
    ex_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    ex_type: Mapped[str] = mapped_column(String(20))          # unposted amount_mismatch fee_mismatch not_settled duplicate failed_captured
    company: Mapped[Optional[str]] = mapped_column(String(4))
    ref: Mapped[Optional[str]] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(server_default='0')
    detail: Mapped[str] = mapped_column(String(500))
    ex_date: Mapped[dt.date]
    status: Mapped[str] = mapped_column(String(20), server_default='Open')   # Open Resolved
    resolution: Mapped[Optional[str]] = mapped_column(String(300))
    resolved_by: Mapped[Optional[str]] = mapped_column(String(16))
    resolved_at: Mapped[Optional[dt.datetime]]


class ErpSyncRun(Base):
    __tablename__ = 'erp_sync_runs'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(20), unique=True)
    started_at: Mapped[dt.datetime] = created_at()
    finished_at: Mapped[Optional[dt.datetime]]
    triggered_by: Mapped[str] = mapped_column(String(60))
    trigger_type: Mapped[str] = mapped_column(String(20))     # manual schedule file
    feeds: Mapped[str] = mapped_column(String(200))
    dry_run: Mapped[bool] = mapped_column(server_default=text('0'))
    created: Mapped[int] = mapped_column(Integer, server_default='0')
    updated: Mapped[int] = mapped_column(Integer, server_default='0')
    skipped: Mapped[int] = mapped_column(Integer, server_default='0')
    failed: Mapped[int] = mapped_column(Integer, server_default='0')
    status: Mapped[str] = mapped_column(String(30))          # Running Completed Completed with errors


class ErpSyncRecord(Base):
    __tablename__ = 'erp_sync_records'
    __table_args__ = (Index('ix_erp_sync_records_feed_src', 'feed', 'source_ref'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('erp_sync_runs.id'), index=True)
    feed: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(3))         # in out
    source_ref: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(20))           # created updated skipped failed
    status: Mapped[str] = mapped_column(String(30), index=True)
    error: Mapped[Optional[str]] = mapped_column(String(500))
    snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    tries: Mapped[int] = mapped_column(Integer, server_default='1')
    created_at: Mapped[dt.datetime] = created_at()


class ErpKey(Base):
    """One row per ERP record ever imported: repeated syncs update instead of duplicating."""
    __tablename__ = 'erp_keys'
    feed: Mapped[str] = mapped_column(String(20), primary_key=True)
    source_ref: Mapped[str] = mapped_column(String(60), primary_key=True)
    version: Mapped[str] = mapped_column(String(80))          # hash of the last imported content
    target_ref: Mapped[Optional[str]] = mapped_column(String(60))
    synced_at: Mapped[dt.datetime] = created_at()


__all__ = ['RzpPayment', 'RzpRefund', 'RzpSettlement', 'RzpWebhookEvent', 'PaymentLink', 'RzpException',
           'ErpSyncRun', 'ErpSyncRecord', 'ErpKey', 'JSON']
