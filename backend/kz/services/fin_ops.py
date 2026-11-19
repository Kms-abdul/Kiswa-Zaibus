"""Finance & tax: banking (statement import, automatic and manual matching, bank-only entries, BRS), cheques, payment proposals, fixed assets
(capitalisation, Companies Act depreciation, Income-tax blocks, transfer, impairment, disposal, physical verification), the compliance calendar,
recurring journals, period and year closing, finance alerts and the scheduler hook."""
import datetime as dt
import hashlib
import json
import random
import re
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit, notify
from ..core.rbac import can
from ..errors import bad, conflict, denied, not_found
from ..models import FixedAsset
from . import fin_gst, fin_tds
from . import fin_vouchers as fv
from .common import check_company, parse_date, today
from .fin_common import fy_of, ho, is_bank, lock_company, month_end, need_ym, r2, txt
from .fin_reports import C, R, open_items, out

SYSTEM = {'id': 'system', 'name': 'System (finance automation)', 'main_role': 'System', 'roles': ['System'], 'rank': 0, 'read_only': False, 'perms': {},
          'companies': [], 'branches': '*'}


# ---------- banking ----------
def bank_accounts(company):
    return db.clean(db.q('SELECT * FROM bank_accounts WHERE company=:c ORDER BY account', c=company))


def book_lines(company, account, frm=None, to=None):
    return db.q('''SELECT j.no jno, j.ref, j.txn_date, j.narration, l.line_no, l.debit-l.credit amt FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                   WHERE j.company=:c AND l.account=:a AND (:f IS NULL OR j.txn_date>=:f) AND (:t IS NULL OR j.txn_date<=:t) ORDER BY j.txn_date, j.id''',
                c=company, a=account, f=frm, t=to)


def _date(s):
    s = str(s or '').strip()
    m = re.fullmatch(r'(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})', s)
    try:
        if m:
            return dt.date(int(m[3]) + (2000 if len(m[3]) == 2 else 0), int(m[2]), int(m[1]))
        return dt.date.fromisoformat(s[:10])
    except ValueError:
        return None


def statement_import(company, account, table, source='Imported file'):
    hi = next((i for i, r in enumerate(table) if any(re.search('date', str(c), re.I) for c in r)
               and any(re.search('debit|withdraw|credit|deposit|amount', str(c), re.I) for c in r)), -1)
    if hi < 0:
        raise bad('Columns needed: Date, Narration / Description, Ref / Cheque no., Withdrawal (debit), Deposit (credit). PDF statements: save as Excel or CSV first.',
                  'invalid_file')
    H = [str(c).lower() for c in table[hi]]

    def ix(*n):
        return next((i for i, h in enumerate(H) if any(x in h for x in n)), -1)
    cD, cN, cR, cW, cP, cA, cB = ix('date'), ix('narration', 'description', 'particular', 'remarks'), ix('ref', 'chq', 'cheque', 'utr'), ix('withdraw', 'debit'), \
        ix('deposit', 'credit'), ix('amount'), ix('balance')

    def n(r, i):
        try:
            return float(re.sub(r'[,\s₹]', '', str(r[i] if i >= 0 and i < len(r) else '') or '0') or 0)
        except ValueError:
            return 0.0
    k = 0
    for r in table[hi + 1:]:
        d = _date(r[cD] if cD < len(r) else '')
        if not d:
            continue
        amt = n(r, cA) if cA >= 0 and cW < 0 else round(n(r, cP) - n(r, cW), 2)
        if not amt:
            continue
        desc = str(r[cN] if 0 <= cN < len(r) else '')[:200]
        ref = str(r[cR] if 0 <= cR < len(r) else '')[:40]
        uniq = hashlib.sha256(f'{company}|{account}|{d}|{amt}|{ref}|{desc}'.encode()).hexdigest()[:40]
        if db.one('SELECT 1 FROM bank_statement_lines WHERE uniq=:u', u=uniq):
            continue
        db.insert('bank_statement_lines', dict(company=company, account=account, sdate=d, narration=desc, ref=ref, amount=amt,
                                               balance=n(r, cB) if cB >= 0 else None, source=source[:40], uniq=uniq, created_at=dt.datetime.now()))
        k += 1
    audit.write('Bank statement imported', f'{company} {account}', new=f'{k} lines from {source}', category='finance', company=company)
    return k


def statement_demo(company, account):
    """Stands in for a bank API feed: the bank's view of the last 40 days built from the books, with typical differences."""
    t = today()
    bl = book_lines(company, account, t - dt.timedelta(days=40), t)
    rnd = random.Random(len(bl) + 7)
    rows = [['Date', 'Narration', 'Ref', 'Withdrawal', 'Deposit']]
    for i, b in enumerate(bl):
        amt = float(b['amt'])
        if i % 9 == 4 and b['txn_date'] > t - dt.timedelta(days=4):
            continue
        d = b['txn_date'] + dt.timedelta(days=round(rnd.random()) if amt > 0 else 0)
        rows.append([f'{d:%d-%m-%Y}', ('NEFT CR ' if amt > 0 else 'NEFT DR ') + (b['narration'] or '')[:30], b['ref'] or '', -amt if amt < 0 else '', amt if amt > 0 else ''])
    rows += [[f'{t - dt.timedelta(days=6):%d-%m-%Y}', 'CHARGES SMS ALERT + GST', '', 59, ''], [f'{t - dt.timedelta(days=3):%d-%m-%Y}', 'IMPS CHG', '', 17.7, ''],
             [f'{t.replace(day=1):%d-%m-%Y}', 'INT CREDIT SWEEP FD', '', '', 1240]]
    return statement_import(company, account, rows, 'Bank feed (demo; no live API)')


def _matched_keys(company, account):
    return {(r['match_journal'], r['match_line']) for r in db.q('SELECT match_journal, match_line FROM bank_statement_lines WHERE company=:c AND account=:a AND match_journal IS NOT NULL',
                                                                c=company, a=account)}


def auto_match(company, account):
    used = _matched_keys(company, account)
    bl = [b for b in book_lines(company, account) if (b['jno'], b['line_no']) not in used]
    words = lambda s: {w for w in re.split(r'[^A-Z0-9]+', str(s).upper()) if len(w) > 3}  # noqa: E731
    n = 0
    for s in db.q('SELECT * FROM bank_statement_lines WHERE company=:c AND account=:a AND match_journal IS NULL ORDER BY sdate', c=company, a=account):
        best, bs = None, -1
        sw = words(f"{s['narration']} {s['ref']}")
        for b in bl:
            if (b['jno'], b['line_no']) in used or abs(float(b['amt']) - float(s['amount'])) > 0.01:
                continue
            dd = abs((b['txn_date'] - s['sdate']).days)
            if dd > 5:
                continue
            sc = 5 - dd + (6 if s['ref'] and s['ref'].upper() in (b['ref'] or '').upper() else 0) + len(sw & words(f"{b['narration']} {b['ref']}"))
            chq = db.val('SELECT cheque_no FROM cheques WHERE voucher_no=:v', v=b['ref'])
            if chq and chq in f"{s['narration']}{s['ref']}":
                sc += 8
            if sc > bs:
                best, bs = b, sc
        if best:
            used.add((best['jno'], best['line_no']))
            db.run('UPDATE bank_statement_lines SET match_journal=:j, match_line=:l, how=:h WHERE id=:i', j=best['jno'], l=best['line_no'],
                   h='Auto · reference' if bs >= 8 else 'Auto · amount + date', i=s['id'])
            _clear_cheque(best['ref'], s['sdate'])
            n += 1
    audit.write('Bank reconciliation run', f'{company} {account}', new=f'{n} matched', category='finance', company=company)
    return n


def _clear_cheque(vno, d):
    c = db.one("SELECT * FROM cheques WHERE voucher_no=:v AND status IN ('Issued','Deposited')", v=vno)
    if c:
        log = (json.loads(c['log']) if isinstance(c['log'], str) else c['log']) or []
        log.append([str(dt.datetime.now())[:19], 'Bank reconciliation', f'Cleared on {d}'])
        db.run("UPDATE cheques SET status='Cleared', log=:l WHERE id=:i", l=json.dumps(log), i=c['id'])


def manual_match(sid, jno, line_no):
    s = db.one('SELECT * FROM bank_statement_lines WHERE id=:i', i=sid)
    if not s or s['company'] not in g.user['companies']:
        raise not_found('Statement line not found.')
    if db.one('SELECT 1 FROM bank_statement_lines WHERE match_journal=:j AND match_line=:l', j=jno, l=line_no):
        raise conflict('That book entry is already matched.', 'duplicate')
    if not db.one('''SELECT 1 FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.no=:j AND l.line_no=:l AND l.account=:a''', j=jno, l=line_no, a=s['account']):
        raise bad('Choose a book entry of the same bank account.', 'invalid')
    db.run('UPDATE bank_statement_lines SET match_journal=:j, match_line=:l, how=:h WHERE id=:i', j=jno, l=line_no, h=f"Manual · {g.user['name']}"[:40], i=sid)
    audit.write('Bank line matched', s['narration'][:40], new=f'{jno}/{line_no}', category='finance', company=s['company'])


def unmatch(sid):
    db.run('UPDATE bank_statement_lines SET match_journal=NULL, match_line=NULL, how=NULL WHERE id=:i AND company IN :c', i=sid, c=tuple(g.user['companies']))


def statement_voucher(sid, account=None):
    """Bank charges / interest seen only on the statement: a bank reconciliation voucher that matches itself once posted."""
    s = db.one('SELECT * FROM bank_statement_lines WHERE id=:i', i=sid)
    if not s or s['match_journal'] or s['company'] not in g.user['companies']:
        raise conflict('Choose an unmatched statement line.', 'wrong_status')
    amt = float(s['amount'])
    a = account or ('5650' if amt < 0 else '4110')
    hob = ho(s['company'])
    lines = [{'account': a, 'debit': -amt, 'branch': hob}, {'account': s['account'], 'credit': -amt, 'branch': hob}] if amt < 0 else \
        [{'account': s['account'], 'debit': amt, 'branch': hob}, {'account': a, 'credit': amt, 'branch': hob}]
    v = fv.create({'vtype': 'BR', 'company': s['company'], 'vdate': str(s['sdate']), 'branch_id': hob, 'dept': 'D-ACC', 'ref': s['ref'] or 'BANK',
                   'narration': f"Bank statement: {s['narration']}"[:300], 'lines': lines, 'options': {'on_post': 'bank_line', 'statement_line': sid}})
    return fv.submit(v.id)


def _bank_line_posted(v):
    sid = (v.options or {}).get('statement_line')
    s = db.one('SELECT account FROM bank_statement_lines WHERE id=:i', i=sid)
    ln = db.val('SELECT l.line_no FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.no=:n AND l.account=:a LIMIT 1', n=v.journal_no, a=s['account'])
    db.run('UPDATE bank_statement_lines SET match_journal=:j, match_line=:l, how=:h WHERE id=:i', j=v.journal_no, l=ln, h=f'Bank voucher {v.no}'[:40], i=sid)


def brs(company, account, asof):
    book = float(db.val('''SELECT COALESCE(SUM(l.debit-l.credit), 0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                           WHERE j.company=:c AND l.account=:a AND j.txn_date<=:d''', c=company, a=account, d=asof) or 0)
    used = _matched_keys(company, account)
    bl = [b for b in book_lines(company, account, asof - dt.timedelta(days=120), asof) if (b['jno'], b['line_no']) not in used]
    issued = [b for b in bl if b['amt'] < 0]
    not_cr = [b for b in bl if b['amt'] > 0]
    bank_only = db.q('SELECT * FROM bank_statement_lines WHERE company=:c AND account=:a AND sdate<=:d AND match_journal IS NULL ORDER BY sdate', c=company, a=account, d=asof)
    derived = round(book - sum(float(b['amt']) for b in issued) - sum(float(b['amt']) for b in not_cr) + sum(float(s['amount']) for s in bank_only), 2)
    last = db.one('SELECT * FROM bank_statement_lines WHERE company=:c AND account=:a AND sdate<=:d ORDER BY sdate DESC, id DESC LIMIT 1', c=company, a=account, d=asof)
    return {'book': round(book, 2), 'issued': db.clean(issued), 'not_credited': db.clean(not_cr), 'bank_only': db.clean(bank_only), 'statement': derived,
            'last_statement': db.clean(last)}


def statement(company, account, only_open=False):
    return db.clean(db.q(f'''SELECT * FROM bank_statement_lines WHERE company=:c AND account=:a {"AND match_journal IS NULL" if only_open else ""}
                             ORDER BY sdate DESC, id DESC LIMIT 1000''', c=company, a=account))


# ---------- cheques ----------
def cheque_set(cid, status, why=''):
    c = db.one('SELECT * FROM cheques WHERE id=:i', i=cid)
    if not c or c['company'] not in g.user['companies']:
        raise not_found('Cheque not found.')
    ok = {'Issued': ('Cleared', 'Stop payment'), 'Deposited': ('Cleared', 'Dishonoured'), 'Received': ('Deposited',)}.get(c['status'], ())
    if status not in ok:
        raise conflict(f"A cheque that is {c['status']} cannot be marked {status}.", 'wrong_status')
    log = (json.loads(c['log']) if isinstance(c['log'], str) else c['log']) or []
    if status in ('Dishonoured', 'Stop payment'):
        if not txt(why):
            raise bad('Give the reason.', 'reason_required')
        v = db.val('SELECT id FROM fin_vouchers WHERE no=:n AND company=:c', n=c['voucher_no'], c=c['company'])
        r = fv.reverse(vid=v, why=f"{status} cheque {c['cheque_no']}: {why}") if v else fv.reverse(journal_no=db.val('SELECT no FROM journals WHERE ref=:r LIMIT 1', r=c['voucher_no']),
                                                                                               why=f"{status} cheque {c['cheque_no']}: {why}")
        log.append([str(dt.datetime.now())[:19], g.user['name'], f'Reversal {r.no}'])
        notify.alert('Cheque ' + status.lower(), f"Cheque {c['cheque_no']} ({c['party']}, ₹{c['amount']:,.0f}) {status.lower()}: {why}", ref=c['cheque_no'],
                     company=c['company'], severity='bad')
    log.append([str(dt.datetime.now())[:19], g.user['name'], status + (f': {why}' if why else '')])
    db.run('UPDATE cheques SET status=:s, log=:l WHERE id=:i', s=status, l=json.dumps(log), i=cid)
    audit.write('Cheque ' + status, c['cheque_no'], new=f"₹{c['amount']:,.2f}", reason=why or None, category='finance', company=c['company'])


# ---------- payables ----------
def pay_proposal(company, upto):
    items = [x for x in open_items(company, 'sup', today()) if (x.get('due_date') or x['date']) <= upto]
    cash = sum(float(db.val('''SELECT COALESCE(SUM(l.debit-l.credit), 0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                                WHERE j.company=:c AND l.account=:a''', c=company, a=b['account']) or 0) for b in bank_accounts(company))
    pri = {r['party']: r['priority'] or 'Normal' for r in db.q('SELECT party, priority FROM party_tax WHERE company=:c', c=company)}
    order = {'High': 0, 'Normal': 1, 'Low': 2}
    items.sort(key=lambda x: (order.get(pri.get(x['party'], 'Normal'), 1), x.get('due_date') or x['date']))
    run_, rows = 0.0, []
    for x in items:
        run_ += x['due']
        rows.append({**{k: (str(v) if isinstance(v, dt.date) else v) for k, v in x.items()}, 'priority': pri.get(x['party'], 'Normal'), 'running': round(run_, 2),
                     'fits': run_ <= cash, 'overdue': (x.get('due_date') or x['date']) < today()})
    return {'cash': round(cash, 2), 'rows': rows}


def pay_proposal_create(company, picks, bank='1120'):
    """picks: [{doc, party, amount}] → one payment voucher per supplier, each through the approval limits."""
    by = {}
    for x in picks:
        by.setdefault(x['party'], []).append(x)
    outv = []
    for p, xs in by.items():
        amt = float(r2(sum(Decimal(str(x['amount'])) for x in xs)))
        v = fv.create({'vtype': 'PV', 'company': company, 'vdate': str(today()), 'branch_id': ho(company), 'party': {'kind': 'sup', 'id': p},
                       'ref': f'Payment run {today()}', 'narration': 'Payment proposal: ' + ', '.join(x['doc'] for x in xs),
                       'lines': [{'account': bank, 'credit': amt, 'branch': ho(company)}], 'options': {'bill_alloc': {x['doc']: x['amount'] for x in xs}}})
        outv.append(fv.view(fv.submit(v.id), False))
    return outv


# ---------- fixed assets ----------
def category(cid):
    return db.one('SELECT * FROM asset_categories WHERE id=:i', i=cid) or {'id': cid, 'name': cid, 'account': '1530', 'life_years': 5, 'it_rate': 15}


def dep_for(a, frm, to):
    """Companies Act, Schedule II: straight line over the useful life, 5 % residual, pro-rata by days (WDV when chosen)."""
    s = max(frm, a.put_to_use)
    e = min(to, a.disposed_on) if a.disposed_on else to
    if e < s:
        return Decimal(0)
    days = Decimal((e - s).days + 1)
    cost = Decimal(str(a.cost))
    res = Decimal(str(a.residual_pct or 5)) / 100
    life = Decimal(a.life_years or category(a.category)['life_years'])
    base = cost * (1 - res)
    acc = Decimal(str(a.acc_dep or 0)) + Decimal(str(a.impairment or 0))
    if a.method == 'WDV':
        rate = 1 - float(res) ** (1 / float(life))
        d = (cost - acc) * Decimal(str(rate)) * days / 365
    else:
        d = base / (life * 365) * days
    return max(Decimal(0), min(r2(d), r2(base - acc)))


def depreciation_run(company, ym, system=False):
    a_, b_ = fin_gst.period(need_ym(ym))
    lock_company(company)
    if db.one('SELECT 1 FROM depreciation_runs WHERE company=:c AND period=:p FOR UPDATE', c=company, p=ym) or \
            db.one("SELECT 1 FROM fin_vouchers WHERE company=:c AND ref=:r AND status IN ('Draft','Pending approval','Posted') FOR UPDATE", c=company, r=f'DEP-{ym}'):
        raise conflict(f'Depreciation for {ym} is already posted.', 'duplicate')
    L, det = [], []
    for a in db.session().query(FixedAsset).filter(FixedAsset.company == company, FixedAsset.status == 'Active', FixedAsset.put_to_use <= b_):
        d = dep_for(a, a_, b_)
        if d < Decimal('0.01'):
            continue
        det.append([a.id, float(d)])
        L += [{'account': '5200', 'debit': float(d), 'branch': a.branch_id}, {'account': '1590', 'credit': float(d), 'branch': a.branch_id}]
    if not L:
        db.insert('depreciation_runs', dict(company=company, period=ym, voucher_no='nil', amount=0))
        return None
    v = fv.create({'vtype': 'DP', 'company': company, 'vdate': str(b_), 'branch_id': ho(company), 'dept': 'D-ACC', 'ref': f'DEP-{ym}',
                   'narration': f'Depreciation for {a_:%b %Y} (Companies Act, SLM) · {len(det)} assets', 'lines': L, 'options': {'on_post': 'depreciation', 'dep': det}},
                  system=system)
    return fv.post(v, approved=True) if system else fv.submit(v.id)


def _dep_posted(v):
    for aid, d in (v.options or {}).get('dep') or []:
        a = db.session().get(FixedAsset, aid)
        a.acc_dep = r2(Decimal(str(a.acc_dep or 0)) + Decimal(str(d)))
        a.dep_log = list(a.dep_log or []) + [[str(v.vdate), d, v.no]]
    db.insert('depreciation_runs', dict(company=v.company, period=f'{v.vdate:%Y-%m}', voucher_no=v.no, amount=v.total))


def capitalise(d):
    """New asset: an asset capitalisation voucher (with GST input credit when there is a bill); the asset becomes Active when it posts."""
    co = d.get('company')
    check_company(g.user, co)
    if not d.get('name') or not float(d.get('cost') or 0) > 0:
        raise bad('Enter the asset name and cost.', 'invalid')
    cat = category(d.get('category'))
    n = int(db.val('SELECT COUNT(*) FROM fixed_assets WHERE company=:c', c=co) or 0) + 1
    br = d.get('branch_id') or ho(co)
    cost = float(r2(d['cost']))
    put = parse_date(d['date'], 'date') if d.get('date') else today()
    pfx = db.val('SELECT online_prefix FROM companies WHERE code=:c', c=co) or co
    a = FixedAsset(no=f'{pfx}/FA/{n:04d}', company=co, branch_id=br, category=cat['id'], name=d['name'][:120], tag=f'{co}-FA-{n:04d}', put_to_use=put, cost=cost, residual_pct=5,
                   method=d.get('method') or 'SLM', life_years=int(d.get('life') or cat['life_years']), status='Pending capitalisation', acc_dep=0, impairment=0,
                   moves=[], verifications=[], dep_log=[])
    db.add(a)
    db.session().flush()
    gst = {'rows': [{'hsn': d.get('hsn') or '8471', 'taxable': cost, 'rate': float(d['gst_rate'])}], 'itc': d.get('itc') or 'Eligible'} if float(d.get('gst_rate') or 0) else None
    from .fin_common import gst_calc
    tax = gst_calc(gst['rows'])['t']['tax'] if gst else 0
    lines = [{'account': cat['account'], 'debit': cost, 'branch': br}] + ([] if d.get('supplier') else [{'account': d.get('bank') or '1120', 'credit': cost + tax, 'branch': br}])
    v = fv.create({'vtype': 'AC', 'company': co, 'vdate': str(put), 'branch_id': br, 'ref': d.get('bill') or a.tag, 'party': {'kind': 'sup', 'id': d['supplier']} if d.get('supplier') else None,
                   'narration': f'Capitalised {a.name} ({a.tag})', 'lines': lines, 'gst': gst, 'options': {'on_post': 'asset_cap', 'asset': a.id}})
    a.voucher_no = v.no
    return a, fv.submit(v.id)


def _asset(aid, active=True):
    a = db.session().get(FixedAsset, aid, with_for_update=True)
    if not a or a.company not in g.user['companies']:
        raise not_found('Asset not found.')
    if active and a.status != 'Active':
        raise conflict('Choose an active asset.', 'wrong_status')
    return a


def _move(a, d, what, detail):
    a.moves = list(a.moves or []) + [[str(d), what, detail]]


def _cap_posted(v):
    a = db.session().get(FixedAsset, v.options['asset'])
    a.status = 'Active'
    _move(a, v.vdate, 'Capitalised', a.branch_id)


def dispose(aid, d):
    a = _asset(aid)
    if not str(d.get('why') or '').strip():
        raise bad('Give the reason.', 'reason_required')
    on = parse_date(d['date'], 'date') if d.get('date') else today()
    extra = dep_for(a, on.replace(day=1), on)
    acc = r2(Decimal(str(a.acc_dep or 0)) + extra + Decimal(str(a.impairment or 0)))
    sale = r2(d.get('sale'))
    cost = r2(a.cost)
    nbv = cost - acc
    gl = sale - nbv
    cat = category(a.category)
    b = a.branch_id
    L = [{'account': '1590', 'debit': float(acc), 'branch': b}, {'account': cat['account'], 'credit': float(cost), 'branch': b}]
    if extra:
        L += [{'account': '5200', 'debit': float(extra), 'branch': b}, {'account': '1590', 'credit': float(extra), 'branch': b}]
    if sale:
        L.append({'account': d.get('bank') or '1120', 'debit': float(sale), 'branch': b})
    if gl > 0:
        L.append({'account': '4120', 'credit': float(gl), 'branch': b})
    if gl < 0:
        L.append({'account': '5960', 'debit': float(-gl), 'branch': b})
    v = fv.create({'vtype': 'JV', 'company': a.company, 'vdate': str(on), 'branch_id': b, 'ref': a.tag,
                   'narration': f"Disposal of {a.name} ({a.tag}): {d['why']}. Book value ₹{nbv:,.0f}, sold for ₹{sale:,.0f}"[:300], 'lines': L,
                   'options': {'on_post': 'asset_dispose', 'asset': a.id, 'extra': float(extra)}})
    return fv.submit(v.id)


def _dispose_posted(v):
    a = db.session().get(FixedAsset, v.options['asset'])
    a.acc_dep = r2(Decimal(str(a.acc_dep or 0)) + Decimal(str(v.options['extra'])))
    a.status, a.disposed_on = 'Disposed', v.vdate
    _move(a, v.vdate, 'Disposed', v.narration)


def impair(aid, amount, why):
    a = _asset(aid)
    amt = r2(amount)
    if not amt > 0 or amt > r2(a.cost) - r2(a.acc_dep) - r2(a.impairment):
        raise bad('Impairment must be between ₹1 and the book value.', 'invalid')
    if not txt(why):
        raise bad('Give the reason.', 'reason_required')
    v = fv.create({'vtype': 'JV', 'company': a.company, 'vdate': str(today()), 'branch_id': a.branch_id, 'ref': a.tag, 'narration': f'Impairment of {a.name}: {why}'[:300],
                   'lines': [{'account': '5210', 'debit': float(amt), 'branch': a.branch_id}, {'account': '1590', 'credit': float(amt), 'branch': a.branch_id}],
                   'options': {'on_post': 'asset_impair', 'asset': a.id, 'amount': float(amt)}})
    return fv.submit(v.id)


def _impair_posted(v):
    a = db.session().get(FixedAsset, v.options['asset'])
    a.impairment = r2(Decimal(str(a.impairment or 0)) + Decimal(str(v.options['amount'])))
    _move(a, v.vdate, 'Impaired', f"₹{v.options['amount']:,.0f}")


def transfer(aid, to, why=''):
    a = _asset(aid)
    if to == a.branch_id:
        raise bad('Choose a different location.', 'invalid')
    cat = category(a.category)
    acc = float(r2(a.acc_dep) + r2(a.impairment))
    L = [{'account': cat['account'], 'debit': float(a.cost), 'branch': to}, {'account': cat['account'], 'credit': float(a.cost), 'branch': a.branch_id}]
    if acc:
        L += [{'account': '1590', 'debit': acc, 'branch': a.branch_id}, {'account': '1590', 'credit': acc, 'branch': to}]
    v = fv.create({'vtype': 'IB', 'company': a.company, 'vdate': str(today()), 'branch_id': a.branch_id, 'ref': a.tag,
                   'narration': f'Asset {a.name} moved {a.branch_id} → {to}: {why}'[:300], 'lines': L, 'options': {'on_post': 'asset_transfer', 'asset': a.id, 'to': to}})
    return fv.submit(v.id)


def _transfer_posted(v):
    a = db.session().get(FixedAsset, v.options['asset'])
    _move(a, v.vdate, 'Transferred', f"{a.branch_id} → {v.options['to']}")
    a.branch_id = v.options['to']


def verify(aid, found, condition, note=''):
    a = _asset(aid, active=False)
    a.verifications = list(a.verifications or []) + [{'date': str(today()), 'by': g.user['name'], 'found': bool(found), 'condition': condition or '', 'note': note or ''}]
    audit.write('Asset verified', a.tag, new=f"{'Found' if found else 'NOT FOUND'} · {condition}", reason=note or None, category='finance', company=a.company)
    if not found:
        notify.alert('Asset not found', f'{a.tag} {a.name} not found at {a.branch_id} during verification', ref=a.tag, company=a.company, branch=a.branch_id, severity='bad')
    return a


def asset_view(a):
    return {**{c: getattr(a, c) for c in ('id', 'no', 'company', 'branch_id', 'category', 'name', 'tag', 'method', 'life_years', 'status', 'voucher_no', 'moves',
                                          'verifications', 'dep_log')},
            'put_to_use': str(a.put_to_use), 'disposed_on': str(a.disposed_on) if a.disposed_on else None, 'cost': float(a.cost), 'acc_dep': float(a.acc_dep or 0),
            'impairment': float(a.impairment or 0), 'net_block': 0 if a.status == 'Disposed' else float(r2(a.cost) - r2(a.acc_dep) - r2(a.impairment)),
            'category_name': category(a.category)['name']}


def it_blocks(company, fy_code):
    """Income-tax depreciation by block of assets (WDV); additions used for under 180 days in the year get half the rate."""
    fy = db.one('SELECT * FROM fin_years WHERE company=:c AND code=:f', c=company, f=fy_code) or fy_of(company, today())
    years = db.q('SELECT * FROM fin_years WHERE company=:c ORDER BY from_date', c=company)
    m = {}
    for a in db.session().query(FixedAsset).filter(FixedAsset.company == company, FixedAsset.put_to_use <= fy['to_date']):
        cat = category(a.category)
        x = m.setdefault((cat['it_rate'], cat['id']), {'block': cat['name'], 'rate': float(cat['it_rate']), 'open': 0.0, 'add_full': 0.0, 'add_half': 0.0, 'deletions': 0.0})
        wdv = float(a.cost)
        for f in years:
            if f['to_date'] < fy['from_date'] and f['to_date'] >= a.put_to_use:
                half = a.put_to_use >= f['from_date'] and (f['to_date'] - a.put_to_use).days < 180
                wdv -= wdv * float(cat['it_rate']) / 100 * (0.5 if half else 1)
        if a.put_to_use < fy['from_date']:
            x['open'] += wdv
        elif (fy['to_date'] - a.put_to_use).days >= 179:
            x['add_full'] += float(a.cost)
        else:
            x['add_half'] += float(a.cost)
    res = []
    for x in m.values():
        dep = round((x['open'] + x['add_full'] - x['deletions']) * x['rate'] / 100 + x['add_half'] * x['rate'] / 200, 2)
        res.append({**{k: round(v, 2) if isinstance(v, float) else v for k, v in x.items()}, 'depreciation': dep,
                    'close': round(x['open'] + x['add_full'] + x['add_half'] - x['deletions'] - dep, 2)})
    return res


# ---------- recurring journals ----------
def recurring_run(upto=None):
    t = upto or today()
    n = 0
    for r in db.q('SELECT * FROM recurring_journals WHERE active=1 AND next_date<=:t AND next_date<=end_date FOR UPDATE', t=t):
        guard, nd = 0, r['next_date']
        while nd <= t and nd <= r['end_date'] and guard < 13:
            guard += 1
            lines = json.loads(r['lines']) if isinstance(r['lines'], str) else r['lines']
            try:
                with db.session().begin_nested():
                    tds = {'section': r['tds_section'], 'base': next((x.get('debit') for x in lines if x.get('debit')), 0)} if r['tds_section'] else None
                    v = fv.create({'vtype': r['vtype'], 'company': r['company'], 'vdate': str(nd), 'branch_id': lines[0].get('branch') or ho(r['company']),
                                   'narration': f"{r['narration']} · {nd:%d-%m-%Y}", 'ref': f"RJ{r['id']}/{nd:%Y-%m}", 'lines': lines, 'tds': tds,
                                   'party': {'kind': 'sup', 'id': r['party_id']} if r['party_id'] else None, 'options': {'recurring': r['id']}}, system=True)
                    fv.submit(v, system=True)
                n += 1
            except Exception as e:  # noqa: BLE001 — a failed recurring entry raises an alert and the schedule moves on
                notify.alert('Recurring journal failed', f"{r['name']}: {getattr(e, 'message', str(e))}", ref=f"RJ{r['id']}", company=r['company'], severity='bad')
            m1 = (nd.replace(day=1) + dt.timedelta(days=32)).replace(day=1)
            nd = m1.replace(day=min(r['day'], 28))
        db.run('UPDATE recurring_journals SET next_date=:n, last_run=:t WHERE id=:i', n=nd, t=dt.datetime.now(), i=r['id'])
    return n


# ---------- periods ----------
def close_period(company, upto, why):
    if not fv.is_closer(g.user):
        raise denied('Only the CFO, Director or Super Admin closes a period.', 'forbidden')
    if not txt(why):
        raise bad('Give the reason.', 'reason_required')
    old = db.val('SELECT books_locked_to FROM companies WHERE code=:c', c=company)
    if old and upto <= old:
        raise conflict(f'Already closed up to {old:%d-%m-%Y}.', 'duplicate')
    db.run('UPDATE companies SET books_locked_to=:d WHERE code=:c', d=upto, c=company)
    audit.write('Period closed', company, old=str(old), new=str(upto), reason=why, category='finance', company=company)
    notify.alert('Period closed', f"{company} books closed up to {upto:%d-%m-%Y} by {g.user['name']}: {why}", ref=company, company=company, severity='info')


def reopen_period(company, to, why):
    if not any(r in ('CFO', 'Director', 'Owner', 'Chairman', 'Super Admin') for r in g.user['roles']) or not can(g.user, 'restore', 'fin'):
        raise denied('Only the Director, CFO or Super Admin can reopen a closed period.', 'forbidden')
    if not txt(why):
        raise bad('Give the reason for reopening.', 'reason_required')
    old = db.val('SELECT books_locked_to FROM companies WHERE code=:c', c=company)
    if not old or to >= old:
        raise bad(f'Choose a date before the current close date{f" {old:%d-%m-%Y}" if old else ""}.', 'invalid')
    db.run('UPDATE companies SET books_locked_to=:d WHERE code=:c', d=to, c=company)
    from . import fin_yearend                 # closed years after the date are reopened: their closing journals are reversed (2.6)
    fin_yearend.reopen_from(company, to, txt(why))
    audit.write('Period reopened', company, old=str(old), new=str(to), reason=why, category='finance', company=company)
    notify.alert('Period reopened', f"{company} books reopened from {to + dt.timedelta(days=1):%d-%m-%Y} by {g.user['name']} ({g.user['main_role']}): {why}",
                 ref=company, company=company, severity='bad')


def close_year(company, code, why, split=None, acknowledge=False):
    """Kept for the older Setup button; the full year-end closing (checklist, profit transfer, reopen) is services/fin_yearend.py."""
    from . import fin_yearend
    y = db.one('SELECT * FROM fin_years WHERE company=:c AND code=:f', c=company, f=code)
    if not y:
        raise not_found('Year not found.')
    return fin_yearend.close(company, code, why, split or fin_yearend.default_split(company), acknowledge)


# ---------- compliance calendar ----------
KINDS = [('gstr1', 'GSTR-1', 'GST', 'monthly', 11), ('gstr3b', 'GSTR-3B and GST payment', 'GST', 'monthly', 20), ('tdspay', 'TDS / TCS deposit (Form 141)', 'TDS', 'monthly', 7),
         ('tdsret', 'TDS returns (Form 138 / 140 / 144)', 'TDS', 'quarterly', 0), ('f16a', 'TDS certificates (Form 131)', 'TDS', 'quarterly16', 0),
         ('tcs', 'TCS return (Form 143)', 'TCS', 'quarterly', 0), ('pf', 'PF contribution & ECR', 'Labour', 'monthly', 15), ('esi', 'ESI contribution', 'Labour', 'monthly', 15),
         ('pt', 'Professional Tax (Telangana)', 'Labour', 'monthly', 10), ('lwf', 'Labour Welfare Fund (Telangana)', 'Labour', 'annual', '01-31'),
         ('advtax', 'Advance tax instalment', 'Income tax', 'advtax', 0), ('taxaudit', 'Tax audit report (Form 26, old 3CA/3CD)', 'Income tax', 'annual', '09-30'),
         ('itr', 'Income-tax return (audit case)', 'Income tax', 'annual', '10-31'), ('gstr9', 'GSTR-9 / 9C annual return', 'GST', 'annual', '12-31'),
         ('roc', 'ROC / MCA annual filings', 'ROC', 'annual', '10-30'), ('stataudit', 'Statutory audit sign-off', 'Audit', 'annual', '09-15'),
         ('intaudit', 'Internal audit review', 'Audit', 'quarterly', 0), ('f16', 'Form 130 (old Form 16) to employees', 'TDS', 'annual', '06-15')]
DONE = ('Filed', 'Paid', 'Done', 'Nil', 'Not applicable')


def compliance(company):
    t = today()
    fy = fy_of(company, t)
    y = fy['from_date'].year
    off = (cfg_compliance().get('off') or {}).get(company, ['tcs', 'roc'])
    st = {r['key']: r for r in db.clean(db.q('SELECT * FROM compliance_status WHERE company=:c', c=company))}
    items = []

    def add(k, label, area, per, due, auto=None):
        key = f'{company}|{k}|{per}'
        r = st.get(key, {})
        a = auto() if auto else None
        items.append({'key': key, 'kind': k, 'label': label, 'area': area, 'period': per, 'due': str(due), 'owner': r.get('owner'),
                      'status': a['status'] if a else r.get('status') or 'Pending', 'done_on': a.get('date') if a else r.get('done_on'),
                      'ref': a.get('ref') if a else r.get('ref') or '', 'auto': bool(a), 'note': r.get('note') or ''})
    for k, label, area, freq, day in KINDS:
        if k in off:
            continue
        if freq == 'monthly':
            m = fy['from_date']
            while m <= t + dt.timedelta(days=40):
                ym = f'{m:%Y-%m}'
                nx = month_end(m) + dt.timedelta(days=1)
                due = dt.date(m.year, 4, 30) if k == 'tdspay' and m.month == 3 else nx.replace(day=day)
                auto = None
                if k in ('gstr1', 'gstr3b'):
                    r_ = '1' if k == 'gstr1' else '3B'
                    auto = (lambda r_=r_, ym=ym: (lambda f: f and {'status': 'Filed', 'date': str(f['filed_on']), 'ref': f['arn']})(
                        db.one('SELECT * FROM gst_filings WHERE company=:c AND ret=:r AND period=:p', c=company, r=r_, p=ym)))
                elif k == 'tdspay':
                    def auto(m=m):
                        es = db.q("""SELECT e.status, c.cin, c.cdate FROM tds_entries e LEFT JOIN tds_challans c ON c.id=e.challan_id WHERE e.company=:c AND e.kind='Payable'
                                     AND e.status<>'Reversed' AND e.amount>0 AND e.ded_date BETWEEN :a AND :b""", c=company, a=m, b=month_end(m))
                        if not es:
                            return {'status': 'Nil', 'ref': 'No deductions'} if month_end(m) < t else None
                        return {'status': 'Paid', 'date': str(es[0]['cdate']), 'ref': ', '.join(sorted({e['cin'] for e in es if e['cin']}))} \
                            if all(e['status'] == 'Deposited' for e in es) else None
                add(k, label, area, f'{m:%b %Y}', due, auto)
                m = nx
        elif freq in ('quarterly', 'quarterly16'):
            for q in ('Q1', 'Q2', 'Q3', 'Q4'):
                qa, qb = fin_tds.q_range(fy['from_date'], q)
                if qa > t + dt.timedelta(days=40):
                    continue
                due = fin_tds.q_due(fy['from_date'], q) + dt.timedelta(days=15) if freq == 'quarterly16' else qb + dt.timedelta(days=30) if k == 'intaudit' else fin_tds.q_due(fy['from_date'], q)
                auto = None
                if k == 'tdsret':
                    def auto(q=q, qb=qb):
                        fs = [Q for Q in (fin_tds.quarter(company, fy['code'], q, f) for f in ('24Q', '26Q', '27Q')) if Q['entries']] \
                            if db.one('SELECT 1 FROM fin_years WHERE company=:c AND code=:f', c=company, f=fy['code']) else []
                        if not fs:
                            return {'status': 'Nil', 'ref': 'No deductions'} if qb < t else None
                        return {'status': 'Filed', 'date': fs[0]['filed']['filed_on'], 'ref': ', '.join(Q['filed']['token'] for Q in fs)} if all(Q['filed'] for Q in fs) else None
                add(k, label, area, f"{q} {fy['code']}", due, auto)
        elif freq == 'advtax':
            for d, p in (('06-15', '15%'), ('09-15', '45%'), ('12-15', '75%'), ('03-15', '100%')):
                add(k, label, area, f'{p} by {d}', dt.date.fromisoformat(f"{y + 1 if d.startswith('03') else y}-{d}"))
        else:
            if k == 'lwf':
                add(k, label, area, f'CY {y}', dt.date(y + 1, 1, 31))
            else:
                add(k, label, area, f'FY {y - 1}-{y % 100:02d}', dt.date.fromisoformat(f'{y}-{day}'))
    for x in items:
        d = dt.date.fromisoformat(x['due'])
        x['late'] = x['status'] not in DONE and d < t
        x['soon'] = not x['late'] and x['status'] not in DONE and d <= t + dt.timedelta(days=15)
        x['documents'] = int(db.val("SELECT COUNT(*) FROM evidence WHERE ref=:r AND status='Active'", r='CMP:' + x['key']) or 0)
    return sorted(items, key=lambda x: x['due'])


def cfg_compliance():
    from ..core.security import setting
    return setting('compliance', {}) or {}


def compliance_mark(key, status, ref='', note=''):
    key, ref, note = txt(key), txt(ref), txt(note)
    if key.count('|') < 2 or status not in ('Pending', 'In progress', 'Filed', 'Paid', 'Done', 'Nil', 'Not applicable'):
        raise bad('Choose the compliance item and a valid status.', 'invalid')
    co = key.split('|')[0]
    check_company(g.user, co)
    if status in ('Filed', 'Paid', 'Done') and not (ref or '').strip():
        raise bad('Enter the acknowledgement / challan / reference number.', 'invalid')
    if db.one('SELECT 1 FROM compliance_status WHERE `key`=:k', k=key):
        db.run('UPDATE compliance_status SET status=:s, ref=:r, note=:n, done_on=:d, updated_by=:u WHERE `key`=:k', s=status, r=(ref or '').strip()[:60], n=(note or '')[:300],
               d=today(), u=g.user['id'], k=key)
    else:
        db.insert('compliance_status', dict(key=key, company=co, status=status, ref=(ref or '').strip()[:60], note=(note or '')[:300], done_on=today(), updated_by=g.user['id']))
    audit.write('Compliance updated', ' '.join(key.split('|')[1:]), new=f'{status} {ref or ""}', reason=note or None, category='finance', company=co)


def compliance_alerts():
    n = 0
    for co in [r['code'] for r in db.q('SELECT code FROM companies WHERE active=1')]:
        it = compliance(co)
        late, soon = [x for x in it if x['late']], [x for x in it if x['soon']]
        if late:
            notify.alert('Compliance overdue', f"{len(late)} overdue: " + '; '.join(f"{x['label']} {x['period']}" for x in late[:3]), ref='compliance', company=co, severity='bad')
            n += 1
        if soon:
            notify.alert('Compliance due soon', f"{len(soon)} due in 15 days: " + '; '.join(f"{x['label']} ({x['due']})" for x in soon[:3]), ref='compliance', company=co, severity='warn')
            n += 1
    return n


# ---------- alerts & dashboard ----------
def alerts(company):
    t = today()
    A = []
    for r in db.q('''SELECT l.branch_id, SUM(l.debit-l.credit) v FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.company=:c AND l.account='1100'
                     GROUP BY l.branch_id HAVING v < -0.5''', c=company):
        A.append({'severity': 'bad', 'title': 'Negative cash', 'message': f"{r['branch_id']}: ₹{r['v']:,.0f}", 'go': {'report': 'cash', 'args': {'branch': r['branch_id']}}})
    ns = db.val('SELECT COUNT(*) FROM stock_balance s JOIN branches b ON b.id=s.branch_id WHERE b.company=:c AND s.qty<0', c=company)
    if ns:
        A.append({'severity': 'bad', 'title': 'Negative stock', 'message': f'{ns} item(s) below zero', 'go': None})
    od = [x for x in open_items(company, 'cust', t) if x['days'] > 30]
    if od:
        A.append({'severity': 'warn', 'title': 'Overdue receivables', 'message': f"{len(od)} bills, ₹{sum(x['due'] for x in od):,.0f}", 'go': {'report': 'ageing'}})
    ym = f'{(t.replace(day=1) - dt.timedelta(days=1)):%Y-%m}'
    R_ = fin_gst.reconcile(company, '2B', ym)
    if R_['loaded'] and R_['mismatches']:
        A.append({'severity': 'warn', 'title': 'GST mismatch', 'message': f"{R_['mismatches']} with GSTR-2B for {ym}", 'go': {'report': 'g2b', 'args': {'ym': ym}}})
    pan = fin_tds.pan_issues(company)
    if pan:
        A.append({'severity': 'warn', 'title': 'TDS: PAN issues', 'message': f'{len(pan)} parties without a valid PAN', 'go': {'report': 'tpan'}})
    tp = [e for e in fin_tds.register(company, status='Deducted') if e['kind'] == 'Payable' and e['interest'] and e['interest']['overdue']]
    if tp:
        A.append({'severity': 'bad', 'title': 'TDS overdue', 'message': f"{len(tp)} deductions not deposited by the 7th · ₹{sum(e['amount'] for e in tp):,.0f}", 'go': {'tab': 'tds'}})
    pend = db.val("SELECT COUNT(*) FROM approvals WHERE status='Pending' AND company=:c AND kind IN ('finv','saudit')", c=company)
    if pend:
        A.append({'severity': 'info', 'title': 'Pending approvals', 'message': f'{pend} finance / stock-audit approvals waiting', 'go': {'page': 'approvals'}})
    un = db.val('SELECT COUNT(*) FROM bank_statement_lines WHERE company=:c AND match_journal IS NULL AND sdate < :d', c=company, d=t - dt.timedelta(days=7))
    if un:
        A.append({'severity': 'warn', 'title': 'Unreconciled bank lines', 'message': f'{un} older than 7 days', 'go': {'tab': 'bank'}})
    late = [x for x in compliance(company) if x['late']]
    if late:
        A.append({'severity': 'bad', 'title': 'Compliance overdue', 'message': '; '.join(f"{x['label']} {x['period']}" for x in late[:3]), 'go': {'tab': 'compliance'}})
    return A


def dashboard(company):
    from .fin_reports import balances, pl
    t = today()
    fy = fy_of(company, t)
    p = pl(company, fy['from_date'], t)
    pm = pl(company, t.replace(day=1), t)
    bal = balances(company, dt.date(2000, 1, 1), t)
    cash = round(sum(v for a, v in bal.items() if a in ('1100', '1105')), 2)
    bank = round(sum(v for a, v in bal.items() if is_bank(a)), 2)
    rec, pay = open_items(company, 'cust', t), open_items(company, 'sup', t)
    ym = f'{t:%Y-%m}'
    s = fin_gst.setoff(company, ym)
    trend = []
    m = fy['from_date']
    while m <= t:
        x = pl(company, m, min(month_end(m), t))
        trend.append({'month': f'{m:%b %y}', 'income': x['total_income'], 'expenses': x['total_expenses'], 'net': x['net']})
        m = month_end(m) + dt.timedelta(days=1)
    return {'company': company, 'fy': fy['code'], 'income': p['total_income'], 'expenses': p['total_expenses'], 'net': p['net'], 'month_net': pm['net'], 'cash': cash, 'bank': bank,
            'receivables': round(sum(x['due'] for x in rec), 2), 'payables': round(sum(x['due'] for x in pay), 2),
            'gst_due': s['cash']['total'], 'gst_due_on': s['due'], 'tds_due': round(sum(e['amount'] for e in fin_tds.register(company, status='Deducted') if e['kind'] == 'Payable'), 2),
            'pending': db.val("SELECT COUNT(*) FROM fin_vouchers WHERE company=:c AND status='Pending approval'", c=company),
            'drafts': db.val("SELECT COUNT(*) FROM fin_vouchers WHERE company=:c AND status IN ('Draft','Returned')", c=company),
            'lock': str(db.val('SELECT books_locked_to FROM companies WHERE code=:c', c=company) or '') or None, 'trend': trend, 'alerts': alerts(company)}


def auto_run():
    """Scheduler hook (once a minute, idempotent): recurring journals due, last month's depreciation, daily compliance alerts."""
    prev = getattr(g, 'user', None)
    done = {}
    try:
        g.user = {**SYSTEM, 'companies': [r['code'] for r in db.q('SELECT code FROM companies WHERE active=1')]}
        done['recurring'] = recurring_run()
        t = today()
        last = f'{(t.replace(day=1) - dt.timedelta(days=1)):%Y-%m}'
        n = 0
        for co in g.user['companies']:
            if db.val('SELECT COUNT(*) FROM fixed_assets WHERE company=:c', c=co) and not db.one('SELECT 1 FROM depreciation_runs WHERE company=:c AND period=:p', c=co, p=last):
                try:
                    with db.session().begin_nested():
                        depreciation_run(co, last, system=True)
                    n += 1
                except Exception as e:  # noqa: BLE001
                    notify.alert('Depreciation failed', f'{co} {last}: {getattr(e, "message", str(e))}', ref=f'DEP-{last}', company=co, severity='bad')
        done['depreciation'] = n
        key = f'fin_compliance_alerted:{t}'
        if not db.one('SELECT 1 FROM settings WHERE skey=:k', k=key):
            done['compliance_alerts'] = compliance_alerts()
            db.run('DELETE FROM settings WHERE skey LIKE :p', p='fin_compliance_alerted:%')
            db.insert('settings', dict(skey=key, svalue=json.dumps(True)))
    finally:
        g.user = prev
    return done


fv.ON_POST.update(bank_line=_bank_line_posted, depreciation=_dep_posted, asset_cap=_cap_posted, asset_dispose=_dispose_posted, asset_impair=_impair_posted,
                  asset_transfer=_transfer_posted)


# ---------- asset & bank reports ----------
@R('fareg', 'Assets', 'Fixed Asset Register')
def _fareg(co, a):
    xs = [asset_view(x) for x in db.session().query(FixedAsset).filter(FixedAsset.company == co).order_by(FixedAsset.no)]
    rows = [[x['no'], x['tag'], x['name'], x['category_name'], x['branch_id'], x['put_to_use'], x['cost'], x['acc_dep'], x['impairment'], x['net_block'], x['status'],
             (x['verifications'][-1]['date'] + (' · Found' if x['verifications'][-1]['found'] else ' · Missing')) if x['verifications'] else 'Never'] for x in xs]
    return out('Fixed Asset Register', [C('Asset no.'), C('Tag'), C('Asset'), C('Category'), C('Location'), C('Put to use', 'date'), C('Cost', 'num'), C('Accumulated depreciation', 'num'),
                                        C('Impairment', 'num'), C('Net block', 'num'), C('Status'), C('Last verified')], rows, [{'asset': x['id']} for x in xs],
               totals=['', '', '', '', '', 'Total', *[round(sum(r[i] for r in rows), 2) for i in range(6, 10)], '', ''])


@R('fadep', 'Assets', 'Depreciation Schedule (Companies Act)')
def _fadep(co, a):
    fy = fy_of(co, a['to'])
    rows = []
    for x in db.session().query(FixedAsset).filter(FixedAsset.company == co).order_by(FixedAsset.no):
        yr = round(sum(d[1] for d in (x.dep_log or []) if str(fy['from_date']) <= d[0] <= str(fy['to_date'])), 2)
        old = x.put_to_use < fy['from_date']
        rows.append([x.tag, x.name, category(x.category)['name'], f'{x.life_years} yrs · {x.method}', float(x.cost) if old else 0, 0 if old else float(x.cost),
                     round(float(x.acc_dep or 0) - yr, 2) if old else 0, yr, float(x.acc_dep or 0), round(float(x.cost) - float(x.acc_dep or 0) - float(x.impairment or 0), 2)])
    return out('Depreciation Schedule (Companies Act)', [C('Tag'), C('Asset'), C('Category'), C('Life / method'), C('Opening gross', 'num'), C('Additions', 'num'),
                                                         C('Opening depreciation', 'num'), C('Depreciation this year', 'num'), C('Closing depreciation', 'num'), C('Net block', 'num')],
               rows, totals=['Total', '', '', '', *[round(sum(r[i] for r in rows), 2) for i in range(4, 10)]])


@R('fait', 'Assets', 'Depreciation as per Income-tax Act (block of assets)')
def _fait(co, a):
    B = it_blocks(co, fy_of(co, a['to'])['code'])
    return out('Depreciation as per Income-tax Act', [C('Block'), C('Rate %', 'pct'), C('Opening WDV', 'num'), C('Additions ≥ 180 days', 'num'), C('Additions < 180 days', 'num'),
                                                      C('Deletions', 'num'), C('Depreciation', 'num'), C('Closing WDV', 'num')],
               [[b['block'], b['rate'], b['open'], b['add_full'], b['add_half'], b['deletions'], b['depreciation'], b['close']] for b in B],
               note='Additions used for less than 180 days in the year get half the rate. Figures for the tax audit report (Form 26, old 3CD).')


@R('famov', 'Assets', 'Asset Movement Report')
def _famov(co, a):
    rows = sorted(([m[0], x.tag, x.name, m[1], m[2]] for x in db.session().query(FixedAsset).filter(FixedAsset.company == co) for m in (x.moves or [])
                   if str(a['from']) <= m[0] <= str(a['to'])), key=lambda r: r[0])
    return out('Asset Movement Report', [C('Date', 'date'), C('Tag'), C('Asset'), C('Movement'), C('Detail')], rows)


@R('faver', 'Assets', 'Physical Asset Verification & Variance')
def _faver(co, a):
    rows, drill = [], []
    for x in db.session().query(FixedAsset).filter(FixedAsset.company == co, FixedAsset.status != 'Disposed'):
        v = (x.verifications or [None])[-1]
        bv = round(float(x.cost) - float(x.acc_dep or 0), 2)
        rows.append([x.tag, x.name, x.branch_id, bv, v['date'] if v else '', ('Found' if v['found'] else 'Not found') if v else 'Not verified', v['condition'] if v else '',
                     v['by'] if v else '', bv if v and not v['found'] else 0])
        drill.append({'asset': x.id})
    return out('Physical Asset Verification & Variance', [C('Tag'), C('Asset'), C('Location'), C('Book value', 'num'), C('Verified on', 'date'), C('Result'), C('Condition'),
                                                          C('By'), C('Variance (missing value)', 'num')], rows, drill,
               note='Stock is verified through Stock audit; this register covers fixed assets.')


@R('bbrs', 'Bank', 'Bank Reconciliation Statement')
def _bbrs(co, a):
    acc = a.get('account') or '1120'
    B = brs(co, acc, a['to'])
    rows = [['Balance as per books', B['book']], ['Add: payments / cheques issued but not presented', round(-sum(x['amt'] for x in B['issued']), 2)],
            ['Less: deposits not yet credited by the bank', round(-sum(x['amt'] for x in B['not_credited']), 2)],
            ['Add / (less): bank entries not in books', round(sum(x['amount'] for x in B['bank_only']), 2)], ['Balance as per bank statement (derived)', B['statement']],
            ['Last statement balance imported', (B['last_statement'] or {}).get('balance')]]
    return out(f'Bank reconciliation · {acc}', [C(f"As on {a['to']:%d-%m-%Y}"), C('Amount', 'num')], rows)


@R('bpend', 'Bank', 'Pending Reconciliation Report')
def _bpend(co, a):
    acc = a.get('account') or '1120'
    B = brs(co, acc, a['to'])
    t = today()
    age = lambda d: (t - dt.date.fromisoformat(str(d)[:10])).days  # noqa: E731
    rows = [['Books only · payment', x['txn_date'], x['ref'], x['narration'], x['amt'], age(x['txn_date'])] for x in B['issued']] + \
           [['Books only · deposit', x['txn_date'], x['ref'], x['narration'], x['amt'], age(x['txn_date'])] for x in B['not_credited']] + \
           [['Bank only', x['sdate'], x['ref'], x['narration'], x['amount'], age(x['sdate'])] for x in B['bank_only']]
    return out('Pending Reconciliation Report', [C('Where'), C('Date', 'date'), C('Ref'), C('Narration'), C('Amount', 'num'), C('Age (days)', 'qty')],
               sorted(rows, key=lambda r: -r[5]))


@R('bchq', 'Bank', 'Cheque Register')
def _bchq(co, a):
    rows = [[str(c['cdate']), c['cheque_no'], c['kind'], c['party'], c['account'], float(c['amount']), c['status'], c['voucher_no']]
            for c in db.q('SELECT * FROM cheques WHERE company=:c ORDER BY cdate DESC', c=co)]
    return out('Cheque Register', [C('Date', 'date'), C('Cheque no.'), C('Issued / received'), C('Party'), C('Bank'), C('Amount', 'num'), C('Status'), C('Voucher')], rows,
               [{'voucher': r[7]} for r in rows])


@R('bcash', 'Bank', 'Daily Cash Closing & Handover')
def _bcash(co, a):
    rows = []
    for s in db.q('''SELECT s.no, s.shift_date, s.location, s.counter, s.status, s.figures, s.difference_reason, u.name FROM cash_shifts s LEFT JOIN users u ON u.id=s.user_id
                     JOIN branches b ON b.location=s.location AND b.company=:c WHERE s.shift_date BETWEEN :a AND :z ORDER BY s.shift_date''', c=co, a=a['from'], z=a['to']):
        f = json.loads(s['figures']) if isinstance(s['figures'], str) else (s['figures'] or {})
        rows.append([str(s['shift_date']), s['location'], f"Shift {s['no']} · counter {s['counter']}", f.get('expected'), f.get('counted'), f.get('difference'), s['status'], s['name']])
    return out('Daily Cash Closing & Handover', [C('Date', 'date'), C('Location'), C('Kind'), C('Expected', 'num'), C('Counted', 'num'), C('Short / excess', 'num'), C('Status'), C('By')],
               rows, note='Denomination-wise counts and approvals are in POS → Cashier closing; this report reads them.')
