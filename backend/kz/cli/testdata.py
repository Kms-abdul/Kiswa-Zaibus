"""Test data snapshot (2.8): a complete, consistent set of sample transactions for trying the system.

`flask kz seed --test-data` loads seed/test_data.json.gz into an EMPTY database instead of the go-live master data:
two companies, six locations, 17 users (one per main role), items, students, suppliers, purchase orders, GRNs,
transfers, counter bills, MSEA vouchers, online orders, stock audits, cash shifts and handovers, journals,
finance vouchers, approvals and the audit trail.

Every user gets the temporary password given with --temp-password and must change it at first sign-in.
Sign-in sessions, one-time codes, devices, queued messages and evidence files are not part of the snapshot.
Never load this on the live server: it refuses when KZ_ENV=production unless --force is given.

`flask kz export-test-data` writes a new snapshot from the current database (developers only).
"""
import base64
import datetime as dt
import decimal
import gzip
import json
from pathlib import Path

from sqlalchemy import text

from .. import db

SNAPSHOT = Path(__file__).resolve().parents[2] / 'seed' / 'test_data.json.gz'
# never exported: secrets of live sign-ins, devices, queued messages (may hold one-time codes), evidence rows whose files stay on the old server
SKIP = {'alembic_version', 'sessions', 'login_challenges', 'parent_sessions', 'parent_challenges', 'user_devices', 'outbox', 'evidence'}


def _enc(v):
    if isinstance(v, decimal.Decimal):
        return str(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        s = int(v.total_seconds())
        return f'{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}'
    if isinstance(v, (bytes, bytearray)):
        return {'b64': base64.b64encode(bytes(v)).decode()}
    return v


def _dec(v):
    return base64.b64decode(v['b64']) if isinstance(v, dict) and 'b64' in v else v


def _tables(conn):
    return [r[0] for r in conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() AND table_type='BASE TABLE' ORDER BY table_name"))]


def export_snapshot(path=SNAPSHOT):
    conn = db.session().connection()
    out = {'meta': {'app_version': __import__('kz').__version__, 'migration': conn.execute(text('SELECT version_num FROM alembic_version')).scalar(),
                    'exported_on': dt.date.today().isoformat()}, 'tables': {}}
    for t in _tables(conn):
        if t in SKIP:
            continue
        res = conn.execute(text(f'SELECT * FROM `{t}`'))
        rows = [[_enc(v) for v in r] for r in res]
        if rows:
            out['tables'][t] = {'cols': list(res.keys()), 'rows': rows}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    return {t: len(v['rows']) for t, v in out['tables'].items()}


def load_snapshot(temp_pw, path=SNAPSHOT):
    """Load the snapshot into an empty database. Returns {table: rows} or None when the database already has data."""
    from ..core.security import hash_pw
    if db.val('SELECT COUNT(*) FROM companies'):
        return None
    with gzip.open(path, 'rt', encoding='utf-8') as f:
        snap = json.load(f)
    conn = db.session().connection()
    have = conn.execute(text('SELECT version_num FROM alembic_version')).scalar()
    if have != snap['meta']['migration']:
        raise RuntimeError(f"The snapshot was taken at migration {snap['meta']['migration']}; this database is at {have}. Run `alembic upgrade head` first.")
    existing = set(_tables(conn))
    conn.execute(text('SET FOREIGN_KEY_CHECKS=0'))
    try:
        for t in existing - SKIP:          # tables some migrations pre-fill (roles, settings …) are replaced by the snapshot as a whole
            if t in snap['tables'] and t != 'audit_log':
                conn.execute(text(f'DELETE FROM `{t}`'))
        counts = {}
        for t, d in snap['tables'].items():
            if t not in existing:
                continue
            cols = d['cols']
            sql = text(f"INSERT INTO `{t}` ({', '.join(f'`{c}`' for c in cols)}) VALUES ({', '.join(f':c{i}' for i in range(len(cols)))})")
            batch = [{f'c{i}': _dec(v) for i, v in enumerate(r)} for r in d['rows']]
            for k in range(0, len(batch), 500):
                conn.execute(sql, batch[k:k + 500])
            counts[t] = len(batch)
    finally:
        conn.execute(text('SET FOREIGN_KEY_CHECKS=1'))
    # every user signs in with the temporary password and must change it; old password history is replaced
    h = hash_pw(temp_pw)
    conn.execute(text('UPDATE users SET pw_hash=:h, pw_must_change=1, pw_set_at=NOW(), failed_count=0, locked_at=NULL'), {'h': h})
    conn.execute(text('DELETE FROM password_history'))
    conn.execute(text('INSERT INTO password_history (user_id, pw_hash) SELECT id, pw_hash FROM users'))
    return counts
