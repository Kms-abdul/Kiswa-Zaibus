"""Sign-in: password → (OTP) → (change password) → session cookie.

POST /api/auth/login           {login, password, device_id, captcha_token?, captcha_answer?}
POST /api/auth/otp             {challenge, otp}
POST /api/auth/change-password {challenge, new_password}            (first sign-in / expired password)
POST /api/auth/password        {current_password, new_password}     (signed in)
POST /api/auth/forgot          {login}          → OTP to the registered mobile
POST /api/auth/reset           {challenge, otp, new_password}
POST /api/auth/reauth          {password}       → enables high-risk actions for reauthMin minutes
POST /api/auth/logout
GET  /api/auth/me
"""
import datetime as dt
import secrets

from flask import Blueprint, g, jsonify, make_response, request

from .. import db
from ..core import audit, notify
from ..config import Config
from ..errors import ApiError, bad
from ..core.rbac import body, load_user, login_required
from ..core.security import (captcha_new, captcha_ok, check_pw, new_otp, new_token, otp_hash, password_expired, policy, set_password, sha)

bp = Blueprint('auth', __name__, url_prefix='/api/auth')
GENERIC = 'The ID or password is incorrect.'


def _find(login):
    login = (login or '').strip()
    if not login:
        return None
    digits = ''.join(ch for ch in login if ch.isdigit())
    return db.one('SELECT * FROM users WHERE emp_id=:l OR (LENGTH(:d)=10 AND mobile=:d) OR LOWER(email)=LOWER(:l)', l=login, d=digits)


def _fail(u, pol, msg=GENERIC, code='bad_credentials', event='Failed sign-in'):
    db.keep_on_error()
    extra = {}
    if u:
        n = (u['failed_count'] or 0) + 1
        db.run('UPDATE users SET failed_count=:n WHERE id=:i', n=n, i=u['id'])
        audit.security_event(event, f'Attempt {n}: {msg}', u)
        if n >= int(pol['maxFail']) and u['status'] == 'Active':
            db.run("UPDATE users SET status='Locked', locked_at=NOW(6), lock_reason='Too many failed sign-in attempts' WHERE id=:i", i=u['id'])
            audit.write('Account locked', u['name'], old='Active', new='Locked', reason=f'{n} failed sign-in attempts', category='security', user={'name': 'System'})
            notify.alert('Account locked', f"{u['name']} ({u['emp_id']}) was locked after {n} failed sign-in attempts.", ref=u['emp_id'], severity='bad')
            raise ApiError(423, 'locked', 'Your account is locked. Please contact your administrator.')
        if n >= 3:
            notify.alert('Failed sign-in attempts', f"{n} failed sign-in attempts for {u['name']}.", ref=u['emp_id'])
        if n >= int(pol['captchaAfter']):
            extra['captcha'] = captcha_new()
    else:
        audit.security_event(event, msg)
    raise ApiError(401, code, msg, **extra)


def _gate(u, pol):
    """Status, validity dates and working hours. Returns None or raises."""
    if u['status'] != 'Active':
        db.keep_on_error()
        audit.security_event('Blocked sign-in', f"Account status {u['status']}", u)
        raise ApiError(423 if u['status'] == 'Locked' else 403, 'account_' + u['status'].lower(),
                       'Your account is locked. Please contact your administrator.' if u['status'] == 'Locked' else f"Your account is {u['status'].lower()}.")
    today = dt.date.today()
    if (u['valid_from'] and today < u['valid_from']) or (u['valid_to'] and today > u['valid_to']):
        db.keep_on_error()
        audit.security_event('Blocked sign-in', 'Outside access validity dates', u)
        raise ApiError(403, 'access_expired', 'Your access is not valid today. Please contact your administrator.')
    hours = pol.get('hours') or {}
    if hours.get('on') and u['main_role'] in (hours.get('roles') or []):
        now = dt.datetime.now().strftime('%H:%M')
        if not (hours.get('from', '00:00') <= now <= hours.get('to', '23:59')):
            db.keep_on_error()
            audit.security_event('Blocked sign-in', f'Outside working hours ({hours.get("from")}–{hours.get("to")})', u)
            raise ApiError(403, 'outside_hours', f'Sign-in is allowed between {hours.get("from")} and {hours.get("to")}.')


def _otp_needed(u, pol):
    mode = pol.get('otp', 'all')
    return mode == 'all' or (mode == 'roles' and u['main_role'] in (pol.get('otpRoles') or []))


def _challenge(u, purpose, stage, pol, send=True):
    cid = secrets.token_hex(16)
    otp = new_otp()
    ttl = int(pol.get('otpTtl') or 5)
    db.insert('login_challenges', dict(id=cid, user_id=u['id'], purpose=purpose, otp_hash=otp_hash(cid, otp), stage=stage,
                                       expires_at=dt.datetime.now() + dt.timedelta(minutes=ttl if stage == 'otp' else 15),
                                       ip=request.remote_addr, device=(request.headers.get('User-Agent') or '')[:160]))
    if send and stage == 'otp':
        msg = f'Kiswa-Zaibus: your one-time code is {otp}. It expires in {ttl} minutes. Do not share it.'
        notify.queue('sms', u['mobile'], None, msg, u['name'], 'OTP')
        if u.get('email'):   # second channel, so sign-in still works if SMS is delayed
            notify.queue('email', u['email'], 'Your Kiswa-Zaibus sign-in code', msg, u['name'], 'OTP')
    out = {'challenge': cid, 'stage': stage, 'mobile_hint': '••••••' + u['mobile'][-4:], 'expires_in': ttl * 60}
    if Config.OTP_DEMO and stage == 'otp':
        out['dev_otp'] = otp
    return out


def _get_challenge(cid, purpose=None):
    c = db.one('SELECT * FROM login_challenges WHERE id=:i FOR UPDATE', i=cid or '')
    if not c or c['used_at'] or c['stage'] in ('done', 'failed') or c['expires_at'] < dt.datetime.now() or (purpose and c['purpose'] != purpose):
        raise ApiError(401, 'challenge_expired', 'This step has expired. Please start again.')
    return c


def _start_session(u, pol, device_id):
    if pol.get('concurrent', 'replace') == 'replace':
        db.run("UPDATE sessions SET ended_at=NOW(6), end_reason='Signed in on another device' WHERE user_id=:u AND ended_at IS NULL", u=u['id'])
    elif pol.get('concurrent') == 'block' and db.val('SELECT COUNT(*) FROM sessions WHERE user_id=:u AND ended_at IS NULL', u=u['id']):
        raise ApiError(409, 'already_signed_in', 'You are already signed in on another device.')
    device = (request.headers.get('User-Agent') or '')[:160]
    dev_id = (device_id or '')[:80] or None
    if dev_id:
        known = db.val('SELECT COUNT(*) FROM user_devices WHERE user_id=:u AND device_id=:d', u=u['id'], d=dev_id)
        if not known:
            db.insert('user_devices', dict(user_id=u['id'], device_id=dev_id))
            if pol.get('newDeviceAlert') and db.val('SELECT COUNT(*) FROM user_devices WHERE user_id=:u', u=u['id']) > 1:
                notify.alert('New device sign-in', f"{u['name']} signed in from a new device ({device[:60]}).", ref=u['emp_id'], severity='info')
                audit.security_event('New device sign-in', device, u)
    tok = new_token()
    db.insert('sessions', dict(token_hash=sha(tok), user_id=u['id'], device=device, device_id=dev_id, ip=request.remote_addr))
    db.run('UPDATE users SET failed_count=0, last_login_at=NOW(6), last_login_device=:d, last_login_ip=:ip WHERE id=:i', d=device, ip=request.remote_addr, i=u['id'])
    audit.write('Signed in', u['name'], new=device, category='security', status='Success', user=u)
    full = load_user(u['id'])
    resp = make_response(jsonify({'stage': 'done', 'user': me_payload(full)}))
    resp.set_cookie(Config.COOKIE_NAME, tok, httponly=True, secure=Config.COOKIE_SECURE, samesite='Strict', path='/')
    return resp


def me_payload(u):
    return {'id': u['id'], 'name': u['name'], 'emp_id': u['emp_id'], 'role': u['main_role'], 'roles': u['roles'], 'designation': u['designation'],
            'companies': u['companies'], 'branches': u['branches'], 'rank': u['rank'], 'read_only': u['read_only'],
            'permissions': {k: sorted(v) for k, v in u['perms'].items()}}


@bp.post('/login')
def login():
    b = body()
    pol = policy()
    u = _find(b.get('login'))
    if u and (u['failed_count'] or 0) >= int(pol['captchaAfter']):
        if not captcha_ok(b.get('captcha_token'), b.get('captcha_answer')):
            db.keep_on_error()
            raise ApiError(401, 'captcha_required', 'Please answer the security question.', captcha=captcha_new())
    if not u or not check_pw(b.get('password') or '', db.val('SELECT pw_hash FROM users WHERE id=:i', i=u['id']) if u else None):
        _fail(u, pol)
    _gate(u, pol)
    must_change = bool(u['pw_must_change']) or password_expired(u, pol)
    if _otp_needed(u, pol):
        out = _challenge(u, 'login', 'otp', pol)
        out['then'] = 'change' if must_change else 'done'
        return jsonify(out)
    if must_change:
        return jsonify(_challenge(u, 'login', 'change', pol, send=False))
    return _start_session(u, pol, b.get('device_id'))


@bp.post('/otp')
def otp():
    b = body()
    pol = policy()
    c = _get_challenge(b.get('challenge'), 'login')
    if c['stage'] != 'otp':
        raise bad('No code is expected at this step.')
    u = db.one('SELECT * FROM users WHERE id=:i', i=c['user_id'])
    if otp_hash(c['id'], str(b.get('otp') or '').strip()) != c['otp_hash']:
        tries = c['tries'] + 1
        db.keep_on_error()
        db.run("UPDATE login_challenges SET tries=:t, stage=IF(:t>=3,'failed',stage) WHERE id=:i", t=tries, i=c['id'])
        audit.security_event('Failed sign-in', f'Wrong one-time code (attempt {tries})', u)
        raise ApiError(401, 'bad_otp', 'The code is incorrect.' + (' Please sign in again.' if tries >= 3 else ''), tries_left=max(0, 3 - tries))
    if bool(u['pw_must_change']) or password_expired(u, pol):
        db.run("UPDATE login_challenges SET stage='change', expires_at=:e WHERE id=:i", e=dt.datetime.now() + dt.timedelta(minutes=15), i=c['id'])
        return jsonify({'challenge': c['id'], 'stage': 'change'})
    db.run("UPDATE login_challenges SET stage='done', used_at=NOW(6) WHERE id=:i", i=c['id'])
    return _start_session(u, pol, b.get('device_id'))


@bp.post('/change-password')
def change_password_first():
    b = body()
    pol = policy()
    c = _get_challenge(b.get('challenge'), 'login')
    if c['stage'] != 'change':
        raise bad('Password change is not expected at this step.')
    u = db.one('SELECT * FROM users WHERE id=:i', i=c['user_id'])
    set_password(u, b.get('new_password') or '')
    audit.write('Password changed', u['name'], reason='First sign-in or expired password', category='security', user=u)
    db.run("UPDATE login_challenges SET stage='done', used_at=NOW(6) WHERE id=:i", i=c['id'])
    return _start_session(u, pol, b.get('device_id'))


@bp.post('/password')
@login_required
def change_password():
    b = body()
    full = db.one('SELECT * FROM users WHERE id=:i', i=g.user['id'])
    if not check_pw(b.get('current_password') or '', full['pw_hash']):
        raise ApiError(401, 'bad_credentials', 'Your current password is incorrect.')
    set_password(full, b.get('new_password') or '')
    audit.write('Password changed', full['name'], category='security')
    return jsonify({'ok': True})


@bp.post('/forgot')
def forgot():
    b = body()
    pol = policy()
    u = _find(b.get('login'))
    db.keep_on_error()
    if not u or u['status'] not in ('Active', 'Locked'):
        audit.security_event('Password reset requested', 'Unknown or inactive account: ' + str(b.get('login'))[:40])
        return jsonify({'challenge': secrets.token_hex(16), 'stage': 'otp', 'mobile_hint': '••••••••••'})   # same shape: no account probing
    out = _challenge(u, 'reset', 'otp', pol)
    audit.security_event('Password reset requested', 'OTP sent to registered mobile', u)
    return jsonify(out)


@bp.post('/reset')
def reset():
    b = body()
    c = _get_challenge(b.get('challenge'), 'reset')
    u = db.one('SELECT * FROM users WHERE id=:i', i=c['user_id'])
    if otp_hash(c['id'], str(b.get('otp') or '').strip()) != c['otp_hash']:
        db.keep_on_error()
        db.run("UPDATE login_challenges SET tries=tries+1, stage=IF(tries+1>=3,'failed',stage) WHERE id=:i", i=c['id'])
        raise ApiError(401, 'bad_otp', 'The code is incorrect.')
    if u['status'] == 'Locked' and u['lock_reason'] != 'Too many failed sign-in attempts':
        raise ApiError(423, 'locked', 'Your account is locked by an administrator.')
    set_password(u, b.get('new_password') or '')
    if u['status'] == 'Locked':
        db.run("UPDATE users SET status='Active', locked_at=NULL, lock_reason=NULL WHERE id=:i", i=u['id'])
    db.run("UPDATE login_challenges SET stage='done', used_at=NOW(6) WHERE id=:i", i=c['id'])
    db.run("UPDATE sessions SET ended_at=NOW(6), end_reason='Password reset' WHERE user_id=:u AND ended_at IS NULL", u=u['id'])
    audit.write('Password reset', u['name'], reason='Forgot password (OTP verified)', category='security', user=u)
    return jsonify({'ok': True})


@bp.post('/reauth')
@login_required
def reauth():
    b = body()
    h = db.val('SELECT pw_hash FROM users WHERE id=:i', i=g.user['id'])
    if not check_pw(b.get('password') or '', h):
        db.keep_on_error()
        audit.security_event('Re-authentication failed', 'Wrong password for a high-risk action', g.user)
        raise ApiError(401, 'bad_credentials', 'The password is incorrect.')
    db.run('UPDATE sessions SET reauth_at=NOW(6) WHERE id=:i', i=g.session['id'])
    return jsonify({'ok': True, 'valid_minutes': int(policy().get('reauthMin') or 5)})


@bp.post('/logout')
def logout():
    tok = request.cookies.get(Config.COOKIE_NAME)
    if tok:
        s = db.one('SELECT * FROM sessions WHERE token_hash=:h AND ended_at IS NULL', h=sha(tok))
        if s:
            db.run("UPDATE sessions SET ended_at=NOW(6), end_reason='Signed out' WHERE id=:i", i=s['id'])
            u = db.one('SELECT id, name, main_role FROM users WHERE id=:i', i=s['user_id'])
            audit.write('Signed out', u['name'], category='security', user=u)
    resp = make_response(jsonify({'ok': True}))
    resp.delete_cookie(Config.COOKIE_NAME, path='/')
    return resp


@bp.get('/me')
@login_required
def me():
    return jsonify(me_payload(g.user))
