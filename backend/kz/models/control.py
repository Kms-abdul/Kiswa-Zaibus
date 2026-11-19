"""Control: audit trail (append-only, hash-chained), access log, approvals, routing, evidence, alerts, outbox."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column

from .base import DT6, Base, created_at

LONG_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), 'mysql', 'mariadb')


class AuditLog(Base):
    """Append-only. Database triggers (migration 0002) refuse UPDATE and DELETE.
    hash = sha256(json([prev_hash, at, user_id, user_name, action, ref, old_value, new_value, reason]))."""
    __tablename__ = 'audit_log'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    user_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Optional[str]] = mapped_column(String(60))
    company: Mapped[Optional[str]] = mapped_column(String(8))
    branch_id: Mapped[Optional[str]] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(120))
    ref: Mapped[Optional[str]] = mapped_column(String(120), index=True)
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(String(500))
    category: Mapped[Optional[str]] = mapped_column(String(30), index=True)
    status: Mapped[Optional[str]] = mapped_column(String(30))
    device: Mapped[Optional[str]] = mapped_column(String(160))
    ip: Mapped[Optional[str]] = mapped_column(String(45))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class AuditHead(Base):
    """Single row holding the tip of the audit hash chain; locked FOR UPDATE so writers never fork the chain."""
    __tablename__ = 'audit_head'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_id: Mapped[int] = mapped_column(BigInteger, server_default='0')
    hash: Mapped[str] = mapped_column(String(64))


class AccessLog(Base):
    __tablename__ = 'access_log'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'), index=True)
    user_id: Mapped[Optional[str]] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(40))
    what: Mapped[str] = mapped_column(String(200))
    detail: Mapped[Optional[str]] = mapped_column(String(300))


class Approval(Base):
    __tablename__ = 'approvals'
    __table_args__ = (Index('ix_approvals_status_role', 'status', 'required_role'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    no: Mapped[str] = mapped_column(String(20), unique=True)
    kind: Mapped[str] = mapped_column(String(30))              # see services/approvals.py HANDLERS
    sub_kind: Mapped[Optional[str]] = mapped_column(String(30))
    company: Mapped[Optional[str]] = mapped_column(String(4))
    branch_id: Mapped[Optional[str]] = mapped_column(String(16))   # NULL = Head Office
    amount: Mapped[Decimal] = mapped_column(server_default='0')
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[Optional[str]] = mapped_column(String(1000))
    reason: Mapped[Optional[str]] = mapped_column(String(500))
    ref: Mapped[Optional[str]] = mapped_column(String(60), index=True)     # document the request is about
    payload: Mapped[dict] = mapped_column(JSON)
    required_role: Mapped[str] = mapped_column(String(60))
    super_admin_only: Mapped[bool] = mapped_column(server_default=text('0'))
    status: Mapped[str] = mapped_column(String(12), server_default='Pending')   # Pending Approved Rejected Returned Cancelled
    requested_by: Mapped[str] = mapped_column(String(16))
    requested_role: Mapped[Optional[str]] = mapped_column(String(60))
    requested_at: Mapped[dt.datetime] = created_at()
    decided_by: Mapped[Optional[str]] = mapped_column(String(16))
    decided_at: Mapped[Optional[dt.datetime]]
    result: Mapped[Optional[str]] = mapped_column(String(500))
    escalated_at: Mapped[Optional[dt.datetime]]
    signature: Mapped[Optional[str]] = mapped_column(String(255), index=True)


class ApprovalLog(Base):
    __tablename__ = 'approval_log'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    approval_id: Mapped[int] = mapped_column(ForeignKey('approvals.id', ondelete='CASCADE'), index=True)
    at: Mapped[dt.datetime] = created_at()
    user_name: Mapped[str] = mapped_column(String(120))
    role: Mapped[Optional[str]] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(40))
    remarks: Mapped[Optional[str]] = mapped_column(String(500))


class RoutingRule(Base):
    __tablename__ = 'routing_rules'
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    txn_type: Mapped[str] = mapped_column(String(30))
    location: Mapped[Optional[str]] = mapped_column(String(10))     # NULL = any
    up_to: Mapped[Optional[Decimal]]                                  # NULL = no upper bound
    role: Mapped[str] = mapped_column(ForeignKey('roles.name', onupdate='CASCADE'))


class Evidence(Base):
    __tablename__ = 'evidence'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(String(120), index=True)
    company: Mapped[Optional[str]] = mapped_column(String(4))
    kind: Mapped[str] = mapped_column(String(60))
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[Optional[str]] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_path: Mapped[str] = mapped_column(String(400))
    sha256: Mapped[str] = mapped_column(String(64))
    remarks: Mapped[Optional[str]] = mapped_column(String(500))
    uploaded_by: Mapped[str] = mapped_column(String(16))
    uploaded_at: Mapped[dt.datetime] = created_at()
    status: Mapped[str] = mapped_column(String(12), server_default='Active')            # Active Superseded
    superseded_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    verify_status: Mapped[str] = mapped_column(String(12), server_default='Submitted')  # Submitted Verified Rejected
    verified_by: Mapped[Optional[str]] = mapped_column(String(16))
    verified_at: Mapped[Optional[dt.datetime]]
    verify_remarks: Mapped[Optional[str]] = mapped_column(String(500))


class AlertRule(Base):
    __tablename__ = 'alert_rules'
    alert_type: Mapped[str] = mapped_column(String(60), primary_key=True)
    in_app: Mapped[bool] = mapped_column(server_default=text('1'))
    email: Mapped[bool] = mapped_column(server_default=text('1'))
    whatsapp: Mapped[bool] = mapped_column(server_default=text('0'))
    roles: Mapped[list] = mapped_column(JSON)


class Alert(Base):
    __tablename__ = 'alerts'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company: Mapped[Optional[str]] = mapped_column(String(4))
    branch_id: Mapped[Optional[str]] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(8), server_default='warn')   # info warn bad
    alert_type: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(String(500))
    ref: Mapped[Optional[str]] = mapped_column(String(60))
    created_at: Mapped[dt.datetime] = mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'), index=True)


class Outbox(Base):
    """Outgoing email / SMS / WhatsApp; sent by the scheduler (`flask kz tick`) with retries and back-off."""
    __tablename__ = 'outbox'
    __table_args__ = (Index('ix_outbox_status_created', 'status', 'created_at'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(10))           # email sms whatsapp
    to_name: Mapped[Optional[str]] = mapped_column(String(120))
    address: Mapped[str] = mapped_column(String(160))
    subject: Mapped[Optional[str]] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    alert_type: Mapped[Optional[str]] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(10), server_default='Queued')   # Queued Sending Sent Failed
    attempts: Mapped[int] = mapped_column(Integer, server_default='0')
    last_error: Mapped[Optional[str]] = mapped_column(String(300))
    created_at: Mapped[dt.datetime] = created_at()
    sent_at: Mapped[Optional[dt.datetime]]
    attachment_name: Mapped[Optional[str]] = mapped_column(String(120))
    attachment: Mapped[Optional[str]] = mapped_column(LONG_TEXT)      # CSV text of a scheduled report
