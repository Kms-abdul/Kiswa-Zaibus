"""Release 2.3.1 hardening: regression tests for the findings of the aggressive test round (10 Nov 2026), plus a route sweep:
every /api route for four roles and signed out, with empty, wrong-type and hostile bodies, must never answer 5xx."""
import datetime as dt
import io
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from conftest import as_user, books_balance, ok, sign_in, sql, stock_consistent

DIRECTOR, CFO, CASHIER, INCHARGE, BM, AUDITOR, INV_MGR = ('KZ-HO-001', 'KZ-HO-002', 'KZ-MSA-021', 'KZ-MSA-020', 'KZ-ABD-010', 'KZ-AUD-001', 'KZ-WH-030')
STUDENT = 'MSA260104'
TODAY = dt.date.today()


def race(*fns):
    bar = threading.Barrier(len(fns))

    def run(f):
        bar.wait()
        return f()
    with ThreadPoolExecutor(len(fns)) as ex:
        return list(ex.map(run, fns))


def shift(c):
    c.post('/api/pos/shift/open', json={'location': 'MSA', 'opening': {'KG': 1000, 'ZE': 500}})


def close_shift(c):
    """Leave the till as other tests expect it: no open shift (counted = expected, so it closes without approval)."""
    sh = c.get('/api/pos/shift').get_json() or {}
    if sh.get('id') and sh.get('status') == 'Open':
        live = sh['live']
        ok(c.post(f"/api/pos/shift/{sh['id']}/close", json={'counted': {k: v['expected'] for k, v in live.items()}}))


def test_gst_payment_double_click_creates_one_challan(app):
    cfo, d = as_user(app, CFO), as_user(app, DIRECTOR)
    ym = f'{TODAY:%Y-%m}'
    sql("DELETE FROM gst_challans WHERE company='ZE' AND period=%s", ym)
    rs = race(lambda: cfo.post('/api/fin/gst/pay', json={'company': 'ZE', 'ym': ym, 'cpin': 'C1'}),
              lambda: d.post('/api/fin/gst/pay', json={'company': 'ZE', 'ym': ym, 'cpin': 'C2'}))
    assert sorted(r.status_code for r in rs) in ([201, 409], [400, 400], [409, 409]), [r.get_json() for r in rs]
    assert sql("SELECT COUNT(*) n FROM gst_challans WHERE company='ZE' AND period=%s AND status<>'Cancelled'", ym)[0]['n'] <= 1
    assert cfo.post('/api/fin/gst/pay', json={'company': 'ZE'}).get_json()['error'] == 'invalid_month'


def test_evidence_is_readable_only_by_people_who_may_read_its_document(app):
    cfo = as_user(app, CFO)
    v = ok(cfo.post('/api/fin/vouchers', json={'vtype': 'JV', 'company': 'KG', 'vdate': str(TODAY), 'branch_id': 'KG-HO', 'narration': 'Directors remuneration',
                                              'lines': [{'account': '5400', 'debit': 900}, {'account': '1120', 'credit': 900}], 'submit': True}), 201)
    r = cfo.post('/api/evidence', data={'ref': v['no'], 'kind': 'Payslip', 'company': 'KG', 'file': (io.BytesIO(b'%PDF payslip'), 'payslip.pdf', 'text/html')},
                 content_type='multipart/form-data')
    eid = ok(r, 201)['id']
    d = cfo.get(f'/api/evidence/{eid}/download')
    assert d.status_code == 200 and d.headers['Content-Type'].startswith('application/pdf')        # type from the extension, not the uploader's claim
    cs = as_user(app, CASHIER)
    assert cs.get(f'/api/evidence/{eid}/download').status_code == 404                               # no finance access
    assert cs.get(f"/api/evidence?ref={v['no']}").get_json() == []
    assert cs.post(f'/api/evidence/{eid}/supersede', json={'reason': 'x'}).status_code == 404
    sql("DELETE FROM user_companies WHERE user_id=(SELECT id FROM users WHERE emp_id=%s) AND company='KG'", AUDITOR)
    try:
        assert as_user(app, AUDITOR).get(f'/api/evidence/{eid}/download').status_code == 404       # other company
    finally:
        sql("INSERT IGNORE INTO user_companies (user_id, company) SELECT id, 'KG' FROM users WHERE emp_id=%s", AUDITOR)
    assert as_user(app, DIRECTOR).get(f'/api/evidence/{eid}/download').status_code == 200


def test_malformed_input_gets_a_clean_400(app):
    s = as_user(app, 'KZ-IT-001')
    cfo = as_user(app, CFO)
    assert cfo.post('/api/fin/vouchers', json=[1, 2]).get_json()['error'] == 'invalid_body'
    assert app.test_client().post('/api/shop/auth/start', json=['x']).status_code == 400
    for path in ('/api/fin/gst/pay', '/api/fin/tds/certificate', '/api/fin/bank/match', '/api/fin/bank/voucher', '/api/fin/assets',
                 '/api/fin/assets/depreciation', '/api/fin/compliance/mark', '/api/fin/payroll', '/api/fin/payables/proposal'):
        r = cfo.post(path, json={})
        assert r.status_code in (400, 403), (path, r.status_code, r.get_json())
    assert cfo.put('/api/fin/settings', json={'value': 'x'}).status_code == 400
    assert cfo.put('/api/fin/settings', json={'value': {'dup_days': 'x' * 9000}}).status_code == 400
    assert cfo.post('/api/fin/vouchers/1/reverse', json={'reason': 42}).status_code in (400, 404, 409)
    assert s.post('/api/users', json={'reason': 42}).status_code == 400
    assert s.post('/api/setup/regions', json={'name': 12345, 'code': ['x']}).status_code == 400
    im = as_user(app, INV_MGR)
    assert im.post('/api/items/price-change', json={'rows': 'x', 'reason': 'r'}).status_code == 400
    r = im.post('/api/purchasing/orders', json={'branch_id': 'KG-MYJ', 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 10, 'rate': 10 ** 30}]})
    assert r.status_code == 400, r.get_json()
    bm = as_user(app, BM)
    it = sql("SELECT item_id FROM stock_balance WHERE branch_id='KG-ABD' AND qty>0 LIMIT 1")[0]['item_id']
    assert bm.post('/api/inventory/adjustments', json={'branch_id': 'KG-ABD', 'adj_type': 'Physical count', 'reason': 'x', 'lines': [{'item_id': it, 'qty': 3}]}).get_json()['error'] == 'bad_quantity'
    cs = as_user(app, CASHIER)
    assert cs.post('/api/pos/quote', json={'company': 'KG', 'lines': 'abc'}).status_code == 400
    close_shift(cs)


def test_negative_future_and_fractional_values_are_refused(app):
    cfo = as_user(app, CFO)
    r = cfo.post('/api/accounts/journals', json={'company': 'KG', 'narration': 'x', 'lines': [{'account': '5900', 'debit': -10}, {'account': '5500', 'credit': -10}]})
    assert r.get_json()['error'] == 'negative_amount'
    r = cfo.post('/api/accounts/journals', json={'company': 'KG', 'narration': 'x', 'date': str(TODAY + dt.timedelta(days=30)),
                                                 'lines': [{'account': '5900', 'debit': 10}, {'account': '5500', 'credit': 10}]})
    assert r.get_json()['error'] == 'future_date'
    base = {'vtype': 'JV', 'company': 'KG', 'branch_id': 'KG-HO', 'narration': 'edge', 'submit': True}
    r = cfo.post('/api/fin/vouchers', json={**base, 'vdate': str(TODAY), 'lines': [{'account': '5400', 'debit': -100}, {'account': '1120', 'credit': -100}]})
    assert r.get_json()['error'] == 'invalid_voucher'
    nxt = TODAY + dt.timedelta(days=2)
    fy_end = sql("SELECT MAX(to_date) d FROM fin_years WHERE company='KG'")[0]['d']
    if nxt <= fy_end:
        v = ok(cfo.post('/api/fin/vouchers', json={**base, 'vdate': str(nxt), 'lines': [{'account': '5400', 'debit': 10}, {'account': '1120', 'credit': 10}]}), 201)
        assert v['status'] == 'Pending approval' and 'Post-dated' in sql('SELECT reason FROM approvals WHERE no=%s', v['approval_no'])[0]['reason']
    r = cfo.post('/api/fin/vouchers', json={**base, 'vdate': str(fy_end + dt.timedelta(days=40)), 'lines': [{'account': '5400', 'debit': 10}, {'account': '1120', 'credit': 10}]})
    assert r.get_json()['error'] == 'invalid_voucher' and 'No financial year' in r.get_json()['message']
    cs = as_user(app, CASHIER)
    shift(cs)
    r = cs.post('/api/pos/invoices', json={'branch_id': 'KG-MSA', 'adm_no': STUDENT, 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 2.5}], 'payments': [{'mode': 'cash', 'amount': 872.5}]})
    assert r.get_json()['error'] == 'bad_quantity'
    item = sql("SELECT item_id FROM stock_balance WHERE branch_id='KG-MSA' AND qty>2 LIMIT 1")[0]['item_id']
    a = ok(as_user(app, INCHARGE).post('/api/stock-audits', json={'branch_id': 'KG-MSA', 'audit_type': 'Item', 'items': [item],
                                                                'auditors': [sql('SELECT id FROM users WHERE emp_id=%s', AUDITOR)[0]['id']],
                                                                'supervisor_id': sql('SELECT id FROM users WHERE emp_id=%s', INCHARGE)[0]['id']}), 201)
    r = as_user(app, AUDITOR).post(f"/api/stock-audits/{a['id']}/counts", json={'rows': [{'line_no': 1, 'physical_qty': 2.5}]})
    assert r.get_json()['error'] == 'bad_quantity'
    close_shift(as_user(app, CASHIER))


def test_same_upi_reference_cannot_pay_two_bills(app):
    cs = as_user(app, CASHIER)
    shift(cs)
    sale = {'branch_id': 'KG-MSA', 'adm_no': STUDENT, 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 1}], 'payments': [{'mode': 'upi', 'amount': 349, 'ref': 'UTR-HARD-1'}]}
    ok(cs.post('/api/pos/invoices', json=sale), 201)
    assert cs.post('/api/pos/invoices', json=sale).get_json()['error'] == 'duplicate_payment_ref'
    close_shift(cs)


def test_finance_masters_respect_company_access_and_empty_bodies(app):
    sql("DELETE FROM user_companies WHERE user_id=(SELECT id FROM users WHERE emp_id=%s) AND company='KG'", AUDITOR)
    try:
        au = as_user(app, AUDITOR)
        assert all(r['company'] != 'KG' for r in ok(au.get('/api/fin/masters/employees')))
        assert all(r['company'] != 'KG' for r in ok(au.get('/api/fin/masters/party_tax')))
        assert au.get('/api/fin/masters/party_tax?company=KG').status_code == 403
    finally:
        sql("INSERT IGNORE INTO user_companies (user_id, company) SELECT id, 'KG' FROM users WHERE emp_id=%s", AUDITOR)
    cfo = as_user(app, CFO)
    assert cfo.post('/api/fin/payroll', json={'company': 'KG'}).get_json()['error'] == 'invalid_month'
    assert cfo.post('/api/fin/payables/proposal', json={'company': 'KG'}).get_json()['error'] == 'nothing_selected'


# ---------------------------------------------------------------- sweep
SKIP = {'/api/auth/logout', '/api/auth/password', '/api/settings/lock', '/api/payments/go-live', '/api/auth/change-password', '/api/auth/reset',
        '/api/shop/auth/logout', '/api/erp/sync', '/api/erp/import'}
PUBLIC = re.compile(r'^/api/(health|auth/(login|otp|forgot|reset|change-password|me)|shop/|pay/|public)')
SQLI = "x' OR '1'='1'; DROP TABLE users; --"
BODIES = [None, {}, [1, 2], 'hello', {'company': None, 'branch_id': None, 'lines': None, 'amount': None},
          {'company': 123, 'branch_id': ['KG-MSA'], 'lines': 'abc', 'amount': 'ten', 'qty': {'a': 1}, 'date': 'x', 'reason': 42, 'rows': 5, 'items': 'x', 'ids': 'x', 'value': 'x'},
          {'company': SQLI, 'branch_id': SQLI, 'reason': '<script>' * 50, 'narration': 'ü' * 5000, 'amount': -1e18, 'qty': -999999,
           'lines': [{'item_id': SQLI, 'qty': -5, 'account': SQLI, 'debit': 1e300, 'credit': -1}], 'date': '9999-99-99', 'ym': '2026-13', 'id': -1},
          {'company': 'KG', 'branch_id': 'KG-MSA', 'amount': 10 ** 30, 'qty': 10 ** 18, 'lines': [{'item_id': 'KG-MSA-SHT-24', 'qty': 10 ** 18, 'account': '5500', 'debit': 10 ** 30}]}]


def _path(rule):
    out = rule.rule
    for conv, name in re.findall(r'<(?:(\w+):)?(\w+)>', rule.rule):
        out = re.sub(r'<(?:\w+:)?' + name + '>', '999999' if conv == 'int' else ('KG' if name in ('co', 'company') else 'NOPE-404'), out, count=1)
    return out


def test_route_sweep_never_answers_5xx(app):
    rules = [r for r in app.url_map.iter_rules() if r.rule.startswith('/api') and r.rule not in SKIP]
    bad = []
    anon = app.test_client()
    for r in rules:
        for m in sorted(r.methods - {'HEAD', 'OPTIONS'}):
            res = getattr(anon, m.lower())(_path(r), **({} if m == 'GET' else {'json': {}}))
            assert res.status_code < 500, (m, r.rule)
            assert res.status_code >= 400 or PUBLIC.match(r.rule), f'{m} {r.rule} answered {res.status_code} without sign-in'
    for emp in ('KZ-IT-001', CFO, CASHIER, AUDITOR):
        c = app.test_client()
        sign_in(c, emp)
        for r in rules:
            for m in sorted(r.methods - {'HEAD', 'OPTIONS'}):
                for b in ([None] if m == 'GET' else BODIES):
                    res = getattr(c, m.lower())(_path(r), **({} if b is None else {'json': b}))
                    if res.status_code == 401:
                        sign_in(c, emp)
                        res = getattr(c, m.lower())(_path(r), **({} if b is None else {'json': b}))
                    if res.status_code >= 500:
                        bad.append(f'{emp} {m} {r.rule} {str(b)[:40]} -> {res.status_code}')
    assert not bad, bad[:20]
    c = app.test_client()
    sign_in(c, CASHIER)
    close_shift(c)
    for co, (d, cr) in books_balance().items():
        assert d == cr
    assert stock_consistent() == ()
