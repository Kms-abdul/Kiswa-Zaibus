"""Finance & tax: the report library (65 reports in three groups — Financial, GST, TDS). Every report reads the posted books; each returns
{title, columns: [{h, t}], rows: [[…]], drill: [{report, args} | None], totals?, note?} so the screen, Excel and PDF all render the same table.
Column types: text, date, num (money), qty, pct. Drill-down: a row may open another report (trial balance → ledger → voucher)."""
import datetime as dt
from collections import defaultdict

from .. import db
from ..errors import bad
from . import fin_gst, fin_tds
from .fin_common import account_names, fy_of, group, is_bank, is_cash, month_end

REPORTS = {}
BUCKETS = (('0–30', 0, 30), ('31–60', 31, 60), ('61–90', 61, 90), ('91–180', 91, 180), ('Over 180', 181, 10 ** 9))


def R(key, grp, title):
    def deco(fn):
        REPORTS[key] = {'key': key, 'group': grp, 'title': title, 'fn': fn}
        return fn
    return deco


def C(h, t='text'):
    return {'h': h, 't': t}


def out(title, cols, rows, drill=None, **kw):
    return {'title': title, 'columns': cols, 'rows': rows, 'drill': drill or [None] * len(rows), **kw}


def _d(s, default):
    return dt.date.fromisoformat(s[:10]) if s else default


def run(key, company, args):
    r = REPORTS.get(key)
    if not r:
        raise bad('Unknown report.', 'unknown_report')
    today = dt.date.today()
    fy = fy_of(company, today)
    a = {'from': _d(args.get('from'), fy['from_date']), 'to': _d(args.get('to'), today), **{k: v for k, v in args.items() if k not in ('from', 'to')}}
    res = r['fn'](company, a)
    res.update(key=key, group=r['group'], company=company, period={'from': str(a['from']), 'to': str(a['to'])})
    return res


def catalogue():
    return [{'key': r['key'], 'group': r['group'], 'title': r['title']} for r in REPORTS.values()]


# ---------- ledger helpers ----------
# Year-end closing journals (2.6) move a closed year's income and expenses to capital / reserves on its last day. Reports of a period
# leave out the closing journals dated inside that period, so P&L, budgets and comparisons show what the year earned and the trial
# balance shows the year before closing; the balance sheet keeps them (closing=True). Closing journals dated before the period are
# part of its opening balances, so a later year starts with income and expenses at zero.
CLOSING_TYPES = ('Year closing', 'Year closing reversal')


def movements(company, frm, to, branch=None, cc=None, dept=None, party=None, closing=False):
    rows = db.q('''SELECT l.account, SUM(l.debit) dr, SUM(l.credit) cr FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                   WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b AND (:br IS NULL OR l.branch_id=:br) AND (:cc IS NULL OR l.cost_centre=:cc)
                   AND (:dp IS NULL OR l.dept=:dp) AND (:p IS NULL OR l.party=:p) AND (:cl=1 OR j.type NOT IN :xt) GROUP BY l.account''',
                c=company, a=frm, b=to, br=branch, cc=cc, dp=dept, p=party, cl=1 if closing else 0, xt=CLOSING_TYPES)
    return {r['account']: (float(r['dr'] or 0), float(r['cr'] or 0)) for r in rows}


def balances(company, frm, to, **f):
    return {a: round(dr - cr, 2) for a, (dr, cr) in movements(company, frm, to, **f).items()}


def _grp(code):
    return db.val('SELECT grp FROM accounts WHERE code=:c LIMIT 1', c=code)


def _kinds(company):
    return {r['code']: r['grp'] for r in db.q('SELECT code, grp FROM accounts WHERE company=:c', c=company)}


def pl(company, frm, to, **f):
    b, k, n = balances(company, frm, to, **f), _kinds(company), account_names(company)
    inc = [(a, n.get(a, a), -v) for a, v in sorted(b.items()) if k.get(a) == 'I' and abs(v) > 0.004]
    exp = [(a, n.get(a, a), v) for a, v in sorted(b.items()) if k.get(a) == 'X' and abs(v) > 0.004]
    ti, te = round(sum(x[2] for x in inc), 2), round(sum(x[2] for x in exp), 2)
    cogs = round(sum(v for a, _, v in exp if '5000' <= a <= '5199'), 2)
    return {'income': inc, 'expenses': exp, 'total_income': ti, 'total_expenses': te, 'gross': round(ti - cogs, 2), 'net': round(ti - te, 2)}


def bs(company, to, **f):
    f = {**f, 'closing': True}               # the balance sheet includes year-end closing journals (profit already moved to capital)
    b, k, n = balances(company, dt.date(2000, 1, 1), to, **f), _kinds(company), account_names(company)
    fy = fy_of(company, to)
    profit = pl(company, fy['from_date'], to, **f)['net']
    retained = round(-sum(v for a, v in balances(company, dt.date(2000, 1, 1), fy['from_date'] - dt.timedelta(days=1), **f).items() if k.get(a) in ('I', 'X')), 2)
    assets = [(a, n.get(a, a), v) for a, v in sorted(b.items()) if k.get(a) == 'A' and abs(v) > 0.004]
    liab = [(a, n.get(a, a), -v) for a, v in sorted(b.items()) if k.get(a) == 'L' and abs(v) > 0.004]
    cap = [(a, n.get(a, a), -v) for a, v in sorted(b.items()) if k.get(a) in ('C', 'E') and abs(v) > 0.004]
    ta = round(sum(x[2] for x in assets), 2)
    tl = round(sum(x[2] for x in liab) + sum(x[2] for x in cap) + profit + retained, 2)
    return {'assets': assets, 'liabilities': liab, 'capital': cap, 'profit': profit, 'retained': retained, 'total_assets': ta, 'total_liabilities': tl, 'diff': round(ta - tl, 2)}


def _filters(a):
    return {'branch': a.get('branch') or None, 'cc': a.get('cc') or None, 'dept': a.get('dept') or None}


def ledger_rows(company, frm, to, accounts=None, party=None, branch=None):
    q = '''SELECT j.txn_date, j.no, j.ref, j.type, j.narration, l.account, l.branch_id, l.party, l.debit, l.credit FROM journal_lines l JOIN journals j ON j.id=l.journal_id
           WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b AND (:acc IS NULL OR l.account IN :accs) AND (:p IS NULL OR l.party=:p) AND (:br IS NULL OR l.branch_id=:br)
           ORDER BY j.txn_date, j.id, l.line_no'''
    return db.q(q, c=company, a=frm, b=to, acc=1 if accounts else None, accs=tuple(accounts or ('',)), p=party, br=branch)


def opening(company, before, accounts=None, party=None, branch=None):
    return float(db.val('''SELECT COALESCE(SUM(l.debit-l.credit), 0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.company=:c AND j.txn_date<:a
                           AND (:acc IS NULL OR l.account IN :accs) AND (:p IS NULL OR l.party=:p) AND (:br IS NULL OR l.branch_id=:br)''',
                        c=company, a=before, acc=1 if accounts else None, accs=tuple(accounts or ('',)), p=party, br=branch) or 0)


def _book(company, a, accounts, title):
    op = opening(company, a['from'], accounts, a.get('party'), a.get('branch'))
    rows, drill, bal = [[str(a['from']), '', 'Opening balance', '', None, None, round(op, 2)]], [None], op
    for r in ledger_rows(company, a['from'], a['to'], accounts, a.get('party'), a.get('branch')):
        bal += float(r['debit']) - float(r['credit'])
        rows.append([str(r['txn_date']), r['ref'] or r['no'], r['type'], f"{r['narration']} · {r['account']}", float(r['debit']) or None, float(r['credit']) or None, round(bal, 2)])
        drill.append({'voucher': r['ref'] or r['no'], 'journal': r['no']})
    return out(title, [C('Date', 'date'), C('Voucher'), C('Type'), C('Particulars'), C('Debit', 'num'), C('Credit', 'num'), C('Balance', 'num')], rows, drill)


def open_items(company, kind, asof):
    """Open documents for outstanding / ageing: sales invoices and sales vouchers (receivable), supplier bills and purchase vouchers (payable)."""
    items = []
    if kind == 'cust':
        for r in db.q('''SELECT i.no, i.inv_date d, i.customer_id p, c.name, i.total, i.balance FROM invoices i LEFT JOIN customers c ON c.id=i.customer_id
                         WHERE i.company=:c AND i.balance>0.004 AND i.inv_date<=:t''', c=company, t=asof):
            items.append({'no': r['no'], 'date': r['d'], 'party': r['p'] or '', 'name': r['name'] or r['p'] or 'Walk-in', 'amount': float(r['total']), 'due': float(r['balance'])})
        types = ('SI',)
    else:
        for r in db.q('''SELECT b.bill_no no, b.bill_date d, b.due_date dd, b.supplier_id p, s.name, b.amount, b.amount-b.paid-b.adjusted bal FROM supplier_bills b
                         LEFT JOIN suppliers s ON s.id=b.supplier_id WHERE b.company=:c AND b.amount-b.paid-b.adjusted>0.004 AND b.bill_date<=:t''', c=company, t=asof):
            items.append({'no': r['no'], 'date': r['d'], 'due_date': r['dd'], 'party': r['p'], 'name': r['name'] or r['p'], 'amount': float(r['amount']), 'due': float(r['bal'])})
        types = ('PI', 'EX')
    for r in db.q("""SELECT no, ref, vdate, party_id, party_name, total, tds_amount, paid FROM fin_vouchers WHERE company=:c AND vtype IN :t AND status='Posted'
                     AND vdate<=:d""", c=company, t=types, d=asof):
        bal = float(r['total']) - float(r['tds_amount'] or 0) - float(r['paid'] or 0)
        if bal > 0.004 and r['party_id']:
            items.append({'no': r['ref'] or r['no'], 'date': r['vdate'], 'party': r['party_id'], 'name': r['party_name'], 'amount': float(r['total']), 'due': round(bal, 2)})
    for x in items:
        x['days'] = (asof - x['date']).days
    return items


# ---------- financial ----------
@R('tb', 'Financial', 'Trial Balance')
def _tb(co, a):
    m, n = movements(co, a['from'], a['to'], **_filters(a)), account_names(co)
    ob = {r['account']: float(r['v']) for r in db.q('''SELECT l.account, SUM(l.debit-l.credit) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                                                    WHERE j.company=:c AND j.txn_date<:a GROUP BY l.account''', c=co, a=a['from'])}
    rows, drill = [], []
    for acc in sorted(set(m) | set(ob)):
        o, (dr, cr) = ob.get(acc, 0), m.get(acc, (0, 0))
        cl = round(o + dr - cr, 2)
        if abs(o) < 0.005 and not dr and not cr:
            continue
        head, sub = group(acc)
        rows.append([acc, n.get(acc, acc), head, sub, round(o, 2), round(dr, 2), round(cr, 2), cl if cl > 0 else None, -cl if cl < 0 else None])
        drill.append({'report': 'gl', 'args': {'account': acc}})
    tot = [round(sum((r[i] or 0) for r in rows), 2) for i in range(4, 9)]
    return out('Trial Balance', [C('Code'), C('Account'), C('Group'), C('Sub-group'), C('Opening', 'num'), C('Debit', 'num'), C('Credit', 'num'), C('Closing Dr', 'num'),
                                 C('Closing Cr', 'num')], rows, drill, totals=['', 'Total', '', '', *tot], balanced=abs(tot[3] - tot[4]) < 0.05)


@R('day', 'Financial', 'Day Book')
def _day(co, a):
    rows, drill = [], []
    for j in db.q('''SELECT j.txn_date, j.no, j.ref, j.type, j.narration, SUM(l.debit) amt FROM journals j JOIN journal_lines l ON l.journal_id=j.id
                     WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b GROUP BY j.id ORDER BY j.txn_date, j.id''', c=co, a=a['from'], b=a['to']):
        rows.append([str(j['txn_date']), j['ref'] or j['no'], j['type'], j['narration'], float(j['amt'])])
        drill.append({'voucher': j['ref'] or j['no'], 'journal': j['no']})
    return out('Day Book', [C('Date', 'date'), C('Voucher'), C('Type'), C('Narration'), C('Amount', 'num')], rows, drill)


@R('cash', 'Financial', 'Cash Book')
def _cash(co, a):
    return _book(co, a, ['1100', '1105'], 'Cash Book')


@R('bank', 'Financial', 'Bank Book')
def _bank(co, a):
    accts = [a['account']] if a.get('account') else [r['code'] for r in db.q('SELECT code FROM accounts WHERE company=:c', c=co) if is_bank(r['code'])]
    return _book(co, a, accts, 'Bank Book')


@R('gl', 'Financial', 'General Ledger')
def _gl(co, a):
    acc = a.get('account') or '1100'
    r = _book(co, a, [acc], f"General Ledger · {acc} {account_names(co).get(acc, '')}")
    r['args'] = {'account': acc}
    return r


@R('party', 'Financial', 'Party Ledger')
def _party(co, a):
    if not a.get('party'):
        rows = db.q('''SELECT l.party, SUM(l.debit) dr, SUM(l.credit) cr FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                       WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b AND l.party IS NOT NULL GROUP BY l.party ORDER BY l.party''', c=co, a=a['from'], b=a['to'])
        names = _party_names(co)
        return out('Party Ledger · all parties', [C('Party'), C('Name'), C('Debit', 'num'), C('Credit', 'num'), C('Net', 'num')],
                   [[r['party'], names.get(r['party'], r['party']), float(r['dr']), float(r['cr']), round(float(r['dr'] - r['cr']), 2)] for r in rows],
                   [{'report': 'party', 'args': {'party': r['party']}} for r in rows])
    return _book(co, a, None, f"Party Ledger · {_party_names(co).get(a['party'], a['party'])}")


def _party_names(co):
    m = {r['id']: r['name'] for r in db.q('SELECT id, name FROM customers WHERE company=:c', c=co)}
    m.update({r['id']: r['name'] for r in db.q('SELECT id, name FROM suppliers WHERE company=:c', c=co)})
    m.update({r['id']: r['name'] for r in db.q('SELECT id, name FROM employees WHERE company=:c', c=co)})
    return m


def _outstanding(co, a, kind, title):
    rows = sorted(open_items(co, kind, a['to']), key=lambda x: -x['days'])
    return out(title, [C('Document'), C('Date', 'date'), C('Party'), C('Amount', 'num'), C('Outstanding', 'num'), C('Days', 'qty')],
               [[x['no'], str(x['date']), x['name'], x['amount'], x['due'], x['days']] for x in rows], [{'voucher': x['no']} for x in rows],
               totals=['', '', 'Total', round(sum(x['amount'] for x in rows), 2), round(sum(x['due'] for x in rows), 2), ''])


@R('custout', 'Financial', 'Customer Outstanding')
def _custout(co, a):
    return _outstanding(co, a, 'cust', 'Customer Outstanding')


@R('vendout', 'Financial', 'Vendor Outstanding')
def _vendout(co, a):
    return _outstanding(co, a, 'sup', 'Vendor Outstanding')


@R('ageing', 'Financial', 'Ageing Analysis')
def _ageing(co, a):
    kind = a.get('kind') or 'cust'
    by = defaultdict(lambda: [0.0] * len(BUCKETS))
    names = {}
    for x in open_items(co, kind, a['to']):
        i = next(k for k, (_, lo, hi) in enumerate(BUCKETS) if lo <= max(0, x['days']) <= hi)
        by[x['party']][i] += x['due']
        names[x['party']] = x['name']
    rows = [[names[p], *[round(v, 2) for v in b], round(sum(b), 2)] for p, b in sorted(by.items(), key=lambda t: -sum(t[1]))]
    return out(f"Ageing Analysis · {'receivables' if kind == 'cust' else 'payables'}", [C('Party'), *[C(b[0], 'num') for b in BUCKETS], C('Total', 'num')], rows,
               [{'report': 'party', 'args': {'party': p}} for p, _ in sorted(by.items(), key=lambda t: -sum(t[1]))],
               totals=['Total', *[round(sum(r[i] for r in rows), 2) for i in range(1, len(BUCKETS) + 2)]])


@R('rps', 'Financial', 'Receivables and Payables Summary')
def _rps(co, a):
    rows = []
    for kind, label in (('cust', 'Receivables'), ('sup', 'Payables')):
        xs = open_items(co, kind, a['to'])
        b = [round(sum(x['due'] for x in xs if lo <= max(0, x['days']) <= hi), 2) for _, lo, hi in BUCKETS]
        rows.append([label, len({x['party'] for x in xs}), len(xs), *b, round(sum(b), 2)])
    return out('Receivables and Payables Summary', [C('Side'), C('Parties', 'qty'), C('Documents', 'qty'), *[C(b[0], 'num') for b in BUCKETS], C('Total', 'num')], rows,
               [{'report': 'ageing', 'args': {'kind': 'cust'}}, {'report': 'ageing', 'args': {'kind': 'sup'}}])


@R('pl', 'Financial', 'Profit and Loss Statement')
def _pl(co, a):
    p = pl(co, a['from'], a['to'], **_filters(a))
    rows, drill = [['INCOME', '', None]], [None]
    for acc, nm, v in p['income']:
        rows.append([acc, nm, round(v, 2)])
        drill.append({'report': 'gl', 'args': {'account': acc}})
    rows.append(['', 'Total income', p['total_income']])
    rows.append(['EXPENSES', '', None])
    drill += [None, None]
    for acc, nm, v in p['expenses']:
        rows.append([acc, nm, round(v, 2)])
        drill.append({'report': 'gl', 'args': {'account': acc}})
    rows += [['', 'Total expenses', p['total_expenses']], ['', 'Gross profit', p['gross']], ['', 'NET PROFIT / (LOSS)', p['net']]]
    drill += [None, None, None]
    return out('Profit and Loss Statement', [C('Code'), C('Particulars'), C('Amount', 'num')], rows, drill, net=p['net'])


@R('bs', 'Financial', 'Balance Sheet')
def _bs(co, a):
    b = bs(co, a['to'], **_filters(a))
    rows, drill = [], []
    for title, items in (('CAPITAL & LIABILITIES', None), ('Capital', b['capital']), ('Current liabilities', b['liabilities'])):
        rows.append(['', title, None])
        drill.append(None)
        for acc, nm, v in items or []:
            rows.append([acc, nm, round(v, 2)])
            drill.append({'report': 'gl', 'args': {'account': acc}})
    rows += [['', 'Profit brought forward', b['retained']], ['', 'Profit for the year', b['profit']], ['', 'Total capital & liabilities', b['total_liabilities']], ['', 'ASSETS', None]]
    drill += [None] * 4
    for acc, nm, v in b['assets']:
        rows.append([acc, nm, round(v, 2)])
        drill.append({'report': 'gl', 'args': {'account': acc}})
    rows.append(['', 'Total assets', b['total_assets']])
    drill.append(None)
    return out(f"Balance Sheet as at {a['to']:%d-%m-%Y}", [C('Code'), C('Particulars'), C('Amount', 'num')], rows, drill, balanced=abs(b['diff']) < 0.05, diff=b['diff'])


@R('cf', 'Financial', 'Cash Flow Statement')
def _cf(co, a):
    """Indirect method: net profit, non-cash items, working-capital changes, investing (fixed assets) and financing (capital)."""
    p = pl(co, a['from'], a['to'])['net']
    mv = balances(co, a['from'], a['to'])
    dep = mv.get('5200', 0) + mv.get('5210', 0)
    wc = [(nm, -sum(v for acc, v in mv.items() if lo <= acc <= hi)) for nm, lo, hi in
          (('Trade receivables', '1200', '1299'), ('Inventory', '1300', '1309'), ('GST & tax assets', '1310', '1699'), ('Advances & deposits', '1700', '1899'),
           ('Trade payables', '2000', '2099'), ('Duties, payroll & provisions', '2100', '2399'))]
    op = p + dep + sum(x for _, x in wc)
    inv = -sum(v for acc, v in mv.items() if '1500' <= acc <= '1589')
    fin = -sum(v for acc, v in mv.items() if acc.startswith('3') or '2400' <= acc <= '2499' or '1400' <= acc <= '1499')
    cashacc = [acc for acc in mv if is_cash(acc) or is_bank(acc) or acc in ('1110', '1115')]
    opening_cash = sum(opening(co, a['from'], [acc]) for acc in cashacc) if cashacc else 0
    net = round(op + inv + fin, 2)
    rows = [['A. Operating activities', None], ['Net profit', round(p, 2)], ['Add: depreciation & impairment', round(dep, 2)],
            *[[f'Change in {nm}', round(x, 2)] for nm, x in wc], ['Net cash from operating activities', round(op, 2)],
            ['B. Investing activities: purchase / sale of fixed assets', round(inv, 2)], ['C. Financing activities: capital & inter-company', round(fin, 2)],
            ['Net increase / (decrease) in cash and bank', net], ['Opening cash and bank', round(opening_cash, 2)], ['Closing cash and bank', round(opening_cash + net, 2)]]
    return out('Cash Flow Statement (indirect method)', [C('Particulars'), C('Amount', 'num')], rows)


@R('ff', 'Financial', 'Fund Flow Statement')
def _ff(co, a):
    b0, b1 = bs(co, a['from'] - dt.timedelta(days=1)), bs(co, a['to'])
    wc = lambda b: sum(v for acc, _, v in b['assets'] if acc < '1500' or acc >= '1600') - sum(v for _, _, v in b['liabilities'])  # noqa: E731
    fa = lambda b: sum(v for acc, _, v in b['assets'] if '1500' <= acc <= '1599')  # noqa: E731
    p = pl(co, a['from'], a['to'])
    dep = balances(co, a['from'], a['to']).get('5200', 0)
    rows = [['Sources: funds from operations (profit + depreciation)', round(p['net'] + dep, 2)], ['Sources: capital introduced', round(sum(v for _, _, v in b1['capital']) - sum(v for _, _, v in b0['capital']), 2)],
            ['Applications: fixed assets acquired (net)', round(fa(b1) - fa(b0) + dep, 2)], ['Working capital at start', round(wc(b0), 2)],
            ['Working capital at end', round(wc(b1), 2)], ['Increase / (decrease) in working capital', round(wc(b1) - wc(b0), 2)]]
    return out('Fund Flow Statement', [C('Particulars'), C('Amount', 'num')], rows)


def _analysis(co, a, kind, title):
    k, n = _kinds(co), account_names(co)
    rows = db.q('''SELECT l.account, l.branch_id, SUM(l.debit-l.credit) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                   WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b GROUP BY l.account, l.branch_id''', c=co, a=a['from'], b=a['to'])
    by = defaultdict(lambda: defaultdict(float))
    for r in rows:
        if k.get(r['account']) == kind:
            by[r['account']][r['branch_id']] += float(r['v']) * (1 if kind == 'X' else -1)
    brs = sorted({b for x in by.values() for b in x})
    tot = sum(sum(x.values()) for x in by.values()) or 1
    data = sorted(by.items(), key=lambda t: -sum(t[1].values()))
    return out(title, [C('Code'), C('Account'), *[C(b, 'num') for b in brs], C('Total', 'num'), C('Share', 'pct')],
               [[acc, n.get(acc, acc), *[round(x.get(b, 0), 2) for b in brs], round(sum(x.values()), 2), round(sum(x.values()) / tot * 100, 1)] for acc, x in data],
               [{'report': 'gl', 'args': {'account': acc}} for acc, _ in data])


@R('expana', 'Financial', 'Expense Analysis')
def _expana(co, a):
    return _analysis(co, a, 'X', 'Expense Analysis')


@R('incana', 'Financial', 'Income Analysis')
def _incana(co, a):
    return _analysis(co, a, 'I', 'Income Analysis')


def _dim_pl(co, a, col, title, names=None):
    k = _kinds(co)
    rows = db.q(f'''SELECT COALESCE(l.{col}, '(unallocated)') d, l.account, SUM(l.debit-l.credit) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                    WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b GROUP BY d, l.account''', c=co, a=a['from'], b=a['to'])
    by = defaultdict(lambda: [0.0, 0.0, 0.0])
    for r in rows:
        v = float(r['v'])
        if k.get(r['account']) == 'I':
            by[r['d']][0] -= v
        elif k.get(r['account']) == 'X':
            by[r['d']][1 if r['account'] < '5200' else 2] += v
    data = sorted(by.items())
    res = [[d, (names or {}).get(d, d), round(i, 2), round(c, 2), round(i - c, 2), round(o, 2), round(i - c - o, 2), round((i - c - o) / i * 100, 1) if i else None]
           for d, (i, c, o) in data]
    return out(title, [C('Code'), C('Name'), C('Income', 'num'), C('Cost of sales', 'num'), C('Gross profit', 'num'), C('Other expenses', 'num'), C('Net profit', 'num'),
                       C('Margin', 'pct')], res, totals=['', 'Total', *[round(sum(r[i] for r in res), 2) for i in range(2, 7)], None])


@R('bpl', 'Financial', 'Branch-wise P&L')
def _bpl(co, a):
    return _dim_pl(co, a, 'branch_id', 'Branch-wise P&L', {r['id']: r['name'] for r in db.q('SELECT b.id, l.name FROM branches b JOIN locations l ON l.code=b.location')})


@R('dpl', 'Financial', 'Department-wise P&L')
def _dpl(co, a):
    return _dim_pl(co, a, 'dept', 'Department-wise P&L', {r['id']: r['name'] for r in db.q('SELECT id, name FROM cost_centres')})


@R('cpl', 'Financial', 'Cost Centre-wise P&L')
def _cpl(co, a):
    return _dim_pl(co, a, 'cost_centre', 'Cost Centre-wise P&L', {r['id']: r['name'] for r in db.q('SELECT id, name FROM cost_centres')})


@R('bva', 'Financial', 'Budget vs Actual')
def _bva(co, a):
    fy = fy_of(co, a['to'])
    bud = {r['account']: float(r['amount']) for r in db.q('SELECT account, amount FROM budgets WHERE company=:c AND fy=:f', c=co, f=fy['code'])}
    mv, n, k = balances(co, fy['from_date'], a['to']), account_names(co), _kinds(co)
    months = max(1, (a['to'].year - fy['from_date'].year) * 12 + a['to'].month - fy['from_date'].month + 1)
    rows = []
    for acc in sorted(set(bud) | {x for x in mv if k.get(x) in ('I', 'X')}):
        act = abs(mv.get(acc, 0))
        b = bud.get(acc, 0)
        pro = round(b * months / 12, 2)
        rows.append([acc, n.get(acc, acc), 'Income' if k.get(acc) == 'I' else 'Expense', b, pro, round(act, 2), round(act - pro, 2), round((act - pro) / pro * 100, 1) if pro else None])
    return out(f"Budget vs Actual · FY {fy['code']} ({months} months)", [C('Code'), C('Account'), C('Type'), C('Annual budget', 'num'), C('Budget to date', 'num'),
                                                                        C('Actual', 'num'), C('Variance', 'num'), C('Variance %', 'pct')], rows,
               [{'report': 'gl', 'args': {'account': r[0]}} for r in rows])


def _periods(frm, to, by):
    cols, d = [], frm.replace(day=1)
    if by == 'q':
        while d <= to:
            q = fin_tds.quarter_of(d)
            fy = fy_of('KG', d)
            qa, qb = fin_tds.q_range(fy['from_date'], q)
            cols.append((f"{q} {fy['code']}", max(qa, frm), min(qb, to)))
            d = qb + dt.timedelta(days=1)
        return cols
    if by == 'y':
        while d <= to:
            fy = fy_of('KG', d)
            cols.append((f"FY {fy['code']}", max(fy['from_date'], frm), min(fy['to_date'], to)))
            d = fy['to_date'] + dt.timedelta(days=1)
        return cols
    while d <= to:
        cols.append((f'{d:%b %Y}', max(d, frm), min(month_end(d), to)))
        d = month_end(d) + dt.timedelta(days=1)
    return cols


def _matrix(co, a, by, title):
    k, n = _kinds(co), account_names(co)
    cols = _periods(a['from'], a['to'], by)
    data = [balances(co, x, y) for _, x, y in cols]
    accs = sorted({acc for m in data for acc in m if k.get(acc) in ('I', 'X')})
    rows = [[acc, n.get(acc, acc), *[round(-m.get(acc, 0) if k[acc] == 'I' else m.get(acc, 0), 2) for m in data]] for acc in accs]
    net = [round(sum(-m.get(acc, 0) for acc in accs if k[acc] == 'I') - sum(m.get(acc, 0) for acc in accs if k[acc] == 'X'), 2) for m in data]
    rows.append(['', 'NET PROFIT / (LOSS)', *net])
    return out(title, [C('Code'), C('Account'), *[C(c[0], 'num') for c in cols]], rows, [{'report': 'gl', 'args': {'account': r[0]}} if r[0] else None for r in rows])


@R('mstmt', 'Financial', 'Monthly Financial Statements')
def _mstmt(co, a):
    return _matrix(co, a, 'm', 'Monthly Financial Statements')


@R('qstmt', 'Financial', 'Quarterly Financial Statements')
def _qstmt(co, a):
    return _matrix(co, a, 'q', 'Quarterly Financial Statements')


@R('astmt', 'Financial', 'Annual Financial Statements')
def _astmt(co, a):
    return _matrix(co, {**a, 'from': min(a['from'], fy_of(co, a['to'])['from_date'].replace(year=fy_of(co, a['to'])['from_date'].year - 1))}, 'y', 'Annual Financial Statements')


@R('cmp', 'Financial', 'Comparative Financial Statements')
def _cmp(co, a):
    days = (a['to'] - a['from']).days
    pa, pb = a['from'] - dt.timedelta(days=days + 1), a['from'] - dt.timedelta(days=1)
    cur, prev, k, n = balances(co, a['from'], a['to']), balances(co, pa, pb), _kinds(co), account_names(co)
    rows = []
    for acc in sorted({x for x in set(cur) | set(prev) if k.get(x) in ('I', 'X')}):
        s = -1 if k[acc] == 'I' else 1
        c, p = round(s * cur.get(acc, 0), 2), round(s * prev.get(acc, 0), 2)
        rows.append([acc, n.get(acc, acc), p, c, round(c - p, 2), round((c - p) / abs(p) * 100, 1) if p else None])
    return out(f'Comparative statement: {pa:%d-%m-%Y}–{pb:%d-%m-%Y} vs this period', [C('Code'), C('Account'), C('Previous', 'num'), C('Current', 'num'), C('Change', 'num'),
                                                                                    C('Change %', 'pct')], rows)


@R('ratio', 'Financial', 'Ratio Analysis')
def _ratio(co, a):
    b, p = bs(co, a['to']), pl(co, a['from'], a['to'])
    ca = sum(v for acc, _, v in b['assets'] if acc < '1500' or acc >= '1600')
    cl = sum(v for _, _, v in b['liabilities']) or 1
    inv = sum(v for acc, _, v in b['assets'] if '1300' <= acc <= '1309')
    eq = sum(v for _, _, v in b['capital']) + b['profit'] + b['retained'] or 1
    rec = sum(v for acc, _, v in b['assets'] if '1200' <= acc <= '1299')
    sales = sum(v for acc, _, v in p['income'] if acc < '4100') or 1
    days = max(1, (a['to'] - a['from']).days + 1)
    rows = [['Current ratio', round(ca / cl, 2), 'Current assets ÷ current liabilities'], ['Quick ratio', round((ca - inv) / cl, 2), '(Current assets − stock) ÷ current liabilities'],
            ['Gross margin %', round(p['gross'] / sales * 100, 1), 'Gross profit ÷ sales'], ['Net margin %', round(p['net'] / sales * 100, 1), 'Net profit ÷ sales'],
            ['Return on equity %', round(p['net'] / eq * 100, 1), 'Net profit ÷ capital and reserves'], ['Debtor days', round(rec / sales * days, 0), 'Receivables ÷ sales × days'],
            ['Stock days', round(inv / ((sum(v for acc, _, v in p['expenses'] if acc < '5200') or 1)) * days, 0), 'Stock ÷ cost of sales × days'],
            ['Debt-equity', round(cl / eq, 2), 'Liabilities ÷ capital and reserves']]
    return out('Ratio Analysis', [C('Ratio'), C('Value', 'num'), C('How it is worked out')], rows)


@R('scrut', 'Financial', 'Audit Schedule and Ledger Scrutiny')
def _scrut(co, a):
    """Entries an auditor would query: round-sum journals, weekend / holiday posting, manual journals to cash, large expenses, back-dated entries."""
    rows = []
    for j in db.q('''SELECT j.no, j.ref, j.type, j.txn_date, j.created_at, j.narration, SUM(l.debit) amt, MAX(l.account IN ('1100','1105')) cash
                     FROM journals j JOIN journal_lines l ON l.journal_id=j.id WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b GROUP BY j.id''', c=co, a=a['from'], b=a['to']):
        why = []
        amt = float(j['amt'])
        if amt >= 10000 and amt % 10000 == 0:
            why.append('Round amount')
        if j['txn_date'].weekday() == 6:
            why.append('Posted on a Sunday')
        if j['type'] in ('Journal', 'Journal Voucher') and j['cash']:
            why.append('Manual journal touching cash')
        if j['created_at'] and (j['created_at'].date() - j['txn_date']).days > 7:
            why.append(f"Back-dated {(j['created_at'].date() - j['txn_date']).days} days")
        if why:
            rows.append([str(j['txn_date']), j['ref'] or j['no'], j['type'], j['narration'], amt, '; '.join(why)])
    return out('Audit Schedule and Ledger Scrutiny', [C('Date', 'date'), C('Voucher'), C('Type'), C('Narration'), C('Amount', 'num'), C('Why it is flagged')], rows,
               [{'voucher': r[1]} for r in rows])


@R('rpt', 'Financial', 'Related Party Transaction Report')
def _rpt(co, a):
    rel = [r['party'] for r in db.q('SELECT party FROM party_tax WHERE company=:c AND related=1', c=co)]
    names = _party_names(co)
    rows = []
    for r in db.q('''SELECT j.txn_date, j.ref, j.no, j.type, l.party, l.debit, l.credit FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                     WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b AND (l.party IN :p OR l.account BETWEEN '1400' AND '1499' OR l.account BETWEEN '2400' AND '2499')''',
                  c=co, a=a['from'], b=a['to'], p=tuple(rel) or ('',)):
        rows.append([str(r['txn_date']), r['ref'] or r['no'], r['type'], names.get(r['party'], r['party'] or 'Inter-company'), float(r['debit']) or None, float(r['credit']) or None])
    return out('Related Party Transaction Report', [C('Date', 'date'), C('Voucher'), C('Type'), C('Related party'), C('Debit', 'num'), C('Credit', 'num')], rows)


@R('ibr', 'Financial', 'Inter-branch Reconciliation Report')
def _ibr(co, a):
    rows = db.q('''SELECT l.branch_id, SUM(l.debit) dr, SUM(l.credit) cr FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                   WHERE j.company=:c AND j.txn_date<=:b AND l.account='1410' GROUP BY l.branch_id''', c=co, b=a['to'])
    res = [[r['branch_id'], float(r['dr']), float(r['cr']), round(float(r['dr'] - r['cr']), 2)] for r in rows]
    net = round(sum(r[3] for r in res), 2)
    return out('Inter-branch Reconciliation Report', [C('Branch'), C('Debit', 'num'), C('Credit', 'num'), C('Balance', 'num')], res,
               totals=['Net (should be nil)', None, None, net], note='Balanced' if abs(net) < 0.05 else f'Out of balance by ₹{net:,.2f}')


@R('icr', 'Financial', 'Inter-company Reconciliation Report')
def _icr(co, a):
    rows = []
    for c in [r['code'] for r in db.q('SELECT code FROM companies WHERE active=1')]:
        rec = opening(c, a['to'] + dt.timedelta(days=1), ['1400'])
        pay = -opening(c, a['to'] + dt.timedelta(days=1), ['2400'])
        rows.append([c, round(rec, 2), round(pay, 2)])
    diff = round(sum(r[1] for r in rows) - sum(r[2] for r in rows), 2)
    return out('Inter-company Reconciliation Report', [C('Company'), C('Due from group (1400)', 'num'), C('Due to group (2400)', 'num')], rows,
               note='Balanced across the group' if abs(diff) < 0.05 else f'Difference ₹{diff:,.2f}: an entry is missing on one side')


# ---------- GST ----------
def _ym(a):
    return a.get('ym') or f"{a['to']:%Y-%m}"


@R('g1', 'GST', 'GSTR-1 (outward supplies)')
def _g1(co, a):
    G = fin_gst.gstr1(co, _ym(a))
    rows = [['B2B invoices', len(G['b2b']), round(sum(d['taxable'] for d in G['b2b']), 2), round(sum(d['tax'] for d in G['b2b']), 2)],
            ['B2C large (inter-state > limit)', len(G['b2cl']), round(sum(d['taxable'] for d in G['b2cl']), 2), round(sum(d['tax'] for d in G['b2cl']), 2)],
            ['B2C small (rate / state summary)', len(G['b2cs']), round(sum(x['taxable'] for x in G['b2cs']), 2), round(sum(x['igst'] + x['cgst'] + x['sgst'] for x in G['b2cs']), 2)],
            ['Credit / debit notes (registered)', len(G['cdnr']), round(sum(d['taxable'] for d in G['cdnr']), 2), round(sum(d['tax'] for d in G['cdnr']), 2)],
            ['Credit notes (unregistered, large)', len(G['cdnur']), round(sum(d['taxable'] for d in G['cdnur']), 2), round(sum(d['tax'] for d in G['cdnur']), 2)],
            ['Nil-rated supplies', None, round(sum(G['nil'].values()), 2), 0],
            ['HSN summary lines', len(G['hsn_b2b']) + len(G['hsn_b2c']), None, None], ['Document series', len(G['docs']), None, None]]
    return out(f"GSTR-1 · {G['fp']} · {G['gstin']}", [C('Table'), C('Count', 'qty'), C('Taxable value', 'num'), C('Tax', 'num')], rows, filed=G['filed'])


@R('g3b', 'GST', 'GSTR-3B (summary return)')
def _g3b(co, a):
    G = fin_gst.gstr3b(co, _ym(a))
    r = lambda label, o: [label, o['taxable'], o['igst'], o['cgst'], o['sgst'], o['cess']]  # noqa: E731
    s = G['setoff']
    rows = [r('3.1(a) Outward taxable supplies', G['t31a']), r('3.1(c) Nil-rated / exempt', G['t31c']), r('3.1(d) Inward supplies on reverse charge', G['t31d']),
            r('4(A)(3) ITC: inward supplies on reverse charge', G['itc_rcm']), r('4(A)(5) ITC: all other', G['itc_other']), r('4(D) Ineligible ITC (s.17(5))', G['itc_blocked']),
            r('5 Exempt / nil inward', G['t5']), ['6.1 Paid in cash (after set-off)', None, s['cash']['igst'], s['cash']['cgst'], s['cash']['sgst'], s['cash']['cess']],
            ['   Reverse-charge tax paid in cash', None, None, None, None, s['cash']['rcm']], ['   Interest / late fee', None, None, s['interest'], s['fee'], None]]
    return out(f"GSTR-3B · {fin_gst.fp(_ym(a))}", [C('Table'), C('Taxable', 'num'), C('IGST', 'num'), C('CGST', 'num'), C('SGST / UTGST', 'num'), C('Cess', 'num')], rows,
               due=s['due'], filed=s['filed'], paid=s['paid'])


def _recon(co, a, kind):
    R_ = fin_gst.reconcile(co, kind, _ym(a))
    rows = []
    for x in R_['rows']:
        b, p = x['books'] or {}, x['portal'] or {}
        rows.append([x['status'], b.get('gstin') or p.get('gstin'), b.get('name') or p.get('name'), b.get('no') or p.get('inv_no'), b.get('date') or p.get('inv_date'),
                     b.get('tax'), round(p['igst'] + p['cgst'] + p['sgst'] + p['cess'], 2) if p else None, x['diff']])
    return out(f'GSTR-{kind} Reconciliation · {_ym(a)}', [C('Status'), C('GSTIN'), C('Supplier'), C('Invoice'), C('Date', 'date'), C('Tax in books', 'num'),
                                                          C(f'Tax in {kind}', 'num'), C('Difference', 'num')], rows,
               note=f"{R_['mismatches']} mismatch(es) · ITC at risk ₹{R_['itc_at_risk']:,.0f}" if R_['loaded'] else f'GSTR-{kind} not loaded yet: import the portal file.')


@R('g2b', 'GST', 'GSTR-2B Reconciliation Report')
def _g2b(co, a):
    return _recon(co, a, '2B')


@R('g2a', 'GST', 'GSTR-2A Reconciliation Report')
def _g2a(co, a):
    return _recon(co, a, '2A')


@R('g9', 'GST', 'GSTR-9 Annual Return working')
def _g9(co, a):
    G = fin_gst.gstr9(co, a.get('fy') or fy_of(co, a['to'])['code'])
    rows = [[r['ym'], r['out']['taxable'], r['out']['igst'], r['out']['cgst'], r['out']['sgst'], r['nil'], r['itc']['igst'] + r['itc']['cgst'] + r['itc']['sgst'], r['cash'],
             'Filed' if r['filed'] else 'Not filed'] for r in G['rows']]
    return out(f"GSTR-9 working · FY {G['fy']}", [C('Month'), C('Taxable', 'num'), C('IGST', 'num'), C('CGST', 'num'), C('SGST', 'num'), C('Nil-rated', 'num'), C('ITC', 'num'),
                                                C('Paid in cash', 'num'), C('3B')], rows,
               totals=['Total', G['out']['taxable'], G['out']['igst'], G['out']['cgst'], G['out']['sgst'], G['nil'], round(sum(G['itc'].values()), 2), G['cash'], ''])


@R('g9c', 'GST', 'GSTR-9C Reconciliation working')
def _g9c(co, a):
    G = fin_gst.gstr9c(co, a.get('fy') or fy_of(co, a['to'])['code'])
    rows = [[x[0], x[1]] for x in G['items']] + [['Output tax in books (2100 + 2110 + 2120)', G['tax_books']], ['Input tax credit in books', G['itc_books']]]
    return out('GSTR-9C reconciliation working', [C('Particulars'), C('Amount', 'num')], rows)


def _docs(co, a, f):
    return [d for d in fin_gst.docs(co, a['from'], a['to']) if f(d)]


def _reg(co, a, f, title):
    ds = _docs(co, a, f)
    rows = [[d['date'], d['no'], d['type'], d['name'], d['gstin'], d['pos'], d['sign'] * d['taxable'], d['sign'] * d['igst'], d['sign'] * d['cgst'], d['sign'] * d['sgst'],
             d['sign'] * d['cess'], d['sign'] * d['val']] for d in ds]
    return out(title, [C('Date', 'date'), C('Document'), C('Type'), C('Party'), C('GSTIN'), C('POS'), C('Taxable', 'num'), C('IGST', 'num'), C('CGST', 'num'), C('SGST', 'num'),
                       C('Cess', 'num'), C('Value', 'num')], rows, [{'voucher': d.get('our_ref') or d['no']} for d in ds],
               totals=['', 'Total', '', '', '', '', *[round(sum(r[i] for r in rows), 2) for i in range(6, 12)]])


@R('gsales', 'GST', 'Sales Register with GST')
def _gsales(co, a):
    return _reg(co, a, lambda d: d['dir'] == 'out', 'Sales Register with GST')


@R('gpur', 'GST', 'Purchase Register with GST')
def _gpur(co, a):
    return _reg(co, a, lambda d: d['dir'] == 'in', 'Purchase Register with GST')


def _hsn(co, a, sac):
    m = defaultdict(lambda: [0.0] * 6)
    desc = {r['code']: r['description'] for r in db.q('SELECT code, description FROM hsn_codes')}
    for d in fin_gst.docs(co, a['from'], a['to']):
        if d['dir'] != 'out':
            continue
        for r in d['rows']:
            if str(r['hsn']).startswith('99') != sac:
                continue
            x = m[(r['hsn'] or 'NA', r['rate'])]
            for i, k in enumerate(('qty', 'taxable', 'igst', 'cgst', 'sgst', 'cess')):
                x[i] += d['sign'] * (r[k] or 0)
    rows = [[h, desc.get(h, ''), rate, *[round(v, 2) for v in x]] for (h, rate), x in sorted(m.items())]
    return out('SAC Summary' if sac else 'HSN Summary', [C('SAC' if sac else 'HSN'), C('Description'), C('Rate', 'pct'), C('Qty', 'qty'), C('Taxable', 'num'), C('IGST', 'num'),
                                                       C('CGST', 'num'), C('SGST', 'num'), C('Cess', 'num')], rows)


@R('ghsn', 'GST', 'HSN Summary')
def _ghsn(co, a):
    return _hsn(co, a, False)


@R('gsac', 'GST', 'SAC Summary')
def _gsac(co, a):
    return _hsn(co, a, True)


@R('gliab', 'GST', 'Tax Liability Register')
def _gliab(co, a):
    rows, m = [], f"{a['from']:%Y-%m}"
    while m <= f"{a['to']:%Y-%m}":
        s = fin_gst.setoff(co, m)
        L = s['liability']
        rows.append([m, L['igst'], L['cgst'], L['sgst'], L['cess'], s['books']['rcm'], round(sum(sum(x.values()) for x in s['use'].values()), 2), s['cash']['total'], s['interest'],
                     s['fee'], 'Paid' if s['paid'] else 'Due ' + s['due']])
        m = fin_gst.next_ym(m)
    return out('Tax Liability Register', [C('Month'), C('IGST', 'num'), C('CGST', 'num'), C('SGST', 'num'), C('Cess', 'num'), C('RCM', 'num'), C('Paid by credit', 'num'),
                                          C('Paid in cash', 'num'), C('Interest', 'num'), C('Late fee', 'num'), C('Status')], rows)


@R('gitc', 'GST', 'Input Tax Credit Register')
def _gitc(co, a):
    ds = _docs(co, a, lambda d: d['dir'] == 'in')
    rows = [[d['date'], d['no'], d['name'], d['gstin'], d['itc'], d['rcm'] and 'Yes' or '', d['sign'] * d['igst'], d['sign'] * d['cgst'], d['sign'] * d['sgst'], d['sign'] * d['cess'],
             d['sign'] * d['tax'] if d['itc'] == 'Eligible' else 0] for d in ds]
    return out('Input Tax Credit Register', [C('Date', 'date'), C('Bill'), C('Supplier'), C('GSTIN'), C('ITC'), C('RCM'), C('IGST', 'num'), C('CGST', 'num'), C('SGST', 'num'),
                                             C('Cess', 'num'), C('Credit taken', 'num')], rows, [{'voucher': d.get('our_ref') or d['no']} for d in ds])


@R('grcm', 'GST', 'RCM Register')
def _grcm(co, a):
    return _reg(co, a, lambda d: d['dir'] == 'in' and d['rcm'], 'RCM Register')


@R('geinv', 'GST', 'E-Invoice Register')
def _geinv(co, a):
    ds = _docs(co, a, lambda d: fin_gst.einvoice_applies(co, d))
    rows = [[d['date'], d['no'], d['type'], d['name'], d['gstin'], d['val'], (d['einvoice'] or {}).get('status') or 'Pending', ((d['einvoice'] or {}).get('irn') or '')[:20],
             (d['einvoice'] or {}).get('ack_no'), 'Generated locally' if (d['einvoice'] or {}).get('local') else ''] for d in ds]
    return out('E-Invoice Register', [C('Date', 'date'), C('Document'), C('Type'), C('Buyer'), C('GSTIN'), C('Value', 'num'), C('Status'), C('IRN'), C('Ack no'), C('Note')], rows)


@R('gewb', 'GST', 'E-Way Bill Register')
def _gewb(co, a):
    lim = 50000
    ds = _docs(co, a, lambda d: d['dir'] == 'out' and d['val'] >= lim)
    rows = []
    for d in ds:
        e = (d['einvoice'] or {}).get('ewb') or {}
        e = __import__('json').loads(e) if isinstance(e, str) else e
        rows.append([d['date'], d['no'], d['name'], d['val'], e.get('no') or 'Required', e.get('vehicle'), e.get('km'), e.get('valid')])
    return out('E-Way Bill Register', [C('Date', 'date'), C('Document'), C('Party'), C('Value', 'num'), C('E-way bill'), C('Vehicle'), C('Km', 'qty'), C('Valid to', 'date')], rows)


@R('gcdn', 'GST', 'GST Credit / Debit Note Register')
def _gcdn(co, a):
    return _reg(co, a, lambda d: d['type'] in ('CN', 'DN'), 'GST Credit / Debit Note Register')


@R('gpay', 'GST', 'GST Payment and Challan Register')
def _gpay(co, a):
    rows = []
    for c in db.q('SELECT * FROM gst_challans WHERE company=:c AND cdate BETWEEN :a AND :b ORDER BY cdate', c=co, a=a['from'], b=a['to']):
        cash = c['cash'] if isinstance(c['cash'], dict) else __import__('json').loads(c['cash'] or '{}')
        rows.append([str(c['cdate']), c['period'], c['cpin'], c['cin'], cash.get('igst'), cash.get('cgst'), cash.get('sgst'), cash.get('rcm'), float(c['interest']), float(c['late_fee']),
                     c['voucher_no'], c['status']])
    return out('GST Payment and Challan Register', [C('Date', 'date'), C('Period'), C('CPIN'), C('CIN'), C('IGST', 'num'), C('CGST', 'num'), C('SGST', 'num'), C('RCM', 'num'),
                                                    C('Interest', 'num'), C('Late fee', 'num'), C('Voucher'), C('Status')], rows)


@R('gmis', 'GST', 'GST Mismatch Report')
def _gmis(co, a):
    rows = []
    ym = _ym(a)
    for x in fin_gst.reconcile(co, '2B', ym)['rows']:
        if x['status'] != 'Matched':
            b, p = x['books'] or {}, x['portal'] or {}
            rows.append(['GSTR-2B', x['status'], b.get('name') or p.get('name'), b.get('no') or p.get('inv_no'), x['diff']])
    a_, b_ = fin_gst.period(ym)
    for d in fin_gst.docs(co, a_, b_):
        from .fin_common import gstin_check
        if d['gstin'] and not gstin_check(d['gstin'])['ok']:
            rows.append(['Invalid GSTIN', gstin_check(d['gstin'])['msg'], d['name'], d['no'], d['tax']])
        if d['dir'] == 'out' and any(not r['hsn'] for r in d['rows']) and d['gstin']:
            rows.append(['Missing HSN', 'B2B document without HSN', d['name'], d['no'], d['tax']])
    return out(f'GST Mismatch Report · {ym}', [C('Check'), C('Issue'), C('Party'), C('Document'), C('Tax at stake', 'num')], rows)


@R('gbws', 'GST', 'Branch-wise / State-wise GST Summary')
def _gbws(co, a):
    m = defaultdict(lambda: [0.0] * 5)
    for d in fin_gst.docs(co, a['from'], a['to']):
        if d['dir'] != 'out':
            continue
        x = m[(d['branch'] or '', d['pos'])]
        for i, k in enumerate(('taxable', 'igst', 'cgst', 'sgst', 'cess')):
            x[i] += d['sign'] * d[k]
    from .fin_common import GST_STATES
    rows = [[b, f'{s} {GST_STATES.get(s, "")}', *[round(v, 2) for v in x]] for (b, s), x in sorted(m.items())]
    return out('Branch-wise / State-wise GST Summary', [C('Branch'), C('Place of supply'), C('Taxable', 'num'), C('IGST', 'num'), C('CGST', 'num'), C('SGST', 'num'), C('Cess', 'num')], rows)


# ---------- TDS ----------
def _tds_rows(co, a, **f):
    return fin_tds.register(co, a['from'], a['to'], **f)


@R('tded', 'TDS', 'TDS Deduction Register')
def _tded(co, a):
    rs = _tds_rows(co, a)
    rows = [[r['ded_date'], r['voucher_no'], r['name'], r['pan'], r['section'], r['form'], r['base'], r['rate'], r['amount'], r['kind'], r['status']] for r in rs]
    return out('TDS Deduction Register', [C('Date', 'date'), C('Voucher'), C('Deductee'), C('PAN'), C('Section'), C('Form'), C('Base', 'num'), C('Rate', 'pct'), C('TDS', 'num'),
                                          C('Kind'), C('Status')], rows, [{'voucher': r['voucher_no']} for r in rs])


@R('tpayable', 'TDS', 'TDS Payable Register')
def _tpayable(co, a):
    rs = [r for r in _tds_rows(co, a) if r['kind'] == 'Payable' and r['status'] == 'Deducted']
    rows = [[r['ded_date'], r['voucher_no'], r['name'], r['section'], r['amount'], r['interest']['due'], r['interest']['total'], 'Overdue' if r['interest']['overdue'] else 'Due']
            for r in rs]
    return out('TDS Payable Register', [C('Date', 'date'), C('Voucher'), C('Deductee'), C('Section'), C('TDS', 'num'), C('Deposit by', 'date'), C('Interest so far', 'num'), C('Status')],
               rows, totals=['', '', 'Total', '', round(sum(r[4] for r in rows), 2), '', round(sum(r[6] for r in rows), 2), ''])


@R('tpay', 'TDS', 'TDS Payment Register')
def _tpay(co, a):
    rows = [[str(c['cdate']), c['bsr'], c['serial'], c['cin'], ', '.join(__import__('json').loads(c['sections']) if isinstance(c['sections'], str) else c['sections'] or []),
             float(c['amount']), float(c['interest']), c['voucher_no'], c['status']]
            for c in db.q('SELECT * FROM tds_challans WHERE company=:c AND cdate BETWEEN :a AND :b ORDER BY cdate', c=co, a=a['from'], b=a['to'])]
    return out('TDS Payment Register (Form 141 challans)', [C('Date', 'date'), C('BSR'), C('Serial'), C('CIN'), C('Sections'), C('Tax', 'num'), C('Interest', 'num'),
                                                            C('Voucher'), C('Status')], rows, [{'voucher': r[7]} for r in rows])


@R('tchrec', 'TDS', 'Challan Reconciliation Report')
def _tchrec(co, a):
    rows = []
    for c in db.q('SELECT * FROM tds_challans WHERE company=:c AND cdate BETWEEN :a AND :b', c=co, a=a['from'], b=a['to']):
        used = float(db.val('SELECT COALESCE(SUM(amount), 0) FROM tds_entries WHERE challan_id=:i', i=c['id']) or 0)
        rows.append([c['cin'], str(c['cdate']), float(c['amount']), used, round(float(c['amount']) - used, 2), 'Matched' if abs(float(c['amount']) - used) < 1 else 'Difference'])
    return out('Challan Reconciliation Report', [C('CIN'), C('Date', 'date'), C('Challan amount', 'num'), C('Linked deductions', 'num'), C('Difference', 'num'), C('Status')], rows)


def _group_tds(co, a, key, title, label):
    m = defaultdict(lambda: [0, 0.0, 0.0])
    names = {}
    for r in _tds_rows(co, a):
        k = r[key]
        names[k] = r['name'] if key == 'party' else r['section']
        m[k][0] += 1
        m[k][1] += r['base']
        m[k][2] += r['amount']
    rows = [[k, names[k], n, round(b, 2), round(t, 2)] for k, (n, b, t) in sorted(m.items())]
    return out(title, [C(label), C('Name'), C('Entries', 'qty'), C('Base', 'num'), C('TDS', 'num')], rows)


@R('tvendor', 'TDS', 'Vendor-wise TDS Report')
def _tvendor(co, a):
    return _group_tds(co, a, 'party', 'Vendor-wise TDS Report', 'Party')


@R('tsec', 'TDS', 'Section-wise TDS Report')
def _tsec(co, a):
    return _group_tds(co, a, 'section', 'Section-wise TDS Report', 'Section')


@R('tpan', 'TDS', 'PAN Validation / Missing PAN Report')
def _tpan(co, a):
    rs = fin_tds.pan_issues(co)
    return out('PAN Validation / Missing PAN Report', [C('Kind'), C('Code'), C('Name'), C('PAN'), C('Issue'), C('Section')],
               [[r['kind'], r['id'], r['name'], r['pan'], r['issue'], r['section']] for r in rs])


@R('tthr', 'TDS', 'Threshold Monitoring Report')
def _tthr(co, a):
    rs = fin_tds.thresholds(co)
    return out('Threshold Monitoring Report', [C('Supplier'), C('Section'), C('PAN'), C('Single limit', 'num'), C('Year limit', 'num'), C('Credited this year', 'num'),
                                               C('TDS deducted', 'num'), C('Used', 'pct'), C('Status')],
               [[r['name'], r['section'], r['pan'], r['single'], r['year'], r['credited'], r['deducted'], r['pct'], r['status']] for r in rs])


@R('tint', 'TDS', 'TDS Interest and Late Fee Report')
def _tint(co, a):
    rs = [r for r in _tds_rows(co, a) if r['interest'] and r['interest']['total']]
    return out('TDS Interest and Late Fee Report', [C('Date', 'date'), C('Voucher'), C('Deductee'), C('TDS', 'num'), C('Late deduction (1%)', 'num'), C('Late payment (1.5%)', 'num'),
                                                    C('Total', 'num')],
               [[r['ded_date'], r['voucher_no'], r['name'], r['amount'], r['interest']['late_deduction'], r['interest']['late_payment'], r['interest']['total']] for r in rs])


def _form(co, a, form):
    fy = a.get('fy') or fy_of(co, a['to'])['code']
    q = a.get('quarter') or fin_tds.quarter_of(a['to'])
    Q = fin_tds.quarter(co, fy, q, form)
    rows = [[e['ded_date'], e['name'], e['pan'], e['section'], e['base'], e['rate'], e['amount'], e['cin'] or '', e['challan_status'] or 'Not deposited'] for e in Q['entries']]
    return out(f"{Q['form_name']} working ({Q['form_label']}) · {q} {fy}", [C('Date', 'date'), C('Deductee'), C('PAN'), C('Section'), C('Amount paid / credited', 'num'),
                                                                           C('Rate', 'pct'), C('TDS', 'num'), C('Challan CIN'), C('Deposit')], rows,
               note=f"Due {Q['due']} · late fee ₹{Q['fee_234e']:,.0f} · {'filed, token ' + Q['filed']['token'] if Q['filed'] else 'not filed'}")


@R('t24q', 'TDS', 'Form 138 working (old 24Q, salary)')
def _t24q(co, a):
    return _form(co, a, '24Q')


@R('t26q', 'TDS', 'Form 140 working (old 26Q, resident non-salary)')
def _t26q(co, a):
    return _form(co, a, '26Q')


@R('t27q', 'TDS', 'Form 144 working (old 27Q, non-resident)')
def _t27q(co, a):
    return _form(co, a, '27Q')


@R('t27eq', 'TDS', 'Form 143 working (old 27EQ, TCS)')
def _t27eq(co, a):
    return _form(co, a, '27EQ')


@R('tf16', 'TDS', 'Form 130 working (old Form 16) · employee certificates')
def _tf16(co, a):
    fy = fy_of(co, a['to'])
    rows = []
    for e in db.q('SELECT * FROM employees WHERE company=:c ORDER BY id', c=co):
        r = db.one('''SELECT COALESCE(SUM(base), 0) b, COALESCE(SUM(amount), 0) t FROM tds_entries WHERE company=:c AND party=:p AND section='192' AND ded_date BETWEEN :a AND :z''',
                   c=co, p=e['id'], a=fy['from_date'], z=fy['to_date'])
        rows.append([e['id'], e['name'], e['pan'] or 'PAN missing', float(r['b']), float(r['t']), fin_tds.salary_tds(e['gross']) * 12])
    return out(f"Form 130 working · FY {fy['code']}", [C('Employee'), C('Name'), C('PAN'), C('Salary paid', 'num'), C('TDS deducted', 'num'), C('Estimated annual tax', 'num')], rows)


@R('tf16a', 'TDS', 'Form 131 data (old 16A) · vendor-wise summary')
def _tf16a(co, a):
    rows = []
    for c in db.q('SELECT * FROM tds_certificates WHERE company=:c ORDER BY fy, quarter', c=co):
        rows.append([c['fy'], c['quarter'], c['party'], _party_names(co).get(c['party'], c['party']), c['cert_no'], str(c['issued_on']), float(c['amount'])])
    return out('Form 131 data (certificates issued)', [C('FY'), C('Quarter'), C('Party'), C('Name'), C('Certificate'), C('Issued', 'date'), C('TDS', 'num')], rows)


@R('tstat', 'TDS', 'Quarterly Return Filing Status')
def _tstat(co, a):
    fy = fy_of(co, a['to'])
    rows = []
    for form in fin_tds.FORMS:
        for q in ('Q1', 'Q2', 'Q3', 'Q4'):
            Q = fin_tds.quarter(co, fy['code'], q, form) if db.one('SELECT 1 FROM fin_years WHERE company=:c AND code=:f', c=co, f=fy['code']) else None
            if not Q or (not Q['entries'] and not Q['filed']):
                continue
            rows.append([Q['form_name'], q, Q['due'], len(Q['entries']), Q['total'], Q['undeposited'], 'Filed ' + Q['filed']['filed_on'] if Q['filed'] else 'Not filed', Q['fee_234e']])
    return out(f"Quarterly Return Filing Status · FY {fy['code']}", [C('Form'), C('Quarter'), C('Due', 'date'), C('Entries', 'qty'), C('TDS', 'num'), C('Not deposited', 'qty'),
                                                                    C('Status'), C('Late fee', 'num')], rows)
