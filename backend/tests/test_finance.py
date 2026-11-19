"""Finance & tax (release 2.3): vouchers with GST and TDS, maker → approver routing, reversal, GST returns and set-off, TDS challans and
returns, banking, fixed assets, periods, the 73-report library with Excel / PDF exports, and the masters.
After every test the books must balance and stock must equal its ledger."""
import datetime as dt
import io

import pytest

from conftest import as_user, books_balance, ok, sql, stock_consistent

DIRECTOR, CFO, AUDITOR, CASHIER = 'KZ-HO-001', 'KZ-HO-002', 'KZ-MSA-021', 'KZ-MSA-021'
HO = 'KG-HO'
TODAY = dt.date.today()


@pytest.fixture(autouse=True)
def invariants(app):
    yield
    for co, (d, c) in books_balance().items():
        assert d == c, f'{co} books do not balance: {d} vs {c}'
    assert stock_consistent() == (), 'stock_balance differs from stock_ledger'


def attach(c, ref):
    r = c.post('/api/evidence', data={'ref': ref, 'kind': 'Invoice / bill', 'file': (io.BytesIO(b'%PDF-1.4 bill'), 'bill.pdf')}, content_type='multipart/form-data')
    assert r.status_code == 201, r.get_json()


def approve(app, no, who=DIRECTOR, action='approve', remarks='Checked'):
    aid = sql('SELECT id FROM approvals WHERE no=%s', no)[0]['id']
    return ok(as_user(app, who).post(f'/api/approvals/{aid}/decide', json={'action': action, 'remarks': remarks}))


def jlines(jno):
    return {r['account']: (float(r['debit']), float(r['credit'])) for r in
            sql('SELECT l.account, SUM(l.debit) debit, SUM(l.credit) credit FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.no=%s GROUP BY l.account', jno)}


def test_meta_dashboard_and_validation(app):
    c = as_user(app, CFO)
    m = ok(c.get('/api/fin/meta?company=KG'))
    assert len(m['types']) == 21 and len(m['reports']) == 73 and m['can']['close'] and {'tb', 'g3b', 't26q', 'fareg', 'bbrs'} <= {r['key'] for r in m['reports']}
    assert any(a['code'] == '1311' for a in m['accounts']) and m['tds_sections'] and m['docs_service']['configured'] in (True, False)
    d = ok(c.get('/api/fin/dashboard?company=KG'))
    assert {'income', 'net', 'cash', 'bank', 'gst_due', 'tds_due', 'trend', 'alerts'} <= set(d)
    v = ok(c.post('/api/fin/validate', json={'gstin': '36AADCD5521K1Z3', 'pan': 'ABCPE1234F'}))
    assert v['pan']['ok'] and 'ok' in v['gstin']
    assert not ok(c.post('/api/fin/validate', json={'gstin': '36AADCD5521K1Z0'}))['gstin']['ok']
    assert as_user(app, CASHIER).get('/api/fin/meta?company=KG').status_code == 403          # store cashier has no finance access


def test_expense_with_gst_and_tds_posts_one_balanced_journal(app):
    c = as_user(app, CFO)
    sql("UPDATE suppliers SET gstin='36ABMPC3302H1Z3' WHERE id='KS3'")            # the demo GSTIN fails the checksum; use a valid one
    sql("UPDATE party_tax SET section='194J(b)', pan='ABMPC3302H' WHERE party='KS3'")
    body = {'vtype': 'EX', 'company': 'KG', 'vdate': str(TODAY), 'branch_id': HO, 'dept': 'D-ACC', 'ref': 'ACO/26-27/118', 'party': {'kind': 'sup', 'id': 'KS3'},
            'narration': 'Statutory audit & GST advisory', 'lines': [{'account': '5310', 'debit': 60000}],
            'gst': {'rows': [{'hsn': '998231', 'taxable': 60000, 'rate': 18}], 'itc': 'Eligible'}, 'tds': {'section': '194J(b)', 'base': 60000}}
    pv = ok(c.post('/api/fin/vouchers/preview', json=body))
    assert pv['tax'] == 10800 and pv['tds']['amount'] == 6000 and not pv['errors'] and pv['diff'] == 0
    v = ok(c.post('/api/fin/vouchers', json=body), 201)
    assert v['status'] == 'Draft' and v['no'].startswith('KG/EXP/')
    r = c.post(f"/api/fin/vouchers/{v['id']}/submit", json={})
    assert r.get_json()['error'] == 'evidence_required'                                   # every expense needs its bill
    attach(c, v['no'])
    v = ok(c.post(f"/api/fin/vouchers/{v['id']}/submit", json={}))
    assert v['status'] == 'Posted' and v['journal_no']
    L = jlines(v['journal_no'])
    assert L['5310'] == (60000, 0) and L['1311'] == (5400, 0) and L['1312'] == (5400, 0) and L['2300'] == (0, 6000) and L['2000'] == (0, 64800)
    t = sql("SELECT section, base, amount, status FROM tds_entries WHERE voucher_no=%s", v['no'])[0]
    assert t['section'] == '194J(b)' and float(t['amount']) == 6000 and t['status'] == 'Deducted'
    # the same supplier bill cannot be booked twice
    dup = c.post('/api/fin/vouchers', json={**body, 'submit': True})
    assert dup.get_json()['error'] == 'duplicate_bill'
    # posted vouchers are never edited
    assert c.put(f"/api/fin/vouchers/{v['id']}", json={'narration': 'x'}).get_json()['error'] == 'wrong_status'


def test_rule_routing_approval_and_reversal(app):
    c = as_user(app, CFO)
    body = {'vtype': 'JV', 'company': 'KG', 'vdate': str(TODAY), 'branch_id': HO, 'narration': 'Partner drawings regrouped',
            'lines': [{'account': '5900', 'debit': 5000}, {'account': '3000', 'credit': 5000}]}
    v = ok(c.post('/api/fin/vouchers', json={**body, 'submit': True}), 201)
    assert v['status'] == 'Pending approval' and v['approver_role'] == 'Director'           # rule: any JV touching capital
    ap = sql('SELECT required_role FROM approvals WHERE no=%s', v['approval_no'])[0]
    assert ap['required_role'] == 'Director'
    assert c.post(f"/api/approvals/{sql('SELECT id FROM approvals WHERE no=%s', v['approval_no'])[0]['id']}/decide",
                                  json={'action': 'approve', 'remarks': 'x'}).status_code == 403   # the maker cannot approve
    approve(app, v['approval_no'])
    v = ok(c.get(f"/api/fin/vouchers/{v['id']}"))
    assert v['status'] == 'Posted' and v['approved_by'] and L_ok(v['journal_no'])
    # reversal: a new RX voucher (always checked), the original becomes Reversed when it posts
    r = ok(c.post(f"/api/fin/vouchers/{v['id']}/reverse", json={'reason': 'Wrong ledger'}), 201)
    assert r['vtype'] == 'RX' and r['status'] == 'Pending approval'
    assert c.post(f"/api/fin/vouchers/{v['id']}/reverse", json={'reason': 'again'}).get_json()['error'] == 'duplicate'
    approve(app, r['approval_no'])
    assert ok(c.get(f"/api/fin/vouchers/{v['id']}"))['status'] == 'Reversed'
    # a returned voucher can be corrected and resubmitted; a rejected one is cancelled
    v2 = ok(c.post('/api/fin/vouchers', json={**body, 'submit': True}), 201)
    approve(app, v2['approval_no'], action='return', remarks='Attach the partner letter')
    v2 = ok(c.get(f"/api/fin/vouchers/{v2['id']}"))
    assert v2['status'] == 'Returned' and v2['can']['edit']
    ok(c.put(f"/api/fin/vouchers/{v2['id']}", json={'narration': 'Partner drawings regrouped (letter attached)'}))
    v2 = ok(c.post(f"/api/fin/vouchers/{v2['id']}/submit", json={}))
    approve(app, v2['approval_no'], action='reject', remarks='Not needed')
    v2 = ok(c.post(f"/api/fin/vouchers/{v2['id']}/cancel", json={'reason': 'Rejected by Director'}))
    assert v2['status'] == 'Cancelled'


def L_ok(jno):
    L = jlines(jno)
    return round(sum(d for d, _ in L.values()), 2) == round(sum(cr for _, cr in L.values()), 2)


def test_gst_returns_setoff_payment_and_filing(app):
    c = as_user(app, CFO)
    ym = f'{TODAY:%Y-%m}'
    # an inter-state B2B sales invoice (IGST)
    cust = sql("SELECT id FROM customers WHERE company='KG' ORDER BY id LIMIT 1")[0]['id']
    sql("UPDATE customers SET gstin='37AAATL7781M1ZW' WHERE id=%s", cust)
    si = ok(c.post('/api/fin/vouchers', json={'vtype': 'SI', 'company': 'KG', 'vdate': str(TODAY), 'branch_id': 'KG-ABD', 'party': {'kind': 'cust', 'id': cust},
                                             'narration': 'Uniform supply, 2,200 sets', 'lines': [{'account': '4000', 'credit': 1540000}],
                                             'gst': {'rows': [{'hsn': '6203', 'taxable': 1540000, 'rate': 5}]}, 'submit': True}), 201)
    assert si['status'] == 'Pending approval' and si['approver_role'] == 'CFO'          # rule: any voucher above ₹5 lakh
    approve(app, si['approval_no'])                                                    # the Director may approve a CFO-level request
    si = ok(c.get(f"/api/fin/vouchers/{si['id']}"))
    assert si['status'] == 'Posted' and jlines(si['journal_no'])['2120'] == (0, 77000)
    s = ok(c.get(f'/api/fin/gst/summary?company=KG&ym={ym}'))['setoff']
    assert s['liability']['igst'] >= 77000 and s['cash']['total'] > 0 and 'use' in s and s['due']
    g1 = ok(c.get(f'/api/fin/gst/return/1?company=KG&ym={ym}'))
    assert any(d['no'] == si['no'] for d in g1['b2b'])
    j = c.get(f'/api/fin/gst/return/1/json?company=KG&ym={ym}')
    assert j.status_code == 200 and b'"b2b"' in j.data
    ok(c.get(f'/api/fin/gst/return/3B?company=KG&ym={ym}'))
    assert c.get(f'/api/fin/gst/return/3B/json?company=KG&ym={ym}').status_code == 200
    # e-invoice for the B2B invoice (company registration has e-invoicing on)
    sql("UPDATE gst_registrations SET einvoice=1 WHERE company='KG'")
    e = ok(c.post('/api/fin/gst/einvoice', json={'company': 'KG', 'no': si['no']}))
    assert len(e['irn']) == 64 and e['status'] == 'Generated'
    assert c.post('/api/fin/gst/einvoice', json={'company': 'KG', 'no': si['no']}).get_json()['error'] == 'duplicate'
    ok(c.post('/api/fin/gst/eway', json={'company': 'KG', 'no': si['no'], 'generate': True, 'vehicle': 'ts09ab1234', 'km': 420}))
    # GSTR-3B cannot be filed before the tax is paid; pay through a GST adjustment voucher (always checked), then file
    assert c.post('/api/fin/gst/file', json={'company': 'KG', 'ret': '3B', 'ym': ym, 'arn': 'AA3611260012345'}).get_json()['error'] == 'unpaid'
    p = ok(c.post('/api/fin/gst/pay', json={'company': 'KG', 'ym': ym, 'cpin': '26113600012345', 'cin': 'HDFC2611180001'}), 201)
    assert p['vtype'] == 'GA' and p['status'] == 'Pending approval'
    attach(c, p['no'])                                                                 # the challan copy (GST adjustments need their document)
    approve(app, p['approval_no'])
    assert sql("SELECT status FROM gst_challans WHERE company='KG' AND period=%s", ym)[0]['status'] == 'Paid'
    ok(c.post('/api/fin/gst/file', json={'company': 'KG', 'ret': '1', 'ym': ym, 'arn': 'AA3611260011111'}))
    ok(c.post('/api/fin/gst/file', json={'company': 'KG', 'ret': '3B', 'ym': ym, 'arn': 'AA3611260012345'}))
    # the filed month is now closed for ordinary posting: a backdated voucher needs CFO / Director approval
    v = ok(c.post('/api/fin/vouchers', json={'vtype': 'CV', 'company': 'KG', 'vdate': str(TODAY), 'branch_id': HO, 'narration': 'Cash deposited',
                                            'lines': [{'account': '1120', 'debit': 100}, {'account': '1100', 'credit': 100}], 'submit': True}), 201)
    assert v['status'] == 'Pending approval' and 'GSTR-3B is filed' in sql('SELECT reason FROM approvals WHERE no=%s', v['approval_no'])[0]['reason']
    approve(app, v['approval_no'])
    assert ok(c.get(f"/api/fin/vouchers/{v['id']}"))['status'] == 'Posted'
    # GSTR-2B reconciliation with the sample portal rows
    r = ok(c.post('/api/fin/gst/portal', json={'company': 'KG', 'kind': '2B', 'ym': ym, 'sample': 1}))
    assert r['loaded'] and any(x['status'].startswith('In 2B, not in books') for x in r['rows'])
    sql("DELETE FROM gst_filings WHERE company='KG' AND period=%s", ym)          # reopen the month for the tests that follow


def test_tds_challan_return_and_certificate(app):
    c = as_user(app, CFO)
    e = sql("SELECT id, amount FROM tds_entries WHERE company='KG' AND kind='Payable' AND status='Deducted' AND amount>0")
    assert e
    assert c.post('/api/fin/tds/pay', json={'company': 'KG', 'ids': [x['id'] for x in e], 'bsr': '12', 'serial': '1'}).get_json()['error'] == 'invalid'
    v = ok(c.post('/api/fin/tds/pay', json={'company': 'KG', 'ids': [x['id'] for x in e], 'bsr': '0510308', 'serial': '00412'}), 201)
    if v['status'] == 'Pending approval':
        approve(app, v['approval_no'])
    assert {x['status'] for x in sql('SELECT status FROM tds_entries WHERE id IN %s', tuple(x['id'] for x in e))} == {'Deposited'}
    ch = sql("SELECT cin, status FROM tds_challans WHERE voucher_no=%s", v['no'])[0]
    assert ch['status'] == 'Paid' and ch['cin'].startswith('0510308')
    fy = sql("SELECT code FROM fin_years WHERE company='KG' AND status='Open' ORDER BY from_date DESC LIMIT 1")[0]['code']
    q = {1: 'Q4', 2: 'Q4', 3: 'Q4', 4: 'Q1', 5: 'Q1', 6: 'Q1', 7: 'Q2', 8: 'Q2', 9: 'Q2', 10: 'Q3', 11: 'Q3', 12: 'Q3'}[TODAY.month]
    Q = ok(c.get(f'/api/fin/tds/quarter?company=KG&fy={fy}&quarter={q}&form=26Q'))
    assert Q['form_name'] == 'Form 140' and Q['entries'] and Q['undeposited'] == 0
    assert c.post('/api/fin/tds/file', json={'company': 'KG', 'fy': fy, 'quarter': q, 'form': '26Q', 'token': '123'}).get_json()['error'] == 'invalid'
    ok(c.post('/api/fin/tds/file', json={'company': 'KG', 'fy': fy, 'quarter': q, 'form': '26Q', 'token': '123456789012345'}))
    cert = ok(c.post('/api/fin/tds/certificate', json={'company': 'KG', 'fy': fy, 'quarter': q, 'party': 'KS3'}), 201)
    assert cert['form'] == 'Form 131' and cert['cert_no'].startswith('F131-')
    chk = ok(c.get('/api/fin/tds/checks?company=KG'))
    assert any(p['issue'] for p in chk['pan']) or chk['thresholds'] is not None


def test_bank_cheques_assets_payroll_and_periods(app):
    c = as_user(app, CFO)
    # bank: demo feed import, automatic matching, bank-only charges posted with a bank voucher
    r = ok(c.post('/api/fin/bank/import', json={'company': 'KG', 'account': '1120', 'demo': 1}))
    assert r['imported'] >= 3
    st = ok(c.get('/api/fin/bank/statement?company=KG&account=1120&open=1'))
    chg = next(x for x in st['lines'] if 'CHARGES' in x['narration'])
    bv = ok(c.post('/api/fin/bank/voucher', json={'line': chg['id']}), 201)
    if bv['status'] == 'Pending approval':
        approve(app, bv['approval_no'])
    assert sql('SELECT match_journal FROM bank_statement_lines WHERE id=%s', chg['id'])[0]['match_journal']
    csv = 'Date,Narration,Ref,Withdrawal,Deposit\n05-11-2026,NEFT CR TEST,UTR1,,1500\n'
    r = c.post('/api/fin/bank/import', data={'company': 'KG', 'account': '1120', 'file': (io.BytesIO(csv.encode()), 'stmt.csv')}, content_type='multipart/form-data')
    assert ok(r)['imported'] == 1
    assert ok(c.post('/api/fin/bank/import', data={'company': 'KG', 'account': '1120', 'file': (io.BytesIO(csv.encode()), 'stmt.csv')},
                     content_type='multipart/form-data'))['imported'] == 0          # the same line is never imported twice
    # cheque payment, then stop payment raises a reversal for approval
    pv = ok(c.post('/api/fin/vouchers', json={'vtype': 'PV', 'company': 'KG', 'vdate': str(TODAY), 'branch_id': HO, 'party': {'kind': 'sup', 'id': 'KS1'},
                                             'narration': 'Part payment', 'lines': [{'account': '1120', 'credit': 5000}], 'options': {'cheque': '004512'}, 'submit': True}), 201)
    assert pv['status'] == 'Posted'
    chq = sql('SELECT id, status FROM cheques WHERE voucher_no=%s', pv['no'])[0]
    assert chq['status'] == 'Issued'
    assert c.post(f"/api/fin/cheques/{chq['id']}/status", json={'status': 'Stop payment'}).get_json()['error'] == 'reason_required'
    ok(c.post(f"/api/fin/cheques/{chq['id']}/status", json={'status': 'Stop payment', 'reason': 'Lost in courier'}))
    assert sql("SELECT COUNT(*) n FROM fin_vouchers WHERE vtype='RX' AND ref=%s", pv['no'])[0]['n'] == 1
    # fixed asset: capitalisation is always checked; depreciation posts monthly
    a = ok(c.post('/api/fin/assets', json={'company': 'KG', 'branch_id': 'KG-ABD', 'category': 'IT', 'name': 'Billing PCs (6)', 'cost': 186000,
                                          'date': str(TODAY.replace(day=1) - dt.timedelta(days=40)), 'gst_rate': 18}), 201)
    assert a['asset']['status'] == 'Pending capitalisation' and a['voucher']['status'] == 'Pending approval'
    attach(c, a['voucher']['no'])
    approve(app, a['voucher']['approval_no'])
    aid = a['asset']['id']
    assert ok(c.get('/api/fin/assets?company=KG'))[-1]['status'] == 'Active'
    last = f"{TODAY.replace(day=1) - dt.timedelta(days=1):%Y-%m}"
    d = ok(c.post('/api/fin/assets/depreciation', json={'company': 'KG', 'ym': last}))
    if d.get('status') == 'Pending approval':
        approve(app, d['approval_no'])
    assert float(sql('SELECT acc_dep FROM fixed_assets WHERE id=%s', aid)[0]['acc_dep']) > 0
    assert c.post('/api/fin/assets/depreciation', json={'company': 'KG', 'ym': last}).get_json()['error'] == 'duplicate'
    ok(c.post(f'/api/fin/assets/{aid}/verify', json={'found': True, 'condition': 'Good'}))
    # payroll: a salary voucher draft with PF / ESI / PT / TDS
    p = ok(c.post('/api/fin/payroll', json={'company': 'KG', 'month': last}), 201)
    assert p['vtype'] == 'SAL' and p['status'] == 'Draft' and abs(p['total']) > 0
    assert any(x['account'] == '2300' for x in p['built'])                     # the high earner has TDS under s.392
    # periods: close and reopen
    lock = TODAY.replace(day=1) - dt.timedelta(days=60)
    ok(c.post('/api/fin/periods/close', json={'company': 'KG', 'date': str(lock), 'reason': 'Month-end close'}))
    assert as_user(app, AUDITOR).post('/api/fin/periods/reopen', json={'company': 'KG', 'date': str(lock - dt.timedelta(days=30)), 'reason': 'x'}).status_code == 403
    ok(as_user(app, DIRECTOR).post('/api/fin/periods/reopen', json={'company': 'KG', 'date': str(lock - dt.timedelta(days=400)), 'reason': 'Audit adjustment'}))
    sql("UPDATE companies SET books_locked_to=NULL WHERE code='KG'")
    assert ok(c.get('/api/fin/compliance?company=KG'))


def test_every_report_runs_and_exports(app):
    c = as_user(app, CFO)
    cat = ok(c.get('/api/fin/reports'))
    for r in cat:
        d = c.get(f"/api/fin/reports/{r['key']}?company=KG&from={TODAY.replace(month=4, day=1) if TODAY.month >= 4 else TODAY.replace(year=TODAY.year - 1, month=4, day=1)}")
        assert d.status_code == 200, (r['key'], d.get_json())
        j = d.get_json()
        assert j['columns'] and isinstance(j['rows'], list) and len(j['drill']) == len(j['rows']), r['key']
    tb = ok(c.get('/api/fin/reports/tb?company=KG'))
    assert tb['balanced']
    bs = ok(c.get('/api/fin/reports/bs?company=KG'))
    assert bs['balanced'], bs['diff']
    x = c.get('/api/fin/reports/tb?company=KG&format=xlsx')
    assert x.status_code == 200 and x.data[:2] == b'PK' and x.headers['X-Rendered-By'] in ('node', 'python')
    p = c.get('/api/fin/reports/pl?company=KG&format=pdf')
    assert p.status_code == 200 and p.data[:4] == b'%PDF'
    assert c.get('/api/fin/reports/tb?company=KG&format=csv').status_code == 200
    assert as_user(app, CASHIER).get('/api/fin/reports/tb?company=KG').status_code == 403


def test_masters_and_settings(app):
    c = as_user(app, CFO)
    ok(c.put('/api/fin/masters/cost_centres', json={'row': {'id': 'CC-TST', 'name': 'Test centre', 'kind': 'Cost centre', 'active': 1}, 'reason': 'New centre'}))
    assert sql("SELECT name FROM cost_centres WHERE id='CC-TST'")[0]['name'] == 'Test centre'
    assert c.put('/api/fin/masters/party_tax', json={'row': {'party': 'KS2', 'company': 'KG', 'pan': 'BAD'}}).get_json()['error'] == 'invalid'
    ok(c.put('/api/fin/masters/party_tax', json={'row': {'party': 'KS2', 'company': 'KG', 'pan': 'AAEFB7714Q', 'section': '194C'}, 'reason': 'PAN from GSTIN'}))
    ok(c.put('/api/fin/masters/budgets', json={'row': {'company': 'KG', 'fy': sql("SELECT code FROM fin_years WHERE company='KG' AND status='Open' LIMIT 1")[0]['code'],
                                                       'account': '5310', 'amount': 500000}, 'reason': 'Annual budget'}))
    s = ok(c.put('/api/fin/settings', json={'value': {'dup_days': 10}, 'reason': 'Wider duplicate window'}))
    assert s['dup_days'] == 10 and s['gst']['interest'] == 18
    assert as_user(app, AUDITOR).put('/api/fin/settings', json={'value': {'dup_days': 1}}).status_code == 403
