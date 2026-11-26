"""Operational report library (release 2.4): sales, stock, purchasing, cash, MSEA and online, controls, and the basic financial
statements. Every report returns the same table shape as the finance library — {title, columns: [{h, t}], rows, drill, totals?, note?}
— so the screen, Print, PDF, Excel and CSV render the same table. Column types: text, date, num (money), qty, pct.

Access: every report needs Reports → view; each one may need more (`need`: any of the listed (action, screen) pairs). Rows are limited
to the user's companies and locations; cost and margin columns appear only with Reports → cost; financial statements need Reports →
financial figures."""
import datetime as dt
import json
from collections import defaultdict
from decimal import Decimal

from flask import g

from .. import db
from ..core.rbac import can, scope_sql
from ..errors import bad, denied
from .common import parse_date

REPORTS = {}
SALE_TYPES = ('Sale', 'Online sale', 'Sale (delivery)')


def R(key, group, title, need=(), fin=False, period=True):
    def deco(fn):
        REPORTS[key] = {'key': key, 'group': group, 'title': title, 'need': need, 'fin': fin, 'period': period, 'fn': fn}
        return fn
    return deco


def C(h, t='text'):
    return {'h': h, 't': t}


def out(title, cols, rows, drill=None, **kw):
    return {'title': title, 'columns': cols, 'rows': rows, 'drill': drill or [None] * len(rows), **kw}


def allowed(r, u):
    if not can(u, 'view', 'rep'):
        return False
    if r['fin'] and not can(u, 'fin', 'rep'):
        return False
    return not r['need'] or any(can(u, a, k) for a, k in r['need'])


def catalogue(u):
    return [{'key': r['key'], 'group': r['group'], 'title': r['title'], 'period': r['period']} for r in REPORTS.values() if allowed(r, u)]


class F:
    """Filters of one run: companies, period, location, and the scope of the signed-in user."""

    def __init__(self, args):
        u = g.user
        self.u, self.args = u, args
        co = args.get('company')
        if co and co not in u['companies']:
            raise denied('You do not have access to this company.', 'company_denied')
        self.cos = (co,) if co else tuple(u['companies']) or ('',)
        self.company = co
        t = dt.date.today()
        self.frm = parse_date(args.get('from'), t.replace(day=1), 'From')
        self.to = parse_date(args.get('to'), t, 'To')
        if self.frm > self.to:
            raise bad('From date is after the To date.', 'bad_date')
        self.branch = args.get('branch') or None
        if self.branch and u['branches'] != '*' and self.branch not in u['branches']:
            raise denied('This location is outside your access.', 'out_of_scope')
        self.cost = can(u, 'cost', 'rep')

    def where(self, col):
        """SQL restricting `col` (a branch column) to the user's scope and the chosen location."""
        w, p = scope_sql(self.u, col)
        if self.branch:
            w += f' AND {col}=:_br'
            p['_br'] = self.branch
        return w, p

    def get(self, k, default=None):
        v = self.args.get(k)
        return v if v not in (None, '') else default


def run(key, args):
    r = REPORTS.get(key)
    if not r:
        raise bad('Unknown report.', 'unknown_report')
    if not allowed(r, g.user):
        raise denied('Your role cannot open this report.', 'forbidden')
    f = F(args)
    res = r['fn'](f)
    res['rows'] = [[_v(x) for x in row] for row in res['rows']]
    if res.get('totals'):
        res['totals'] = [_v(x) for x in res['totals']]
    res.update(key=key, group=r['group'], period={'from': str(f.frm), 'to': str(f.to)} if r['period'] else {'from': '', 'to': str(f.to)},
               company=res.get('company') or f.company or ', '.join(f.cos))
    return res


def _v(x):
    """One cell, JSON-ready: Decimal to float, dates to ISO text, MySQL TIME to HH:MM."""
    if isinstance(x, Decimal):
        return float(x)
    if isinstance(x, dt.timedelta):
        s = int(x.total_seconds())
        return f'{s // 3600:02d}:{s % 3600 // 60:02d}'
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat(sep=' ')[:16] if isinstance(x, dt.datetime) else x.isoformat()
    return x


def _tot(rows, idx, label_at=0, n=None):
    if not rows:
        return None
    n = n or len(rows[0])
    t = [''] * n
    if n:
        t[label_at] = 'Total'
    for i in idx:
        t[i] = round(sum((r[i] or 0) for r in rows), 2)
    return t


def _j(v):
    if isinstance(v, (dict, list)) or v is None:
        return v or {}
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return {}


def _names(table, ids, key='id', col='name'):
    ids = tuple({i for i in ids if i})
    if not ids:
        return {}
    return {r[key]: r[col] for r in db.q(f'SELECT {key}, {col} FROM {table} WHERE {key} IN :i', i=ids)}


SALES = (('view', 'pos'), ('view', 'rep'))
STOCK = (('view', 'inv'), ('view', 'rep'))
PUR = (('view', 'pur'),)
CASH = (('view', 'cash'),)
MSEA = (('view', 'msea'),)
ONL = (('view', 'onl'),)
CTL = (('view', 'audit'), ('view', 'ctl.users'))


# ================================================================== SALES
@R('sales-register', 'Sales', 'Sales register', SALES)
def _sales_register(f):
    w, p = f.where('i.branch_id')
    if f.get('adm_no'):                                  # one student's bills (from "Students purchased")
        w += ' AND i.adm_no=:adm'
        p['adm'] = f.get('adm_no').strip().upper()
    rows = db.q(f'''SELECT i.id, i.inv_date, i.inv_time, i.no, i.branch_id, i.channel, COALESCE(c.name, i.adm_no, 'Walk-in') party, u.name cashier, i.taxable,
                           i.cgst, i.sgst, i.igst, i.total, i.balance, i.status FROM invoices i LEFT JOIN customers c ON c.id=i.customer_id
                    LEFT JOIN users u ON u.id=i.cashier_id WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t ORDER BY i.inv_date, i.id''',
                c=f.cos, f=f.frm, t=f.to, **p)
    data = [[str(r['inv_date']), _v(r['inv_time']) or '', r['no'], r['branch_id'], r['channel'], r['party'], r['cashier'], float(r['taxable']), float(r['cgst']),
             float(r['sgst']), float(r['igst']), float(r['total']), float(r['balance']), r['status']] for r in rows]
    return out('Sales register', [C('Date', 'date'), C('Time'), C('Bill'), C('Location'), C('Channel'), C('Student / customer'), C('Cashier'), C('Taxable', 'num'),
                                  C('CGST', 'num'), C('SGST', 'num'), C('IGST', 'num'), C('Total', 'num'), C('Balance', 'num'), C('Status')], data,
               [{'invoice': r['id']} for r in rows], totals=_tot(data, range(7, 13)))


def _collections(f, group_sql, label):
    w, p = f.where('i.branch_id')
    pays = db.q(f'''SELECT {group_sql} g, p.mode, SUM(p.amount) a FROM invoice_payments p JOIN invoices i ON i.id=p.invoice_id
                    WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.status<>'Cancelled' GROUP BY g, p.mode''', c=f.cos, f=f.frm, t=f.to, **p)
    by = defaultdict(lambda: defaultdict(float))
    for r in pays:
        by[r['g']][r['mode']] += float(r['a'])
    return by


@R('daily-sales', 'Sales', 'Daily sales and collection', SALES)
def _daily(f):
    w, p = f.where('i.branch_id')
    s = {r['d']: r for r in db.q(f'''SELECT i.inv_date d, COUNT(*) bills, SUM(i.gross) gross, SUM(i.discount) disc, SUM(i.taxable) taxable,
                                         SUM(i.cgst+i.sgst+i.igst) tax, SUM(i.total) total FROM invoices i
                                  WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.status<>'Cancelled' GROUP BY i.inv_date''',
                                 c=f.cos, f=f.frm, t=f.to, **p)}
    ret = {r['d']: float(r['a']) for r in db.q(f'''SELECT n.cn_date d, SUM(n.amount) a FROM credit_notes n JOIN invoices i ON i.id=n.invoice_id
                                                  WHERE {w} AND n.company IN :c AND n.cn_date BETWEEN :f AND :t GROUP BY n.cn_date''', c=f.cos, f=f.frm, t=f.to, **p)}
    col = _collections(f, 'i.inv_date', 'day')
    rows = []
    for d in sorted(set(s) | set(ret)):
        x = s.get(d) or {}
        m = col.get(d, {})
        rows.append([str(d), int(x.get('bills') or 0), float(x.get('gross') or 0), float(x.get('disc') or 0), float(x.get('taxable') or 0), float(x.get('tax') or 0),
                     float(x.get('total') or 0), ret.get(d, 0.0), round(float(x.get('total') or 0) - ret.get(d, 0.0), 2), round(m.get('cash', 0), 2),
                     round(m.get('upi', 0), 2), round(m.get('card', 0), 2), round(sum(v for k, v in m.items() if k not in ('cash', 'upi', 'card')), 2)])
    return out('Daily sales and collection', [C('Date', 'date'), C('Bills', 'qty'), C('Gross', 'num'), C('Discount', 'num'), C('Taxable', 'num'), C('GST', 'num'),
                                              C('Sales', 'num'), C('Returns', 'num'), C('Net sales', 'num'), C('Cash', 'num'), C('UPI', 'num'), C('Card', 'num'),
                                              C('Credit / voucher / other', 'num')], rows,
               [{'report': 'sales-register', 'args': {'from': r[0], 'to': r[0]}} for r in rows], totals=_tot(rows, range(1, 13)))


@R('cashier-sales', 'Sales', 'Cashier-wise sales', SALES)
def _cashier(f):
    w, p = f.where('i.branch_id')
    s = db.q(f'''SELECT i.cashier_id u, COUNT(*) bills, SUM(i.total) total, SUM(i.discount) disc, SUM(i.override_by IS NOT NULL) overrides FROM invoices i
                 WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.status<>'Cancelled' GROUP BY i.cashier_id''', c=f.cos, f=f.frm, t=f.to, **p)
    ret = {r['u']: float(r['a']) for r in db.q(f'''SELECT n.created_by u, SUM(n.amount) a FROM credit_notes n JOIN invoices i ON i.id=n.invoice_id
                                                  WHERE {w} AND n.company IN :c AND n.cn_date BETWEEN :f AND :t GROUP BY n.created_by''', c=f.cos, f=f.frm, t=f.to, **p)}
    col = _collections(f, 'i.cashier_id', 'cashier')
    names = _names('users', [r['u'] for r in s] + list(ret))
    rows = []
    for r in s:
        m = col.get(r['u'], {})
        rows.append([names.get(r['u'], r['u'] or 'Online / system'), int(r['bills']), float(r['total']), round(float(r['total']) / int(r['bills']), 2) if r['bills'] else 0,
                     float(r['disc']), int(r['overrides'] or 0), ret.get(r['u'], 0.0), round(m.get('cash', 0), 2), round(m.get('upi', 0), 2), round(m.get('card', 0), 2),
                     round(sum(v for k, v in m.items() if k not in ('cash', 'upi', 'card')), 2)])
    rows.sort(key=lambda x: -x[2])
    return out('Cashier-wise sales', [C('Cashier'), C('Bills', 'qty'), C('Sales', 'num'), C('Average bill', 'num'), C('Discount', 'num'), C('Overrides', 'qty'),
                                      C('Returns given', 'num'), C('Cash', 'num'), C('UPI', 'num'), C('Card', 'num'), C('Other', 'num')], rows,
               totals=_tot(rows, [1, 2, 4, 5, 6, 7, 8, 9, 10]))


@R('payment-modes', 'Sales', 'Payment-mode collection', SALES)
def _modes(f):
    w, p = f.where('i.branch_id')
    rs = db.q(f'''SELECT p.mode, COUNT(DISTINCT i.id) bills, COUNT(*) n, SUM(p.amount) a, SUM(p.ref IS NOT NULL AND p.ref<>'') refs FROM invoice_payments p
                  JOIN invoices i ON i.id=p.invoice_id WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.status<>'Cancelled'
                  GROUP BY p.mode ORDER BY a DESC''',
              c=f.cos, f=f.frm, t=f.to, **p)
    tot = sum(float(r['a']) for r in rs) or 1
    rows = [[r['mode'].upper(), int(r['bills']), int(r['n']), float(r['a']), round(float(r['a']) / tot * 100, 1), int(r['refs'] or 0)] for r in rs]
    return out('Payment-mode collection', [C('Mode'), C('Bills', 'qty'), C('Payments', 'qty'), C('Amount', 'num'), C('Share', 'pct'), C('With reference', 'qty')], rows,
               totals=_tot(rows, [1, 2, 3, 5]), note='Payments on cancelled bills are left out. Refunds of returns are in the Returns register; cash handed over is in the Cash handover register.')


@R('returns-register', 'Sales', 'Sales returns / credit notes register', SALES)
def _returns(f):
    w, p = f.where('i.branch_id')
    rs = db.q(f'''SELECT n.id, n.no, n.cn_date, i.no inv, i.id iid, i.branch_id, COALESCE(c.name, i.adm_no, 'Walk-in') party, n.kind, n.`condition` cond, n.reason,
                         n.refund_mode, n.taxable, n.tax, n.amount, (SELECT COALESCE(SUM(qty),0) FROM credit_note_lines WHERE credit_note_id=n.id) qty,
                         n.approval_no, u.name by_name FROM credit_notes n JOIN invoices i ON i.id=n.invoice_id LEFT JOIN customers c ON c.id=i.customer_id
                  LEFT JOIN users u ON u.id=n.created_by WHERE {w} AND n.company IN :c AND n.cn_date BETWEEN :f AND :t ORDER BY n.cn_date, n.id''',
              c=f.cos, f=f.frm, t=f.to, **p)
    rows = [[str(r['cn_date']), r['no'], r['inv'], r['branch_id'], r['party'], r['kind'], r['cond'], r['reason'], r['refund_mode'], int(r['qty']), float(r['taxable']),
             float(r['tax']), float(r['amount']), r['approval_no'] or '', r['by_name']] for r in rs]
    return out('Sales returns / credit notes register', [C('Date', 'date'), C('Credit note'), C('Against bill'), C('Location'), C('Student / customer'), C('Kind'),
                                                        C('Condition'), C('Reason'), C('Refund mode'), C('Qty', 'qty'), C('Taxable', 'num'), C('GST', 'num'),
                                                        C('Amount', 'num'), C('Approval'), C('By')], rows, [{'invoice': r['iid']} for r in rs],
               totals=_tot(rows, [9, 10, 11, 12]))


@R('discount-register', 'Sales', 'Discount register', SALES)
def _discounts(f):
    w, p = f.where('i.branch_id')
    rs = db.q(f'''SELECT i.id, i.inv_date, i.no, i.branch_id, it.name, l.qty, l.rate, l.disc_pct, l.package_name, u.name cashier, ov.name override_name, i.override_reason
                  FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id JOIN items it ON it.id=l.item_id LEFT JOIN users u ON u.id=i.cashier_id
                  LEFT JOIN users ov ON ov.id=i.override_by WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND l.disc_pct>0
                  ORDER BY l.disc_pct DESC, i.inv_date''', c=f.cos, f=f.frm, t=f.to, **p)
    rows = [[str(r['inv_date']), r['no'], r['branch_id'], r['name'], int(r['qty']), float(r['rate']), float(r['disc_pct']),
             round(float(r['rate']) * int(r['qty']) * float(r['disc_pct']) / 100, 2), r['package_name'] or '', r['cashier'],
             (r['override_name'] or '') + (f" — {r['override_reason']}" if r['override_reason'] else '')] for r in rs]
    return out('Discount register', [C('Date', 'date'), C('Bill'), C('Location'), C('Item'), C('Qty', 'qty'), C('Rate', 'num'), C('Discount', 'pct'),
                                     C('Discount amount', 'num'), C('Package / scheme'), C('Cashier'), C('Approved above limit by')], rows,
               [{'invoice': r['id']} for r in rs], totals=_tot(rows, [4, 7]))


@R('school-class-sales', 'Sales', 'School and class-wise sales', SALES)
def _school(f):
    w, p = f.where('i.branch_id')
    rs = db.q(f'''SELECT COALESCE(sc.name, '(no school)') school, COALESCE(s.class_name, '—') cls, COUNT(DISTINCT i.adm_no) students, COUNT(DISTINCT i.id) bills,
                         SUM(l.qty-l.returned_qty) qty, SUM(l.amount * (l.qty-l.returned_qty) / l.qty) amount
                  FROM invoices i JOIN invoice_lines l ON l.invoice_id=i.id LEFT JOIN students s ON s.adm_no=i.adm_no LEFT JOIN schools sc ON sc.id=s.school_id
                  WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.status<>'Cancelled' AND i.adm_no IS NOT NULL
                  GROUP BY school, cls ORDER BY school, cls''', c=f.cos, f=f.frm, t=f.to, **p)
    rows = [[r['school'], r['cls'], int(r['students']), int(r['bills']), int(r['qty'] or 0), round(float(r['amount'] or 0), 2)] for r in rs]
    return out('School and class-wise sales', [C('School'), C('Class'), C('Students', 'qty'), C('Bills', 'qty'), C('Qty', 'qty'), C('Amount', 'num')], rows,
               totals=_tot(rows, [2, 3, 4, 5]))


def _bought(f):
    """Per student: bills, quantity and amount bought in the period (net of returns; cancelled and fully returned bills left out)."""
    w, p = f.where('i.branch_id')
    sql = f'''SELECT i.adm_no, COUNT(DISTINCT i.id) bills, SUM(l.qty-l.returned_qty) qty, SUM(l.amount * (l.qty-l.returned_qty) / l.qty) amount,
                       MAX(i.inv_date) last_bill
                FROM invoices i JOIN invoice_lines l ON l.invoice_id=i.id
                WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.status NOT IN ('Cancelled', 'Returned') AND i.adm_no IS NOT NULL
                GROUP BY i.adm_no HAVING SUM(l.qty-l.returned_qty) > 0'''
    return sql, dict(c=f.cos, f=f.frm, t=f.to, **p)


def _student_where(f):
    """Which students: active ones (or anyone who bought), of one campus / academic year when chosen."""
    w, p = "(s.status='Active' OR b.adm_no IS NOT NULL)", {}
    if f.get('campus'):
        w += ' AND s.campus=:campus'
        p['campus'] = f.get('campus').strip().upper()
    if f.get('year'):
        w += ' AND s.academic_year=:year'
        p['year'] = f.get('year').strip()
    if f.get('cls'):
        w += ' AND s.class_name=:cls'
        p['cls'] = f.get('cls').strip()
    if f.get('section'):
        w += ' AND s.section=:sec'
        p['sec'] = f.get('section').strip()
    return w, p


@R('student-purchases', 'Sales', 'Students purchased by branch', SALES)
def _student_purchases(f):
    bsql, bp = _bought(f)
    sw, sp = _student_where(f)
    rs = db.q(f'''WITH b AS ({bsql})
                  SELECT COALESCE(s.campus, '') campus, MAX(s.branch_name) branch, MAX(s.location) location, COUNT(*) students,
                         COUNT(b.adm_no) bought, SUM(b.bills) bills, SUM(b.qty) qty, SUM(b.amount) amount
                  FROM students s LEFT JOIN b ON b.adm_no=s.adm_no WHERE {sw}
                  GROUP BY COALESCE(s.campus, '') ORDER BY campus''', **bp, **sp)
    rows = []
    for r in rs:
        n, k = int(r['students']), int(r['bought'])
        rows.append([r['campus'] or '(no code)', r['branch'] or '', r['location'] or '', n, k, n - k, round(k / n * 100, 1) if n else 0,
                     int(r['bills'] or 0), int(r['qty'] or 0), round(float(r['amount'] or 0), 2)])
    tot = _tot(rows, [3, 4, 5, 7, 8, 9])
    if tot:
        tot[6] = round(tot[4] / tot[3] * 100, 1) if tot[3] else 0
    cols = [C('Branch code'), C('Branch name'), C('Location'), C('Students', 'qty'), C('Purchased', 'qty'), C('Not purchased', 'qty'),
            C('% purchased', 'pct'), C('Bills', 'qty'), C('Qty', 'qty'), C('Amount', 'num')]
    drill = [{'report': 'student-purchases-class', 'args': {'campus': r[0] if r[0] != '(no code)' else ''}} for r in rows]
    return out('Students purchased by branch', cols, rows, drill, totals=tot,
               note='Students: active students of the branch (and anyone who bought). Purchased: at least one bill in the period, '
                    'net of returns; cancelled and fully returned bills are not counted.')


@R('student-purchases-class', 'Sales', 'Students purchased by class', SALES)
def _student_purchases_class(f):
    bsql, bp = _bought(f)
    sw, sp = _student_where(f)
    rs = db.q(f'''WITH b AS ({bsql})
                  SELECT COALESCE(s.campus, '') campus, MAX(s.branch_name) branch, s.class_name cls, COALESCE(s.section, '') sec, COUNT(*) students,
                         COUNT(b.adm_no) bought, SUM(b.qty) qty, SUM(b.amount) amount
                  FROM students s LEFT JOIN b ON b.adm_no=s.adm_no WHERE {sw}
                  GROUP BY COALESCE(s.campus, ''), s.class_name, COALESCE(s.section, '') ORDER BY campus, cls, sec''', **bp, **sp)
    rows = []
    for r in rs:
        n, k = int(r['students']), int(r['bought'])
        rows.append([r['campus'] or '(no code)', r['branch'] or '', r['cls'], r['sec'], n, k, n - k, round(k / n * 100, 1) if n else 0,
                     int(r['qty'] or 0), round(float(r['amount'] or 0), 2)])
    tot = _tot(rows, [4, 5, 6, 8, 9])
    if tot:
        tot[7] = round(tot[5] / tot[4] * 100, 1) if tot[4] else 0
    cols = [C('Branch code'), C('Branch name'), C('Class'), C('Section'), C('Students', 'qty'), C('Purchased', 'qty'), C('Not purchased', 'qty'),
            C('% purchased', 'pct'), C('Qty', 'qty'), C('Amount', 'num')]
    drill = [{'report': 'student-purchase-list', 'args': {'campus': r[0] if r[0] != '(no code)' else '', 'cls': r[2], 'section': r[3]}} for r in rows]
    title = 'Students purchased by class' + (f" · {f.get('campus').upper()}" if f.get('campus') else '')
    return out(title, cols, rows, drill, totals=tot, note='Click a class to see its students: who purchased and who has not.')


LIST_CAP = 5000


@R('student-purchase-list', 'Sales', 'Student-wise purchases', SALES)
def _student_purchase_list(f):
    bsql, bp = _bought(f)
    sw, sp = _student_where(f)
    bought = (f.get('bought') or '').lower()
    if bought == 'yes':
        sw += ' AND b.adm_no IS NOT NULL'
    elif bought == 'no':
        sw += ' AND b.adm_no IS NULL'
    if f.get('q'):                                       # find a student by admission number or name
        sw += ' AND (s.adm_no LIKE :q OR s.name LIKE :q)'
        sp['q'] = '%' + f.get('q').strip().replace('%', '').replace('_', r'\_')[:60] + '%'
    rs = db.q(f'''WITH b AS ({bsql})
                  SELECT s.adm_no, s.name, s.campus, s.branch_name, s.class_name, s.section, s.status, b.bills, b.qty, b.amount, b.last_bill
                  FROM students s LEFT JOIN b ON b.adm_no=s.adm_no WHERE {sw}
                  ORDER BY s.campus, s.class_name, s.section, s.name LIMIT {LIST_CAP + 1}''', **bp, **sp)
    more = len(rs) > LIST_CAP
    rows = [[r['adm_no'], r['name'], r['campus'] or '', r['branch_name'] or '', r['class_name'], r['section'] or '', r['status'],
             'Yes' if r['bills'] else 'No', int(r['bills'] or 0), int(r['qty'] or 0), round(float(r['amount'] or 0), 2), r['last_bill']]
            for r in rs[:LIST_CAP]]
    cols = [C('Admission no'), C('Student'), C('Branch code'), C('Branch name'), C('Class'), C('Section'), C('Status'), C('Purchased'),
            C('Bills', 'qty'), C('Qty', 'qty'), C('Amount', 'num'), C('Last bill', 'date')]
    drill = [{'report': 'student-items', 'args': {'adm_no': r[0]}} if r[8] else None for r in rows]
    title = 'Students ' + {'yes': 'who purchased', 'no': 'who have not purchased'}.get(bought, 'and their purchases') +             (f" · {f.get('campus').upper()}" if f.get('campus') else '') + (f" · class {f.get('cls')}" if f.get('cls') else '') +             (f"-{f.get('section')}" if f.get('section') else '')
    return out(title, cols, rows, drill, totals=_tot(rows, [8, 9, 10]),
               note=f'Showing the first {LIST_CAP:,} students; choose a branch code to see one branch in full.' if more else None)


@R('student-items', 'Sales', 'One student: items bought', SALES)
def _student_items(f):
    adm = (f.get('adm_no') or '').strip().upper()
    cols = [C('Date', 'date'), C('Bill'), C('Location'), C('Item code'), C('Item'), C('Size'), C('Qty', 'qty'), C('Returned', 'qty'), C('Amount', 'num'), C('Status')]
    if not adm:
        return out('One student: items bought', cols, [], note='Enter an admission number (or open a student from Student-wise purchases).')
    st = db.one('SELECT adm_no, name, class_name, section, campus, branch_name, academic_year, status FROM students WHERE adm_no=:a', a=adm)
    w, p = f.where('i.branch_id')
    rs = db.q(f'''SELECT i.id, i.inv_date, i.no, i.branch_id, l.item_id, COALESCE(it.name, l.description) item, it.size, l.qty, l.returned_qty,
                         l.amount * (l.qty-l.returned_qty) / l.qty amount, i.status
                  FROM invoices i JOIN invoice_lines l ON l.invoice_id=i.id LEFT JOIN items it ON it.id=l.item_id
                  WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.adm_no=:adm AND i.status<>'Cancelled'
                  ORDER BY i.inv_date, i.id, l.line_no''', c=f.cos, f=f.frm, t=f.to, adm=adm, **p)
    rows = [[r['inv_date'], r['no'], r['branch_id'], r['item_id'], r['item'], r['size'] or '', int(r['qty']), int(r['returned_qty'] or 0),
             round(float(r['amount'] or 0), 2), r['status']] for r in rs]
    who = (f"{st['adm_no']} · {st['name']} · class {st['class_name']}{('-' + st['section']) if st['section'] else ''} · "
           f"{st['campus'] or ''} {st['branch_name'] or ''} · {st['academic_year'] or ''} · {st['status']}") if st else f'{adm} (not in the student list)'
    return out(f'Items bought: {adm}', cols, rows, [{'invoice': r['id']} for r in rs], totals=_tot(rows, [6, 7, 8]),
               note=who + ('' if rows else ' · no purchases in this period.'))


@R('category-sales', 'Sales', 'Category-wise sales', SALES)
def _category(f):
    w, p = f.where('i.branch_id')
    rs = db.q(f'''SELECT it.category, SUM(l.qty-l.returned_qty) qty, SUM(l.taxable * (l.qty-l.returned_qty) / l.qty) taxable, SUM(l.tax * (l.qty-l.returned_qty) / l.qty) tax,
                         SUM(l.unit_cost * (l.qty-l.returned_qty)) cost FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id JOIN items it ON it.id=l.item_id
                  WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.status<>'Cancelled' GROUP BY it.category ORDER BY taxable DESC''',
              c=f.cos, f=f.frm, t=f.to, **p)
    tot = sum(float(r['taxable'] or 0) for r in rs) or 1
    cols = [C('Category'), C('Qty', 'qty'), C('Taxable', 'num'), C('GST', 'num'), C('Share', 'pct')]
    rows = []
    for r in rs:
        tx = round(float(r['taxable'] or 0), 2)
        row = [r['category'] or 'Other', int(r['qty'] or 0), tx, round(float(r['tax'] or 0), 2), round(tx / tot * 100, 1)]
        if f.cost:
            c_ = round(float(r['cost'] or 0), 2)
            row += [c_, round(tx - c_, 2), round((tx - c_) / tx * 100, 1) if tx else None]
        rows.append(row)
    if f.cost:
        cols += [C('Cost', 'num'), C('Margin', 'num'), C('Margin %', 'pct')]
    return out('Category-wise sales', cols, rows, [{'report': 'item-sales', 'args': {'category': r[0]}} for r in rows],
               totals=_tot(rows, [1, 2, 3] + ([5, 6] if f.cost else [])))


@R('item-sales', 'Sales', 'Item-wise sales', SALES)
def _item_sales(f):
    w, p = f.where('i.branch_id')
    cat = f.get('category')
    rs = db.q(f'''SELECT l.item_id, it.name, it.category, it.size, SUM(l.qty-l.returned_qty) qty, SUM(l.taxable * (l.qty-l.returned_qty) / l.qty) taxable,
                         SUM(l.unit_cost * (l.qty-l.returned_qty)) cost FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id JOIN items it ON it.id=l.item_id
                  WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.status<>'Cancelled' AND (:cat IS NULL OR it.category=:cat)
                  GROUP BY l.item_id, it.name, it.category, it.size ORDER BY qty DESC''', c=f.cos, f=f.frm, t=f.to, cat=cat, **p)
    cols = [C('Item'), C('Name'), C('Category'), C('Size'), C('Qty', 'qty'), C('Taxable', 'num')]
    rows = []
    for r in rs:
        row = [r['item_id'], r['name'], r['category'], r['size'], int(r['qty'] or 0), round(float(r['taxable'] or 0), 2)]
        if f.cost:
            row += [round(float(r['cost'] or 0), 2), round(float(r['taxable'] or 0) - float(r['cost'] or 0), 2)]
        rows.append(row)
    if f.cost:
        cols += [C('Cost', 'num'), C('Margin', 'num')]
    return out('Item-wise sales' + (f' · {cat}' if cat else ''), cols, rows, [{'report': 'item-ledger', 'args': {'item': r[0]}} for r in rows],
               totals=_tot(rows, [4, 5] + ([6, 7] if f.cost else [])))


@R('hourly-sales', 'Sales', 'Hourly sales', SALES)
def _hourly(f):
    w, p = f.where('i.branch_id')
    rs = db.q(f'''SELECT HOUR(i.inv_time) h, COUNT(*) bills, SUM(i.total) total FROM invoices i
                  WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.status<>'Cancelled' AND i.inv_time IS NOT NULL GROUP BY h ORDER BY h''',
              c=f.cos, f=f.frm, t=f.to, **p)
    days = max(1, (f.to - f.frm).days + 1)
    rows = [[f"{int(r['h']):02d}:00–{int(r['h']) + 1:02d}:00", int(r['bills']), float(r['total']), round(float(r['total']) / int(r['bills']), 2), round(int(r['bills']) / days, 1)]
            for r in rs if r['h'] is not None]
    return out('Hourly sales', [C('Hour'), C('Bills', 'qty'), C('Sales', 'num'), C('Average bill', 'num'), C('Bills per day', 'qty')], rows,
               totals=_tot(rows, [1, 2]), note='Use it to plan counter staff for peak hours.')


@R('pending-deliveries', 'Sales', 'Pending deliveries', SALES, period=False)
def _pending(f):
    w, p = f.where('i.branch_id')
    rs = db.q(f'''SELECT i.id, i.inv_date, i.no, i.branch_id, i.adm_no, COALESCE(c.name, i.adm_no) party, it.name, l.qty, l.delivered_qty, l.returned_qty, i.pending_reason
                  FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id JOIN items it ON it.id=l.item_id LEFT JOIN customers c ON c.id=i.customer_id
                  WHERE {w} AND i.company IN :c AND i.status<>'Cancelled' AND l.delivered_qty < l.qty - l.returned_qty ORDER BY i.inv_date''', c=f.cos, **p)
    t = dt.date.today()
    rows = [[str(r['inv_date']), r['no'], r['branch_id'], r['party'], r['name'], int(r['qty']), int(r['delivered_qty']), int(r['qty'] - r['returned_qty'] - r['delivered_qty']),
             (t - r['inv_date']).days, r['pending_reason'] or ''] for r in rs]
    return out('Pending deliveries', [C('Bill date', 'date'), C('Bill'), C('Location'), C('Student / customer'), C('Item'), C('Billed', 'qty'), C('Delivered', 'qty'),
                                      C('Pending', 'qty'), C('Days waiting', 'qty'), C('Reason')], rows, [{'invoice': r['id']} for r in rs], totals=_tot(rows, [5, 6, 7]))


# ================================================================== STOCK
def _cost_cols(f, cols):
    return cols if f.cost else [c for c in cols if c['t'] != 'num']


@R('stock-valuation', 'Stock', 'Stock valuation (item-wise)', STOCK, period=False)
def _valuation(f):
    w, p = f.where('s.branch_id')
    rs = db.q(f'''SELECT s.branch_id, s.item_id, it.name, it.category, it.size, s.qty, s.reserved, it.cost, it.price FROM stock_balance s JOIN items it ON it.id=s.item_id
                  JOIN branches b ON b.id=s.branch_id WHERE {w} AND b.company IN :c AND s.qty<>0 ORDER BY s.branch_id, it.category, it.name''', c=f.cos, **p)
    rows = []
    for r in rs:
        row = [r['branch_id'], r['item_id'], r['name'], r['category'], r['size'], int(r['qty']), int(r['reserved'] or 0)]
        if f.cost:
            row += [float(r['cost'] or 0), round(int(r['qty']) * float(r['cost'] or 0), 2)]
        row += [float(r['price'] or 0), round(int(r['qty']) * float(r['price'] or 0), 2)]
        rows.append(row)
    cols = [C('Location'), C('Item'), C('Name'), C('Category'), C('Size'), C('Qty', 'qty'), C('Reserved', 'qty')] + \
        ([C('Cost', 'num'), C('Cost value', 'num')] if f.cost else []) + [C('Price', 'num'), C('Retail value', 'num')]
    return out('Stock valuation', cols, rows, [{'report': 'item-ledger', 'args': {'item': r[1], 'branch': r[0]}} for r in rows],
               totals=_tot(rows, [5, 6] + ([8, 10] if f.cost else [8])))


@R('stock-summary', 'Stock', 'Stock summary by location', STOCK, period=False)
def _stock_summary(f):
    w, p = f.where('s.branch_id')
    rs = db.q(f'''SELECT s.branch_id, l.name, l.type, COUNT(*) items, SUM(s.qty>0) in_stock, SUM(s.qty) qty, SUM(s.qty*it.cost) cost, SUM(s.qty*it.price) retail,
                         SUM(it.reorder_level>0 AND s.qty<=it.reorder_level) low FROM stock_balance s JOIN items it ON it.id=s.item_id JOIN branches b ON b.id=s.branch_id
                  JOIN locations l ON l.code=b.location WHERE {w} AND b.company IN :c GROUP BY s.branch_id, l.name, l.type ORDER BY l.type, l.name''', c=f.cos, **p)
    rows = [[r['branch_id'], r['name'], r['type'], int(r['items']), int(r['in_stock'] or 0), int(r['qty'] or 0)] + ([round(float(r['cost'] or 0), 2)] if f.cost else [])
            + [round(float(r['retail'] or 0), 2), int(r['low'] or 0)] for r in rs]
    cols = [C('Location'), C('Name'), C('Type'), C('Items', 'qty'), C('In stock', 'qty'), C('Qty', 'qty')] + ([C('Cost value', 'num')] if f.cost else []) + \
        [C('Retail value', 'num'), C('Below reorder', 'qty')]
    n = len(cols)
    return out('Stock summary by location', cols, rows, [{'report': 'stock-valuation', 'args': {'branch': r[0]}} for r in rows], totals=_tot(rows, range(3, n)))


def _bucket(t):
    t = t or ''
    if t.startswith('Opening'):
        return 'open'
    if t in ('GRN', 'Purchase return'):
        return 'pur'
    if t == 'Transfer in':
        return 'tin'
    if t == 'Transfer out':
        return 'tout'
    if t == 'Sales return':
        return 'ret'
    if t in SALE_TYPES:
        return 'sale'
    return 'adj'


@R('stock-movement', 'Stock', 'Stock movement (opening, in, out, closing)', STOCK)
def _movement(f):
    w, p = f.where('l.branch_id')
    op = {(r['branch_id'], r['item_id']): int(r['q']) for r in db.q(f'''SELECT l.branch_id, l.item_id, SUM(l.qty) q FROM stock_ledger l
                                                                       WHERE {w} AND l.company IN :c AND l.txn_date < :f GROUP BY l.branch_id, l.item_id''', c=f.cos, f=f.frm, **p)}
    mv = defaultdict(lambda: defaultdict(int))
    for r in db.q(f'''SELECT l.branch_id, l.item_id, l.txn_type, SUM(l.qty) q FROM stock_ledger l WHERE {w} AND l.company IN :c AND l.txn_date BETWEEN :f AND :t
                      GROUP BY l.branch_id, l.item_id, l.txn_type''', c=f.cos, f=f.frm, t=f.to, **p):
        mv[(r['branch_id'], r['item_id'])][_bucket(r['txn_type'])] += int(r['q'])
    names = _names('items', [k[1] for k in set(op) | set(mv)])
    rows = []
    for k in sorted(set(op) | set(mv)):
        m = mv.get(k, {})
        o = op.get(k, 0) + m.get('open', 0)
        close = o + sum(v for b, v in m.items() if b != 'open')
        if not any((o, close, *m.values())):
            continue
        rows.append([k[0], k[1], names.get(k[1], k[1]), o, m.get('pur', 0), m.get('tin', 0), m.get('ret', 0), m.get('tout', 0), m.get('sale', 0), m.get('adj', 0), close])
    return out('Stock movement', [C('Location'), C('Item'), C('Name'), C('Opening', 'qty'), C('Purchases (net)', 'qty'), C('Transfers in', 'qty'), C('Sales returns', 'qty'),
                                  C('Transfers out', 'qty'), C('Sales', 'qty'), C('Adjustments', 'qty'), C('Closing', 'qty')], rows,
               [{'report': 'item-ledger', 'args': {'item': r[1], 'branch': r[0]}} for r in rows], totals=_tot(rows, range(3, 11)),
               note='Out-going quantities are negative. Closing = opening + all movements.')


@R('item-ledger', 'Stock', 'Item stock ledger', STOCK)
def _item_ledger(f):
    item = f.get('item')
    if not item:
        return out('Item stock ledger', [C('Note')], [['Choose an item (open it from Stock valuation, Stock movement or Item-wise sales).']])
    w, p = f.where('l.branch_id')
    it = db.one('SELECT name, company FROM items WHERE id=:i', i=item)
    if not it or it['company'] not in f.cos:
        raise bad('Item not found.', 'unknown_item')
    opening = int(db.val(f'SELECT COALESCE(SUM(l.qty),0) FROM stock_ledger l WHERE {w} AND l.item_id=:i AND l.txn_date < :f', i=item, f=f.frm, **p) or 0)
    rs = db.q(f'''SELECT l.txn_date, l.branch_id, l.txn_type, l.ref, l.qty, l.unit_cost, u.name by_name FROM stock_ledger l LEFT JOIN users u ON u.id=l.created_by
                  WHERE {w} AND l.item_id=:i AND l.txn_date BETWEEN :f AND :t ORDER BY l.txn_date, l.id''', i=item, f=f.frm, t=f.to, **p)
    bal, rows = opening, [[str(f.frm), '', 'Opening balance', '', None, None, opening, '']]
    for r in rs:
        bal += int(r['qty'])
        rows.append([str(r['txn_date']), r['branch_id'], r['txn_type'], r['ref'], int(r['qty']) if r['qty'] > 0 else None, -int(r['qty']) if r['qty'] < 0 else None, bal, r['by_name'] or ''])
    return out(f"Item stock ledger · {item} {it['name']}", [C('Date', 'date'), C('Location'), C('Movement'), C('Reference'), C('In', 'qty'), C('Out', 'qty'),
                                                          C('Balance', 'qty'), C('By')], rows, args={'item': item})


@R('low-stock', 'Stock', 'Low stock / reorder', STOCK, period=False)
def _low(f):
    w, p = f.where('s.branch_id')
    t = dt.date.today()
    rs = db.q(f'''SELECT s.branch_id, s.item_id, it.name, it.category, it.supplier_id, s.qty, it.reorder_level,
                         (SELECT COALESCE(-SUM(l.qty),0) FROM stock_ledger l WHERE l.branch_id=s.branch_id AND l.item_id=s.item_id AND l.txn_type IN :st AND l.txn_date>=:d) sold30
                  FROM stock_balance s JOIN items it ON it.id=s.item_id JOIN branches b ON b.id=s.branch_id
                  WHERE {w} AND b.company IN :c AND it.active=1 AND it.reorder_level>0 AND s.qty<=it.reorder_level ORDER BY s.qty - it.reorder_level, it.name''',
              c=f.cos, st=SALE_TYPES, d=t - dt.timedelta(days=30), **p)
    sup = _names('suppliers', [r['supplier_id'] for r in rs])
    rows = [[r['branch_id'], r['item_id'], r['name'], r['category'], int(r['qty']), int(r['reorder_level']), int(r['reorder_level']) - int(r['qty']), int(r['sold30']),
             max(int(r['reorder_level']) * 2 - int(r['qty']), int(r['sold30']) - int(r['qty']), 0), sup.get(r['supplier_id'], '')] for r in rs]
    return out('Low stock / reorder', [C('Location'), C('Item'), C('Name'), C('Category'), C('In stock', 'qty'), C('Reorder level', 'qty'), C('Below by', 'qty'),
                                       C('Sold in 30 days', 'qty'), C('Suggested order', 'qty'), C('Usual supplier')], rows,
               [{'report': 'item-ledger', 'args': {'item': r[1], 'branch': r[0]}} for r in rows],
               note='Suggested order = the larger of twice the reorder level or the last 30 days of sales, less stock in hand.')


@R('slow-moving', 'Stock', 'Slow and non-moving stock', STOCK, period=False)
def _slow(f):
    days = int(f.get('days', 90)) if str(f.get('days', 90)).isdigit() else 90
    w, p = f.where('s.branch_id')
    t = dt.date.today()
    rs = db.q(f'''SELECT s.branch_id, s.item_id, it.name, it.category, s.qty, it.cost,
                         (SELECT MAX(l.txn_date) FROM stock_ledger l WHERE l.branch_id=s.branch_id AND l.item_id=s.item_id AND l.txn_type IN :st) last_sale,
                         (SELECT MAX(l.txn_date) FROM stock_ledger l WHERE l.branch_id=s.branch_id AND l.item_id=s.item_id AND l.qty>0) last_in
                  FROM stock_balance s JOIN items it ON it.id=s.item_id JOIN branches b ON b.id=s.branch_id WHERE {w} AND b.company IN :c AND s.qty>0''',
              c=f.cos, st=SALE_TYPES, **p)
    rows = []
    for r in rs:
        idle = (t - (r['last_sale'] or r['last_in'] or t)).days
        if idle < days:
            continue
        row = [r['branch_id'], r['item_id'], r['name'], r['category'], int(r['qty']), str(r['last_sale']) if r['last_sale'] else 'Never', idle,
               'Non-moving' if not r['last_sale'] else 'Slow-moving']
        if f.cost:
            row.insert(5, round(int(r['qty']) * float(r['cost'] or 0), 2))
        rows.append(row)
    rows.sort(key=lambda x: -x[-2])
    cols = [C('Location'), C('Item'), C('Name'), C('Category'), C('Qty', 'qty')] + ([C('Cost value', 'num')] if f.cost else []) + \
        [C('Last sale', 'date'), C('Days without sale', 'qty'), C('Status')]
    return out(f'Slow and non-moving stock (no sale for {days}+ days)', cols, rows, [{'report': 'item-ledger', 'args': {'item': r[1], 'branch': r[0]}} for r in rows],
               totals=_tot(rows, [4] + ([5] if f.cost else [])))


AGE = (('0–30 days', 0, 30), ('31–90', 31, 90), ('91–180', 91, 180), ('181–365', 181, 365), ('Over 1 year', 366, 10 ** 6))


@R('stock-ageing', 'Stock', 'Stock ageing', STOCK, period=False)
def _ageing(f):
    w, p = f.where('s.branch_id')
    t = dt.date.today()
    rs = db.q(f'''SELECT s.branch_id, s.item_id, it.name, it.category, s.qty, it.cost,
                         (SELECT MAX(l.txn_date) FROM stock_ledger l WHERE l.branch_id=s.branch_id AND l.item_id=s.item_id AND l.qty>0) last_in
                  FROM stock_balance s JOIN items it ON it.id=s.item_id JOIN branches b ON b.id=s.branch_id WHERE {w} AND b.company IN :c AND s.qty>0''', c=f.cos, **p)
    by = defaultdict(lambda: [0.0] * len(AGE))
    for r in rs:
        age = (t - (r['last_in'] or t)).days
        i = next(k for k, (_, lo, hi) in enumerate(AGE) if lo <= age <= hi)
        by[(r['branch_id'], r['category'] or 'Other')][i] += int(r['qty']) * (float(r['cost'] or 0) if f.cost else 1)
    rows = [[b, c, *[round(v, 2) for v in x], round(sum(x), 2)] for (b, c), x in sorted(by.items())]
    unit = 'num' if f.cost else 'qty'
    return out('Stock ageing (by last receipt) · ' + ('cost value' if f.cost else 'quantity'), [C('Location'), C('Category'), *[C(a[0], unit) for a in AGE], C('Total', unit)],
               rows, totals=_tot(rows, range(2, 2 + len(AGE) + 1)), note='Age = days since the item last came into the location (purchase, transfer or return).')


@R('negative-stock', 'Stock', 'Negative and over-reserved stock', STOCK, period=False)
def _negative(f):
    w, p = f.where('s.branch_id')
    rs = db.q(f'''SELECT s.branch_id, s.item_id, it.name, s.qty, s.reserved FROM stock_balance s JOIN items it ON it.id=s.item_id JOIN branches b ON b.id=s.branch_id
                  WHERE {w} AND b.company IN :c AND (s.qty<0 OR s.reserved>s.qty) ORDER BY s.qty''', c=f.cos, **p)
    rows = [[r['branch_id'], r['item_id'], r['name'], int(r['qty']), int(r['reserved'] or 0), 'Negative stock' if r['qty'] < 0 else 'Reserved more than in stock'] for r in rs]
    return out('Negative and over-reserved stock', [C('Location'), C('Item'), C('Name'), C('Qty', 'qty'), C('Reserved', 'qty'), C('Problem')], rows,
               [{'report': 'item-ledger', 'args': {'item': r[1], 'branch': r[0]}} for r in rows],
               note='Should always be empty. If not, run a stock audit for these items.' if rows else 'No problems: no negative or over-reserved stock.')


def _transfers(f, status=None, period_=True):
    wf, p = f.where('t.from_branch')
    wt, p2 = scope_sql(f.u, 't.to_branch')
    p.update({k + '2': v for k, v in p2.items()})
    wt = wt.replace(':_scope', ':_scope2')
    if f.branch:
        wt += ' AND t.to_branch=:_br'
    rs = db.q(f'''SELECT t.id, t.no, t.txn_date, t.from_branch, t.to_branch, t.status, t.value, t.reason, t.dispatched_at, t.received_at,
                         SUM(l.qty) qty, SUM(l.received_qty) rec, SUM(l.damaged_qty) dmg FROM transfers t JOIN transfer_lines l ON l.transfer_id=t.id
                  WHERE (({wf}) OR ({wt})) AND t.company IN :c {'AND t.txn_date BETWEEN :f AND :t' if period_ else ''} {'AND t.status=:st' if status else ''}
                  GROUP BY t.id ORDER BY t.txn_date, t.id''', c=f.cos, f=f.frm, t=f.to, st=status, **p)
    return rs


@R('transfer-register', 'Stock', 'Transfer register', STOCK)
def _transfer_reg(f):
    rs = _transfers(f)
    rows = [[str(r['txn_date']), r['no'], r['from_branch'], r['to_branch'], r['status'], int(r['qty'] or 0), int(r['rec'] or 0), int(r['dmg'] or 0)]
            + ([float(r['value'] or 0)] if f.cost else []) + [r['reason'] or ''] for r in rs]
    cols = [C('Date', 'date'), C('Transfer'), C('From'), C('To'), C('Status'), C('Sent', 'qty'), C('Received', 'qty'), C('Damaged', 'qty')] + \
        ([C('Value', 'num')] if f.cost else []) + [C('Reason')]
    return out('Transfer register', cols, rows, totals=_tot(rows, [5, 6, 7] + ([8] if f.cost else [])))


@R('transfers-in-transit', 'Stock', 'Transfers in transit', STOCK, period=False)
def _transit(f):
    rs = _transfers(f, 'Dispatched', False)
    t = dt.datetime.now()
    rows = [[r['no'], r['from_branch'], r['to_branch'], str(r['dispatched_at'] or '')[:16], round((t - r['dispatched_at']).total_seconds() / 86400, 1) if r['dispatched_at'] else None,
             int(r['qty'] or 0)] + ([float(r['value'] or 0)] if f.cost else []) for r in rs]
    cols = [C('Transfer'), C('From'), C('To'), C('Dispatched'), C('Days in transit', 'qty'), C('Qty', 'qty')] + ([C('Value', 'num')] if f.cost else [])
    return out('Transfers in transit', cols, rows, totals=_tot(rows, [5] + ([6] if f.cost else [])), note='Dispatched but not yet received at the destination.')


@R('adjustment-register', 'Stock', 'Stock adjustment register', STOCK)
def _adj_reg(f):
    w, p = f.where('a.branch_id')
    rs = db.q(f'''SELECT a.txn_date, a.no, a.branch_id, a.adj_type, a.reason, a.status, a.value, a.audit_no, a.journal_no, u.name by_name, ap.name appr,
                         SUM(GREATEST(l.qty,0)) qin, SUM(LEAST(l.qty,0)) qout FROM stock_adjustments a JOIN stock_adjustment_lines l ON l.adjustment_id=a.id
                  LEFT JOIN users u ON u.id=a.created_by LEFT JOIN users ap ON ap.id=a.approved_by
                  WHERE {w} AND a.company IN :c AND a.txn_date BETWEEN :f AND :t GROUP BY a.id ORDER BY a.txn_date, a.id''', c=f.cos, f=f.frm, t=f.to, **p)
    rows = [[str(r['txn_date']), r['no'], r['branch_id'], r['adj_type'], r['reason'], r['status'], int(r['qin'] or 0), int(r['qout'] or 0)]
            + ([float(r['value'] or 0)] if f.cost else []) + [r['audit_no'] or '', r['journal_no'] or '', r['by_name'] or '', r['appr'] or ''] for r in rs]
    cols = [C('Date', 'date'), C('Adjustment'), C('Location'), C('Type'), C('Reason'), C('Status'), C('Qty in', 'qty'), C('Qty out', 'qty')] + \
        ([C('Value', 'num')] if f.cost else []) + [C('Stock audit'), C('Voucher'), C('By'), C('Approved by')]
    return out('Stock adjustment register', cols, rows, totals=_tot(rows, [6, 7] + ([8] if f.cost else [])))


# ================================================================== PURCHASING
@R('po-register', 'Purchasing', 'Purchase order register', PUR)
def _po_reg(f):
    w, p = f.where('o.branch_id')
    rs = db.q(f'''SELECT o.id, o.created_at, o.no, o.branch_id, s.name sup, o.status, o.value, o.grn_value, o.required_by, u.name by_name FROM purchase_orders o
                  LEFT JOIN suppliers s ON s.id=o.supplier_id LEFT JOIN users u ON u.id=o.created_by
                  WHERE {w} AND o.company IN :c AND DATE(o.created_at) BETWEEN :f AND :t ORDER BY o.created_at''', c=f.cos, f=f.frm, t=f.to, **p)
    rows = [[str(r['created_at'])[:10], r['no'], r['branch_id'], r['sup'] or '(not chosen)', r['status'], float(r['value'] or 0), float(r['grn_value'] or 0),
             str(r['required_by'] or ''), r['by_name'] or ''] for r in rs]
    return out('Purchase order register', [C('Date', 'date'), C('PO'), C('Deliver to'), C('Supplier'), C('Status'), C('PO value', 'num'), C('Received value', 'num'),
                                           C('Required by', 'date'), C('Raised by')], rows, totals=_tot(rows, [5, 6]))


@R('pending-po', 'Purchasing', 'Pending purchase orders (not fully received)', PUR, period=False)
def _pending_po(f):
    w, p = f.where('o.branch_id')
    t = dt.date.today()
    rs = db.q(f'''SELECT o.no, o.created_at, o.branch_id, s.name sup, o.required_by, o.status, it.name item, l.qty, l.received_qty, l.rejected_qty, l.rate
                  FROM purchase_order_lines l JOIN purchase_orders o ON o.id=l.po_id JOIN items it ON it.id=l.item_id LEFT JOIN suppliers s ON s.id=o.supplier_id
                  WHERE {w} AND o.company IN :c AND o.status NOT IN ('Cancelled','Closed','Draft','Rejected') AND l.received_qty - l.rejected_qty < l.qty
                  ORDER BY o.required_by IS NULL, o.required_by, o.no''', c=f.cos, **p)
    rows = [[r['no'], str(r['created_at'])[:10], r['branch_id'], r['sup'] or '', r['item'], int(r['qty']), int(r['received_qty'] - r['rejected_qty']),
             int(r['qty'] - r['received_qty'] + r['rejected_qty']), round(float(r['rate']) * int(r['qty'] - r['received_qty'] + r['rejected_qty']), 2),
             str(r['required_by'] or ''), max(0, (t - r['required_by']).days) if r['required_by'] else None, r['status']] for r in rs]
    return out('Pending purchase orders', [C('PO'), C('Date', 'date'), C('Deliver to'), C('Supplier'), C('Item'), C('Ordered', 'qty'), C('Received (good)', 'qty'),
                                           C('Pending', 'qty'), C('Pending value', 'num'), C('Required by', 'date'), C('Days overdue', 'qty'), C('Status')], rows,
               totals=_tot(rows, [5, 6, 7, 8]))


@R('grn-register', 'Purchasing', 'Goods receipt (GRN) register', PUR)
def _grn(f):
    w, p = f.where('o.branch_id')
    rs = db.q(f'''SELECT g.txn_date, g.no, o.no po, o.branch_id, s.name sup, g.value, g.line_items, u.name by_name FROM goods_receipts g JOIN purchase_orders o ON o.id=g.po_id
                  LEFT JOIN suppliers s ON s.id=o.supplier_id LEFT JOIN users u ON u.id=g.received_by
                  WHERE {w} AND o.company IN :c AND g.txn_date BETWEEN :f AND :t ORDER BY g.txn_date, g.id''', c=f.cos, f=f.frm, t=f.to, **p)
    rows = []
    for r in rs:
        li = _j(r['line_items'])
        q = sum(int(x.get('received', 0) or 0) for x in li) if isinstance(li, list) else None
        rj = sum(int(x.get('rejected', 0) or 0) for x in li) if isinstance(li, list) else None
        rows.append([str(r['txn_date']), r['no'], r['po'], r['branch_id'], r['sup'] or '', q, rj, float(r['value'] or 0), r['by_name'] or ''])
    return out('Goods receipt (GRN) register', [C('Date', 'date'), C('GRN'), C('PO'), C('Location'), C('Supplier'), C('Received', 'qty'), C('Rejected', 'qty'),
                                                C('Value', 'num'), C('Received by')], rows, totals=_tot(rows, [5, 6, 7]))


@R('supplier-purchases', 'Purchasing', 'Supplier-wise purchases and dues', PUR)
def _sup(f):
    pr = {'c': f.cos, 'f': f.frm, 't': f.to}
    grn = {r['s']: float(r['v']) for r in db.q('''SELECT o.supplier_id s, SUM(g.value) v FROM goods_receipts g JOIN purchase_orders o ON o.id=g.po_id
                                                 WHERE o.company IN :c AND g.txn_date BETWEEN :f AND :t GROUP BY o.supplier_id''', **pr)}
    bills = {r['s']: r for r in db.q('''SELECT supplier_id s, COUNT(*) n, SUM(amount) a FROM supplier_bills WHERE company IN :c AND bill_date BETWEEN :f AND :t
                                       GROUP BY supplier_id''', **pr)}
    paid = {r['s']: float(r['a']) for r in db.q('''SELECT supplier_id s, SUM(amount) a FROM supplier_payments WHERE company IN :c AND pay_date BETWEEN :f AND :t
                                                  GROUP BY supplier_id''', **pr)}
    dn = {r['s']: float(r['a']) for r in db.q('''SELECT o.supplier_id s, SUM(d.amount) a FROM debit_notes d JOIN purchase_orders o ON o.id=d.po_id
                                                WHERE d.company IN :c AND DATE(d.created_at) BETWEEN :f AND :t GROUP BY o.supplier_id''', **pr)}
    due = {r['s']: float(r['a']) for r in db.q('''SELECT supplier_id s, SUM(amount-paid-adjusted) a FROM supplier_bills WHERE company IN :c GROUP BY supplier_id''', c=f.cos)}
    ids = set(grn) | set(bills) | set(paid) | set(dn) | {k for k, v in due.items() if abs(v) > 0.004}
    names = _names('suppliers', ids)
    rows = [[names.get(s, s), round(grn.get(s, 0), 2), int((bills.get(s) or {}).get('n') or 0), round(float((bills.get(s) or {}).get('a') or 0), 2), round(dn.get(s, 0), 2),
             round(paid.get(s, 0), 2), round(due.get(s, 0), 2)] for s in ids if s]
    rows.sort(key=lambda x: -x[3])
    return out('Supplier-wise purchases and dues', [C('Supplier'), C('Goods received', 'num'), C('Bills', 'qty'), C('Billed', 'num'), C('Returns (debit notes)', 'num'),
                                                    C('Paid', 'num'), C('Outstanding now', 'num')], rows, totals=_tot(rows, range(1, 7)))


@R('purchase-returns', 'Purchasing', 'Purchase returns / debit notes', PUR)
def _pret(f):
    rs = db.q('''SELECT d.created_at, d.no, o.no po, s.name sup, it.name item, d.qty, d.amount, d.reason, u.name by_name FROM debit_notes d
                 JOIN purchase_orders o ON o.id=d.po_id LEFT JOIN suppliers s ON s.id=o.supplier_id LEFT JOIN items it ON it.id=d.item_id LEFT JOIN users u ON u.id=d.created_by
                 WHERE d.company IN :c AND DATE(d.created_at) BETWEEN :f AND :t ORDER BY d.created_at''', c=f.cos, f=f.frm, t=f.to)
    rows = [[str(r['created_at'])[:10], r['no'], r['po'], r['sup'] or '', r['item'] or '', int(r['qty'] or 0), float(r['amount'] or 0), r['reason'] or '', r['by_name'] or ''] for r in rs]
    return out('Purchase returns / debit notes', [C('Date', 'date'), C('Debit note'), C('PO'), C('Supplier'), C('Item'), C('Qty', 'qty'), C('Amount', 'num'), C('Reason'),
                                                  C('By')], rows, totals=_tot(rows, [5, 6]))


@R('price-comparison', 'Purchasing', 'Supplier price comparison (quotes)', PUR)
def _quotes(f):
    w, p = f.where('o.branch_id')
    pos = db.q(f'''SELECT o.id, o.no, o.created_at, o.supplier_id chosen FROM purchase_orders o WHERE {w} AND o.company IN :c AND DATE(o.created_at) BETWEEN :f AND :t
                   AND EXISTS (SELECT 1 FROM purchase_quotes q WHERE q.po_id=o.id) ORDER BY o.created_at''', c=f.cos, f=f.frm, t=f.to, **p)
    rows = []
    for o in pos:
        lines = db.q('SELECT l.line_no, it.name, l.qty FROM purchase_order_lines l JOIN items it ON it.id=l.item_id WHERE l.po_id=:i ORDER BY l.line_no', i=o['id'])
        qs = db.q('SELECT supplier_id, rates, lead_days, selected FROM purchase_quotes WHERE po_id=:i', i=o['id'])
        names = _names('suppliers', [q['supplier_id'] for q in qs])
        for k, ln in enumerate(lines):
            rates = [(q['supplier_id'], float((_j(q['rates']) or [None] * (k + 1))[k]) if k < len(_j(q['rates']) or []) and (_j(q['rates']))[k] is not None else None, q) for q in qs]
            valid = [r for r in rates if r[1] is not None]
            low = min((r[1] for r in valid), default=None)
            for sid, rate, q in valid:
                rows.append([o['no'], str(o['created_at'])[:10], ln['name'], int(ln['qty']), names.get(sid, sid), rate, q['lead_days'], 'Yes' if sid == o['chosen'] else '',
                             round((rate - low) / low * 100, 1) if low else None, round((rate - low) * int(ln['qty']), 2) if low is not None else None])
    return out('Supplier price comparison', [C('PO'), C('Date', 'date'), C('Item'), C('Qty', 'qty'), C('Supplier'), C('Rate', 'num'), C('Lead days', 'qty'), C('Chosen'),
                                             C('Above lowest', 'pct'), C('Extra cost', 'num')], rows,
               note='A chosen supplier above the lowest rate needs a reason on the PO; Extra cost shows what that choice cost.')


# ================================================================== CASH
@R('shift-closing', 'Cash', 'Cashier shift closing (short / excess)', CASH)
def _shifts(f):
    w, p = f.where('b.id')
    rs = db.q(f'''SELECT DISTINCT s.id, s.no, s.shift_date, s.location, s.counter, s.status, s.opened_at, s.closed_at, s.figures, s.difference_reason, u.name
                  FROM cash_shifts s LEFT JOIN users u ON u.id=s.user_id JOIN branches b ON b.location=s.location
                  WHERE {w} AND b.company IN :c AND s.shift_date BETWEEN :f AND :t ORDER BY s.shift_date, s.id''', c=f.cos, f=f.frm, t=f.to, **p)
    rows = []
    for r in rs:
        for co, x in sorted(_j(r['figures']).items()):
            if co not in f.cos or not isinstance(x, dict):
                continue
            rows.append([str(r['shift_date']), r['no'], r['location'], r['counter'] or '', r['name'], co, str(r['opened_at'] or '')[11:16], str(r['closed_at'] or '')[11:16],
                         x.get('opening'), x.get('cash_sales'), x.get('cash_refunds'), x.get('handed_over'), x.get('expected'), x.get('counted'), x.get('difference'),
                         r['status'], r['difference_reason'] or ''])
    return out('Cashier shift closing', [C('Date', 'date'), C('Shift'), C('Location'), C('Counter'), C('Cashier'), C('Company'), C('Opened'), C('Closed'),
                                         C('Opening', 'num'), C('Cash sales', 'num'), C('Cash refunds', 'num'), C('Handed over', 'num'), C('Expected', 'num'),
                                         C('Counted', 'num'), C('Short (−) / excess (+)', 'num'), C('Status'), C('Reason')], rows,
               totals=_tot(rows, [8, 9, 10, 11, 12, 13, 14]))


@R('cash-handovers', 'Cash', 'Cash handover register', CASH)
def _handovers(f):
    w, p = f.where('h.branch_id')
    rs = db.q(f'''SELECT h.txn_date, h.no, h.branch_id, fu.name frm, cl.name clerk, h.amount, h.counted, h.difference, h.status, h.reason, h.accepted_at FROM cash_handovers h
                  LEFT JOIN users fu ON fu.id=h.from_user LEFT JOIN users cl ON cl.id=h.clerk_id
                  WHERE {w} AND h.company IN :c AND h.txn_date BETWEEN :f AND :t ORDER BY h.txn_date, h.id''', c=f.cos, f=f.frm, t=f.to, **p)
    rows = [[str(r['txn_date']), r['no'], r['branch_id'], r['frm'] or '', r['clerk'] or '', float(r['amount'] or 0), float(r['counted'] or 0) if r['counted'] is not None else None,
             float(r['difference'] or 0), r['status'], str(r['accepted_at'] or '')[:16], r['reason'] or ''] for r in rs]
    return out('Cash handover register', [C('Date', 'date'), C('Handover'), C('Location'), C('From'), C('To (clerk)'), C('Amount', 'num'), C('Counted', 'num'),
                                          C('Difference', 'num'), C('Status'), C('Accepted'), C('Reason')], rows, totals=_tot(rows, [5, 6, 7]))


@R('owner-instructions', 'Cash', 'Owner instruction register', CASH)
def _instr(f):
    rs = db.q('''SELECT DATE(o.created_at) d, o.no, o.company, o.type, cl.name clerk, o.amount, o.recipient, o.purpose, o.status, o.executed_at, vb.name verified, o.journal_no
                 FROM owner_instructions o LEFT JOIN users cl ON cl.id=o.clerk_id LEFT JOIN users vb ON vb.id=o.verified_by
                 WHERE o.company IN :c AND DATE(o.created_at) BETWEEN :f AND :t ORDER BY o.created_at''', c=f.cos, f=f.frm, t=f.to)
    rows = [[str(r['d']), r['no'], r['company'], r['type'], r['clerk'] or '', float(r['amount'] or 0), r['recipient'] or '', r['purpose'] or '', r['status'],
             str(r['executed_at'] or '')[:16], r['verified'] or '', r['journal_no'] or ''] for r in rs]
    return out('Owner instruction register', [C('Date', 'date'), C('Instruction'), C('Company'), C('Type'), C('Clerk'), C('Amount', 'num'), C('Recipient'), C('Purpose'),
                                              C('Status'), C('Executed'), C('Verified by'), C('Voucher')], rows, totals=_tot(rows, [5]))


# ================================================================== MSEA & ONLINE
@R('msea-vouchers', 'MSEA & online', 'MSEA voucher utilisation', MSEA)
def _msea(f):
    rs = db.q('''SELECT v.no, v.adm_no, s.name, s.class_name, v.company, v.scope, v.max_value, v.used_value, v.valid_to, v.status FROM msea_vouchers v
                 LEFT JOIN students s ON s.adm_no=v.adm_no WHERE v.company IN :c AND DATE(v.created_at) BETWEEN :f AND :t ORDER BY v.created_at''', c=f.cos, f=f.frm, t=f.to)
    t = dt.date.today()
    rows = [[r['no'], r['adm_no'], r['name'] or '', r['class_name'] or '', r['company'], r['scope'], float(r['max_value'] or 0), float(r['used_value'] or 0),
             round(float(r['max_value'] or 0) - float(r['used_value'] or 0), 2), round(float(r['used_value'] or 0) / float(r['max_value']) * 100, 1) if r['max_value'] else None,
             str(r['valid_to'] or ''), 'Expired' if r['valid_to'] and r['valid_to'] < t and r['status'] == 'Active' else r['status']] for r in rs]
    return out('MSEA voucher utilisation', [C('Voucher'), C('Adm. no'), C('Student'), C('Class'), C('Company'), C('Scope'), C('Entitled', 'num'), C('Used', 'num'),
                                            C('Balance', 'num'), C('Used', 'pct'), C('Valid to', 'date'), C('Status')], rows, totals=_tot(rows, [6, 7, 8]))


@R('sponsored-issues', 'MSEA & online', 'Sponsored student issues', MSEA)
def _sponsored(f):
    w, p = f.where('i.branch_id')
    rs = db.q(f'''SELECT i.id, i.inv_date, i.no, i.branch_id, i.adm_no, s.name, s.class_name, i.msea_voucher_no, i.total,
                         (SELECT SUM(qty) FROM invoice_lines WHERE invoice_id=i.id) qty FROM invoices i LEFT JOIN students s ON s.adm_no=i.adm_no
                  WHERE {w} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t AND i.msea_voucher_no IS NOT NULL ORDER BY i.inv_date''', c=f.cos, f=f.frm, t=f.to, **p)
    rows = [[str(r['inv_date']), r['no'], r['branch_id'], r['adm_no'], r['name'] or '', r['class_name'] or '', r['msea_voucher_no'], int(r['qty'] or 0), float(r['total'])] for r in rs]
    return out('Sponsored student issues', [C('Date', 'date'), C('Bill'), C('Location'), C('Adm. no'), C('Student'), C('Class'), C('Voucher'), C('Qty', 'qty'),
                                            C('Value', 'num')], rows, [{'invoice': r['id']} for r in rs], totals=_tot(rows, [7, 8]))


@R('online-orders', 'MSEA & online', 'Online orders by status', ONL)
def _online(f):
    st = f.get('status')
    rs = db.q('''SELECT o.no, o.placed_at, o.adm_no, s.name, o.mobile, o.warehouse_branch, o.collect_at, o.total, o.status, o.invoice_no,
                        (SELECT SUM(qty) FROM online_order_lines WHERE order_id=o.id) qty FROM online_orders o LEFT JOIN students s ON s.adm_no=o.adm_no
                 WHERE o.company IN :c AND DATE(o.placed_at) BETWEEN :f AND :t AND (:st IS NULL OR o.status=:st) ORDER BY o.placed_at''', c=f.cos, f=f.frm, t=f.to, st=st)
    rows = [[str(r['placed_at'] or '')[:16], r['no'], r['adm_no'], r['name'] or '', r['mobile'] or '', r['warehouse_branch'] or '', str(r['collect_at'] or ''),
             int(r['qty'] or 0), float(r['total'] or 0), r['status'], r['invoice_no'] or ''] for r in rs]
    summ = defaultdict(lambda: [0, 0.0])
    for r in rows:
        summ[r[9]][0] += 1
        summ[r[9]][1] += r[8]
    return out('Online orders' + (f' · {st}' if st else ''), [C('Placed'), C('Order'), C('Adm. no'), C('Student'), C('Mobile'), C('Warehouse'), C('Collect at'),
                                                              C('Qty', 'qty'), C('Total', 'num'), C('Status'), C('Bill')], rows, totals=_tot(rows, [7, 8]),
               note=' · '.join(f'{k}: {v[0]} (₹{v[1]:,.0f})' for k, v in sorted(summ.items())) or None)


@R('payment-links', 'MSEA & online', 'Payment links and refunds', ONL)
def _links(f):
    rs = db.q('''SELECT DATE(l.created_at) d, l.id, l.company, l.purpose, l.adm_no, l.invoice_no, l.amount, l.paid, l.status, l.paid_at, l.expires_on, u.name by_name
                 FROM payment_links l LEFT JOIN users u ON u.id=l.created_by WHERE l.company IN :c AND DATE(l.created_at) BETWEEN :f AND :t ORDER BY l.created_at''',
              c=f.cos, f=f.frm, t=f.to)
    rows = [['Link', str(r['d']), r['id'], r['company'], r['purpose'] or '', r['adm_no'] or r['invoice_no'] or '', float(r['amount'] or 0), float(r['paid'] or 0), r['status'],
             str(r['paid_at'] or r['expires_on'] or '')[:16], r['by_name'] or ''] for r in rs]
    for r in db.q('''SELECT DATE(f.created_at) d, f.no, o.company, f.request_no, o.no ord, f.amount, f.status, f.completed_at, f.original_payment FROM refunds f
                     JOIN online_orders o ON o.id=f.order_id WHERE o.company IN :c AND DATE(f.created_at) BETWEEN :f AND :t ORDER BY f.created_at''', c=f.cos, f=f.frm, t=f.to):
        rows.append(['Refund', str(r['d']), r['no'], r['company'], f"Order {r['ord']}" + (f" · request {r['request_no']}" if r['request_no'] else ''), r['original_payment'] or '',
                     -float(r['amount'] or 0), -float(r['amount'] or 0) if r['status'] == 'Refunded' else 0.0, r['status'], str(r['completed_at'] or '')[:16], ''])
    return out('Payment links and refunds', [C('Kind'), C('Date', 'date'), C('Link / refund'), C('Company'), C('Purpose'), C('Student / bill / payment'), C('Amount', 'num'),
                                             C('Collected / refunded', 'num'), C('Status'), C('Paid / done / expires'), C('By')], rows, totals=_tot(rows, [6, 7]))


# ================================================================== CONTROLS
@R('approvals-register', 'Controls', 'Approvals register (with turnaround)', (('view', 'appr'), ('view', 'audit')))
def _approvals(f):
    rs = db.q('''SELECT a.no, a.kind, a.title, a.company, a.branch_id, a.amount, a.required_role, a.status, a.requested_at, a.decided_at, a.escalated_at,
                        rq.name req, dc.name dec_ FROM approvals a LEFT JOIN users rq ON rq.id=a.requested_by LEFT JOIN users dc ON dc.id=a.decided_by
                 WHERE (a.company IN :c OR a.company IS NULL) AND DATE(a.requested_at) BETWEEN :f AND :t ORDER BY a.requested_at''', c=f.cos, f=f.frm, t=f.to)
    now = dt.datetime.now()
    rows = [[str(r['requested_at'])[:16], r['no'], r['kind'], r['title'], r['company'] or '', r['branch_id'] or '', float(r['amount'] or 0), r['req'] or '', r['required_role'],
             r['status'], r['dec_'] or '', str(r['decided_at'] or '')[:16], round(((r['decided_at'] or now) - r['requested_at']).total_seconds() / 3600, 1),
             'Yes' if r['escalated_at'] else ''] for r in rs]
    pend = [r for r in rows if r[9] == 'Pending']
    done = [r[12] for r in rows if r[9] != 'Pending']
    return out('Approvals register', [C('Requested'), C('Approval'), C('Kind'), C('Title'), C('Company'), C('Location'), C('Amount', 'num'), C('Requested by'),
                                      C('Needs'), C('Status'), C('Decided by'), C('Decided'), C('Hours (to decide / waiting)', 'num'), C('Escalated')], rows,
               note=f'{len(pend)} pending · average turnaround {round(sum(done) / len(done), 1) if done else 0} hours for {len(done)} decided')


@R('audit-trail', 'Controls', 'Audit trail', CTL)
def _audit(f):
    q, cat, who = f.get('q'), f.get('category'), f.get('user')
    rs = db.q('''SELECT at, user_name, role, company, branch_id, action, ref, old_value, new_value, reason, category, ip FROM audit_log
                 WHERE (company IN :c OR company IS NULL) AND DATE(at) BETWEEN :f AND :t AND (:cat IS NULL OR category=:cat) AND (:w IS NULL OR user_name LIKE :wl)
                 AND (:q IS NULL OR action LIKE :ql OR ref LIKE :ql OR reason LIKE :ql) ORDER BY id DESC LIMIT 5000''',
              c=f.cos, f=f.frm, t=f.to, cat=cat, w=who, wl=f'%{who}%', q=q, ql=f'%{q}%')
    short = lambda v: (str(v)[:120] + '…') if v and len(str(v)) > 120 else (v or '')  # noqa: E731
    rows = [[str(r['at'])[:19], r['user_name'] or '', r['role'] or '', r['company'] or '', r['branch_id'] or '', r['action'], r['ref'] or '', short(r['old_value']),
             short(r['new_value']), r['reason'] or '', r['category'] or '', r['ip'] or ''] for r in rs]
    return out('Audit trail', [C('When'), C('User'), C('Role'), C('Company'), C('Location'), C('Action'), C('Reference'), C('Before'), C('After'), C('Reason'), C('Area'),
                               C('IP')], rows, note='Newest first, up to 5,000 rows; narrow with the search, user and area filters. The trail cannot be edited or deleted.')


@R('signin-activity', 'Controls', 'User sign-in activity', (('view', 'ctl.users'), ('view', 'audit')))
def _signins(f):
    rs = db.q('''SELECT e.at, e.user_name, u.emp_id, u.main_role, e.type, e.detail, e.device, e.ip FROM security_events e LEFT JOIN users u ON u.id=e.user_id
                 WHERE DATE(e.at) BETWEEN :f AND :t ORDER BY e.id DESC LIMIT 5000''', f=f.frm, t=f.to)
    rows = [[str(r['at'])[:19], r['user_name'] or '', r['emp_id'] or '', r['main_role'] or '', r['type'], r['detail'] or '', r['device'] or '', r['ip'] or ''] for r in rs]
    fails = sum(1 for r in rows if 'Failed' in r[4] or 'locked' in r[4].lower())
    return out('User sign-in activity', [C('When'), C('User'), C('Emp. ID'), C('Role'), C('Event'), C('Detail'), C('Device'), C('IP')], rows,
               note=f'{len(rows)} events · {fails} failed sign-ins or lock-outs')


@R('price-history', 'Controls', 'Price change history', (('view', 'inv'), ('view', 'audit')))
def _prices(f):
    rs = db.q('''SELECT a.at, a.user_name, a.company, a.ref, it.name, a.old_value, a.new_value, a.reason, a.status FROM audit_log a LEFT JOIN items it ON it.id=a.ref
                 WHERE a.action='Price changed' AND (a.company IN :c OR a.company IS NULL) AND DATE(a.at) BETWEEN :f AND :t ORDER BY a.id DESC''', c=f.cos, f=f.frm, t=f.to)

    def num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    rows = []
    for r in rs:
        o, n = num(r['old_value']), num(r['new_value'])
        rows.append([str(r['at'])[:16], r['ref'], r['name'] or '', o, n, round((n - o) / o * 100, 1) if o and n is not None else None, r['reason'] or '', r['user_name'] or '',
                     r['status'] or ''])
    return out('Price change history', [C('When'), C('Item'), C('Name'), C('Old price', 'num'), C('New price', 'num'), C('Change', 'pct'), C('Reason'), C('Approved by'),
                                        C('Status')], rows)


# ================================================================== FINANCIAL STATEMENTS (from the finance library, per company)
def _fin(key, title, extra=None):
    def fn(f):
        from . import fin_reports
        co = f.company or f.cos[0]
        args = {'from': str(f.frm), 'to': str(f.to), **({'branch': f.branch} if f.branch else {}), **(extra or {}), **{k: v for k, v in f.args.items() if k in ('account', 'kind')}}
        res = fin_reports.run(key, co, args)
        res['title'] = f'{res["title"]} · {co}'
        res['company'] = co
        res['drill'] = [d if d and d.get('report') in ('gl', 'party', 'ageing') else None for d in res['drill']]
        res['drill'] = [{'report': {'gl': 'ledger', 'party': 'ledger', 'ageing': 'receivables-ageing'}[d['report']], 'args': d.get('args', {})} if d else None for d in res['drill']]
        return res
    return fn


for _k, _fk, _t, _x, _per in (('trial-balance', 'tb', 'Trial balance', None, True), ('profit-loss', 'pl', 'Profit and loss', None, True),
                              ('balance-sheet', 'bs', 'Balance sheet', None, False), ('day-book', 'day', 'Day book', None, True),
                              ('ledger', 'gl', 'Account ledger', None, True), ('cash-book', 'cash', 'Cash book', None, True),
                              ('receivables-ageing', 'ageing', 'Receivables ageing', {'kind': 'cust'}, False), ('payables-ageing', 'ageing', 'Payables ageing', {'kind': 'sup'}, False),
                              ('gst-summary', 'gliab', 'GST liability summary', None, True)):
    R(_k, 'Financial', _t, (), fin=True, period=_per)(_fin(_fk, _t, _x))
