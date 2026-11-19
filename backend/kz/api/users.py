"""User administration (Access & Control Center → Users). Every create or change goes through maker-checker
when 'access' is on in the maker-checker settings (default on); the change applies only after approval."""
import datetime as dt
import re
import secrets
import string

from flask import Blueprint, g, jsonify, request

from .. import db
from ..core import audit, notify
from ..core.approvals import handler, mc_on, on_reject, request as mc_request
from ..config import Config
from ..errors import bad, conflict, denied, not_found
from ..core.rbac import body, high_risk, require
from ..core.routing import route_role
from ..core.security import hash_pw

bp = Blueprint('users', __name__, url_prefix='/api/users')
STATUSES = ('Active', 'Inactive', 'Locked', 'Suspended', 'Resigned')
FIELDS = ('emp_id', 'name', 'mobile', 'email', 'department', 'designation', 'main_role', 'manager_id', 'status', 'join_date', 'relieve_date',
          'valid_from', 'valid_to', 'all_branches')


def _view(u):
    uid = u['id']
    u = db.clean(u)
    u.pop('pw_hash', None)
    u['roles'] = [r['role'] for r in db.q('SELECT role FROM user_roles WHERE user_id=:i', i=uid)]
    u['companies'] = [r['company'] for r in db.q('SELECT company FROM user_companies WHERE user_id=:i', i=uid)]
    u['branches'] = [r['branch_id'] for r in db.q('SELECT branch_id FROM user_branches WHERE user_id=:i', i=uid)]
    u['zones'] = [r['zone_id'] for r in db.q('SELECT zone_id FROM user_zones WHERE user_id=:i', i=uid)]
    u['regions'] = [r['region_id'] for r in db.q('SELECT region_id FROM user_regions WHERE user_id=:i', i=uid)]
    return u


def _validate(d, existing=None):
    out = {}
    for k in FIELDS:
        if k in d:
            out[k] = d[k] if d[k] != '' else None
    if existing is None:
        for k in ('emp_id', 'name', 'mobile', 'main_role'):
            if not out.get(k):
                raise bad(f'{k.replace("_", " ").capitalize()} is required.', 'missing_' + k)
    if 'mobile' in out and out['mobile']:
        m = re.sub(r'\D', '', str(out['mobile']))[-10:]
        if not re.fullmatch(r'[6-9]\d{9}', m):
            raise bad('Mobile must be a 10-digit Indian mobile number.', 'bad_mobile')
        out['mobile'] = m
    if out.get('email') and not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', out['email']):
        raise bad('Email address is not valid.', 'bad_email')
    if 'status' in out and out['status'] not in STATUSES:
        raise bad('Unknown status.')
    if out.get('valid_from') and out.get('valid_to') and out['valid_to'] < out['valid_from']:
        raise bad('Access end date is before the start date.', 'bad_dates')
    roles = [out['main_role']] if out.get('main_role') else []
    roles += list(d.get('roles') or [])
    for r in roles:
        if not db.val('SELECT COUNT(*) FROM roles WHERE name=:r AND active=1', r=r):
            raise bad(f'Unknown role: {r}', 'bad_role')
    for c in d.get('companies') or []:
        if not db.val('SELECT COUNT(*) FROM companies WHERE code=:c', c=c):
            raise bad(f'Unknown company: {c}')
    for b in d.get('branches') or []:
        if not db.val('SELECT COUNT(*) FROM branches WHERE id=:b AND active=1', b=b):
            raise bad(f'Branch {b} does not exist or is closed.', 'bad_branch')
    uid = (existing or {}).get('id')
    for col in ('emp_id', 'mobile', 'name'):
        if out.get(col) and db.val(f'SELECT COUNT(*) FROM users WHERE {col}=:v AND id<>:i', v=out[col], i=uid or ''):
            raise conflict(f'Another user already has this {col.replace("_", " ")}.', 'duplicate_' + col)
    scope = {k: list(d[k]) for k in ('roles', 'companies', 'branches', 'zones', 'regions') if k in d}
    return out, scope


def _apply_scope(uid, scope):
    for key, table, col in (('roles', 'user_roles', 'role'), ('companies', 'user_companies', 'company'), ('branches', 'user_branches', 'branch_id'),
                            ('zones', 'user_zones', 'zone_id'), ('regions', 'user_regions', 'region_id')):
        if key in scope:
            db.run(f'DELETE FROM {table} WHERE user_id=:u', u=uid)
            for v in scope[key]:
                db.run(f'INSERT IGNORE INTO {table} (user_id, {col}) VALUES (:u, :v)', u=uid, v=v)


def _temp_password():
    alphabet = string.ascii_letters + string.digits
    core = ''.join(secrets.choice(alphabet) for _ in range(9))
    return 'Kz#' + core + str(secrets.randbelow(10))


def _grants_super_admin(fields, scope):
    return fields.get('main_role') == 'Super Admin' or 'Super Admin' in (scope.get('roles') or [])


@handler('mc:user')
def apply_create(p, apr):
    f, scope = p['fields'], p['scope']
    if db.val('SELECT COUNT(*) FROM users WHERE emp_id=:e OR mobile=:m', e=f['emp_id'], m=f['mobile']):
        raise conflict('A user with this employee ID or mobile was created meanwhile.')
    n = (db.val("SELECT MAX(CAST(SUBSTRING(id, 2) AS UNSIGNED)) FROM users WHERE id REGEXP '^u[0-9]+$'") or 0) + 1
    uid = f'u{n}'
    temp = _temp_password()
    row = dict(f, id=uid, pw_hash=hash_pw(temp), pw_set_at=dt.datetime.now(), pw_must_change=1, status=f.get('status') or 'Active')
    db.insert('users', row)
    db.insert('password_history', dict(user_id=uid, pw_hash=row['pw_hash']))
    _apply_scope(uid, scope)
    notify.queue('sms', f['mobile'], None, f'Kiswa-Zaibus: your account {f["emp_id"]} is ready. Temporary password: {temp} (change it at first sign-in).',
                 f['name'], 'OTP')
    audit.write('User created', f['name'], new={**f, **scope}, reason=apr['reason'], category='security', status='Approved')
    if Config.OTP_DEMO:          # test mode only: handed back in the HTTP response, never stored in approvals or the audit log
        g.dev_temp_password = temp
    return f'User {uid} created; temporary password sent to ••••••{f["mobile"][-4:]}'


@handler('mc:user_update')
def apply_update(p, apr):
    uid = p['id']
    cur = db.one('SELECT * FROM users WHERE id=:i FOR UPDATE', i=uid)
    if not cur:
        raise not_found('User no longer exists.')
    f, scope = p['fields'], p['scope']
    old = _view(cur)
    if f:
        sets = ', '.join(f'`{k}`=:{k}' for k in f)
        db.run(f'UPDATE users SET {sets} WHERE id=:_id', _id=uid, **f)
    _apply_scope(uid, scope)
    if f.get('status') and f['status'] != 'Active':
        db.run("UPDATE sessions SET ended_at=NOW(6), end_reason=:r WHERE user_id=:u AND ended_at IS NULL", r=f'Account {f["status"].lower()}', u=uid)
    if f.get('status') == 'Active' and cur['status'] == 'Locked':
        db.run('UPDATE users SET failed_count=0, locked_at=NULL, lock_reason=NULL WHERE id=:i', i=uid)
    changed = {k: [old.get(k), v] for k, v in {**f, **scope}.items() if old.get(k) != v}
    audit.write('User access changed', cur['name'], old={k: v[0] for k, v in changed.items()}, new={k: v[1] for k, v in changed.items()},
                reason=apr['reason'], category='security', status='Approved')
    return f'User {uid} updated ({", ".join(changed) or "no change"})'


@bp.get('')
@require('view', 'ctl.users')
def list_users():
    qs = (request.args.get('q') or '').strip()
    st = request.args.get('status')
    sql = 'SELECT * FROM users WHERE 1=1'
    p = {}
    if qs:
        sql += ' AND (name LIKE :q OR emp_id LIKE :q OR mobile LIKE :q OR main_role LIKE :q)'
        p['q'] = f'%{qs}%'
    if st:
        sql += ' AND status=:s'
        p['s'] = st
    rows = db.q(sql + ' ORDER BY name', **p)
    return jsonify([_view(r) for r in rows])


@bp.get('/<uid>')
@require('view', 'ctl.users')
def get_user(uid):
    u = db.one('SELECT * FROM users WHERE id=:i', i=uid)
    if not u:
        raise not_found('User not found.')
    out = _view(u)
    out['sessions'] = db.clean(db.q('SELECT id, device, ip, created_at, last_seen, ended_at, end_reason FROM sessions WHERE user_id=:i ORDER BY id DESC LIMIT 10', i=uid))
    return jsonify(out)


@bp.post('')
@require('users', 'ctl.users')
@high_risk('users')
def create_user():
    d = body()
    reason = str(d.get('reason') or '').strip()
    if not reason:
        raise bad('Please give a reason for this access request.', 'reason_required')
    f, scope = _validate(d)
    f['all_branches'] = 1 if d.get('all_branches') else 0
    payload = {'fields': f, 'scope': scope}
    sa = _grants_super_admin(f, scope)
    if not mc_on('access'):
        if sa and 'Super Admin' not in g.user['roles']:
            raise denied('Only a Super Admin can grant Super Admin.')
        out = {'status': 'Applied', 'result': apply_create(payload, {'reason': reason})}
        if g.get('dev_temp_password'):
            out['dev_temp_password'] = g.dev_temp_password
        return jsonify(out), 201
    res = mc_request('mc', f'New user · {f["name"]} ({f["main_role"]})', payload, route_role('mc', None, 0, 'Director'), sub_kind='user',
                     reason=reason, super_admin_only=sa, signature=f'user:new:{f["emp_id"]}',
                     detail=f'{f["emp_id"]} · {f["mobile"]} · companies {",".join(scope.get("companies", []))}')
    return jsonify(res), 202


@bp.put('/<uid>')
@require('users', 'ctl.users')
@high_risk('users')
def update_user(uid):
    if uid == g.user['id']:
        raise denied('You cannot change your own access.', 'self_change')
    cur = db.one('SELECT * FROM users WHERE id=:i', i=uid)
    if not cur:
        raise not_found('User not found.')
    d = body()
    reason = str(d.get('reason') or '').strip()
    if not reason:
        raise bad('Please give a reason for this change.', 'reason_required')
    f, scope = _validate(d, cur)
    if 'all_branches' in d:
        f['all_branches'] = 1 if d['all_branches'] else 0
    payload = {'id': uid, 'fields': f, 'scope': scope}
    sa = _grants_super_admin(f, scope) or cur['main_role'] == 'Super Admin'
    if not mc_on('access'):
        if sa and 'Super Admin' not in g.user['roles']:
            raise denied('Only a Super Admin can change Super Admin access.')
        return jsonify({'status': 'Applied', 'result': apply_update(payload, {'reason': reason})})
    res = mc_request('mc', f'Change user · {cur["name"]}', payload, route_role('mc', None, 0, 'Director'), sub_kind='user_update', reason=reason,
                     super_admin_only=sa, signature=f'user:upd:{uid}:{sorted(f.items())}:{sorted((k, tuple(v)) for k, v in scope.items())}')
    return jsonify(res), 202


@bp.post('/<uid>/unlock')
@require('users', 'ctl.users')
@high_risk('users')
def unlock(uid):
    cur = db.one('SELECT * FROM users WHERE id=:i', i=uid)
    if not cur:
        raise not_found('User not found.')
    if cur['status'] != 'Locked':
        raise conflict('This account is not locked.')
    reason = str(body().get('reason') or '').strip()
    if not reason:
        raise bad('Please give a reason.', 'reason_required')
    payload = {'id': uid, 'fields': {'status': 'Active'}, 'scope': {}}
    if not mc_on('access'):
        return jsonify({'status': 'Applied', 'result': apply_update(payload, {'reason': reason})})
    return jsonify(mc_request('mc', f'Unlock account · {cur["name"]}', payload, route_role('mc', None, 0, 'Director'), sub_kind='user_update',
                              reason=reason, signature=f'user:unlock:{uid}')), 202


@bp.post('/<uid>/sessions/end')
@require('users', 'ctl.users')
def end_sessions(uid):
    n = db.run("UPDATE sessions SET ended_at=NOW(6), end_reason='Ended by administrator' WHERE user_id=:u AND ended_at IS NULL", u=uid).rowcount
    audit.write('Sessions ended', uid, new=f'{n} session(s)', category='security')
    return jsonify({'ended': n})


# ---------------------------------------------------------------- effective-dated access changes (resignation, transfer, suspension)
CHANGE_TYPES = ('Resign', 'Transfer', 'Suspend', 'Reinstate')


@bp.get('/<uid>/schedule')
@require('view', 'ctl.users')
def schedule_list(uid):
    return jsonify(db.clean(db.q('''SELECT s.*, r.name AS requested_by_name FROM access_schedule s LEFT JOIN users r ON r.id=s.requested_by
                                    WHERE s.user_id=:u ORDER BY s.effective_on DESC, s.id DESC''', u=uid)))


@bp.get('/schedule/upcoming')
@require('view', 'ctl.users')
def schedule_upcoming():
    return jsonify(db.clean(db.q('''SELECT s.*, u.name AS user_name, u.emp_id FROM access_schedule s JOIN users u ON u.id=s.user_id
                                    WHERE s.status IN ('Scheduled','Pending approval') ORDER BY s.effective_on''')))


@handler('mc:access_sched')
def apply_schedule(p, apr):
    db.run("UPDATE access_schedule SET status='Scheduled' WHERE id=:i AND status='Pending approval'", i=p['id'])
    audit.write('Access change scheduled', p['user'], new=p, reason=apr.get('reason'), category='security', status='Approved')
    return f"{p['change_type']} of {p['user']} scheduled for {p['effective_on']}"


@on_reject('mc:access_sched')
def reject_schedule(p, apr, reason):
    db.run("UPDATE access_schedule SET status='Rejected' WHERE id=:i AND status='Pending approval'", i=p['id'])


@bp.post('/<uid>/schedule')
@require('users', 'ctl.users')
@high_risk('users')
def schedule_add(uid):
    u = db.one('SELECT id, name, status FROM users WHERE id=:i', i=uid)
    if not u:
        raise not_found('User not found.')
    d = body()
    kind = d.get('change_type')
    if kind not in CHANGE_TYPES:
        raise bad('Choose resignation, transfer, suspension or reinstatement.', 'invalid')
    reason = str(d.get('reason') or '').strip()
    if not reason:
        raise bad('Give the reason (e.g. resignation letter reference).', 'reason_required')
    try:
        eff = dt.date.fromisoformat(str(d.get('effective_on')))
    except ValueError:
        raise bad('Choose the effective date.', 'bad_date') from None
    if eff < dt.date.today():
        raise bad('The effective date is in the past. Change the user directly instead.', 'bad_date')
    data = {}
    if kind == 'Transfer':
        brs = [b for b in (d.get('branches') or []) if db.val('SELECT COUNT(*) FROM branches WHERE id=:b AND active=1', b=b)]
        if not brs:
            raise bad('Choose the new location(s).', 'bad_branch')
        data['branches'] = brs
    if db.val("SELECT COUNT(*) FROM access_schedule WHERE user_id=:u AND status IN ('Scheduled','Pending approval') AND effective_on=:e", u=uid, e=eff):
        raise conflict('A change is already scheduled for this user on that date.', 'duplicate_request')
    sid = db.insert('access_schedule', dict(user_id=uid, change_type=kind, effective_on=eff, data=data, reason=reason,
                                            status='Pending approval' if mc_on('access') else 'Scheduled', requested_by=g.user['id']))
    payload = {'id': sid, 'user': u['name'], 'change_type': kind, 'effective_on': str(eff), **data}
    if not mc_on('access'):
        audit.write('Access change scheduled', u['name'], new=payload, reason=reason, category='security')
        return jsonify({'id': sid, 'status': 'Scheduled'}), 201
    a = mc_request('mc', f'{kind} · {u["name"]} on {eff}', payload, route_role('mc', None, 0, 'Director'), sub_kind='access_sched', reason=reason,
                   signature=f'sched:{uid}:{eff}')
    return jsonify(a), 202


@bp.post('/schedule/<int:sid>/cancel')
@require('users', 'ctl.users')
def schedule_cancel(sid):
    reason = str(body().get('reason') or '').strip()
    if not reason:
        raise bad('Give the reason.', 'reason_required')
    if not db.run("UPDATE access_schedule SET status='Cancelled' WHERE id=:i AND status IN ('Scheduled','Pending approval')", i=sid).rowcount:
        raise conflict('Only a change that has not been applied yet can be cancelled.', 'wrong_status')
    audit.write('Scheduled access change cancelled', str(sid), reason=reason, category='security')
    return jsonify({'id': sid, 'status': 'Cancelled'})
