"""Reports and dashboard. Every report accepts ?company=KG|ZE (default: all the user's companies) and returns JSON,
or CSV with ?format=csv. Financial reports need 'fin' on Reports; stock value needs 'cost'.

    /api/dashboard                          tiles for the signed-in user
    /api/reports/sales-register             invoices in a period
    /api/reports/sales-summary?by=day|branch|category|cashier|mode
    /api/reports/item-sales                 quantity and value by item
    /api/reports/stock-valuation            on-hand x moving-average cost by branch
    /api/reports/gst                        output tax by rate, input credit, net payable (period)
    /api/reports/trial-balance?to=
    /api/reports/profit-loss?from=&to=
    /api/reports/balance-sheet?to=
    /api/reports/ledger?account=&branch=&party=&from=&to=
    /api/reports/day-book?date=
    /api/reports/receivables-ageing  /payables-ageing
    /api/reports/online-orders
"""
import csv
import datetime as dt
import io
from decimal import Decimal

from flask import Blueprint, Response, g, jsonify, request

from .. import db
from ..core.rbac import can, login_required, require, scope_sql
from ..errors import bad, denied
from ..services.common import parse_date

bp = Blueprint('reports', __name__, url_prefix='/api')
GROUP_NAMES = {'A': 'Assets', 'L': 'Liabilities', 'E': 'Equity', 'I': 'Income', 'X': 'Expenses'}


def cos():
    c = request.args.get('company')
    if c:
        if c not in g.user['companies']:
            raise denied('You do not have access to this company.', 'company_denied')
        return (c,)
    return tuple(g.user['companies']) or ('',)


def period():
    t = dt.date.today()
    f = parse_date(request.args.get('from'), t.replace(day=1), 'From')
    to = parse_date(request.args.get('to'), t, 'To')
    if f > to:
        raise bad('From date is after the To date.', 'bad_date')
    return f, to


def out(rows, columns=None):
    rows = db.clean(rows)
    if request.args.get('format') == 'csv':
        buf = io.StringIO()
        cols = columns or (list(rows[0].keys()) if rows else [])
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction='ignore')
        w.writeheader()
        for r in rows:
            w.writerow(r)
        name = request.path.rsplit('/', 1)[-1]
        db.insert('access_log', dict(user_id=g.user['id'], action='export', what=f'Report {name}', detail=str(dict(request.args))[:300]))
        return Response(buf.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={name}-{dt.date.today()}.csv'})
    return jsonify(rows)


def need_fin():
    if not can(g.user, 'fin', 'rep'):
        raise denied('Financial reports need the "financial figures" permission.', 'forbidden')


# ---------------------------------------------------------------- dashboard
@bp.get('/dashboard')
@login_required
def dashboard():
    u = g.user
    where, p = scope_sql(u, 'branch_id')
    c = tuple(u['companies']) or ('',)
    today = dt.date.today()
    tiles = {}
    if can(u, 'view', 'pos') or can(u, 'view', 'rep'):
        rows = db.q(f'''SELECT company, SUM(CASE WHEN inv_date=:t THEN total ELSE 0 END) today, SUM(total) mtd, COUNT(CASE WHEN inv_date=:t THEN 1 END) bills
                        FROM invoices WHERE {where} AND company IN :c AND inv_date >= :m AND status <> 'Cancelled' GROUP BY company''',
                    t=today, m=today.replace(day=1), c=c, **p)
        tiles['sales'] = db.clean(rows)
        cn = db.q(f'''SELECT i.company, SUM(n.amount) amt FROM credit_notes n JOIN invoices i ON i.id=n.invoice_id WHERE {where.replace('branch_id', 'i.branch_id')}
                      AND n.cn_date >= :m AND i.company IN :c GROUP BY i.company''', m=today.replace(day=1), c=c, **p)
        tiles['returns_mtd'] = db.clean(cn)
        # 2.8: same days of last month, per-location split, collections today and gross profit month to date
        lm_end = today.replace(day=1) - dt.timedelta(days=1)
        lm_from = lm_end.replace(day=1)
        lm_to = lm_from.replace(day=min(today.day, lm_end.day))
        tiles['prev_mtd'] = db.clean(db.q(f'''SELECT company, SUM(total) amt FROM invoices WHERE {where} AND company IN :c AND inv_date BETWEEN :f AND :t
                                             AND status <> 'Cancelled' GROUP BY company''', f=lm_from, t=lm_to, c=c, **p))
        tiles['by_branch'] = db.clean(db.q(f'''SELECT i.branch_id, i.company, COALESCE(l.name, i.branch_id) name,
                                                  SUM(CASE WHEN i.inv_date=:t THEN i.total ELSE 0 END) today, SUM(i.total) mtd,
                                                  COUNT(CASE WHEN i.inv_date=:t THEN 1 END) bills
                                               FROM invoices i LEFT JOIN branches b ON b.id=i.branch_id LEFT JOIN locations l ON l.code=b.location
                                               WHERE {where.replace('branch_id', 'i.branch_id')} AND i.company IN :c AND i.inv_date >= :m AND i.status <> 'Cancelled'
                                               GROUP BY i.branch_id, i.company, l.name ORDER BY mtd DESC''', t=today, m=today.replace(day=1), c=c, **p))
        paid = db.val(f'''SELECT COALESCE(SUM(pm.amount),0) FROM invoice_payments pm JOIN invoices i ON i.id=pm.invoice_id
                          WHERE {where.replace('branch_id', 'i.branch_id')} AND i.company IN :c AND i.inv_date=:t AND i.status <> 'Cancelled'
                          AND pm.mode <> 'credit' ''', t=today, c=c, **p) or 0
        rcpt = db.val(f'''SELECT COALESCE(SUM(amount),0) FROM receipts WHERE {where} AND company IN :c AND rcpt_date=:t''', t=today, c=c, **p) or 0
        tiles['collections_today'] = float(paid) + float(rcpt)
        if can(u, 'cost', 'inv') or can(u, 'fin', 'rep'):
            gp = db.one(f'''SELECT COALESCE(SUM(l.taxable),0) taxable, COALESCE(SUM(l.taxable - l.qty*COALESCE(l.unit_cost,0)),0) gp
                            FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id
                            WHERE {where.replace('branch_id', 'i.branch_id')} AND i.company IN :c AND i.inv_date >= :m AND i.status <> 'Cancelled' ''',
                         m=today.replace(day=1), c=c, **p)
            tiles['gp_mtd'] = db.clean(gp)
    if can(u, 'view', 'appr'):
        tiles['pending_approvals'] = db.val("SELECT COUNT(*) FROM approvals WHERE status='Pending'")
    if can(u, 'view', 'inv'):
        tiles['low_stock'] = db.val(f'''SELECT COUNT(*) FROM stock_balance s JOIN items i ON i.id=s.item_id WHERE {where.replace('branch_id', 's.branch_id')}
                                       AND i.reorder_level > 0 AND s.qty <= i.reorder_level''', **p)
        if can(u, 'cost', 'inv'):
            tiles['stock_value'] = db.clean(db.q(f'''SELECT b.company, ROUND(SUM(s.qty*i.cost),2) value FROM stock_balance s JOIN items i ON i.id=s.item_id
                                                    JOIN branches b ON b.id=s.branch_id WHERE {where.replace('branch_id', 's.branch_id')} AND b.company IN :c
                                                    GROUP BY b.company''', c=c, **p))
    if can(u, 'fin', 'rep') or can(u, 'view', 'acc'):
        tiles['balances'] = db.clean(db.q('''SELECT j.company, l.account, ROUND(SUM(l.debit-l.credit),2) bal FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                                             WHERE j.company IN :c AND l.account IN ('1100','1120','1200','1210','2000','1115') GROUP BY j.company, l.account''', c=c))
    if can(u, 'view', 'onl'):
        tiles['online'] = db.clean(db.q('SELECT status, COUNT(*) n FROM online_orders WHERE company IN :c AND placed_at >= :d GROUP BY status',
                                        c=c, d=today - dt.timedelta(days=30)))
    if can(u, 'view', 'ctl.sec'):
        tiles['security_events_today'] = db.val('SELECT COUNT(*) FROM security_events WHERE at >= CURDATE()')
    trend = db.q(f'''SELECT inv_date d, company, SUM(total) t FROM invoices WHERE {where} AND company IN :c AND inv_date >= :f AND status <> 'Cancelled'
                     GROUP BY inv_date, company ORDER BY inv_date''', c=c, f=today - dt.timedelta(days=29), **p) if 'sales' in tiles else []
    tiles['trend'] = db.clean(trend)
    return jsonify(tiles)


# ---------------------------------------------------------------- my work (2.5): what is waiting for this user, with a link to each
@bp.get('/my-work')
@login_required
def my_work():
    """Small counts of the things waiting for the signed-in user. Each item says what to do and links to the screen that does it.
    Only work the user can act on is counted (same rules as the screens)."""
    from ..core import approvals as appr
    from ..services import online
    u = g.user
    me = u['id']
    c = tuple(u['companies']) or ('',)
    out = []

    def add(key, label, n, to, tone='warn', hint=None):
        if n:
            out.append({'key': key, 'label': label, 'count': int(n), 'to': to, 'tone': tone, 'hint': hint})

    if can(u, 'view', 'appr'):
        pend = db.q("SELECT * FROM approvals WHERE status='Pending' ORDER BY id DESC LIMIT 300")
        mine = [a for a in pend if (not a['branch_id'] or u['branches'] == '*' or a['branch_id'] in u['branches']) and appr.why_not(u, a) is None]
        add('approvals', 'Approvals waiting for your decision', len(mine), '/approvals', 'bad' if any(a.get('escalated_at') for a in mine) else 'warn')
    if can(u, 'add', 'pos'):
        sh = db.one("SELECT no, opened_at FROM cash_shifts WHERE user_id=:u AND status='Open' ORDER BY id DESC LIMIT 1", u=me)
        if sh and sh['opened_at'] and sh['opened_at'].date() < dt.date.today():
            add('old_shift', f"Shift {sh['no']} is still open from {sh['opened_at']:%d %b}. Close it before billing today.", 1, '/pos', 'bad')
    if can(u, 'view', 'saudit'):
        n = db.val("""SELECT COUNT(*) FROM stock_audits WHERE status IN ('Draft','In Progress') AND JSON_CONTAINS(auditors, JSON_QUOTE(:m))""", m=me)
        add('count', 'Stock audits for you to count', n, '/stock-audits')
        n = db.val("SELECT COUNT(*) FROM stock_audits WHERE status='Submitted' AND supervisor_id=:m", m=me)
        add('verify', 'Stock audits for you to verify', n, '/stock-audits')
    if can(u, 'view', 'inv'):
        w, p = scope_sql(u, 't.to_branch')
        add('receive_transfer', 'Transfers arriving at your location — receive them', db.val(f"SELECT COUNT(*) FROM transfers t WHERE {w} AND t.status='In transit'", **p),
            '/transfers', 'info')
    if can(u, 'view', 'pur') and can(u, 'edit', 'inv'):
        w, p = scope_sql(u, 'o.branch_id')
        add('grn', 'Purchase orders to receive goods against', db.val(f"""SELECT COUNT(*) FROM purchase_orders o WHERE {w} AND o.company IN :c
                                                                         AND o.status IN ('PO approved','Partly received')""", c=c, **p), '/purchasing', 'info')
    if can(u, 'view', 'cash'):
        add('handover', 'Cash handovers for you to count and accept',
            db.val("SELECT COUNT(*) FROM cash_handovers WHERE clerk_id=:m AND status='Awaiting clerk'", m=me), '/cash')
    if can(u, 'view', 'onl'):
        step_of = {'Payment Confirmed': 'pick', 'Picking': 'pick', 'Quality Check': 'qc', 'Packed': 'dispatch', 'Dispatched to Branch': 'branch'}
        roles = set(u['roles'])
        mine = [s for s, st in step_of.items() if roles & (online.STEP_ROLES.get(st, set()) | online.ALL_STEPS)]
        if mine:
            add('online', 'Online orders waiting for your step', db.val('SELECT COUNT(*) FROM online_orders WHERE company IN :c AND status IN :s', c=c, s=tuple(mine)),
                '/online', 'info')
    if can(u, 'view', 'pos'):
        w, p = scope_sql(u, 'i.branch_id')
        add('deliver', 'Bills with items still to hand over', db.val(f"""SELECT COUNT(DISTINCT i.id) FROM invoices i JOIN invoice_lines l ON l.invoice_id=i.id
                                                                     WHERE {w} AND i.company IN :c AND i.status<>'Cancelled'
                                                                     AND l.delivered_qty < l.qty - l.returned_qty""", c=c, **p),
            '/invoices?tab=pending', 'info')
    if can(u, 'view', 'fin'):
        add('drafts', 'Your finance vouchers still in draft', db.val("SELECT COUNT(*) FROM fin_vouchers WHERE maker=:m AND status='Draft'", m=me), '/finance', 'info')
    if can(u, 'view', 'inv'):
        w, p = scope_sql(u, 's.branch_id')
        add('low', 'Items at or below reorder level', db.val(f"""SELECT COUNT(*) FROM stock_balance s JOIN items i ON i.id=s.item_id WHERE {w}
                                                               AND i.reorder_level > 0 AND s.qty <= i.reorder_level""", **p),
            '/reports?r=low-stock' if can(u, 'view', 'rep') else '/stock?low=1', 'info')
    return jsonify(out)


# ---------------------------------------------------------------- sales
@bp.get('/reports/sales-register')
@require('view', 'rep')
def sales_register():
    f, t = period()
    where, p = scope_sql(g.user, 'i.branch_id')
    rows = db.q(f'''SELECT i.no, i.inv_date, i.inv_time, i.company, i.branch_id, i.channel, i.adm_no, c.name customer, i.taxable, i.cgst, i.sgst, i.igst,
                           i.round_off, i.total, i.balance, i.status, u.name cashier
                    FROM invoices i LEFT JOIN customers c ON c.id=i.customer_id LEFT JOIN users u ON u.id=i.cashier_id
                    WHERE {where} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t ORDER BY i.inv_date, i.id''', c=cos(), f=f, t=t, **p)
    return out(rows)


@bp.get('/reports/sales-summary')
@require('view', 'rep')
def sales_summary():
    f, t = period()
    by = request.args.get('by', 'day')
    where, p = scope_sql(g.user, 'i.branch_id')
    if by == 'mode':
        rows = db.q(f'''SELECT i.company, pmt.mode, COUNT(DISTINCT i.id) bills, SUM(pmt.amount) amount FROM invoice_payments pmt JOIN invoices i ON i.id=pmt.invoice_id
                        WHERE {where} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t GROUP BY i.company, pmt.mode ORDER BY i.company, amount DESC''',
                    c=cos(), f=f, t=t, **p)
        return out(rows)
    if by == 'category':
        rows = db.q(f'''SELECT i.company, it.category, SUM(l.qty - l.returned_qty) qty, SUM(l.amount * (l.qty - l.returned_qty) / l.qty) amount
                        FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id JOIN items it ON it.id=l.item_id
                        WHERE {where} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t GROUP BY i.company, it.category ORDER BY amount DESC''',
                    c=cos(), f=f, t=t, **p)
        return out(rows)
    col = {'day': 'i.inv_date', 'branch': 'i.branch_id', 'cashier': 'u.name'}.get(by)
    if not col:
        raise bad('by must be day, branch, category, cashier or mode.', 'invalid')
    rows = db.q(f'''SELECT i.company, {col} AS `group`, COUNT(*) bills, SUM(i.taxable) taxable, SUM(i.cgst+i.sgst+i.igst) tax, SUM(i.total) total
                    FROM invoices i LEFT JOIN users u ON u.id=i.cashier_id WHERE {where} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t
                    AND i.status <> 'Cancelled' GROUP BY i.company, {col} ORDER BY i.company, {col}''', c=cos(), f=f, t=t, **p)
    return out(rows)


@bp.get('/reports/item-sales')
@require('view', 'rep')
def item_sales():
    f, t = period()
    where, p = scope_sql(g.user, 'i.branch_id')
    cost = ', SUM(l.unit_cost * (l.qty - l.returned_qty)) cost' if can(g.user, 'cost', 'rep') else ''
    rows = db.q(f'''SELECT l.item_id, it.name, it.category, it.size, SUM(l.qty - l.returned_qty) qty, SUM(l.taxable * (l.qty - l.returned_qty) / l.qty) taxable{cost}
                    FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id JOIN items it ON it.id=l.item_id
                    WHERE {where} AND i.company IN :c AND i.inv_date BETWEEN :f AND :t GROUP BY l.item_id, it.name, it.category, it.size ORDER BY qty DESC''',
                c=cos(), f=f, t=t, **p)
    return out(rows)


# ---------------------------------------------------------------- stock
@bp.get('/reports/stock-valuation')
@require('view', 'rep')
def stock_valuation():
    if not can(g.user, 'cost', 'rep') and not can(g.user, 'cost', 'inv'):
        raise denied('Stock value needs the "cost" permission.', 'forbidden')
    where, p = scope_sql(g.user, 's.branch_id')
    detail = request.args.get('detail') == '1'
    if detail:
        rows = db.q(f'''SELECT s.branch_id, i.id item_id, i.name, i.category, s.qty, i.cost, ROUND(s.qty*i.cost,2) value FROM stock_balance s
                        JOIN items i ON i.id=s.item_id JOIN branches b ON b.id=s.branch_id WHERE {where} AND b.company IN :c AND s.qty <> 0
                        ORDER BY s.branch_id, i.category, i.name''', c=cos(), **p)
    else:
        rows = db.q(f'''SELECT b.company, s.branch_id, i.category, SUM(s.qty) qty, ROUND(SUM(s.qty*i.cost),2) value FROM stock_balance s
                        JOIN items i ON i.id=s.item_id JOIN branches b ON b.id=s.branch_id WHERE {where} AND b.company IN :c
                        GROUP BY b.company, s.branch_id, i.category ORDER BY b.company, s.branch_id, i.category''', c=cos(), **p)
    return out(rows)


# ---------------------------------------------------------------- tax and books
@bp.get('/reports/gst')
@require('view', 'rep')
def gst():
    need_fin()
    f, t = period()
    sales_rows = db.q('''SELECT i.company, l.gst_rate, SUM(l.taxable) taxable, SUM(l.tax) tax FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id
                         WHERE i.company IN :c AND i.inv_date BETWEEN :f AND :t GROUP BY i.company, l.gst_rate''', c=cos(), f=f, t=t)
    ret_rows = db.q('''SELECT company, SUM(taxable) taxable, SUM(tax) tax FROM credit_notes WHERE company IN :c AND cn_date BETWEEN :f AND :t GROUP BY company''',
                    c=cos(), f=f, t=t)
    itc = db.q('''SELECT j.company, SUM(l.debit - l.credit) itc FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                  WHERE j.company IN :c AND l.account='1310' AND j.txn_date BETWEEN :f AND :t GROUP BY j.company''', c=cos(), f=f, t=t)
    res = []
    for co in cos():
        out_tax = sum((r['tax'] for r in sales_rows if r['company'] == co), Decimal(0))
        ret_tax = sum((r['tax'] or 0 for r in ret_rows if r['company'] == co), Decimal(0))
        credit = sum((r['itc'] or 0 for r in itc if r['company'] == co), Decimal(0))
        res.append({'company': co, 'from': str(f), 'to': str(t), 'by_rate': db.clean([r for r in sales_rows if r['company'] == co]),
                    'output_tax': float(out_tax), 'returns_tax': float(ret_tax), 'input_credit': float(credit), 'net_payable': float(out_tax - ret_tax - credit)})
    return jsonify(res)


def _balances(to=None, frm=None, closing=True):
    sql = '''SELECT j.company, l.account, a.name, a.grp, SUM(l.debit) dr, SUM(l.credit) cr FROM journal_lines l JOIN journals j ON j.id=l.journal_id
             LEFT JOIN accounts a ON a.company=j.company AND a.code=l.account WHERE j.company IN :c'''
    p = {'c': cos()}
    if to:
        sql += ' AND j.txn_date <= :t'
        p['t'] = to
    if frm:
        sql += ' AND j.txn_date >= :f'
        p['f'] = frm
    if request.args.get('branch'):
        sql += ' AND l.branch_id=:b'
        p['b'] = request.args['branch']
    if not closing:                          # P&L of a period: leave out year-end closing journals (2.6)
        sql += " AND j.type NOT IN ('Year closing', 'Year closing reversal')"
    return db.q(sql + ' GROUP BY j.company, l.account, a.name, a.grp ORDER BY j.company, l.account', **p)


@bp.get('/reports/trial-balance')
@require('view', 'rep')
def trial_balance():
    need_fin()
    to = parse_date(request.args.get('to'))
    rows = []
    for r in _balances(to):
        net = (r['dr'] or 0) - (r['cr'] or 0)
        rows.append({'company': r['company'], 'account': r['account'], 'name': r['name'], 'group': GROUP_NAMES.get(r['grp'], r['grp']),
                     'debit': float(net) if net > 0 else 0.0, 'credit': float(-net) if net < 0 else 0.0})
    return out(rows)


@bp.get('/reports/profit-loss')
@require('view', 'rep')
def profit_loss():
    need_fin()
    f, t = period()
    res = []
    for co in cos():
        inc, exp = [], []
        for r in _balances(t, f, closing=False):
            if r['company'] != co:
                continue
            if r['grp'] == 'I':
                inc.append({'account': r['account'], 'name': r['name'], 'amount': float((r['cr'] or 0) - (r['dr'] or 0))})
            elif r['grp'] == 'X':
                exp.append({'account': r['account'], 'name': r['name'], 'amount': float((r['dr'] or 0) - (r['cr'] or 0))})
        ti, te = sum(x['amount'] for x in inc), sum(x['amount'] for x in exp)
        res.append({'company': co, 'from': str(f), 'to': str(t), 'income': inc, 'expenses': exp, 'total_income': round(ti, 2), 'total_expenses': round(te, 2),
                    'net_profit': round(ti - te, 2)})
    return jsonify(res)


@bp.get('/reports/balance-sheet')
@require('view', 'rep')
def balance_sheet():
    need_fin()
    to = parse_date(request.args.get('to'))
    res = []
    for co in cos():
        rows = [r for r in _balances(to) if r['company'] == co]
        assets = [{'account': r['account'], 'name': r['name'], 'amount': float((r['dr'] or 0) - (r['cr'] or 0))} for r in rows if r['grp'] == 'A']
        liab = [{'account': r['account'], 'name': r['name'], 'amount': float((r['cr'] or 0) - (r['dr'] or 0))} for r in rows if r['grp'] == 'L']
        eq = [{'account': r['account'], 'name': r['name'], 'amount': float((r['cr'] or 0) - (r['dr'] or 0))} for r in rows if r['grp'] == 'E']
        profit = sum(float((r['cr'] or 0) - (r['dr'] or 0)) for r in rows if r['grp'] in ('I', 'X'))
        ta = round(sum(x['amount'] for x in assets), 2)
        tl = round(sum(x['amount'] for x in liab) + sum(x['amount'] for x in eq) + profit, 2)
        res.append({'company': co, 'as_of': str(to), 'assets': assets, 'liabilities': liab, 'equity': eq, 'profit_to_date': round(profit, 2),
                    'total_assets': ta, 'total_liabilities_equity': tl, 'balanced': abs(ta - tl) < 0.01})
    return jsonify(res)


@bp.get('/reports/ledger')
@require('view', 'rep')
def ledger_report():
    need_fin()
    f, t = period()
    acct = request.args.get('account')
    if not acct:
        raise bad('Choose the account.', 'invalid')
    sql = '''SELECT j.company, j.txn_date, j.no, j.type, j.ref, j.narration, l.branch_id, l.party, l.debit, l.credit FROM journal_lines l
             JOIN journals j ON j.id=l.journal_id WHERE j.company IN :c AND l.account=:a'''
    p = {'c': cos(), 'a': acct}
    for k in ('branch', 'party'):
        if request.args.get(k):
            sql += f' AND l.{"branch_id" if k == "branch" else "party"}=:{k}'
            p[k] = request.args[k]
    opening = db.val(sql.replace('SELECT j.company, j.txn_date, j.no, j.type, j.ref, j.narration, l.branch_id, l.party, l.debit, l.credit',
                                 'SELECT COALESCE(SUM(l.debit-l.credit),0)') + ' AND j.txn_date < :f', f=f, **p) or 0
    rows = db.q(sql + ' AND j.txn_date BETWEEN :f AND :t ORDER BY j.txn_date, j.id', f=f, t=t, **p)
    bal = Decimal(str(opening))
    for r in rows:
        bal += (r['debit'] or 0) - (r['credit'] or 0)
        r['balance'] = bal
    if request.args.get('format') == 'csv':
        return out(rows)
    return jsonify({'account': acct, 'opening': float(opening), 'closing': float(bal), 'rows': db.clean(rows)})


@bp.get('/reports/day-book')
@require('view', 'rep')
def day_book():
    need_fin()
    d = parse_date(request.args.get('date'))
    rows = db.q('''SELECT j.company, j.no, j.type, j.ref, j.narration, SUM(l.debit) amount, u.name created_by FROM journals j JOIN journal_lines l ON l.journal_id=j.id
                   LEFT JOIN users u ON u.id=j.created_by WHERE j.company IN :c AND j.txn_date=:d GROUP BY j.id ORDER BY j.id''', c=cos(), d=d)
    return out(rows)


@bp.get('/reports/receivables-ageing')
@require('view', 'rep')
def receivables():
    need_fin()
    rows = db.q('''SELECT i.company, c.name customer, i.no, i.inv_date, i.total, i.balance, DATEDIFF(CURDATE(), i.inv_date) days FROM invoices i
                   LEFT JOIN customers c ON c.id=i.customer_id WHERE i.company IN :c AND i.balance > 0 ORDER BY days DESC''', c=cos())
    for r in rows:
        d = r['days']
        r['bucket'] = '0-30' if d <= 30 else '31-60' if d <= 60 else '61-90' if d <= 90 else '90+'
    return out(rows)


@bp.get('/reports/payables-ageing')
@require('view', 'rep')
def payables():
    need_fin()
    rows = db.q('''SELECT b.company, s.name supplier, b.bill_no, b.bill_date, b.due_date, b.amount, b.amount - b.paid - b.adjusted outstanding,
                          DATEDIFF(CURDATE(), b.due_date) overdue_days FROM supplier_bills b JOIN suppliers s ON s.id=b.supplier_id
                   WHERE b.company IN :c AND b.amount - b.paid - b.adjusted > 0 ORDER BY b.due_date''', c=cos())
    return out(rows)


@bp.get('/reports/online-orders')
@require('view', 'rep')
def online_report():
    f, t = period()
    rows = db.q('''SELECT company, status, COUNT(*) orders, SUM(total) value FROM online_orders WHERE company IN :c AND DATE(placed_at) BETWEEN :f AND :t
                   GROUP BY company, status ORDER BY company, status''', c=cos(), f=f, t=t)
    return out(rows)


# ---------------------------------------------------------------- report library (2.4)
# One table shape for every report, so the screen, Print, PDF, Excel and CSV all show the same thing.
#   GET /api/reports/library                         reports the user may open, grouped, with the formats they may use
#   GET /api/reports/library/<key>?company&from&to&branch&…[&format=print|pdf|xlsx|csv]
FORMAT_RIGHT = {'print': 'print', 'pdf': 'pdf', 'xlsx': 'export', 'csv': 'export'}


def _formats(u):
    return [f for f, a in FORMAT_RIGHT.items() if can(u, a, 'rep')]


@bp.get('/reports/library')
@require('view', 'rep')
def report_library():
    from ..services import op_reports
    return jsonify({'reports': op_reports.catalogue(g.user), 'formats': _formats(g.user)})


@bp.get('/reports/library/<key>')
@require('view', 'rep')
def report_library_run(key):
    from ..services import docs_render, op_reports
    fmt = request.args.get('format') or ''
    if fmt and fmt not in FORMAT_RIGHT:
        raise bad('Format must be print, pdf, xlsx or csv.', 'invalid')
    if fmt and not can(g.user, FORMAT_RIGHT[fmt], 'rep'):
        raise denied({'print': 'Your role cannot print reports.', 'pdf': 'Your role cannot download reports as PDF.'}.get(fmt, 'Your role cannot export reports.'),
                     'forbidden')
    rep = op_reports.run(key, request.args.to_dict())
    if not fmt:
        return jsonify(rep)
    what = f"Report {rep['title']} ({fmt.upper()})"
    if fmt == 'print':
        db.insert('access_log', dict(user_id=g.user['id'], action='print', what=what, detail=str(dict(request.args))[:300]))
        return jsonify(rep)
    names = {r['code']: r['name'] for r in db.q('SELECT code, name FROM companies WHERE code IN :c', c=tuple(rep['company'].split(', ')) or ('',))}
    meta_ = {'company_name': ', '.join(names.values()) or rep['company']}
    name = f"{key}-{(request.args.get('company') or 'all')}-{rep['period']['to']}"
    db.insert('access_log', dict(user_id=g.user['id'], action='export', what=what, detail=str(dict(request.args))[:300]))
    if fmt == 'csv':
        data, how, mime = docs_render.csv_bytes(rep), 'python', 'text/csv'
    elif fmt == 'xlsx':
        (data, how), mime = docs_render.xlsx(rep, meta_), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else:
        (data, how), mime = docs_render.pdf(rep, meta_), 'application/pdf'
    return Response(data, mimetype=mime, headers={'Content-Disposition': f'attachment; filename="{name}.{fmt}"', 'X-Rendered-By': how})
