"""Passwords set by the administrator: a first password when creating a user, and a Super Admin reset. In both cases the user must
choose their own password at the next sign-in, and the password is never stored or logged in clear."""
from conftest import as_user, ok, sql

DIRECTOR, SUPER = 'KZ-HO-001', 'KZ-IT-001'
FIRST = 'Welcome#2026Kz'
RESET = 'Reset#Pass2026x'


def approve_latest(app, who=DIRECTOR):
    aid = sql("SELECT id FROM approvals WHERE status='Pending' ORDER BY id DESC LIMIT 1")[0]['id']
    return ok(as_user(app, who).post(f'/api/approvals/{aid}/decide', json={'action': 'approve', 'remarks': 'ok'}))


def signs_in_then_must_change(app, emp_id, password):
    c = app.test_client()
    j = c.post('/api/auth/login', json={'login': emp_id, 'password': password, 'device_id': 'pytest-pw'}).get_json()
    assert j.get('stage') in ('otp', 'change'), j
    if j['stage'] == 'otp':
        j = c.post('/api/auth/otp', json={'challenge': j['challenge'], 'otp': j['dev_otp'], 'device_id': 'pytest-pw'}).get_json()
    assert j['stage'] == 'change', j                                    # forced to choose their own password
    return c, j


NEW_USER = {'emp_id': 'KZ-PW-001', 'name': 'Ayesha Siddiqua', 'mobile': '9876509876', 'main_role': 'Store Cashier',
            'companies': ['KG'], 'all_branches': True, 'reason': 'test: first password set by admin'}


def test_create_user_with_first_password(app):
    s = as_user(app, SUPER)
    weak = s.post('/api/users', json=dict(NEW_USER, password='abc'))
    assert weak.status_code == 400 and weak.get_json()['error'] == 'weak_password'
    named = s.post('/api/users', json=dict(NEW_USER, password='Ayesha#2026Long'))      # contains the user's name
    assert named.status_code == 400 and named.get_json()['error'] == 'weak_password'
    r = s.post('/api/users', json=dict(NEW_USER, password=FIRST))
    assert r.status_code in (201, 202), r.get_json()
    if r.status_code == 202:
        assert not sql('SELECT COUNT(*) n FROM approvals WHERE payload LIKE %s', f'%{FIRST}%')[0]['n']   # only a hash is in the approval
        approve_latest(app)
    u = sql("SELECT pw_must_change, pw_hash FROM users WHERE emp_id='KZ-PW-001'")[0]
    assert u['pw_must_change'] == 1 and FIRST not in u['pw_hash']
    assert not sql('SELECT COUNT(*) n FROM audit_log WHERE new_value LIKE %s', f'%{FIRST}%')[0]['n']
    c, j = signs_in_then_must_change(app, 'KZ-PW-001', FIRST)
    r = c.post('/api/auth/change-password', json={'challenge': j['challenge'], 'new_password': 'MyOwn#Pass2026', 'device_id': 'pytest-pw'})
    assert r.status_code == 200 and r.get_json()['stage'] == 'done'


def test_super_admin_resets_password(app):
    s = as_user(app, SUPER)
    uid = sql("SELECT id FROM users WHERE emp_id='KZ-PW-001'")[0]['id']
    sql("UPDATE users SET status='Locked', failed_count=5 WHERE id=%s", uid)
    assert s.post(f'/api/users/{uid}/password', json={'password': RESET}).get_json()['error'] == 'reason_required'
    assert s.post(f'/api/users/{uid}/password', json={'password': 'short', 'reason': 'x'}).get_json()['error'] == 'weak_password'
    assert s.post(f'/api/users/{uid}/password', json={'password': 'MyOwn#Pass2026', 'reason': 'x'}).get_json()['error'] == 'password_reused'
    r = ok(s.post(f'/api/users/{uid}/password', json={'password': RESET, 'reason': 'forgot password, asked by phone'}))
    assert r['must_change'] is True
    row = sql('SELECT status, failed_count, pw_must_change FROM users WHERE id=%s', uid)[0]
    assert row == {'status': 'Active', 'failed_count': 0, 'pw_must_change': 1}                    # unlocked, forced change
    assert not sql("SELECT COUNT(*) n FROM sessions WHERE user_id=%s AND ended_at IS NULL", uid)[0]['n']
    assert sql("SELECT COUNT(*) n FROM audit_log WHERE action='Password reset by administrator'")[0]['n'] >= 1
    assert not sql('SELECT COUNT(*) n FROM audit_log WHERE new_value LIKE %s OR reason LIKE %s', f'%{RESET}%', f'%{RESET}%')[0]['n']
    signs_in_then_must_change(app, 'KZ-PW-001', RESET)


def test_only_super_admin_resets_and_not_self(app):
    me = sql('SELECT id FROM users WHERE emp_id=%s', SUPER)[0]['id']
    uid = sql("SELECT id FROM users WHERE emp_id='KZ-PW-001'")[0]['id']
    assert as_user(app, DIRECTOR).post(f'/api/users/{uid}/password', json={'password': RESET, 'reason': 'x'}).status_code == 403
    r = as_user(app, SUPER).post(f'/api/users/{me}/password', json={'password': 'Another#Pass2026', 'reason': 'x'})
    assert r.status_code == 403 and r.get_json()['error'] == 'self_change'
