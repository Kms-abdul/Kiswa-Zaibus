"""Passwords, OTPs, session tokens and the sign-in CAPTCHA."""
import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import time

import bcrypt

from . import db
from .config import Config
from .errors import bad

DEFAULT_POLICY = {'minLen': 10, 'upper': True, 'lower': True, 'digit': True, 'special': True, 'expiryDays': 90, 'history': 5,
                  'maxFail': 5, 'captchaAfter': 3, 'otp': 'all', 'otpRoles': [], 'otpTtl': 5, 'reauthMin': 5, 'idleMin': 15,
                  'concurrent': 'replace', 'hours': {'on': False}, 'newDeviceAlert': True, 'ipRestrict': '',
                  'highRisk': ['approve', 'reverse', 'users', 'restore', 'delete']}


def setting(key, default=None):
    row = db.one('SELECT svalue FROM settings WHERE skey=:k', k=key)
    if not row:
        return default
    v = row['svalue']
    return json.loads(v) if isinstance(v, str) else v


def policy():
    p = dict(DEFAULT_POLICY)
    p.update(setting('security', {}) or {})
    return p


# ---- passwords --------------------------------------------------------------
def hash_pw(pw):
    return bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode()


def check_pw(pw, h):
    if not h:
        return False
    try:
        return bcrypt.checkpw(pw.encode('utf-8'), h.encode())
    except ValueError:
        return False


def policy_errors(pw, user, pol=None):
    p = pol or policy()
    errs = []
    if len(pw) < int(p['minLen']):
        errs.append(f"at least {p['minLen']} characters")
    if p.get('upper') and not re.search(r'[A-Z]', pw):
        errs.append('an upper-case letter')
    if p.get('lower') and not re.search(r'[a-z]', pw):
        errs.append('a lower-case letter')
    if p.get('digit') and not re.search(r'\d', pw):
        errs.append('a digit')
    if p.get('special') and not re.search(r'[^A-Za-z0-9]', pw):
        errs.append('a special character')
    low = pw.lower()
    for part in re.split(r'\s+', (user.get('name') or '').lower()):
        if len(part) >= 3 and part in low:
            errs.append('no part of your name')
            break
    if user.get('emp_id') and user['emp_id'].lower() in low:
        errs.append('not your employee ID')
    if user.get('mobile') and user['mobile'][-6:] in pw:
        errs.append('not your mobile number')
    return errs


def set_password(user, new_pw, must_change=False):
    p = policy()
    errs = policy_errors(new_pw, user, p)
    if errs:
        raise bad('Password must have ' + ', '.join(errs) + '.', 'weak_password')
    n = int(p.get('history') or 0)
    if n:
        for r in db.q('SELECT pw_hash FROM password_history WHERE user_id=:u ORDER BY id DESC LIMIT ' + str(n), u=user['id']):
            if check_pw(new_pw, r['pw_hash']):
                raise bad(f'You cannot reuse any of your last {n} passwords.', 'password_reused')
    h = hash_pw(new_pw)
    db.run('UPDATE users SET pw_hash=:h, pw_set_at=NOW(6), pw_must_change=:m, failed_count=0 WHERE id=:u', h=h, m=1 if must_change else 0, u=user['id'])
    db.insert('password_history', dict(user_id=user['id'], pw_hash=h))


def password_expired(user, pol=None):
    p = pol or policy()
    days = int(p.get('expiryDays') or 0)
    return bool(days and user.get('pw_set_at') and user['pw_set_at'] < dt.datetime.now() - dt.timedelta(days=days))


# ---- tokens, OTP ------------------------------------------------------------
def sha(s):
    return hashlib.sha256(s.encode('utf-8')).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)


def new_otp():
    return f'{secrets.randbelow(1_000_000):06d}'


def otp_hash(challenge_id, otp):
    return hmac.new(Config.SECRET_KEY.encode(), f'{challenge_id}:{otp}'.encode(), hashlib.sha256).hexdigest()


# ---- CAPTCHA (stateless, signed) --------------------------------------------
def captcha_new():
    a, b = secrets.randbelow(9) + 1, secrets.randbelow(9) + 1
    exp = int(time.time()) + 300
    sig = hmac.new(Config.SECRET_KEY.encode(), f'cap:{a + b}:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]
    return {'question': f'What is {a} + {b}?', 'token': f'{exp}.{sig}'}


def captcha_ok(token, answer):
    try:
        exp, sig = (token or '').split('.', 1)
        if int(exp) < time.time():
            return False
        good = hmac.new(Config.SECRET_KEY.encode(), f'cap:{int(str(answer).strip())}:{exp}'.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(good, sig)
    except (ValueError, TypeError):
        return False
