"""Year-end closing (2.6): checklist, profit transfer to capital, reports before and after closing, locking, reopening and re-closing.

The previous year (2025-26) of Kiswa Garments gets a few entries first: sales 10,000 and rent 3,000 at two locations, so the year's
profit is 7,000."""
import pytest

from conftest import as_user, books_balance, ok, sql

CO, FY, FY_NEXT = 'KG', '2025-26', '2026-27'


def _jv(no, date, lines):
    sql("INSERT INTO journals (no, company, txn_date, posted_on, type, ref, narration) VALUES (%s, %s, %s, %s, 'Journal', %s, 'Test entry for year-end')",
        no, CO, date, date, no)
    jid = sql('SELECT id FROM journals WHERE no=%s AND company=%s', no, CO)[0]['id']
    for n, (acc, br, dr, cr) in enumerate(lines, 1):
        sql('INSERT INTO journal_lines (journal_id, line_no, account, branch_id, debit, credit) VALUES (%s, %s, %s, %s, %s, %s)', jid, n, acc, br, dr, cr)


@pytest.fixture(scope='module')
def year(app):
    # a new installation marks the year before go-live Closed without a closing entry; open it for these tests
    sql("UPDATE fin_years SET status='Open' WHERE company=%s AND code=%s AND closing_journal IS NULL", CO, FY)
    sql("UPDATE companies SET books_locked_to=NULL WHERE code=%s AND books_locked_to < '2026-04-01'", CO)
    if not sql("SELECT 1 FROM journals WHERE no='YE-TEST-1'"):
        _jv('YE-TEST-1', '2025-06-10', [('1100', 'KG-HO', 6000, 0), ('4000', 'KG-HO', 0, 6000)])
        _jv('YE-TEST-2', '2025-09-15', [('1100', 'KG-MSA', 4000, 0), ('4000', 'KG-MSA', 0, 4000)])
        _jv('YE-TEST-3', '2026-02-01', [('5300', 'KG-HO', 3000, 0), ('1100', 'KG-HO', 0, 3000)])
    return app


def _rep(c, key, **a):
    q = '&'.join(f'{k}={v}' for k, v in {'company': CO, **a}.items())
    return ok(c.get(f'/api/fin/reports/{key}?{q}'))


def _row(rep, label):
    return next((r[2] for r in rep['rows'] if r[1] == label), 0)


def test_view_and_checklist(year):
    c = as_user(year, 'KZ-HO-002')
    v = ok(c.get(f'/api/fin/year-end?company={CO}&fy={FY}'))
    assert v['year']['code'] == FY and v['year']['status'] == 'Open'
    assert v['income'] == 10000 and v['expenses'] == 3000 and v['net'] == 7000
    assert v['split'] == [{'account': '3000', 'name': 'Capital Account', 'pct': 100.0, 'amount': 7000.0}]
    keys = {x['key']: x['status'] for x in v['checks']}
    assert keys['ended'] == 'ok' and keys['vouchers'] == 'ok' and keys['balanced'] == 'ok'
    assert {x['account'] for x in v['closing_lines']} == {'4000', '5300'}
    # the current year has not ended
    cur = ok(c.get(f'/api/fin/year-end?company={CO}&fy={FY_NEXT}'))
    assert next(x for x in cur['checks'] if x['key'] == 'ended')['status'] == 'block'


def test_rules_before_closing(year):
    c = as_user(year, 'KZ-HO-002')
    r = c.post('/api/fin/year-end/close', json={'company': CO, 'fy': FY_NEXT, 'reason': 'x', 'split': [{'account': '3000', 'pct': 100}], 'acknowledge': True})
    assert r.status_code == 409 and r.get_json()['error'] == 'year_not_ready'
    r = c.post('/api/fin/year-end/close', json={'company': CO, 'fy': FY, 'reason': 'Accounts final', 'split': [{'account': '3000', 'pct': 100}]})
    assert r.status_code == 409 and r.get_json()['error'] == 'review_warnings'          # GST months not filed in the test data
    for bad_split in ([], [{'account': '4000', 'pct': 100}], [{'account': '3000', 'pct': 60}], [{'account': '3000', 'pct': 'x'}]):
        r = c.post('/api/fin/year-end/close', json={'company': CO, 'fy': FY, 'reason': 'Accounts final', 'split': bad_split, 'acknowledge': True})
        assert r.status_code == 400, bad_split
    assert c.post('/api/fin/year-end/close', json={'company': CO, 'fy': FY, 'reason': '', 'split': [{'account': '3000', 'pct': 100}], 'acknowledge': True}).status_code == 400
    for emp in ('KZ-WH-030', 'KZ-AUD-001', 'KZ-MSA-021'):
        r = as_user(year, emp).post('/api/fin/year-end/close', json={'company': CO, 'fy': FY, 'reason': 'x', 'split': [{'account': '3000', 'pct': 100}], 'acknowledge': True})
        assert r.status_code == 403, emp
    assert sql("SELECT status FROM fin_years WHERE company=%s AND code=%s", CO, FY)[0]['status'] == 'Open'


def test_close_moves_profit_and_keeps_reports_right(year):
    c = as_user(year, 'KZ-HO-002')
    cap_before = float(sql("SELECT COALESCE(SUM(l.credit-l.debit),0) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.company=%s AND l.account='3000'", CO)[0]['v'])
    b0 = _rep(c, 'bs', to='2026-03-31')
    assert b0['balanced'] is True
    v = ok(c.post('/api/fin/year-end/close', json={'company': CO, 'fy': FY, 'reason': 'Accounts finalised', 'split': [{'account': '3000', 'pct': 100}], 'acknowledge': True}))
    y = sql('SELECT * FROM fin_years WHERE company=%s AND code=%s', CO, FY)[0]
    assert y['status'] == 'Closed' and y['closing_journal'] and float(y['net_profit']) == 7000 and v['year']['status'] == 'Closed'
    j = sql("SELECT * FROM journals WHERE company=%s AND no=%s", CO, y['closing_journal'])[0]
    assert j['type'] == 'Year closing' and str(j['txn_date']) == '2026-03-31'
    cap_after = float(sql("SELECT COALESCE(SUM(l.credit-l.debit),0) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.company=%s AND l.account='3000'", CO)[0]['v'])
    assert round(cap_after - cap_before, 2) == 7000
    # income and expense accounts of the year are zero after closing, at every location
    left = sql('''SELECT l.account, l.branch_id, SUM(l.debit-l.credit) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                  WHERE j.company=%s AND j.txn_date BETWEEN '2025-04-01' AND '2026-03-31' AND l.account IN ('4000','5300') GROUP BY l.account, l.branch_id''', CO)
    assert all(abs(float(r['v'])) < 0.005 for r in left)
    assert books_balance()
    # P&L of the closed year still shows what it earned
    p = _rep(c, 'pl', **{'from': '2025-04-01', 'to': '2026-03-31'})
    assert p['net'] == 7000
    # the balance sheet balances, the profit now sits in capital, nothing shown twice
    b = _rep(c, 'bs', to='2026-03-31')
    assert b['balanced'] is True and _row(b, 'Profit for the year') == 0 and _row(b0, 'Profit for the year') - _row(b, 'Profit for the year') == 7000
    assert _row(b, 'Capital Account') - _row(b0, 'Capital Account') == 7000
    assert _row(b, 'Total capital & liabilities') == _row(b0, 'Total capital & liabilities')
    # the trial balance of the closed year shows it before closing and balances; next year's opening has no income / expense
    tb = _rep(c, 'tb', **{'from': '2025-04-01', 'to': '2026-03-31'})
    assert tb['balanced'] is True and any(r[0] == '4000' for r in tb['rows'])
    tb2 = _rep(c, 'tb', **{'from': '2026-04-01', 'to': '2026-04-01'})
    opening = {r[0]: r[4] for r in tb2['rows']}
    assert abs(opening.get('4000', 0) or 0) < 0.005 and abs(opening.get('5300', 0) or 0) < 0.005
    # the year is locked: nothing can be posted into it
    lock = sql('SELECT books_locked_to FROM companies WHERE code=%s', CO)[0]['books_locked_to']
    assert str(lock) >= '2026-03-31'
    r = c.post('/api/fin/year-end/close', json={'company': CO, 'fy': FY, 'reason': 'again', 'split': [{'account': '3000', 'pct': 100}], 'acknowledge': True})
    assert r.status_code == 409


def test_reopen_and_close_again(year):
    cfo = as_user(year, 'KZ-HO-002')
    assert cfo.post('/api/fin/year-end/reopen', json={'company': CO, 'fy': FY, 'reason': 'Auditor adjustment'}).status_code == 403   # Director only
    d = as_user(year, 'KZ-HO-001')
    assert d.post('/api/fin/year-end/reopen', json={'company': CO, 'fy': FY, 'reason': ''}).status_code == 400
    old = sql('SELECT closing_journal FROM fin_years WHERE company=%s AND code=%s', CO, FY)[0]['closing_journal']
    ok(d.post('/api/fin/year-end/reopen', json={'company': CO, 'fy': FY, 'reason': 'Auditor adjustment: bonus provision'}))
    y = sql('SELECT * FROM fin_years WHERE company=%s AND code=%s', CO, FY)[0]
    assert y['status'] == 'Open' and y['reopen_reason'].startswith('Auditor') and y['closing_journal'] is None
    assert sql('SELECT reversed_by_no FROM journals WHERE company=%s AND no=%s', CO, old)[0]['reversed_by_no']
    assert str(sql('SELECT books_locked_to FROM companies WHERE code=%s', CO)[0]['books_locked_to']) < '2025-04-01'
    v = ok(cfo.get(f'/api/fin/year-end?company={CO}&fy={FY}'))
    assert v['net'] == 7000                                   # the reversal does not double anything
    # an adjustment in the reopened year, then close again with a 60 / 40 split
    _jv('YE-TEST-4', '2026-03-31', [('5300', 'KG-HO', 1000, 0), ('1100', 'KG-HO', 0, 1000)])
    sql("INSERT IGNORE INTO accounts (company, code, name, grp, is_control, active) VALUES ('KG', '3100', 'Reserves & Surplus', 'E', 0, 1)")
    v = ok(cfo.post('/api/fin/year-end/close', json={'company': CO, 'fy': FY, 'reason': 'Final after audit',
                                                      'split': [{'account': '3000', 'pct': 60}, {'account': '3100', 'pct': 40}], 'acknowledge': True}))
    s = sql('SELECT close_summary, net_profit FROM fin_years WHERE company=%s AND code=%s', CO, FY)[0]
    assert float(s['net_profit']) == 6000
    res = sql('''SELECT COALESCE(SUM(l.credit-l.debit),0) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                 WHERE j.company=%s AND l.account='3100' AND j.type='Year closing' ''', CO)[0]['v']
    assert float(res) == 2400
    assert books_balance()
    # alerts and audit trail record both events
    assert sql("SELECT 1 FROM audit_log WHERE action='Financial year reopened'") and sql("SELECT 1 FROM alerts WHERE alert_type='Financial year closed'")
    assert v['year']['status'] == 'Closed'
