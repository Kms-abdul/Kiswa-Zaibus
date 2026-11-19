"""Database engine and the per-request session.

One SQLAlchemy Session per request (flask.g.db). It commits when the response is successful (< 400) and rolls
back otherwise. Code that must keep its writes even when it answers with an error (failed sign-in counters,
lockouts, security events) calls keep_on_error().

Two styles are used on purpose:
  * ORM (session.add / session.get / select(Model)) for documents and their lines;
  * SQL text through q()/one()/val()/run() for lookups and reports.
Both run in the same transaction.
"""
import json
from datetime import timedelta
from decimal import Decimal

from flask import g, has_app_context
import datetime as _dt

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

_engine = None
_factory = None


def init_engine(url, pool_size=10, connect_args=None):
    global _engine, _factory
    _engine = create_engine(url, pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True, pool_recycle=1800,
                            isolation_level='READ COMMITTED', connect_args=connect_args or {}, future=True)
    _factory = sessionmaker(bind=_engine, autoflush=True, expire_on_commit=False, future=True)

    @event.listens_for(_engine, 'connect')
    def _same_clock(dbapi_conn, _rec):
        # NOW() / CURRENT_TIMESTAMP in the database must match Python's datetime.now(): sessions, OTP expiry and idle
        # time-outs compare the two. Each connection uses the app server's UTC offset (IST +05:30 on OCI when TZ is set).
        off = _dt.datetime.now().astimezone().utcoffset() or _dt.timedelta(0)
        mins = int(off.total_seconds() // 60)
        cur = dbapi_conn.cursor()
        cur.execute(f"SET time_zone = '{'+' if mins >= 0 else '-'}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}'")
        cur.close()
    return _engine


def engine():
    return _engine


def session():
    if 'db' not in g:
        g.db = _factory()
    return g.db


def keep_on_error():
    g.keep_on_error = True


def finish(ok: bool):
    s = g.pop('db', None)
    if s is None:
        return
    try:
        if ok or g.get('keep_on_error'):
            s.commit()
        else:
            s.rollback()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _params(p):
    return {k: (json.dumps(v, ensure_ascii=False, default=str) if isinstance(v, (dict, list)) else v) for k, v in (p or {}).items()}


def q(sql, **p):
    return [dict(r._mapping) for r in session().execute(text(sql), _params(p))]


def one(sql, **p):
    r = session().execute(text(sql), _params(p)).first()
    return dict(r._mapping) if r else None


def val(sql, **p):
    r = session().execute(text(sql), _params(p)).first()
    return r[0] if r else None


def run(sql, **p):
    return session().execute(text(sql), _params(p))


def add(obj):
    s = session()
    s.add(obj)
    s.flush()
    return obj


JSON_COLS = {'payload', 'svalue', 'roles', 'data', 'figures', 'denominations', 'opening', 'snapshot', 'line_items', 'entitlement',
             'used_qty', 'student_snapshot', 'events', 'execution', 'rates', 'applied', 'draft', 'lines', 'adm_nos', 'params', 'recipients', 'options'}


def clean(rows):
    """Make SQL rows JSON-ready (Decimal -> float, dates -> ISO text, JSON text -> objects)."""
    def fix(r):
        o = {}
        for k, v in r.items():
            if isinstance(v, Decimal):
                v = float(v)
            elif isinstance(v, timedelta):          # MySQL TIME comes back as timedelta
                secs = int(v.total_seconds())
                v = f'{secs // 3600:02d}:{secs % 3600 // 60:02d}:{secs % 60:02d}'
            elif hasattr(v, 'isoformat'):
                v = v.isoformat(sep=' ') if hasattr(v, 'hour') and hasattr(v, 'day') else v.isoformat()
            elif isinstance(v, str) and k in JSON_COLS and v[:1] in '[{':
                try:
                    v = json.loads(v)
                except ValueError:
                    pass
            o[k] = v
        return o
    if isinstance(rows, list):
        return [fix(r) for r in rows]
    return fix(rows) if rows else rows


def in_app():
    return has_app_context()


def insert(table, row):
    """INSERT one row with SQL text (used by core modules that predate the ORM models). Returns lastrowid."""
    cols = ', '.join(f'`{k}`' for k in row)
    ph = ', '.join(f':{k}' for k in row)
    return session().execute(text(f'INSERT INTO `{table}` ({cols}) VALUES ({ph})'), _params(row)).lastrowid
