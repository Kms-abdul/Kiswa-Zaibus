from conftest import NEW_PW, TEMP, sign_in, sql


def test_health(client):
    r = client.get('/api/health')
    assert r.status_code == 200 and r.get_json()['database'] == 'ok'


def test_first_sign_in_needs_otp_and_password_change(client):
    r = client.post('/api/auth/login', json={'login': 'MSEA-OFF-004', 'password': TEMP})
    j = r.get_json()
    assert j['stage'] == 'otp' and j['then'] == 'change' and len(j['dev_otp']) == 6
    assert client.get('/api/auth/me').status_code == 401              # no session yet
    r = client.post('/api/auth/otp', json={'challenge': j['challenge'], 'otp': '000000' if j['dev_otp'] != '000000' else '111111'})
    assert r.status_code == 401 and r.get_json()['error'] == 'bad_otp'
    r = client.post('/api/auth/otp', json={'challenge': j['challenge'], 'otp': j['dev_otp']})
    c = r.get_json()
    assert c['stage'] == 'change'
    # policy: name and the temporary (reused) password are refused
    r = client.post('/api/auth/change-password', json={'challenge': c['challenge'], 'new_password': 'Fatima#2026xyz'})
    assert r.status_code == 400 and r.get_json()['error'] == 'weak_password'
    r = client.post('/api/auth/change-password', json={'challenge': c['challenge'], 'new_password': 'short1A!'})
    assert r.status_code == 400
    r = client.post('/api/auth/change-password', json={'challenge': c['challenge'], 'new_password': TEMP + 'Aa1!'})
    assert r.status_code == 200
    NEW_PW['MSEA-OFF-004'] = TEMP + 'Aa1!'
    me = client.get('/api/auth/me').get_json()
    assert me['role'] == 'MSEA Coordinator' and me['emp_id'] == 'MSEA-OFF-004'
    assert 'Set-Cookie' not in client.get('/api/auth/me').headers
    # the OTP SMS was queued, and the session cookie is HttpOnly + SameSite=Strict
    assert sql("SELECT COUNT(*) n FROM outbox WHERE alert_type='OTP' AND address='9848010142'")[0]['n'] >= 1


def test_cookie_flags(app, client):
    sign_in(app.test_client(), 'KZ-HO-002')
    r = client.post('/api/auth/login', json={'login': 'KZ-HO-002', 'password': NEW_PW['KZ-HO-002']})
    j = r.get_json()
    r = client.post('/api/auth/otp', json={'challenge': j['challenge'], 'otp': j['dev_otp']})
    ck = r.headers['Set-Cookie']
    assert 'HttpOnly' in ck and 'SameSite=Strict' in ck


def test_lockout_and_captcha_then_reset(client):
    emp = 'MSEA-CSH-002'
    for n in range(1, 4):
        r = client.post('/api/auth/login', json={'login': emp, 'password': 'wrong-' + str(n)})
        assert r.status_code == 401
    assert 'captcha' in r.get_json()
    r = client.post('/api/auth/login', json={'login': emp, 'password': TEMP})
    assert r.status_code == 401 and r.get_json()['error'] == 'captcha_required'
    cap = r.get_json()['captcha']
    a, b = [int(x) for x in cap['question'].replace('What is ', '').rstrip('?').split(' + ')]
    for n in range(2):
        r = client.post('/api/auth/login', json={'login': emp, 'password': 'bad', 'captcha_token': cap['token'], 'captcha_answer': a + b})
    assert r.status_code == 423 and r.get_json()['error'] == 'locked'
    assert sql('SELECT status FROM users WHERE emp_id=%s', emp)[0]['status'] == 'Locked'
    # even the right password is refused now
    r = client.post('/api/auth/login', json={'login': emp, 'password': TEMP, 'captcha_token': cap['token'], 'captcha_answer': a + b})
    assert r.status_code in (401, 423)
    assert sql("SELECT COUNT(*) n FROM alerts WHERE alert_type='Account locked'")[0]['n'] >= 1
    # forgot password with OTP unlocks a lockout caused by failed attempts
    r = client.post('/api/auth/forgot', json={'login': emp})
    j = r.get_json()
    r = client.post('/api/auth/reset', json={'challenge': j['challenge'], 'otp': j['dev_otp'], 'new_password': 'Reset#Pass2026q'})
    assert r.status_code == 200, r.get_json()
    assert sql('SELECT status, failed_count FROM users WHERE emp_id=%s', emp)[0] == {'status': 'Active', 'failed_count': 0}
    NEW_PW[emp] = 'Reset#Pass2026q'
    sign_in(client, emp)


def test_forgot_does_not_reveal_accounts(client):
    r = client.post('/api/auth/forgot', json={'login': 'NO-SUCH-USER'})
    assert r.status_code == 200 and r.get_json()['stage'] == 'otp' and 'dev_otp' not in r.get_json()


def test_concurrent_session_is_replaced(app):
    a, b = app.test_client(), app.test_client()
    sign_in(a, 'KZ-HO-002')
    assert a.get('/api/auth/me').status_code == 200
    sign_in(b, 'KZ-HO-002')
    assert b.get('/api/auth/me').status_code == 200
    assert a.get('/api/auth/me').status_code == 401


def test_idle_timeout(client):
    sign_in(client, 'KZ-HO-002')
    sql("UPDATE sessions SET last_seen = NOW() - INTERVAL 20 MINUTE WHERE user_id='u2' AND ended_at IS NULL")
    r = client.get('/api/auth/me')
    assert r.status_code == 401 and r.get_json()['error'] == 'session_expired'


def test_logout(client):
    sign_in(client, 'KZ-HO-002')
    assert client.post('/api/auth/logout').status_code == 200
    assert client.get('/api/auth/me').status_code == 401


def test_inactive_and_expired_users_blocked(client):
    sql("UPDATE users SET status='Suspended' WHERE emp_id='MSEA-CLK-001'")
    r = client.post('/api/auth/login', json={'login': 'MSEA-CLK-001', 'password': TEMP})
    assert r.status_code == 403 and r.get_json()['error'] == 'account_suspended'
    sql("UPDATE users SET status='Active', valid_to = CURDATE() - INTERVAL 1 DAY WHERE emp_id='MSEA-CLK-001'")
    r = client.post('/api/auth/login', json={'login': 'MSEA-CLK-001', 'password': TEMP})
    assert r.status_code == 403 and r.get_json()['error'] == 'access_expired'
    sql("UPDATE users SET valid_to = NULL WHERE emp_id='MSEA-CLK-001'")


def test_mobile_number_login(app, client):
    sign_in(app.test_client(), 'KZ-HO-002')
    r = client.post('/api/auth/login', json={'login': '98480 10114', 'password': NEW_PW['KZ-HO-002']})
    assert r.status_code == 200 and r.get_json()['stage'] == 'otp'


def test_non_json_mutation_refused(client):
    r = client.post('/api/auth/login', data='login=x&password=y', content_type='application/x-www-form-urlencoded')
    assert r.status_code == 415
