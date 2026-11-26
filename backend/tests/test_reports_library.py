"""Report library (2.4): every report opens and exports (Print / PDF / Excel / CSV) for every role that can see it, rows stay inside the
user's scope, formats follow the Reports rights, and the figures agree with the books and the stock ledger."""
import io

import pytest
from openpyxl import load_workbook

from conftest import as_user, ok, sql

ROLES = ['KZ-IT-001', 'KZ-HO-001', 'KZ-HO-002', 'KZ-AUD-001', 'KZ-ABD-010', 'KZ-MSA-020', 'KZ-MSA-021', 'KZ-WH-030', 'MSEA-OFF-004']
WIDE = {'from': '2020-01-01', 'to': '2030-12-31'}


def _q(args):
    return '&'.join(f'{k}={v}' for k, v in args.items())


def test_every_report_every_role_json(app):
    seen = set()
    for emp in ROLES:
        c = as_user(app, emp)
        r = c.get('/api/reports/library')
        if r.status_code == 403:
            continue
        lib = ok(r)
        for rep in lib['reports']:
            res = c.get(f"/api/reports/library/{rep['key']}?{_q(WIDE)}")
            assert res.status_code == 200, (emp, rep['key'], res.status_code, res.get_json())
            d = res.get_json()
            n = len(d['columns'])
            assert all(len(row) == n for row in d['rows']), (emp, rep['key'])
            assert len(d['drill']) == len(d['rows'])
            if d.get('totals'):
                assert len(d['totals']) == n
            seen.add(rep['key'])
    from kz.services.op_reports import REPORTS
    assert seen == set(REPORTS), set(REPORTS) - seen


@pytest.mark.parametrize('fmt', ['pdf', 'xlsx', 'csv', 'print'])
def test_every_report_every_format(app, fmt):
    c = as_user(app, 'KZ-HO-001')
    before = sql("SELECT COUNT(*) n FROM access_log WHERE what LIKE %s", f'Report %({fmt.upper()})')[0]['n']
    reps = ok(c.get('/api/reports/library'))['reports']
    for rep in reps:
        r = c.get(f"/api/reports/library/{rep['key']}?{_q(WIDE)}&company=KG&format={fmt}")
        assert r.status_code == 200, (rep['key'], fmt, r.get_json())
        if fmt == 'pdf':
            assert r.data[:5] == b'%PDF-' and r.headers['X-Rendered-By'] in ('node', 'python')
        elif fmt == 'xlsx':
            ws = load_workbook(io.BytesIO(r.data)).active
            assert ws['A1'].value
        elif fmt == 'csv':
            assert r.data.startswith(b'\xef\xbb\xbf')
        else:
            assert r.get_json()['columns']
    after = sql("SELECT COUNT(*) n FROM access_log WHERE what LIKE %s", f'Report %({fmt.upper()})')[0]['n']
    assert after - before == len(reps) == 50            # every print / export is logged; the Director sees the whole library


def test_formats_follow_report_rights(app):
    c = as_user(app, 'KZ-MSA-021')                      # store cashier: no Reports export rights in the seed
    lib = c.get('/api/reports/library')
    if lib.status_code == 403:
        assert c.get('/api/reports/library/daily-sales').status_code == 403
        return
    fm = lib.get_json()['formats']
    for f in ('pdf', 'xlsx', 'csv', 'print'):
        r = c.get(f'/api/reports/library/daily-sales?format={f}')
        assert r.status_code == (200 if f in fm else 403), (f, fm)


def test_unknown_and_bad_input(app):
    c = as_user(app, 'KZ-HO-001')
    assert c.get('/api/reports/library/no-such').status_code == 400
    assert c.get('/api/reports/library/daily-sales?format=doc').status_code == 400
    assert c.get('/api/reports/library/daily-sales?from=2026-02-01&to=2026-01-01').status_code == 400
    assert c.get('/api/reports/library/daily-sales?from=rubbish').status_code == 400
    assert c.get('/api/reports/library/daily-sales?company=XX').status_code == 403
    assert c.get('/api/reports/library/slow-moving?days=abc').status_code == 200
    assert c.get('/api/reports/library/audit-trail?q=%27%20OR%201=1--').status_code == 200


def test_branch_scope_and_cost(app):
    c = as_user(app, 'KZ-ABD-010')                      # branch manager of one branch
    me = ok(c.get('/api/auth/me'))
    mine = set(me['branches']) if isinstance(me['branches'], list) else None
    lib = c.get('/api/reports/library')
    if lib.status_code == 403 or mine is None:
        pytest.skip('branch manager has no Reports access / all branches')
    keys = {r['key'] for r in lib.get_json()['reports']}
    for k, col in (('sales-register', 'Location'), ('stock-valuation', 'Location'), ('stock-movement', 'Location'), ('adjustment-register', 'Location')):
        if k not in keys:
            continue
        d = ok(c.get(f'/api/reports/library/{k}?{_q(WIDE)}'))
        i = [x['h'] for x in d['columns']].index(col)
        assert {r[i] for r in d['rows']} <= mine, (k, {r[i] for r in d['rows']} - mine)
    other = sql('SELECT id FROM branches WHERE id NOT IN %s LIMIT 1', tuple(mine))[0]['id']
    assert c.get(f'/api/reports/library/sales-register?branch={other}').status_code == 403
    d = ok(as_user(app, 'KZ-HO-001').get(f'/api/reports/library/stock-valuation?{_q(WIDE)}'))
    assert 'Cost value' in [x['h'] for x in d['columns']]


def test_financial_reports_need_fin_right(app):
    c = as_user(app, 'KZ-WH-030')                       # inventory manager
    lib = c.get('/api/reports/library')
    if lib.status_code == 403:
        return
    keys = {r['key'] for r in lib.get_json()['reports']}
    from kz.services.op_reports import REPORTS
    fin = {k for k, r in REPORTS.items() if r['fin']}
    if keys & fin:
        return                                            # role has financial figures: nothing to refuse
    assert c.get('/api/reports/library/trial-balance').status_code == 403


def test_figures_agree(app):
    c = as_user(app, 'KZ-IT-001')
    sr = ok(c.get(f'/api/reports/library/sales-register?{_q(WIDE)}'))
    total_i = [x['h'] for x in sr['columns']].index('Total')
    st_i = [x['h'] for x in sr['columns']].index('Status')
    live = round(sum(r[total_i] for r in sr['rows'] if r[st_i] != 'Cancelled'), 2)
    ds = ok(c.get(f'/api/reports/library/daily-sales?{_q(WIDE)}'))
    assert abs(ds['totals'][[x['h'] for x in ds['columns']].index('Sales')] - live) < 0.05
    db_total = float(sql("SELECT COALESCE(SUM(total),0) t FROM invoices WHERE status<>'Cancelled'")[0]['t'])
    assert abs(live - db_total) < 0.05

    pm = ok(c.get(f'/api/reports/library/payment-modes?{_q(WIDE)}'))
    paid = float(sql("SELECT COALESCE(SUM(p.amount),0) a FROM invoice_payments p JOIN invoices i ON i.id=p.invoice_id WHERE i.status<>'Cancelled'")[0]['a'])
    m = ds['totals']
    assert abs(sum(m[9:13]) - paid) < 0.05                # daily collection by mode = all payments on live bills
    assert abs(pm['totals'][3] - paid) < 0.05

    mv = ok(c.get('/api/reports/library/stock-movement?from=2000-01-01&to=2099-12-31'))
    close = {(r[0], r[1]): r[-1] for r in mv['rows']}
    bal = {(r['branch_id'], r['item_id']): int(r['qty']) for r in sql('SELECT branch_id, item_id, qty FROM stock_balance WHERE qty<>0')}
    assert {k: v for k, v in close.items() if v} == bal
    for r in mv['rows']:
        assert r[3] + sum(r[4:10]) == r[10]

    neg = ok(c.get('/api/reports/library/negative-stock'))
    assert neg['rows'] == []

    tb = ok(c.get('/api/reports/library/trial-balance?company=KG&to=2099-12-31'))
    hs = [x['h'] for x in tb['columns']]
    di, ci = next(i for i, h in enumerate(hs) if 'Debit' in h), next(i for i, h in enumerate(hs) if 'Credit' in h)
    assert abs((tb['totals'][di] or 0) - (tb['totals'][ci] or 0)) < 0.05

    it = ok(c.get(f'/api/reports/library/item-sales?{_q(WIDE)}'))
    if it['rows']:
        item = it['rows'][0][0]
        led = ok(c.get(f'/api/reports/library/item-ledger?item={item}&from=2000-01-01&to=2099-12-31'))
        assert led['rows'][-1][6] == sum(int(r['qty']) for r in sql('SELECT qty FROM stock_balance WHERE item_id=%s', item))


def test_legacy_endpoints_still_answer(app):
    c = as_user(app, 'KZ-HO-001')
    for k in ('sales-register', 'item-sales', 'stock-valuation', 'online-orders', 'sales-summary?by=mode'):
        assert c.get(f'/api/reports/{k}').status_code == 200
        assert c.get(f"/api/reports/{k}{'&' if '?' in k else '?'}format=csv").status_code == 200


def test_cost_columns_only_with_cost_right(app):
    checked = 0
    for emp in ROLES:
        c = as_user(app, emp)
        me = ok(c.get('/api/auth/me'))
        if 'view' not in me['permissions'].get('rep', []):
            continue
        has = 'cost' in me['permissions'].get('rep', [])
        for k in ('stock-valuation', 'category-sales', 'item-sales', 'stock-summary', 'slow-moving', 'transfer-register'):
            r = c.get(f'/api/reports/library/{k}?{_q(WIDE)}')
            if r.status_code == 403:
                continue
            hs = [x['h'] for x in r.get_json()['columns']]
            assert any(h in hs for h in ('Cost', 'Cost value', 'Value', 'Margin')) == has, (emp, k, hs)
            checked += 1
    assert checked


def test_stock_audit_reports_print_pdf_excel(app):
    from kz.services.stock_audit_reports import REPORTS
    c = as_user(app, 'KZ-AUD-001')
    for k in REPORTS:
        for f in ('print', 'pdf', 'xlsx', 'csv'):
            r = c.get(f'/api/stock-audits/reports/{k}?format={f}')
            assert r.status_code == 200, (k, f, r.get_json() if r.is_json else r.status_code)
            if f == 'pdf':
                assert r.data[:5] == b'%PDF-'


def test_empty_report_still_renders(app):
    from kz.services import docs_render
    for fn in (docs_render.pdf, docs_render.xlsx):
        data, _ = fn({'title': 'Empty', 'columns': [], 'rows': []})
        assert data


def test_students_purchased_by_branch(app):
    c = as_user(app, 'KZ-IT-001')
    sql("UPDATE students SET branch_name=CONCAT('Branch ', campus) WHERE campus IS NOT NULL")
    rep = ok(c.get(f'/api/reports/library/student-purchases?{_q(WIDE)}'))
    hs = [x['h'] for x in rep['columns']]
    code, n, k, no = hs.index('Branch code'), hs.index('Students'), hs.index('Purchased'), hs.index('Not purchased')
    bought = {r['adm_no'] for r in sql('''SELECT i.adm_no FROM invoices i JOIN invoice_lines l ON l.invoice_id=i.id
                                          WHERE i.status NOT IN ('Cancelled', 'Returned') AND i.adm_no IN (SELECT adm_no FROM students)
                                          GROUP BY i.adm_no HAVING SUM(l.qty-l.returned_qty) > 0''')}
    assert rep['totals'][k] == len(bought) and all(r[n] == r[k] + r[no] for r in rep['rows'])
    assert all(r[hs.index('Branch name')] == f'Branch {r[code]}' for r in rep['rows'] if r[code] != '(no code)')

    camps = ok(c.get('/api/reports/campuses'))
    assert {x['code'] for x in camps} == {r[code] for r in rep['rows'] if r[code] != '(no code)'}
    row = max(rep['rows'], key=lambda r: r[k])                   # the branch with most buyers: drill to its students
    one = ok(c.get(f"/api/reports/library/student-purchase-list?{_q(WIDE)}&{_q(rep['drill'][rep['rows'].index(row)]['args'])}&bought=yes"))
    assert len(one['rows']) == row[k] and all(x[7] == 'Yes' for x in one['rows'])
    nope = ok(c.get(f"/api/reports/library/student-purchase-list?{_q(WIDE)}&campus={row[code]}&bought=no"))
    assert len(nope['rows']) == row[no]
    if one['rows']:
        adm = one['rows'][0][0]
        bills = ok(c.get(f"/api/reports/library/sales-register?{_q(WIDE)}&adm_no={adm}"))
        live = sql("SELECT COUNT(*) n FROM invoices WHERE adm_no=%s", adm)[0]['n']
        assert len(bills['rows']) == live >= one['rows'][0][8]
    filtered = ok(c.get(f"/api/reports/library/student-purchases?{_q(WIDE)}&campus={row[code]}"))
    assert len(filtered['rows']) == 1 and filtered['rows'][0][k] == row[k]
