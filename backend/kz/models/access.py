"""Users, roles, permissions, approval limits, sessions, sign-in challenges, devices, security events."""
import datetime as dt
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import DT6, Base, created_at


class Role(Base):
    __tablename__ = 'roles'
    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(80))                   # shown to people; name stays the key
    description: Mapped[Optional[str]] = mapped_column(String(200))
    approval_rank: Mapped[Decimal] = mapped_column(Numeric(4, 2), server_default='0')   # 0 = cannot approve
    read_only: Mapped[bool] = mapped_column(server_default=text('0'))
    is_custom: Mapped[bool] = mapped_column(server_default=text('0'))
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class PermModule(Base):
    """Permission catalogue: a module (section on the Role Permissions screen). Synced from core/permission_catalog.py."""
    __tablename__ = 'perm_modules'
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    sort: Mapped[int] = mapped_column(Integer, server_default='0')
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class PermScreen(Base):
    """Permission catalogue: a screen key and the actions the code checks on it. Synced from core/permission_catalog.py."""
    __tablename__ = 'perm_screens'
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    module_key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(120))
    sort: Mapped[int] = mapped_column(Integer, server_default='0')
    actions: Mapped[list] = mapped_column(JSON)
    extras: Mapped[list] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(server_default=text('1'))


class Permission(Base):
    """Permission matrix row: role may do `action` on `screen_key` (module or module.screen)."""
    __tablename__ = 'permissions'
    role: Mapped[str] = mapped_column(ForeignKey('roles.name', onupdate='CASCADE'), primary_key=True)
    screen_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    action: Mapped[str] = mapped_column(String(12), primary_key=True)


class ApprovalLimit(Base):
    __tablename__ = 'approval_limits'
    role: Mapped[str] = mapped_column(ForeignKey('roles.name', onupdate='CASCADE'), primary_key=True)
    limit_key: Mapped[str] = mapped_column(String(20), primary_key=True)   # disc po pay refund adj exp cash credit transfer jv dn recon
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), server_default='0')


class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    emp_id: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    mobile: Mapped[str] = mapped_column(String(20), unique=True)
    email: Mapped[Optional[str]] = mapped_column(String(120))
    department: Mapped[Optional[str]] = mapped_column(String(80))
    designation: Mapped[Optional[str]] = mapped_column(String(120))
    main_role: Mapped[str] = mapped_column(ForeignKey('roles.name', onupdate='CASCADE'))
    manager_id: Mapped[Optional[str]] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), server_default='Active')   # Active Inactive Locked Suspended Resigned
    join_date: Mapped[Optional[dt.date]]
    relieve_date: Mapped[Optional[dt.date]]
    valid_from: Mapped[Optional[dt.date]]
    valid_to: Mapped[Optional[dt.date]]
    all_branches: Mapped[bool] = mapped_column(server_default=text('0'))
    pw_hash: Mapped[Optional[str]] = mapped_column(String(100))
    pw_set_at: Mapped[Optional[dt.datetime]]
    pw_must_change: Mapped[bool] = mapped_column(server_default=text('1'))
    failed_count: Mapped[int] = mapped_column(Integer, server_default='0')
    locked_at: Mapped[Optional[dt.datetime]]
    lock_reason: Mapped[Optional[str]] = mapped_column(String(200))
    last_login_at: Mapped[Optional[dt.datetime]]
    last_login_device: Mapped[Optional[str]] = mapped_column(String(160))
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(45))
    created_at: Mapped[dt.datetime] = created_at()


class UserRole(Base):
    __tablename__ = 'user_roles'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    role: Mapped[str] = mapped_column(ForeignKey('roles.name', onupdate='CASCADE'), primary_key=True)


class UserCompany(Base):
    __tablename__ = 'user_companies'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    company: Mapped[str] = mapped_column(ForeignKey('companies.code'), primary_key=True)


class UserBranch(Base):
    __tablename__ = 'user_branches'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    branch_id: Mapped[str] = mapped_column(ForeignKey('branches.id'), primary_key=True)


class UserZone(Base):
    __tablename__ = 'user_zones'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    zone_id: Mapped[str] = mapped_column(ForeignKey('zones.id'), primary_key=True)


class UserRegion(Base):
    __tablename__ = 'user_regions'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    region_id: Mapped[str] = mapped_column(ForeignKey('regions.id'), primary_key=True)


class UserDevice(Base):
    __tablename__ = 'user_devices'
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    device_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    first_seen: Mapped[dt.datetime] = created_at()


class PasswordHistory(Base):
    __tablename__ = 'password_history'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    pw_hash: Mapped[str] = mapped_column(String(100))
    set_at: Mapped[dt.datetime] = created_at()


class LoginChallenge(Base):
    """One-time-code step of sign-in / password reset (staff)."""
    __tablename__ = 'login_challenges'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    purpose: Mapped[str] = mapped_column(String(10))           # login | reset
    otp_hash: Mapped[str] = mapped_column(String(64))
    tries: Mapped[int] = mapped_column(Integer, server_default='0')
    stage: Mapped[str] = mapped_column(String(10), server_default='otp')   # otp | change | done | failed
    expires_at: Mapped[dt.datetime]
    used_at: Mapped[Optional[dt.datetime]]
    ip: Mapped[Optional[str]] = mapped_column(String(45))
    device: Mapped[Optional[str]] = mapped_column(String(160))
    created_at: Mapped[dt.datetime] = created_at()


class Session(Base):
    __tablename__ = 'sessions'
    __table_args__ = (Index('ix_sessions_user_ended', 'user_id', 'ended_at'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    company: Mapped[Optional[str]] = mapped_column(String(4))
    branch_id: Mapped[Optional[str]] = mapped_column(String(16))
    device: Mapped[Optional[str]] = mapped_column(String(160))
    device_id: Mapped[Optional[str]] = mapped_column(String(80))
    ip: Mapped[Optional[str]] = mapped_column(String(45))
    created_at: Mapped[dt.datetime] = created_at()
    last_seen: Mapped[dt.datetime] = mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'))
    reauth_at: Mapped[Optional[dt.datetime]]
    ended_at: Mapped[Optional[dt.datetime]]
    end_reason: Mapped[Optional[str]] = mapped_column(String(120))


class SecurityEvent(Base):
    __tablename__ = 'security_events'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[dt.datetime] = mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'), index=True)
    type: Mapped[str] = mapped_column(String(60))
    user_id: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    user_name: Mapped[Optional[str]] = mapped_column(String(120))
    detail: Mapped[Optional[str]] = mapped_column(String(500))
    device: Mapped[Optional[str]] = mapped_column(String(160))
    ip: Mapped[Optional[str]] = mapped_column(String(45))


class AccessSchedule(Base):
    """Effective-dated access change applied by the scheduler (resignation, transfer, suspension, reinstatement)."""
    __tablename__ = 'access_schedule'
    __table_args__ = (Index('ix_access_schedule_due', 'status', 'effective_on'),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    change_type: Mapped[str] = mapped_column(String(12))       # Resign Transfer Suspend Reinstate
    effective_on: Mapped[dt.date]
    data: Mapped[Optional[dict]] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), server_default='Scheduled')
    requested_by: Mapped[Optional[str]] = mapped_column(String(16))
    created_at: Mapped[dt.datetime] = created_at()
    applied_at: Mapped[Optional[dt.datetime]]


class ParentChallenge(Base):
    """One-time code for a parent signing in to the online store (admission number + registered mobile)."""
    __tablename__ = 'parent_challenges'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    adm_no: Mapped[str] = mapped_column(String(20), index=True)
    mobile: Mapped[str] = mapped_column(String(20))
    otp_hash: Mapped[str] = mapped_column(String(64))
    tries: Mapped[int] = mapped_column(Integer, server_default='0')
    expires_at: Mapped[dt.datetime]
    used_at: Mapped[Optional[dt.datetime]]
    created_at: Mapped[dt.datetime] = created_at()


class ParentSession(Base):
    __tablename__ = 'parent_sessions'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    mobile: Mapped[str] = mapped_column(String(20), index=True)
    adm_nos: Mapped[list] = mapped_column(JSON)                # children reachable with this mobile
    created_at: Mapped[dt.datetime] = created_at()
    last_seen: Mapped[dt.datetime] = mapped_column(DT6, server_default=text('CURRENT_TIMESTAMP(6)'))
    ended_at: Mapped[Optional[dt.datetime]]
