"""Finance & tax: year-end closing (release 2.6).

Closing a financial year is one guided step for the CFO / Director:
  1. Checklist — what must be done first (blocks) and what should be reviewed (warnings, acknowledged at closing): the year has
     ended, earlier years are closed, no draft or pending vouchers in the year, the books balance; GSTR-3B / GSTR-1 filed for every
     month, TDS returns filed and TDS deposited, depreciation run for every month, bank lines reconciled, stock audits finished,
     no cash or bank account with a credit balance.
  2. Preview — income, expenses, the profit or loss, how it is shared between capital / reserve accounts (e.g. one proprietor's
     capital, or partners by their ratio), and the balance sheet the next year opens with.
  3. Close — posts one 'Year closing' journal dated the last day of the year that brings every income and expense account of the
     year to zero and credits the profit (or debits the loss) to the chosen accounts; locks the books to the year end; marks the
     year Closed with a snapshot of the checklist and figures; opens the next year if it does not exist. Password re-entry is needed.
  4. Reopen — Director / Owner only, with a reason and password re-entry: reverses the closing journal, unlocks the year and alerts
     management. A later closed year must be reopened first.

Reports of a period leave out closing journals dated inside it (services/fin_reports.CLOSING_TYPES), so P&L still shows what the
year earned; the balance sheet includes them, so the profit sits in capital / reserves after closing."""
import datetime as dt
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit, notify
from ..core.rbac import can
from ..errors import bad, conflict, denied, not_found
from ..models import Journal
from . import fin_vouchers as fv
from . import ledger
from .common import today
from .fin_common import fy_of, lock_company, month_end, r2, txt
from .fin_reports import CLOSING_TYPES, account_names, bs

REOPENERS = ('Director', 'Owner', 'Chairman', 'Super Admin')
CASH_BANK = ('1100', '1129')


def _year(company, code):
    y = db.one('SELECT * FROM fin_years WHERE company=:c AND code=:f', c=company, f=code)
    if not y:
        raise not_found(f'Financial year {code} not found for {company}.', 'unknown_year')
    return y


def _months(y):
    m, out = y['from_date'].replace(day=1), []
    while m <= y['to_date']:
        out.append(m)
        m = (m + dt.timedelta(days=32)).replace(day=1)
    return out


def _pl_lines(company, y):
    """Income and expense balances of the year by account and location, without earlier closing journals."""
    return db.q('''SELECT l.account, l.branch_id, a.grp, SUM(l.debit) - SUM(l.credit) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                   JOIN accounts a ON a.company=j.company AND a.code=l.account
                   WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b AND a.grp IN ('I','X') AND j.type NOT IN :xt
                   GROUP BY l.account, l.branch_id, a.grp HAVING ABS(SUM(l.debit) - SUM(l.credit)) >= 0.005''',
                c=company, a=y['from_date'], b=y['to_date'], xt=CLOSING_TYPES)


def figures(company, y):
    rows = _pl_lines(company, y)
    inc = r2(sum(-Decimal(str(r['v'])) for r in rows if r['grp'] == 'I'))
    exp = r2(sum(Decimal(str(r['v'])) for r in rows if r['grp'] == 'X'))
    return rows, inc, exp, r2(inc - exp)


# ---------------------------------------------------------------- checklist
def checklist(company, y):
    t = today()
    C = []

    def add(key, title, ok, detail, level='warn', go=None):
        C.append({'key': key, 'title': title, 'status': 'ok' if ok else level, 'detail': 'Done' if ok else detail, 'go': None if ok else go})

    a, b = y['from_date'], y['to_date']
    add('ended', 'The year has ended', b < t, f"The year ends on {b:%d %b %Y}; it can be closed from the next day.", 'block')
    earlier = db.q("""SELECT code FROM fin_years WHERE company=:c AND to_date<:a AND status<>'Closed'
                      AND EXISTS (SELECT 1 FROM journals j WHERE j.company=:c AND j.txn_date BETWEEN fin_years.from_date AND fin_years.to_date)""", c=company, a=a)
    add('earlier', 'Earlier years are closed', not earlier, f"Close {', '.join(e['code'] for e in earlier)} first.", 'block', {'tab': 'ye'})
    pend = db.q("""SELECT no, status FROM fin_vouchers WHERE company=:c AND vdate BETWEEN :a AND :b AND status IN ('Draft','Pending approval') ORDER BY vdate""",
                c=company, a=a, b=b)
    add('vouchers', 'No draft or pending vouchers in the year', not pend,
        f"{len(pend)} voucher(s) not posted: {', '.join(p['no'] for p in pend[:6])}{'…' if len(pend) > 6 else ''}. Post, cancel or get them approved.", 'block',
        {'tab': 'vch'})
    t_ = db.one('''SELECT COALESCE(SUM(l.debit),0) dr, COALESCE(SUM(l.credit),0) cr FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                   WHERE j.company=:c AND j.txn_date BETWEEN :a AND :b''', c=company, a=a, b=b)
    dr, cr = t_['dr'], t_['cr']
    add('balanced', 'The books balance (debits = credits)', abs(Decimal(dr) - Decimal(cr)) < Decimal('0.01'), f'Debits {dr} ≠ credits {cr}. Tell IT before closing.', 'block')

    months = [m for m in _months(y) if month_end(m) < t]
    reg = db.val('SELECT COUNT(*) FROM gst_registrations WHERE company=:c', c=company)
    if reg:
        for ret, name in (('3B', 'GSTR-3B'), ('1', 'GSTR-1')):
            filed = {r['period'] for r in db.q('SELECT period FROM gst_filings WHERE company=:c AND ret=:r', c=company, r=ret)}
            miss = [f'{m:%b %Y}' for m in months if f'{m:%Y-%m}' not in filed]
            add('gst' + ret, f'{name} filed for every month', not miss, f"Not filed: {', '.join(miss)}.", 'warn', {'tab': 'gst'})
    from .fin_tds import FORMS, form_of, party_tax, q_range
    miss = []
    for q in ('Q1', 'Q2', 'Q3', 'Q4'):
        qa, qb = q_range(a, q)
        if qb >= t:
            continue
        es = db.q("SELECT party, section FROM tds_entries WHERE company=:c AND kind='Payable' AND status<>'Reversed' AND amount>0 AND ded_date BETWEEN :a AND :b",
                  c=company, a=qa, b=qb)
        forms = {form_of(e['section'], (party_tax(company, e['party']) or {}).get('resident', True)) for e in es}
        for f in sorted(forms):
            if not db.one('SELECT 1 FROM tds_returns WHERE company=:c AND fy=:y AND form=:f AND quarter=:q', c=company, y=y['code'], f=f, q=q):
                miss.append(f'{FORMS[f][0]} {q}')
    add('tdsret', 'TDS returns filed for every quarter', not miss, f"Not filed: {', '.join(miss)}.", 'warn', {'tab': 'tds'})
    und = db.val("""SELECT COUNT(*) FROM tds_entries e LEFT JOIN tds_challans c ON c.id=e.challan_id WHERE e.company=:co AND e.kind='Payable'
                    AND e.status<>'Reversed' AND e.amount>0 AND e.ded_date BETWEEN :a AND :b AND (c.id IS NULL OR c.status<>'Paid')""", co=company, a=a, b=b)
    add('tdspay', 'TDS deducted in the year is deposited', not und, f'{und} TDS deduction(s) not yet deposited with the government.', 'warn', {'tab': 'tds'})
    has_assets = db.val("SELECT COUNT(*) FROM fixed_assets WHERE company=:c AND put_to_use<=:b AND status IN ('Active','Disposed')", c=company, b=b)
    if has_assets:
        done = {r['period'] for r in db.q('SELECT period FROM depreciation_runs WHERE company=:c', c=company)}
        miss = [f'{m:%b %Y}' for m in months
                if f'{m:%Y-%m}' not in done and db.val("SELECT COUNT(*) FROM fixed_assets WHERE company=:c AND put_to_use<=:e AND status='Active'", c=company, e=month_end(m))]
        add('dep', 'Depreciation posted for every month', not miss, f"Not run: {', '.join(miss)}.", 'warn', {'tab': 'assets'})
    unrec = db.val('SELECT COUNT(*) FROM bank_statement_lines WHERE company=:c AND sdate BETWEEN :a AND :b AND match_journal IS NULL', c=company, a=a, b=b)
    add('bank', 'Bank statements reconciled', not unrec, f'{unrec} bank statement line(s) of the year are not matched to the books.', 'warn', {'tab': 'bank'})
    sa = db.q("SELECT no FROM stock_audits WHERE company=:c AND audit_date BETWEEN :a AND :b AND status NOT IN ('Closed','Cancelled','Rejected')", c=company, a=a, b=b)
    add('saudit', 'Stock audits of the year are finished', not sa,
        f"{len(sa)} stock audit(s) still open: {', '.join(s['no'] for s in sa[:5])}{'…' if len(sa) > 5 else ''}. Their adjustments would post after closing.", 'warn',
        {'page': 'stock-audits'})
    neg = db.q('''SELECT l.account, a.name, SUM(l.debit-l.credit) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                  JOIN accounts a ON a.company=j.company AND a.code=l.account WHERE j.company=:c AND j.txn_date<=:b AND l.account BETWEEN :lo AND :hi
                  GROUP BY l.account, a.name HAVING SUM(l.debit-l.credit) < -0.005''', c=company, b=b, lo=CASH_BANK[0], hi=CASH_BANK[1])
    add('cash', 'No cash or bank account is overdrawn at year end', not neg,
        '; '.join(f"{n['name']} {float(n['v']):,.2f}" for n in neg) + ' (cash cannot be negative; check for missing receipts or wrong postings).', 'warn',
        {'tab': 'rep', 'report': 'cash'})
    return C


# ---------------------------------------------------------------- appropriation
def equity_accounts(company):
    return db.clean(db.q("SELECT code, name FROM accounts WHERE company=:c AND grp IN ('E','C') AND active=1 ORDER BY code", c=company))


def default_split(company):
    last = db.val("SELECT close_summary FROM fin_years WHERE company=:c AND status='Closed' AND close_summary IS NOT NULL ORDER BY to_date DESC LIMIT 1", c=company)
    if isinstance(last, str):
        import json
        last = json.loads(last)
    if last and last.get('split'):
        return [{'account': s['account'], 'pct': s['pct']} for s in last['split']]
    acc = equity_accounts(company)
    return [{'account': acc[0]['code'], 'pct': 100}] if acc else []


def _split(company, split, profit):
    names = {a['code']: a['name'] for a in equity_accounts(company)}
    if not isinstance(split, list) or not split:
        raise bad('Choose the capital / reserve account(s) that take the profit.', 'split_required')
    out, seen = [], set()
    for s in split:
        if not isinstance(s, dict) or str(s.get('account')) not in names:
            raise bad('The profit can go only to a capital or reserve account (group Equity / Capital).', 'invalid_split')
        try:
            pct = Decimal(str(s.get('pct')))
        except Exception:  # noqa: BLE001
            raise bad('Each share must be a percentage.', 'invalid_split') from None
        if not (0 < pct <= 100) or s['account'] in seen:
            raise bad('Each share must be more than 0% and at most 100%, one line per account.', 'invalid_split')
        seen.add(s['account'])
        out.append({'account': str(s['account']), 'name': names[str(s['account'])], 'pct': float(pct)})
    if abs(sum(Decimal(str(x['pct'])) for x in out) - 100) > Decimal('0.001'):
        raise bad('The shares must add up to 100%.', 'invalid_split')
    left = profit
    for i, x in enumerate(out):
        x['amount'] = float(left if i == len(out) - 1 else r2(profit * Decimal(str(x['pct'])) / 100))
        left -= Decimal(str(x['amount']))
    return out


# ---------------------------------------------------------------- view
def view(company, code=None):
    years = db.clean(db.q('SELECT * FROM fin_years WHERE company=:c ORDER BY from_date DESC', c=company))
    if not years:
        raise not_found('No financial years are set up.', 'unknown_year')
    if not code:
        t = today()
        due = [y for y in reversed(years) if y['status'] != 'Closed' and str(y['to_date']) < str(t)]
        code = (due[0] if due else next((y for y in years if y['status'] != 'Closed'), years[0]))['code']
    y = _year(company, code)
    names = account_names(company)
    res = {'years': years, 'year': db.clean([y])[0], 'equity_accounts': equity_accounts(company),
           'can': {'close': fv.is_closer(g.user) and can(g.user, 'approve', 'fin'), 'reopen': _may_reopen(g.user)}}
    if y['status'] == 'Closed':
        res['summary'] = y['close_summary']
        return res
    rows, inc, exp, net = figures(company, y)
    by = {}
    for r in rows:
        by[r['account']] = by.get(r['account'], 0) + float(r['v'])
    split = default_split(company)
    try:
        sp = _split(company, split, net) if split else []
    except Exception:  # noqa: BLE001 — a stale default split is shown as a plain list
        sp = split
    b = bs(company, y['to_date'])
    res.update(checks=checklist(company, y), income=float(inc), expenses=float(exp), net=float(net), split=sp,
               closing_lines=[{'account': a, 'name': names.get(a, a), 'debit': round(-v, 2) if v < 0 else 0, 'credit': round(v, 2) if v > 0 else 0}
                              for a, v in sorted(by.items()) if abs(v) >= 0.005],
               opening_next={'assets': b['total_assets'], 'liabilities': round(sum(x[2] for x in b['liabilities']), 2),
                             'capital_before': round(sum(x[2] for x in b['capital']), 2), 'retained_before': b['retained'], 'profit': b['profit'],
                             'balanced': abs(b['diff']) < 0.05})
    return res


# ---------------------------------------------------------------- close / reopen
def _may_reopen(u):
    return any(r in REOPENERS for r in u['roles']) and can(u, 'restore', 'fin')


def close(company, code, why, split, acknowledge):
    u = g.user
    if not (fv.is_closer(u) and can(u, 'approve', 'fin')):
        raise denied('Only the CFO, Director or Super Admin closes a financial year.', 'forbidden')
    if not txt(why):
        raise bad('Give the reason or reference for closing (e.g. "Accounts finalised and audited").', 'reason_required')
    lock_company(company)
    y = db.one('SELECT * FROM fin_years WHERE company=:c AND code=:f FOR UPDATE', c=company, f=code)
    if not y:
        raise not_found(f'Financial year {code} not found for {company}.', 'unknown_year')
    if y['status'] == 'Closed':
        raise conflict(f'Financial year {code} is already closed.', 'already_closed')
    checks = checklist(company, y)
    blocks = [c for c in checks if c['status'] == 'block']
    if blocks:
        raise conflict('Cannot close yet: ' + ' · '.join(f"{c['title']} — {c['detail']}" for c in blocks), 'year_not_ready')
    warns = [c for c in checks if c['status'] == 'warn']
    if warns and acknowledge is not True:
        raise conflict(f'{len(warns)} item(s) need review: ' + ' · '.join(c['title'] for c in warns) + '. Tick "I have reviewed these" to close anyway.',
                       'review_warnings')
    rows, inc, exp, net = figures(company, y)
    sp = _split(company, split, net)
    j = None
    lines = [{'account': r['account'], 'branch': r['branch_id'], 'credit': float(r['v'])} for r in rows]
    lines += [{'account': s['account'], 'branch': f'{company}-HO', 'credit': s['amount']} for s in sp if abs(s['amount']) >= 0.005]
    if lines:
        j = ledger.post(company, y['to_date'], 'Year closing', f'YE-{code}',
                        f"Year-end closing {code}: {'profit' if net >= 0 else 'loss'} of {abs(net):,.2f} transferred to "
                        + ', '.join(f"{s['name']} {s['pct']:g}%" for s in sp) + f'. {txt(why)}', lines, force=True)
        db.session().flush()
    old_lock = db.val('SELECT books_locked_to FROM companies WHERE code=:c', c=company)
    if not old_lock or old_lock < y['to_date']:
        db.run('UPDATE companies SET books_locked_to=:d WHERE code=:c', d=y['to_date'], c=company)
    nxt = fy_of(company, y['to_date'] + dt.timedelta(days=1))
    db.run("INSERT IGNORE INTO fin_years (company, code, from_date, to_date, status) VALUES (:co, :c, :f, :t, 'Open')",
           co=company, c=nxt['code'], f=nxt['from_date'], t=nxt['to_date'])
    summary = {'income': float(inc), 'expenses': float(exp), 'net': float(net), 'split': sp, 'journal': j.no if j else None,
               'checks': [{k: c[k] for k in ('key', 'title', 'status', 'detail')} for c in checks], 'acknowledged': [c['title'] for c in warns],
               'closed_by': u['name'], 'closed_role': u['main_role'], 'closed_at': dt.datetime.now().isoformat(timespec='minutes'), 'reason': txt(why),
               'lock_before': str(old_lock) if old_lock else None, 'next_year': nxt['code']}
    db.run("""UPDATE fin_years SET status='Closed', closed_by=:u, closed_at=NOW(6), closing_journal=:j, net_profit=:n, close_reason=:r, close_summary=:s,
              reopened_by=NULL, reopened_at=NULL, reopen_reason=NULL WHERE company=:c AND code=:f""",
           u=u['id'], j=j.no if j else None, n=net, r=txt(why)[:300], s=_dump(summary), c=company, f=code)
    audit.write('Financial year closed', f'{company} {code}', new=f"net {net} → {', '.join(s['account'] + ' ' + str(s['amount']) for s in sp)}; journal {j.no if j else '-'}",
                reason=txt(why), category='finance', company=company)
    notify.alert('Financial year closed', f"{company} FY {code} closed by {u['name']} ({u['main_role']}): {'profit' if net >= 0 else 'loss'} {abs(net):,.2f}"
                 + (f"; {len(warns)} reviewed warning(s)" if warns else ''), ref=f'{company} {code}', company=company, severity='info')
    return summary


def _undo(company, y, why):
    """Reverse the closing journal of a year (if any) and mark the year open."""
    if y['closing_journal']:
        j = db.session().query(Journal).filter_by(company=company, no=y['closing_journal']).with_for_update().first()
        if j and not j.reversed_by_no:
            r = ledger.post(company, y['to_date'], 'Year closing reversal', j.no, f"Reversal of year-end closing {y['code']}: {why}",
                            [{'account': ln.account, 'branch': ln.branch_id, 'party': ln.party, 'debit': ln.credit, 'credit': ln.debit} for ln in j.lines], force=True)
            r.reverses_no = j.no
            j.reversed_by_no = r.no
    db.run("""UPDATE fin_years SET status='Open', closed_by=NULL, closed_at=NULL, closing_journal=NULL, net_profit=NULL,
              reopened_by=:u, reopened_at=NOW(6), reopen_reason=:r WHERE company=:c AND code=:f""",
           u=(g.user or {}).get('id'), r=why[:300], c=company, f=y['code'])


def reopen(company, code, why):
    u = g.user
    if not _may_reopen(u):
        raise denied('Only the Director, Owner or Super Admin can reopen a closed financial year.', 'forbidden')
    if not txt(why):
        raise bad('Give the reason for reopening (e.g. "Auditor adjustment: provision for bonus").', 'reason_required')
    lock_company(company)
    y = db.one('SELECT * FROM fin_years WHERE company=:c AND code=:f FOR UPDATE', c=company, f=code)
    if not y:
        raise not_found(f'Financial year {code} not found for {company}.', 'unknown_year')
    if y['status'] != 'Closed':
        raise conflict(f'Financial year {code} is not closed.', 'not_closed')
    later = db.q("SELECT code FROM fin_years WHERE company=:c AND from_date>:d AND status='Closed' ORDER BY from_date", c=company, d=y['to_date'])
    if later:
        raise conflict(f"Reopen {', '.join(x['code'] for x in reversed(later))} first (the latest closed year first).", 'later_year_closed')
    _undo(company, y, txt(why))
    lock = db.val('SELECT books_locked_to FROM companies WHERE code=:c', c=company)
    if lock and lock >= y['from_date']:
        db.run('UPDATE companies SET books_locked_to=:d WHERE code=:c', d=y['from_date'] - dt.timedelta(days=1), c=company)
    audit.write('Financial year reopened', f'{company} {code}', old=f"closed, journal {y['closing_journal'] or '-'}", new='open', reason=txt(why),
                category='finance', company=company)
    notify.alert('Financial year reopened', f"{company} FY {code} reopened by {u['name']} ({u['main_role']}): {txt(why)}", ref=f'{company} {code}',
                 company=company, severity='bad')


def reopen_from(company, to, why):
    """Used when a period is reopened from a date inside closed years: their closing journals are reversed first."""
    for y in db.q("SELECT * FROM fin_years WHERE company=:c AND status='Closed' AND to_date>:d ORDER BY to_date DESC FOR UPDATE", c=company, d=to):
        _undo(company, y, why)


def _dump(x):
    import json
    return json.dumps(x, default=str)
