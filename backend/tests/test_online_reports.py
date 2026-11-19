"""Online store (parent + staff), Razorpay webhook, ERP import, reports, settings, scheduler, doctor."""
import hashlib
import hmac
import io
import json

from conftest import as_user, books_balance, ok, sql, stock_consistent

PARENT_ADM = 'MSA260105'          # Zoya Fatima, Regular, Active


def parent_client(app, adm=PARENT_ADM):
    c = app.test_client()
    mob = sql('SELECT parent_mobile FROM students WHERE adm_no=%s', adm)[0]['parent_mobile']
    assert c.post('/api/shop/auth/start', json={'adm_no': adm, 'mobile': '9000000000'}).get_json()['error'] == 'parent_not_found'
    ch = ok(c.post('/api/shop/auth/start', json={'adm_no': adm, 'mobile': mob}))
    ok(c.post('/api/shop/auth/verify', json={'challenge': ch['challenge'], 'otp': ch['dev_otp']}))
    return c


def staff_step(app, who, oid, step, **body):
    return ok(as_user(app, who).post(f'/api/online/orders/{oid}/step/{step}', json=body))


def test_online_order_full_cycle_and_return(app):
    p = parent_client(app)
    me = ok(p.get('/api/shop/me'))
    assert any(ch['adm_no'] == PARENT_ADM for ch in me['children'])
    cat = ok(p.get(f'/api/shop/catalog/{PARENT_ADM}'))
    shirt = next(x for x in cat['items'] if x['category'] == 'Shirt' and x['in_stock'] > 5)
    assert p.post('/api/shop/orders', json={'adm_no': PARENT_ADM, 'collect_at': 'MSA', 'lines': [{'item_id': shirt['item_id'], 'qty': 99}]}).get_json()['error'] == 'over_limit'
    reserved_before = sql('SELECT reserved FROM stock_balance WHERE branch_id=%s AND item_id=%s', 'KG-MYJ', shirt['item_id'])[0]['reserved']
    placed = ok(p.post('/api/shop/orders', json={'adm_no': PARENT_ADM, 'collect_at': 'MSA', 'lines': [{'item_id': shirt['item_id'], 'qty': 2}]}), 201)
    o = placed['orders'][0]
    assert o['payment']['mode'] == 'simulated'
    assert sql('SELECT reserved FROM stock_balance WHERE branch_id=%s AND item_id=%s', 'KG-MYJ', shirt['item_id'])[0]['reserved'] == reserved_before + 2
    paid = ok(p.post(f"/api/shop/orders/{o['id']}/pay", json={'simulate': True}))
    assert paid['status'] == 'Payment Confirmed' and paid['invoice_no'].startswith('KG/INV/ONL/')
    # warehouse steps by role
    assert as_user(app, 'KZ-WH-032').post(f"/api/online/orders/{o['id']}/step/pick", json={}).get_json()['error'] == 'wrong_step_role'
    staff_step(app, 'KZ-WH-031', o['id'], 'pick')
    staff_step(app, 'KZ-WH-031', o['id'], 'picked')
    qty_before = sql('SELECT qty FROM stock_balance WHERE branch_id=%s AND item_id=%s', 'KG-MYJ', shirt['item_id'])[0]['qty']
    staff_step(app, 'KZ-WH-032', o['id'], 'qc', ok=True)
    assert sql('SELECT qty FROM stock_balance WHERE branch_id=%s AND item_id=%s', 'KG-MYJ', shirt['item_id'])[0]['qty'] == qty_before - 2
    staff_step(app, 'KZ-WH-034', o['id'], 'dispatch')
    j = staff_step(app, 'KZ-MSA-022', o['id'], 'receive')
    code = j['dev_collect_code']
    bc = as_user(app, 'KZ-MSA-022')
    assert bc.post(f"/api/online/orders/{o['id']}/collect", json={'code': '000000' if code != '000000' else '111111'}).get_json()['error'] == 'bad_otp'
    assert ok(bc.post(f"/api/online/orders/{o['id']}/collect", json={'code': code}))['status'] == 'Collected'
    # parent asks for a return; manager approves; finance refunds
    r = ok(p.post(f"/api/shop/orders/{o['id']}/request", json={'type': 'Return', 'reason': 'Too small', 'lines': [{'line_no': 1, 'qty': 1}]}), 201)
    rid = sql('SELECT id FROM order_requests WHERE no=%s', r['no'])[0]['id']
    d = ok(as_user(app, 'KZ-WH-030').post(f'/api/online/requests/{rid}/decide', json={'approve': True}))
    assert d['credit_note_no']
    fid = sql('SELECT id FROM refunds WHERE request_no=%s', r['no'])[0]['id']
    assert as_user(app, 'KZ-WH-030').post(f'/api/online/refunds/{fid}/process').status_code == 403
    assert ok(as_user(app, 'KZ-HO-002').post(f'/api/online/refunds/{fid}/process'))['status'] == 'Refunded'
    orders = ok(p.get('/api/shop/orders'))
    assert orders[0]['status'] in ('Partly returned', 'Returned')


def test_cancellation_releases_reservation(app):
    p = parent_client(app)
    cat = ok(p.get(f'/api/shop/catalog/{PARENT_ADM}'))
    tie = next(x for x in cat['items'] if x['category'] == 'Tie' and x['max_qty'] > 0)
    o = ok(p.post('/api/shop/orders', json={'adm_no': PARENT_ADM, 'collect_at': 'ABD', 'lines': [{'item_id': tie['item_id'], 'qty': 1}]}), 201)['orders'][0]
    ok(p.post(f"/api/shop/orders/{o['id']}/pay", json={'simulate': True}))
    res0 = sql('SELECT reserved FROM stock_balance WHERE branch_id=%s AND item_id=%s', 'KG-MYJ', tie['item_id'])[0]['reserved']
    r = ok(p.post(f"/api/shop/orders/{o['id']}/request", json={'type': 'Cancellation', 'reason': 'Bought at school'}), 201)
    rid = sql('SELECT id FROM order_requests WHERE no=%s', r['no'])[0]['id']
    ok(as_user(app, 'KZ-WH-030').post(f'/api/online/requests/{rid}/decide', json={'approve': True}))
    assert sql('SELECT reserved FROM stock_balance WHERE branch_id=%s AND item_id=%s', 'KG-MYJ', tie['item_id'])[0]['reserved'] == res0 - 1
    assert sql('SELECT status FROM online_orders WHERE id=%s', o['id'])[0]['status'] == 'Cancelled'


def test_razorpay_webhook_signature(app, monkeypatch):
    from kz.config import Config
    monkeypatch.setitem(Config.RAZORPAY, 'KG', {'key_id': '', 'key_secret': '', 'webhook_secret': 'whsec_test'})
    c = app.test_client()
    body = json.dumps({'id': 'evt_T1', 'event': 'payment.captured', 'payload': {'payment': {'entity': {'id': 'pay_T1', 'amount': 10000, 'status': 'captured',
                                                                                                  'method': 'upi', 'created_at': 1793000000}}}}).encode()
    bad = c.post('/webhooks/razorpay/KG', data=body, headers={'X-Razorpay-Signature': 'deadbeef', 'Content-Type': 'application/json'})
    assert bad.status_code == 400
    sig = hmac.new(b'whsec_test', body, hashlib.sha256).hexdigest()
    assert ok(c.post('/webhooks/razorpay/KG', data=body, headers={'X-Razorpay-Signature': sig, 'Content-Type': 'application/json'}))['status'] == 'payment captured'
    assert ok(c.post('/webhooks/razorpay/KG', data=body, headers={'X-Razorpay-Signature': sig, 'Content-Type': 'application/json'}))['status'] == 'duplicate'
    s = json.dumps({'id': 'evt_S1', 'event': 'settlement.processed', 'payload': {'settlement': {'entity': {'id': 'setl_1', 'amount': 9764, 'fees': 236, 'tax': 36, 'utr': 'UTR1'}}}}).encode()
    sig = hmac.new(b'whsec_test', s, hashlib.sha256).hexdigest()
    assert ok(c.post('/webhooks/razorpay/KG', data=s, headers={'X-Razorpay-Signature': sig, 'Content-Type': 'application/json'}))['status'] == 'settlement recorded'
    assert float(sql("SELECT net FROM rzp_settlements WHERE id='setl_1'")[0]['net']) == 97.64


def test_erp_csv_import(app):
    sa = as_user(app, 'KZ-IT-001')
    csv_text = ('adm_no,erp_ref,name,gender,class,section,mobile,category,status,school\n'
                'MSA260201,STU-00201,Asma Begum,F,6,A,9848012345,Regular,Active,MSA\n'
                'MSA260202,STU-00202,Bad Mobile,M,6,A,12345,Regular,Active,MSA\n'
                'MSA260104,STU-99999,Clash,M,6,A,9848012345,Regular,Active,MSA\n')
    r = ok(sa.post('/api/erp/import', data={'file': (io.BytesIO(csv_text.encode()), 'students.csv')}, content_type='multipart/form-data'))
    assert r['created'] == 1 and r['failed'] == 2
    r2 = ok(sa.post('/api/erp/import', data={'file': (io.BytesIO(csv_text.encode()), 'students.csv')}, content_type='multipart/form-data'))
    assert r2['created'] == 0 and r2['skipped'] == 1                    # repeated sync does not duplicate
    assert sql("SELECT name FROM students WHERE adm_no='MSA260201'")[0]['name'] == 'Asma Begum'
    runs = ok(sa.get('/api/erp/runs'))
    rec = ok(sa.get(f"/api/erp/runs/{runs[0]['id']}/records?status=Failed"))
    assert any('mobile' in (x['error'] or '') for x in rec)


def test_reports_balance(app):
    cfo = as_user(app, 'KZ-HO-002')
    tb = ok(cfo.get('/api/reports/trial-balance?company=KG'))
    assert round(sum(r['debit'] for r in tb), 2) == round(sum(r['credit'] for r in tb), 2)
    bs = ok(cfo.get('/api/reports/balance-sheet'))
    assert all(x['balanced'] for x in bs)
    pl = ok(cfo.get('/api/reports/profit-loss?company=KG'))
    assert pl[0]['total_income'] >= 0
    assert ok(cfo.get('/api/reports/gst?company=KG'))[0]['output_tax'] >= 0
    csv_resp = cfo.get('/api/reports/sales-register?format=csv')
    assert csv_resp.status_code == 200 and csv_resp.mimetype == 'text/csv'
    led = ok(cfo.get('/api/reports/ledger?company=KG&account=1100'))
    assert 'closing' in led
    dash = ok(cfo.get('/api/dashboard'))
    assert 'sales' in dash and 'pending_approvals' in dash
    assert as_user(app, 'KZ-MSA-021').get('/api/reports/trial-balance').status_code == 403


def test_settings_and_lock(app):
    d = as_user(app, 'KZ-HO-001')
    cfo = as_user(app, 'KZ-HO-002')
    r = ok(cfo.post('/api/settings/lock', json={'company': 'KG', 'date': '2026-09-30', 'reason': 'September closed'}), 202)
    ok(d.post(f"/api/approvals/{r['id']}/decide", json={'action': 'approve'}))
    assert str(sql("SELECT books_locked_to FROM companies WHERE code='KG'")[0]['books_locked_to']) == '2026-09-30'
    bad = cfo.post('/api/accounts/contra', json={'company': 'KG', 'kind': 'deposit', 'amount': 1, 'branch_id': 'KG-MSA', 'date': '2026-09-15'})
    assert bad.get_json()['error'] == 'books_locked'


def test_scheduler_and_doctor(app):
    from flask import g
    with app.app_context():
        from kz import db
        from kz.services.doctor import run_checks
        from kz.services.scheduler import tick
        g.response_ok = True
        out = tick()
        assert 'escalated' in out
        checks = run_checks()
        db.finish(True)
    failed = [c for c in checks if not c[1] and c[0] not in ('Messages failed in the last 7 days',)]
    assert not failed, failed
    for _co, (dr, cr) in books_balance().items():
        assert dr == cr
    assert stock_consistent() == ()
