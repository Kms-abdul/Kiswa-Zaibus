from conftest import NEW_PW, reauth, sign_in, sql


def test_cashier_cannot_open_user_admin(client):
    sign_in(client, 'KZ-MSA-021')
    r = client.get('/api/users')
    assert r.status_code == 403
    ev = sql("SELECT * FROM security_events WHERE type='Unauthorised access attempt' ORDER BY id DESC LIMIT 1")[0]
    assert ev['user_id'] == 'u4' and 'ctl.users' in ev['detail']


def test_read_only_auditor(client):
    sign_in(client, 'KZ-AUD-001')
    assert client.get('/api/audit').status_code == 200
    assert client.get('/api/items?company=KG').status_code == 200
    r = client.post('/api/items', json={'company': 'KG', 'name': 'X', 'category': 'Shirt'})
    assert r.status_code == 403


def test_branch_scope_on_stock(client):
    me = sign_in(client, 'KZ-ABD-010')
    assert sorted(me['branches']) == ['KG-ABD', 'ZE-ABD']
    rows = client.get('/api/stock').get_json()
    assert rows and {r['branch_id'] for r in rows} <= {'KG-ABD', 'ZE-ABD'}
    assert client.get('/api/stock?branch=KG-KPH').status_code == 403
    org = client.get('/api/org').get_json()
    assert {b['id'] for b in org['branches']} == {'KG-ABD', 'ZE-ABD'}


def test_cost_hidden_without_cost_permission(client):
    sign_in(client, 'KZ-ABD-010')
    item = client.get('/api/items?company=KG&size=1').get_json()['rows'][0]
    perms = client.get('/api/auth/me').get_json()['permissions']
    assert ('cost' in item) == ('cost' in perms.get('inv', []))


def test_closed_uppal_not_in_scope(client):
    sign_in(client, 'KZ-HO-001')
    org = client.get('/api/org').get_json()
    wh = [l for l in org['locations'] if l['code'] == 'WH'][0]
    assert wh['active'] == 0 and wh['stock_moved_to'] == 'MYJ'
    assert not [b for b in org['branches'] if b['location'] == 'WH' and b['active']]
    assert sql("SELECT COALESCE(SUM(qty),0) q FROM stock_balance WHERE branch_id LIKE '%%-WH'")[0]['q'] == 0


def test_user_create_maker_checker(app):
    # maker: Super Admin (only role with user administration); checker: Director
    d, sa, cfo = app.test_client(), app.test_client(), app.test_client()
    dr = app.test_client()
    sign_in(dr, 'KZ-HO-001')
    assert dr.post('/api/users', json={'emp_id': 'x'}).status_code == 403          # Director cannot administer users
    sign_in(d, 'KZ-IT-001')
    new = {'emp_id': 'KZ-ABD-099', 'name': 'Test Cashier Abids', 'mobile': '9876501234', 'email': 'tc@example.in', 'main_role': 'Store Cashier',
           'companies': ['KG', 'ZE'], 'branches': ['KG-ABD', 'ZE-ABD'], 'reason': 'New counter staff for Abids'}
    r = d.post('/api/users', json=new)
    assert r.status_code == 428 and r.get_json()['error'] == 'reauth_required'
    reauth(d, 'KZ-IT-001')
    assert d.post('/api/users', json=dict(new, branches=['KG-WH'])).status_code == 400          # closed branch refused
    assert d.post('/api/users', json=dict(new, mobile='12345')).status_code == 400
    r = d.post('/api/users', json=new)
    assert r.status_code == 202, r.get_json()
    apr = r.get_json()
    assert d.post('/api/users', json=new).status_code == 409                                     # same request already pending
    assert not sql("SELECT * FROM users WHERE emp_id='KZ-ABD-099'")                             # nothing applied yet
    # maker cannot approve own request
    r = d.post(f"/api/approvals/{apr['id']}/decide", json={'action': 'approve'})
    assert r.status_code == 403 and 'own request' in r.get_json()['message']
    # CFO (rank 2.5) is below Director (3)
    sign_in(cfo, 'KZ-HO-002')
    reauth(cfo, 'KZ-HO-002')
    r = cfo.post(f"/api/approvals/{apr['id']}/decide", json={'action': 'approve'})
    assert r.status_code == 403 and 'Director' in r.get_json()['message']
    # Director approves; user is created with a temporary password and must change it
    sa = dr
    lst = sa.get('/api/approvals').get_json()
    assert any(a['id'] == apr['id'] and a['can_decide'] for a in lst)
    r = sa.post(f"/api/approvals/{apr['id']}/decide", json={'action': 'approve'})
    assert r.status_code == 428
    reauth(sa, 'KZ-HO-001')
    r = sa.post(f"/api/approvals/{apr['id']}/decide", json={'action': 'approve', 'remarks': 'ok'})
    assert r.status_code == 200 and r.get_json()['status'] == 'Approved', r.get_json()
    temp = r.get_json()['dev_temp_password']
    assert temp not in r.get_json()['result']
    assert not sql('SELECT id FROM audit_log WHERE new_value LIKE %s OR old_value LIKE %s', f'%{temp}%', f'%{temp}%')
    assert not sql('SELECT id FROM approvals WHERE result LIKE %s', f'%{temp}%')
    u = sql("SELECT * FROM users WHERE emp_id='KZ-ABD-099'")[0]
    assert u['pw_must_change'] == 1 and u['status'] == 'Active'
    n = app.test_client()
    me = sign_in(n, 'KZ-ABD-099', temp)
    assert sorted(me['branches']) == ['KG-ABD', 'ZE-ABD'] and me['role'] == 'Store Cashier'


def test_super_admin_grant_needs_another_super_admin(app):
    sa, d = app.test_client(), app.test_client()
    sign_in(sa, 'KZ-IT-001')
    reauth(sa, 'KZ-IT-001')
    r = sa.put('/api/users/u15', json={'roles': ['Super Admin'], 'reason': 'Back-up administrator'})
    assert r.status_code == 202
    aid = r.get_json()['id']
    assert sql('SELECT super_admin_only FROM approvals WHERE id=%s', aid)[0]['super_admin_only'] == 1
    sign_in(d, 'KZ-HO-001')
    reauth(d, 'KZ-HO-001')
    r = d.post(f'/api/approvals/{aid}/decide', json={'action': 'approve'})
    assert r.status_code == 403 and 'Super Admin' in r.get_json()['message']
    assert d.post(f'/api/approvals/{aid}/cancel', json={}).status_code == 403          # only the requester withdraws
    r = sa.post(f'/api/approvals/{aid}/cancel', json={'reason': 'Raised by mistake'})
    assert r.get_json()['status'] == 'Cancelled'
    assert not sql("SELECT * FROM user_roles WHERE user_id='u15' AND role='Super Admin'")


def test_reject_needs_reason(app):
    sa, d = app.test_client(), app.test_client()
    sign_in(sa, 'KZ-IT-001')
    reauth(sa, 'KZ-IT-001')
    aid = sa.post('/api/users/u16/unlock', json={'reason': 'x'})
    assert aid.status_code == 409                                                       # not locked
    r = sa.put('/api/users/u16', json={'designation': 'Collection Staff, MSA', 'reason': 'Title correction'})
    aid = r.get_json()['id']
    sign_in(d, 'KZ-HO-001')
    reauth(d, 'KZ-HO-001')
    r = d.post(f'/api/approvals/{aid}/decide', json={'action': 'reject'})
    assert r.status_code == 400 and r.get_json()['error'] == 'reason_required'
    r = d.post(f'/api/approvals/{aid}/decide', json={'action': 'return', 'remarks': 'Use full title'})
    assert r.get_json()['status'] == 'Returned'


def test_cannot_change_own_access(client):
    sign_in(client, 'KZ-IT-001')
    reauth(client, 'KZ-IT-001')
    r = client.put('/api/users/u19', json={'status': 'Active', 'reason': 'x'})
    assert r.status_code == 403 and r.get_json()['error'] == 'self_change'
