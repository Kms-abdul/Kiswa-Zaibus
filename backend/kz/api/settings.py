"""Settings: security policy, controls, maker-checker switches, escalation, online store, alert rules, books lock date.
Changes to security, controls and the lock date go through maker-checker ('cfg') when it is switched on."""
from flask import Blueprint, g, jsonify

from .. import db
from ..core import audit
from ..core.approvals import handler, needs_second_person, request as approval_request
from ..core.rbac import body, high_risk, login_required, require
from ..core.security import setting
from ..errors import bad, not_found
from ..services.common import parse_date

bp = Blueprint('settings', __name__, url_prefix='/api/settings')
EDITABLE = {'security', 'controls', 'maker_checker', 'escalation', 'online', 'student_categories', 'counters', 'pos', 'stock_audit'}
NEEDS_APPROVAL = {'security', 'controls', 'maker_checker'}


@bp.get('')
@require('view', 'set')
def all_settings():
    rows = {r['skey']: db.clean(r)['svalue'] for r in db.q('SELECT skey, svalue FROM settings')}
    rows['companies'] = db.clean(db.q('SELECT code, name, books_locked_to FROM companies'))
    rows['alert_rules'] = db.clean(db.q('SELECT * FROM alert_rules ORDER BY alert_type'))
    rows['number_series'] = db.clean(db.q('SELECT * FROM number_series ORDER BY company, series'))
    return jsonify(rows)


def _apply(key, value):
    db.run('UPDATE settings SET svalue=:v, updated_by=:u, updated_at=NOW(6) WHERE skey=:k', v=value, u=g.user['id'], k=key)


@bp.put('/<key>')
@require('edit', 'set')
@high_risk('users')
def update(key):
    if key not in EDITABLE:
        raise not_found('Unknown setting.', 'unknown_setting')
    d = body()
    value, reason = d.get('value'), str(d.get('reason') or '').strip()
    if value is None:
        raise bad('Send the new value.', 'invalid')
    if not reason:
        raise bad('Give the reason for the change.', 'reason_required')
    if key == 'security':
        if int(value.get('minLen', 10)) < 8 or int(value.get('maxFail', 5)) < 3:
            raise bad('Passwords need at least 8 characters and lockout at least 3 attempts.', 'weak_policy')
    old = setting(key)
    if key in NEEDS_APPROVAL and needs_second_person('cfg'):
        a = approval_request('mc', f'Change setting: {key}', {'key': key, 'value': value}, 'Director', sub_kind='cfg', reason=reason, signature=f'cfg:{key}')
        return jsonify(a), 202
    _apply(key, value)
    audit.write('Setting changed', key, old=old, new=value, reason=reason, category='security' if key == 'security' else 'settings')
    return jsonify({'key': key, 'status': 'Applied'})


@handler('mc:cfg')
def _apply_cfg(p, apr):
    if p.get('key') == '_lock':
        db.run('UPDATE companies SET books_locked_to=:d WHERE code=:c', d=p['date'], c=p['company'])
        audit.write('Books locked', p['company'], new=p['date'], reason=apr['reason'], category='accounts', status='Approved')
        return f"{p['company']} books locked to {p['date']}"
    old = setting(p['key'])
    _apply(p['key'], p['value'])
    audit.write('Setting changed', p['key'], old=old, new=p['value'], reason=apr['reason'], category='settings', status='Approved')
    return f"Setting {p['key']} changed"


@bp.post('/lock')
@require('approve', 'acc')
def lock_books():
    d = body()
    co = d.get('company')
    if co not in g.user['companies']:
        raise bad('Choose the company.', 'company_denied')
    date = parse_date(d.get('date'))
    reason = str(d.get('reason') or '').strip()
    if not reason:
        raise bad('Give the reason (e.g. "September closed and reviewed").', 'reason_required')
    a = approval_request('mc', f'Lock {co} books up to {date}', {'key': '_lock', 'company': co, 'date': str(date)}, 'Director', sub_kind='cfg',
                         company=co, reason=reason, signature=f'lock:{co}:{date}')
    return jsonify(a), 202


@bp.put('/alert-rules/<path:alert_type>')
@require('edit', 'set')
def alert_rule(alert_type):
    d = body()
    n = db.run('UPDATE alert_rules SET in_app=:a, email=:e, whatsapp=:w, roles=:r WHERE alert_type=:t', a=1 if d.get('in_app', True) else 0,
               e=1 if d.get('email') else 0, w=1 if d.get('whatsapp') else 0, r=d.get('roles') or [], t=alert_type).rowcount
    if not n:
        db.insert('alert_rules', dict(alert_type=alert_type, in_app=1, email=1 if d.get('email') else 0, whatsapp=1 if d.get('whatsapp') else 0, roles=d.get('roles') or []))
    audit.write('Alert rule changed', alert_type, new=d, category='settings')
    return jsonify({'ok': True})


@bp.get('/public')
@login_required
def public():
    """Settings every signed-in screen may need (no secrets)."""
    controls = setting('controls', {}) or {}
    return jsonify({'controls': {k: controls.get(k) for k in ('cashBillMax', 'returnDays', 'cancelHours', 'reprintMax', 'denomReq', 'evTypes')},
                    'online': {k: (setting('online', {}) or {}).get(k) for k in ('points', 'limits', 'returnDays')},
                    'student_categories': setting('student_categories', []), 'counters': setting('counters', []),
                    'pos': setting('pos', {}) or {}})
