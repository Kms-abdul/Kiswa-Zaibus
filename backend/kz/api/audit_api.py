"""Audit trail search and integrity check, security events, access log, alerts."""
import json

from flask import Blueprint, g, jsonify, request

from .. import db
from ..core import audit
from ..core.rbac import login_required, require

bp = Blueprint('audit', __name__, url_prefix='/api')


@bp.get('/audit')
@require('view', 'audit')
def audit_search():
    a = request.args
    sql = '''SELECT id, at, user_name, role, company, branch_id, action, ref, old_value, new_value, reason, category, status, device, ip
             FROM audit_log WHERE 1=1'''
    p = {}
    if a.get('q'):
        sql += ' AND (action LIKE :q OR ref LIKE :q OR user_name LIKE :q OR new_value LIKE :q)'
        p['q'] = f"%{a['q']}%"
    if a.get('ref'):
        sql += ' AND ref=:ref'
        p['ref'] = a['ref']
    if a.get('from'):
        sql += ' AND at >= :f'
        p['f'] = a['from']
    if a.get('to'):
        sql += ' AND at < DATE_ADD(:t, INTERVAL 1 DAY)'
        p['t'] = a['to']
    if a.get('category'):
        sql += ' AND category=:c'
        p['c'] = a['category']
    rows = db.clean(db.q(sql + ' ORDER BY id DESC LIMIT 500', **p))
    db.insert('access_log', dict(user_id=g.user['id'], action='view', what='Audit trail', detail=json.dumps(dict(a))[:300]))
    return jsonify(rows)


@bp.get('/audit/verify')
@require('audit', 'audit')
def audit_verify():
    ok, n, bad_id = audit.verify()
    return jsonify({'ok': ok, 'checked': n, 'first_bad_id': bad_id})


@bp.get('/security/events')
@require('view', 'ctl.sec')
def sec_events():
    return jsonify(db.clean(db.q('SELECT * FROM security_events ORDER BY id DESC LIMIT 300')))


@bp.get('/alerts')
@login_required
def alerts():
    u = g.user
    rules = {r['alert_type']: (json.loads(r['roles']) if isinstance(r['roles'], str) else r['roles']) for r in db.q('SELECT alert_type, roles FROM alert_rules')}
    out = []
    for a in db.q('SELECT * FROM alerts WHERE created_at >= NOW() - INTERVAL 14 DAY ORDER BY id DESC LIMIT 300'):
        allowed = rules.get(a['alert_type'])
        if allowed and not set(allowed) & set(u['roles']) and 'Super Admin' not in u['roles']:
            continue
        if a['branch_id'] and u['branches'] != '*' and a['branch_id'] not in u['branches']:
            continue
        if a['company'] and a['company'] not in u['companies']:
            continue
        out.append(a)
    return jsonify(db.clean(out[:100]))
