"""Append-only, hash-chained audit trail.

Each entry stores the previous entry's hash and its own hash:
    hash = sha256(json([prev_hash, at, user_id, user_name, action, ref, old_value, new_value, reason]))
The chain tip lives in audit_head and is locked FOR UPDATE, so concurrent writers are serialised and
the chain never forks. The database refuses UPDATE and DELETE on audit_log (triggers in schema.sql).
"""
import datetime as dt
import hashlib
import json

from flask import g, has_request_context, request

from .. import db


def canonical(prev_hash, at, user_id, user_name, action, ref, old_value, new_value, reason):
    body = json.dumps([prev_hash, at.isoformat(timespec='microseconds'), user_id, user_name, action, ref, old_value, new_value, reason],
                      ensure_ascii=False, separators=(',', ':'), default=str)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _s(v):
    if v is None or isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False, default=str)


def write(action, ref=None, old=None, new=None, reason=None, category=None, status=None, company=None, branch=None, user=None):
    u = user or (g.get('user') if has_request_context() else None) or {}
    head = db.one('SELECT last_id, hash FROM audit_head WHERE id=1 FOR UPDATE')
    prev = head['hash']
    at = dt.datetime.now()
    old, new = _s(old), _s(new)
    user_name = u.get('name') or 'System'
    h = canonical(prev, at, u.get('id'), user_name, action, ref, old, new, reason)
    device = ip = None
    if has_request_context():
        device = (request.headers.get('User-Agent') or '')[:160]
        ip = request.remote_addr
    rid = db.insert('audit_log', dict(at=at, user_id=u.get('id'), user_name=user_name, role=u.get('main_role'), company=company,
                                      branch_id=branch, action=action, ref=ref, old_value=old, new_value=new, reason=reason,
                                      category=category, status=status, device=device, ip=ip, prev_hash=prev, hash=h))
    db.run('UPDATE audit_head SET last_id=:i, hash=:h WHERE id=1', i=rid, h=h)
    return rid


def verify(limit=None):
    """Recompute the chain. Returns (ok, checked, first_bad_id)."""
    rows = db.q('SELECT * FROM audit_log ORDER BY id' + (f' LIMIT {int(limit)}' if limit else ''))
    prev = '0' * 64
    for r in rows:
        if r['prev_hash'] != prev:
            return False, rows.index(r), r['id']
        h = canonical(r['prev_hash'], r['at'], r['user_id'], r['user_name'], r['action'], r['ref'], r['old_value'], r['new_value'], r['reason'])
        if h != r['hash']:
            return False, rows.index(r), r['id']
        prev = h
    return True, len(rows), None


def security_event(type_, detail, user=None, user_name=None):
    device = ip = None
    if has_request_context():
        device = (request.headers.get('User-Agent') or '')[:160]
        ip = request.remote_addr
    db.insert('security_events', dict(type=type_, user_id=(user or {}).get('id'), user_name=user_name or (user or {}).get('name'),
                                      detail=detail, device=device, ip=ip))
