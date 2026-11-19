"""Release 2.1: setup screens (locations, accounts, roles, packages, categories), discount schemes, custom fields,
payment links, scheduled report e-mails and effective-dated access changes. Books must still balance after each test."""
import datetime as dt

import pytest

from conftest import as_user, books_balance, ok, sql, stock_consistent

DIRECTOR, CFO, CASHIER, SUPER = 'KZ-HO-001', 'KZ-HO-002', 'KZ-MSA-021', 'KZ-IT-001'
STUDENT = 'MSA260104'


@pytest.fixture(autouse=True)
def invariants(app):
    yield
    for co, (d, c) in books_balance().items():
        assert d == c, f'{co} books do not balance: {d} vs {c}'
    assert stock_consistent() == ()


def approve_latest(app, who=DIRECTOR):
    aid = sql("SELECT id FROM approvals WHERE status='Pending' ORDER BY id DESC LIMIT 1")[0]['id']
    return ok(as_user(app, who).post(f'/api/approvals/{aid}/decide', json={'action': 'approve', 'remarks': 'ok'}))


def test_location_open_edit_close(app):
    d = as_user(app, DIRECTOR)
    zones = ok(d.get('/api/setup/regions'))['zones']
    r = ok(d.post('/api/setup/locations', json={'code': 'TLC', 'name': 'Tolichowki Store', 'type': 'Store', 'zone_id': zones[0]['id'],
                                                 'companies': {'KG': 'TLC', 'ZE': 'TLZ'}}), 201)
    assert set(r['branches']) == {'KG-TLC', 'ZE-TLC'}
    assert d.post('/api/setup/locations', json={'code': 'TLC', 'name': 'x', 'type': 'Store'}).get_json()['error'] == 'duplicate_code'
    ok(d.put('/api/setup/locations/TLC', json={'public_name': 'Kiswa-Zaibus Tolichowki'}))
    ok(d.post('/api/setup/locations/TLC/close', json={'reason': 'Test closure'}))
    assert sql("SELECT active FROM branches WHERE location='TLC'")[0]['active'] == 0
    # a location that still holds stock cannot be closed
    assert d.post('/api/setup/locations/MSA/close', json={'reason': 'x'}).get_json()['error'] == 'stock_remaining'
    assert as_user(app, CASHIER).post('/api/setup/locations', json={'code': 'ZZ', 'name': 'x', 'type': 'Store'}).status_code == 403


def test_chart_of_accounts(app):
    c = as_user(app, CFO)
    ok(c.post('/api/setup/accounts', json={'company': 'KG', 'code': '6150', 'name': 'Pantry expenses'}), 201)
    assert sql("SELECT grp FROM accounts WHERE company='KG' AND code='6150'")[0]['grp'] == 'X'
    assert c.post('/api/setup/accounts', json={'company': 'KG', 'code': '6150', 'name': 'Again'}).get_json()['error'] == 'duplicate_code'
    assert c.put('/api/setup/accounts/KG/1100', json={'active': False}).get_json()['error'] == 'control_account'
    ok(c.put('/api/setup/accounts/KG/6150', json={'name': 'Pantry and tea expenses'}))


def test_role_change_needs_approval_then_applies(app):
    s = as_user(app, SUPER)
    r = s.post('/api/setup/roles', json={'name': 'Night Cashier', 'copy_from': 'Store Cashier', 'reason': 'Evening counter at Abids'})
    assert r.status_code == 202
    approve_latest(app)
    perms = {(x['screen_key'], x['action']) for x in sql("SELECT screen_key, action FROM permissions WHERE role='Night Cashier'")}
    assert ('pos', 'add') in perms
    assert s.put('/api/setup/roles/Super Admin', json={'reason': 'x'}).get_json()['error'] == 'protected_role'
    assert s.put('/api/setup/roles/Night Cashier', json={'permissions': {'pos': ['fly']}, 'reason': 'x'}).get_json()['error'] == 'invalid'


def test_package_and_category_setup(app):
    d = as_user(app, DIRECTOR)
    r = ok(d.post('/api/setup/packages', json={'company': 'KG', 'name': 'Girls PE kit', 'gender': 'Girls', 'disc_pct': 2,
                                               'lines': [{'category': 'Socks', 'qty': 2}, {'item_id': 'KG-MSA-BLT-Std', 'qty': 1}]}), 201)
    ok(d.put(f"/api/setup/packages/{r['id']}", json={'active': False}))
    assert sql('SELECT active FROM packages WHERE id=%s', r['id'])[0]['active'] == 0
    ok(d.put('/api/setup/categories/KG/Tie', json={'max_disc': 10}))
    assert d.put('/api/setup/suppliers/KS1', json={'bank_ifsc': 'BAD'}).get_json()['error'] == 'bad_ifsc'


def test_discount_scheme_applies_without_approval(app):
    d = as_user(app, DIRECTOR)
    ok(d.post('/api/setup/schemes', json={'company': 'KG', 'name': 'Tie festival 10%', 'scope': 'category', 'category': 'Tie', 'pct': 10}), 201)
    c = as_user(app, CASHIER)
    q = ok(c.post('/api/pos/quote', json={'company': 'KG', 'lines': [{'item_id': 'KG-MSA-TIE-Std', 'qty': 1}]}))
    assert q['lines'][0]['scheme'] == 'Tie festival 10%' and q['lines'][0]['disc_pct'] == 10
    sp = ok(c.post('/api/pos/quote', json={'company': 'KG', 'sponsored': True, 'lines': [{'item_id': 'KG-MSA-TIE-Std', 'qty': 1}]}))
    assert sp['lines'][0]['disc_pct'] == 0                       # never on MSEA-sponsored issues
    r = c.post('/api/pos/shift/open', json={'location': 'MSA', 'opening': {'KG': 0, 'ZE': 0}})
    assert r.status_code in (201, 409)
    total = q['totals']['total']
    inv = ok(c.post('/api/pos/invoices', json={'branch_id': 'KG-MSA', 'adm_no': STUDENT, 'lines': [{'item_id': 'KG-MSA-TIE-Std', 'qty': 1}],
                                               'payments': [{'mode': 'cash', 'amount': total}]}), 201)
    assert inv['lines'][0]['disc_pct'] == 10                     # posted, not held for approval
    sid = sql('SELECT id FROM discount_schemes WHERE name=%s', 'Tie festival 10%')[0]['id']
    ok(d.put(f'/api/setup/schemes/{sid}', json={'active': False}))


def test_custom_fields(app):
    d = as_user(app, DIRECTOR)
    f = ok(d.post('/api/setup/custom-fields', json={'entity': 'item', 'label': 'Fabric', 'type': 'select', 'options': 'Cotton, Poly-cotton'}), 201)
    assert d.put('/api/setup/custom-values/item/KG-MSA-SHT-24', json={str(f['id']): 'Silk'}).get_json()['error'] == 'invalid'
    ok(d.put('/api/setup/custom-values/item/KG-MSA-SHT-24', json={str(f['id']): 'Cotton'}))
    assert ok(d.get('/api/setup/custom-values/item/KG-MSA-SHT-24'))[str(f['id'])] == 'Cotton'


def test_payment_link_simulated_posts_receipt(app):
    cfo = as_user(app, CFO)
    pl = ok(cfo.post('/api/payments/links', json={'company': 'KG', 'adm_no': STUDENT, 'purpose': 'advance', 'amount': 500}), 201)
    assert pl['status'] == 'Issued' and '/pay/' in pl['short_url']
    anon = app.test_client()
    assert ok(anon.get(f"/api/pay/{pl['id']}"))['amount'] == 500
    r = ok(anon.post(f"/api/pay/{pl['id']}/simulate", json={}))
    assert r['status'] == 'Paid' and r['receipt_no']
    again = ok(anon.post(f"/api/pay/{pl['id']}/simulate", json={}))                 # idempotent
    assert again['receipt_no'] == r['receipt_no']
    rc = sql('SELECT amount, mode, to_wallet FROM receipts WHERE no=%s', r['receipt_no'])[0]
    assert float(rc['amount']) == 500 and rc['mode'] == 'online' and float(rc['to_wallet']) == 500


def test_report_schedule_emails_csv(app):
    from flask import g
    d = as_user(app, DIRECTOR)
    s = ok(d.post('/api/setup/report-schedules', json={'name': 'Daily sales', 'report': 'sales-register', 'frequency': 'daily', 'run_at': '00:00',
                                                       'recipients': ['owner@example.in'], 'params': {'period': 'today'}}), 201)
    with app.app_context():
        from kz import db
        from kz.services import report_schedules as rs
        g.response_ok = True
        g.user = None
        n = rs.run_due()
        db.finish(True)
    assert n >= 1
    m = sql("SELECT attachment_name, attachment FROM outbox WHERE address='owner@example.in' ORDER BY id DESC LIMIT 1")[0]
    assert m['attachment_name'].startswith('sales-register') and m['attachment'].startswith('no,')
    assert 'rows' in sql('SELECT last_result FROM report_schedules WHERE id=%s', s['id'])[0]['last_result']


def test_scheduled_resignation_applies_on_date(app):
    s = as_user(app, SUPER)
    uid = sql("SELECT id FROM users WHERE emp_id='KZ-WH-031'")[0]['id']
    r = s.post(f'/api/users/{uid}/schedule', json={'change_type': 'Resign', 'effective_on': str(dt.date.today()), 'reason': 'Resignation letter 2026/88'})
    assert r.status_code == 202
    approve_latest(app)
    assert sql('SELECT status FROM access_schedule WHERE user_id=%s ORDER BY id DESC LIMIT 1', uid)[0]['status'] == 'Scheduled'
    from flask import g
    with app.app_context():
        from kz import db
        from kz.services.scheduler import tick
        g.response_ok = True
        tick()
        db.finish(True)
    assert sql('SELECT status FROM users WHERE id=%s', uid)[0]['status'] == 'Resigned'
