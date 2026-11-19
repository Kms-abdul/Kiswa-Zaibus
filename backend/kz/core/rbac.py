"""Who is signed in, what they may do, and which branches they may see.

Mirrors the prototype's rules (acl.js):
  * a user's roles = main role + additional roles; permissions are the union over roles
  * a permission key is 'module' or 'module.screen'; a screen entry overrides its module entry
  * read-only users may only view, print, pdf, export, audit, fin, cost
  * approval rank is the highest rank over the user's roles; limits are the highest limit over roles
  * branch scope = assigned branches ∪ branches of assigned zones/regions (for the user's companies), or all
"""
import datetime as dt
from functools import wraps

from flask import g, request

from .. import db
from ..config import Config
from ..errors import ApiError, denied
from .security import policy, sha

READ_ONLY_ACTIONS = {'view', 'print', 'pdf', 'export', 'audit', 'fin', 'cost'}
# Narrow exceptions for read-only roles: the internal Auditor may count stock audits it is named on (the service checks the
# assignment); it still cannot verify, review, approve or post anything.
RO_EXCEPTIONS = {'saudit': {'edit', 'submit', 'upload', 'import'}}

# Super Admin holds every right on every screen (release 2.7). Only maker-checker (nobody approves their own request) and the
# branch / company scope assigned to the user still apply. The lists must cover every screen key and action used anywhere.
SUPER_ADMIN = 'Super Admin'
ALL_SCREENS = ('dash', 'pos', 'msea', 'onl', 'inv', 'pur', 'cash', 'acc', 'rep', 'appr', 'audit', 'set', 'ctl', 'ctl.int', 'ctl.pay', 'ctl.users',
               'ctl.sec', 'saudit', 'fin')
ALL_ACTIONS = ('view', 'add', 'edit', 'submit', 'approve', 'reject', 'return', 'cancel', 'reverse', 'delete', 'restore', 'print', 'pdf', 'export',
               'import', 'upload', 'audit', 'fin', 'cost', 'master', 'cfgrep', 'users', 'verify', 'review', 'post')


def is_super(u):
    return bool(u) and SUPER_ADMIN in (u.get('roles') or [])


def load_user(user_id):
    u = db.one('SELECT * FROM users WHERE id=:i', i=user_id)
    if not u:
        return None
    roles = [u['main_role']] + [r['role'] for r in db.q('SELECT role FROM user_roles WHERE user_id=:i', i=user_id) if r['role'] != u['main_role']]
    u['roles'] = roles
    rr = db.q('SELECT name, approval_rank, read_only FROM roles WHERE name IN :r AND active=1', r=tuple(roles)) if roles else []
    u['rank'] = max([float(r['approval_rank']) for r in rr] + [0])
    u['read_only'] = bool(rr) and all(r['read_only'] for r in rr)
    perms = {}
    for p in db.q('SELECT screen_key, action FROM permissions WHERE role IN :r', r=tuple(roles)):
        perms.setdefault(p['screen_key'], set()).add(p['action'])
    if SUPER_ADMIN in roles:                 # every screen (including any granted to other roles) with every action
        keys = set(ALL_SCREENS) | {r['screen_key'] for r in db.q('SELECT DISTINCT screen_key FROM permissions')} | set(perms)
        perms = {k: set(ALL_ACTIONS) | perms.get(k, set()) for k in keys}
        u['read_only'] = False
        u['rank'] = max([u['rank']] + [float(r['approval_rank']) for r in db.q('SELECT approval_rank FROM roles WHERE active=1')])
    u['perms'] = perms
    u['companies'] = [r['company'] for r in db.q('SELECT company FROM user_companies WHERE user_id=:i', i=user_id)]
    u['branches'] = branch_scope(u)
    u.pop('pw_hash', None)
    return u


def branch_scope(u):
    if u['all_branches']:
        return '*'
    ids = {r['branch_id'] for r in db.q('SELECT branch_id FROM user_branches WHERE user_id=:i', i=u['id'])}
    locs = {r['location'] for r in db.q(
        '''SELECT l.code AS location FROM locations l JOIN zones z ON z.id=l.zone_id
           WHERE z.id IN (SELECT zone_id FROM user_zones WHERE user_id=:i) OR z.region_id IN (SELECT region_id FROM user_regions WHERE user_id=:i)''', i=u['id'])}
    if locs and u['companies']:
        for b in db.q('SELECT id FROM branches WHERE location IN :l AND company IN :c AND active=1', l=tuple(locs), c=tuple(u['companies'])):
            ids.add(b['id'])
    return sorted(ids)


def actions_for(u, key):
    m = key.split('.')[0]
    if key in u['perms']:
        return u['perms'][key]
    return u['perms'].get(m, set())


def can(u, action, key):
    if not u:
        return False
    if is_super(u):
        return True
    if u['read_only'] and action not in READ_ONLY_ACTIONS and action not in RO_EXCEPTIONS.get(key.split('.')[0], ()):
        return False
    if action in actions_for(u, key):
        return True
    # A screen grant (e.g. ctl.users) makes its module visible.
    if action == 'view' and '.' not in key:
        return any(k.startswith(key + '.') and 'view' in v for k, v in u['perms'].items())
    return False


def in_scope(u, branch_id):
    if branch_id is None:
        return u['branches'] == '*'          # Head Office records need all-location access
    return u['branches'] == '*' or branch_id in u['branches']


def company_ok(u, company):
    return company is None or company in u['companies']


def limit(u, key):
    if is_super(u):                          # the highest limit any role has
        rows = db.q('SELECT MAX(amount) AS a FROM approval_limits WHERE limit_key=:k', k=key)
        return float(rows[0]['a'] or 0) if rows else 0.0
    rows = db.q('SELECT MAX(amount) AS a FROM approval_limits WHERE role IN :r AND limit_key=:k', r=tuple(u['roles']), k=key)
    return float(rows[0]['a'] or 0) if rows else 0.0


def scope_sql(u, col='branch_id'):
    """SQL fragment and params restricting rows to the user's branches."""
    if u['branches'] == '*':
        return '1=1', {}
    if not u['branches']:
        return '1=0', {}
    return f'{col} IN :_scope', {'_scope': tuple(u['branches'])}


# ---- request hooks and decorators --------------------------------------------
def current_session():
    tok = request.cookies.get(Config.COOKIE_NAME)
    if not tok:
        return None
    s = db.one('SELECT * FROM sessions WHERE token_hash=:h AND ended_at IS NULL', h=sha(tok))
    if not s:
        return None
    idle = int(policy().get('idleMin') or 15)
    if s['last_seen'] < dt.datetime.now() - dt.timedelta(minutes=idle):
        db.run("UPDATE sessions SET ended_at=NOW(6), end_reason='Idle timeout' WHERE id=:i", i=s['id'])
        db.keep_on_error()
        raise ApiError(401, 'session_expired', 'Your session ended after inactivity. Please sign in again.')
    db.run('UPDATE sessions SET last_seen=NOW(6) WHERE id=:i', i=s['id'])
    return s


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        s = current_session()
        if not s:
            raise ApiError(401, 'not_signed_in', 'Please sign in.')
        u = load_user(s['user_id'])
        if not u or u['status'] != 'Active':
            db.run("UPDATE sessions SET ended_at=NOW(6), end_reason='Account not active' WHERE id=:i", i=s['id'])
            db.keep_on_error()
            raise ApiError(401, 'account_inactive', 'Your account is not active.')
        g.session = s
        g.user = u
        return fn(*a, **kw)
    return wrapper


def require(action, key):
    """Permission check on top of login. Logs refused attempts as security events."""
    def deco(fn):
        @wraps(fn)
        @login_required
        def wrapper(*a, **kw):
            if not can(g.user, action, key):
                from .audit import security_event
                security_event('Unauthorised access attempt', f'{action} on {key} ({request.method} {request.path})', g.user)
                db.keep_on_error()
                raise denied()
            return fn(*a, **kw)
        return wrapper
    return deco


def need_fresh_password():
    """For actions that always need the password re-entered (year-end close and reopen), whatever the security policy lists."""
    mins = int(policy().get('reauthMin') or 5)
    ra = g.session.get('reauth_at')
    if not ra or ra < dt.datetime.now() - dt.timedelta(minutes=mins):
        raise ApiError(428, 'reauth_required', 'Please re-enter your password to continue.')


def high_risk(kind):
    """Requires a fresh password re-entry (POST /api/auth/reauth) for actions listed in the security policy."""
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            pol = policy()
            if kind in (pol.get('highRisk') or []):
                mins = int(pol.get('reauthMin') or 5)
                ra = g.session.get('reauth_at')
                if not ra or ra < dt.datetime.now() - dt.timedelta(minutes=mins):
                    raise ApiError(428, 'reauth_required', 'Please re-enter your password to continue.')
            return fn(*a, **kw)
        return wrapper
    return deco


def body():
    """The JSON object of the request; {} when there is no body. A body that is JSON but not an object (a list, a string,
    a number) or is not valid JSON is refused with 400 instead of being treated as empty."""
    from ..errors import bad
    if not request.get_data(cache=True):
        return {}
    data = request.get_json(silent=True)
    if data is None and not request.is_json and request.form:
        return request.form.to_dict()
    if not isinstance(data, dict):
        raise bad('The request body must be a JSON object.', 'invalid_body')
    return data
