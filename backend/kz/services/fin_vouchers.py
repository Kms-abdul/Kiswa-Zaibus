"""Finance & tax: the voucher engine. 21 voucher types with GST and TDS worked out automatically, maker → checker → approver by rules and
limits, posting into the general ledger as one balanced journal, reversal (never edit or delete a posted voucher), cancellation of drafts,
duplicate-bill and three-way-match checks, payroll and period locks (books closed, GSTR-3B filed, financial year closed)."""
import datetime as dt
import json
import re
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit, notify
from ..core.approvals import handler, on_reject, on_return, request as approval_request
from ..core.numbering import next_no
from ..core.rbac import can, limit
from ..errors import bad, conflict, denied, not_found
from ..models import FinVoucher
from . import fin_tds
from .common import check_company, check_scope, parse_date, today
from .fin_common import (CLOSERS, txt, HEADS, INPUT, LIMIT_KEY, OUTPUT, VTYPES, cfg, gst_calc, gstin_check, ho, is_bank, is_cash, lock_reason, month_end, r2)

EDITABLE = ('Draft', 'Returned')
DEAD = ('Cancelled', 'Rejected', 'Reversed')
ON_POST, ON_DROP = {}, {}      # options.on_post hooks (TDS challan, GST challan, payroll, depreciation …)


def _rank(role):
    return float(db.val('SELECT approval_rank FROM roles WHERE name=:r', r=role) or 0)


# ---------- parties ----------
def party(company, kind, pid):
    if not pid:
        return None
    if kind == 'cust':
        r = db.one('SELECT id, name, gstin FROM customers WHERE id=:i AND company=:c', i=pid, c=company)
    elif kind == 'sup':
        r = db.one('SELECT id, name, gstin FROM suppliers WHERE id=:i AND company=:c', i=pid, c=company)
    elif kind == 'emp':
        r = db.one('SELECT id, name, NULL AS gstin, pan FROM employees WHERE id=:i AND company=:c', i=pid, c=company)
    else:
        r = None
    if not r:
        raise bad(f'Party {pid} not found for {company}.', 'unknown_party')
    pan = r.get('pan') or (fin_tds.party_tax(company, pid) or {}).get('pan') or ''
    return {'kind': kind, 'id': r['id'], 'name': r['name'], 'gstin': r['gstin'] or '', 'pan': pan}


def party_account(vtype, p, opts):
    kind = VTYPES[vtype][2]
    if opts.get('advance'):
        return '2210' if kind == 'cust' else '1700'
    if p and p['kind'] == 'emp':
        return '2310'
    return '1200' if kind == 'cust' else '2000' if kind == 'sup' else None


# ---------- build & validate ----------
def build(v):
    """Turn what the maker entered into the full journal: GST heads (output / input / reverse charge / blocked credit added to cost),
    TDS (payable, or receivable when the customer deducts) and the balancing party line."""
    t = VTYPES[v.vtype]
    br = v.branch_id or ho(v.company)
    opts = dict(v.options or {})
    L = []
    for ln in v.entered or []:
        dr, cr = r2(ln.get('debit')), r2(ln.get('credit'))
        if not ln.get('account') or (not dr and not cr):
            continue
        L.append({'account': ln['account'], 'branch': ln.get('branch') or br, 'debit': float(dr), 'credit': float(cr), 'party': ln.get('party') or None,
                  'cost_centre': ln.get('cost_centre') or v.cost_centre or None, 'dept': ln.get('dept') or v.dept or None})
    auto = []

    def push(acct, side, amt, why):
        if amt > 0.004:
            auto.append({'account': acct, 'branch': br, 'debit': amt if side == 'debit' else 0, 'credit': amt if side == 'credit' else 0, 'party': None,
                         'cost_centre': v.cost_centre or None, 'dept': v.dept or None, 'auto': why})
    gcalc = None
    gst = v.gst or {}
    if gst.get('rows'):
        pos = gst.get('pos') or (gstin_check(v.gstin)['state'] if v.gstin and gstin_check(v.gstin)['ok'] else None)
        gcalc = gst_calc(gst['rows'], pos, gst.get('inter'))
        T = gcalc['t']
        d = gst.get('dir') or t[4]
        if d in ('out', 'adv'):
            for h in HEADS:
                push(OUTPUT[h], 'credit', T[h], 'Output ' + h.upper())
        elif d == 'outRev':
            for h in HEADS:
                push(OUTPUT[h], 'debit', T[h], 'Output ' + h.upper() + ' reversed')
        elif d in ('in', 'inRev'):
            side = 'debit' if d == 'in' else 'credit'
            itc = gst.get('itc') or 'Eligible'
            if itc == 'Eligible':
                for h in HEADS:
                    push(INPUT[h], side, T[h], 'Input ' + h.upper())
            else:
                tgt = next((x for x in L if x[side] > 0 and not is_bank(x['account']) and not is_cash(x['account'])), None)
                if tgt:
                    tgt[side] = float(r2(tgt[side] + T['tax']))
                    tgt['note'] = f'GST added to cost: ITC {itc.lower()}'
            if gst.get('rcm') and d == 'in':
                for h in HEADS:
                    push('2140', 'credit', T[h], 'RCM liability ' + h.upper())
    v.gst = dict(gst, calc=gcalc) if gst else None
    tds_amt = 0
    tds = dict(v.tds or {})
    if tds.get('section') and t[5] and v.party_id:
        c = fin_tds.calc(v.company, v.party_id, tds['section'], tds.get('base') or 0, v.vdate, v.no if v.no else None)
        tds.update(rate=c['rate'], note=c['note'])
        tds_amt = float(r2(tds.get('override_amount'))) if tds.get('override') else c['amount']
        tds['amount'] = tds_amt
        if tds_amt > 0:
            sec = fin_tds.section(tds['section']) or {}
            if t[5] == 'recv':
                auto.append({'account': '1600', 'branch': br, 'debit': tds_amt, 'credit': 0, 'party': v.party_id, 'cost_centre': None, 'dept': None,
                             'auto': 'TDS deducted by customer'})
            else:
                auto.append({'account': '2305' if sec.get('tcs') else '2300', 'branch': br, 'debit': 0, 'credit': tds_amt, 'party': v.party_id,
                             'cost_centre': None, 'dept': None, 'auto': 'TDS ' + tds['section']})
    v.tds = tds or None
    allx = L + auto
    diff = float(r2(sum(Decimal(str(x['debit'])) for x in allx) - sum(Decimal(str(x['credit'])) for x in allx)))
    pa = party_account(v.vtype, {'kind': v.party_kind} if v.party_kind else None, opts) if v.party_id else None
    if pa and abs(diff) > 0.004:
        allx.append({'account': pa, 'branch': br, 'debit': 0 if diff > 0 else abs(diff), 'credit': abs(diff) if diff > 0 else 0, 'party': v.party_id,
                     'cost_centre': v.cost_centre or None, 'dept': v.dept or None, 'auto': 'Party: ' + (v.party_name or '')})
    dr = float(r2(sum(Decimal(str(x['debit'])) for x in allx)))
    cr = float(r2(sum(Decimal(str(x['credit'])) for x in allx)))
    v.built = allx
    v.total = max(dr, cr)
    v.taxable = gcalc['t']['taxable'] if gcalc else float(r2(sum(Decimal(str(x['debit'] or x['credit'])) for x in L if not is_bank(x['account']) and not is_cash(x['account']))))
    v.tax = gcalc['t']['tax'] if gcalc else 0
    v.tds_amount = tds_amt
    return dr - cr


def validate(v, diff):
    t = VTYPES[v.vtype]
    e = []
    for ln in v.entered or []:
        try:
            if Decimal(str(ln.get('debit') or 0)) < 0 or Decimal(str(ln.get('credit') or 0)) < 0:
                e.append('Amounts cannot be negative: put the amount on the other side instead.')
                break
            if Decimal(str(ln.get('debit') or 0)) and Decimal(str(ln.get('credit') or 0)):
                e.append('A line has either a debit or a credit, not both.')
                break
        except Exception:  # noqa: BLE001
            e.append('Amounts must be numbers.')
            break
    if any(Decimal(str((r or {}).get('taxable') or 0)) < 0 for r in ((v.gst or {}).get('rows') or [])):
        e.append('Taxable values cannot be negative; use a credit / debit note.')
    if v.vdate and not db.one('SELECT 1 FROM fin_years WHERE company=:c AND :d BETWEEN from_date AND to_date', c=v.company, d=v.vdate):
        e.append(f'No financial year is open for {v.vdate:%d-%m-%Y}. Check the date (Setup → Periods).')
    if v.total and Decimal(str(v.total)) >= Decimal('1e13'):
        e.append('The amount is too large.')
    if t[2] and v.vtype in ('SI', 'PI', 'CN', 'DN') and not v.party_id:
        e.append(f"Choose the {'customer' if t[2] == 'cust' else 'supplier'}.")
    if v.vtype in ('RV', 'PV') and not v.party_id and len(v.built) < 2:
        e.append('Choose the party or add both ledger lines.')
    if abs(diff) > 0.011:
        e.append(f'Debits and credits differ by ₹{abs(diff):,.2f}.')
    if not v.built:
        e.append('Add the ledger lines.')
    names = {r['code']: r['active'] for r in db.q('SELECT code, active FROM accounts WHERE company=:c', c=v.company)}
    for x in v.built:
        if x['account'] not in names:
            e.append(f"Unknown account {x['account']}.")
        elif not names[x['account']]:
            e.append(f"Account {x['account']} is not active for {v.company}.")
        if x['debit'] and x['credit']:
            e.append('A line cannot have both debit and credit.')
    if v.vtype == 'CV' and any(not is_bank(x['account']) and not is_cash(x['account']) and x['account'] != '1110' for x in v.built):
        e.append('A contra voucher moves money between cash and bank accounts only.')
    if v.gstin and v.gst and not gstin_check(v.gstin)['ok']:
        e.append(f"Party GSTIN {v.gstin} is not valid: {gstin_check(v.gstin)['msg']}.")
    if v.vtype in ('SI', 'CN') and v.gst and any(not r.get('hsn') for r in v.gst.get('rows', [])):
        e.append('Every GST line needs an HSN / SAC code.')
    return e


def route(v, u):
    """Who must approve: the backdated-entry rule, then the approval-rule table, then the maker's limit and 'always needs a checker' types."""
    why = lock_reason(v.company, v.vdate)
    if why:
        return {'role': 'CFO', 'why': why + ': backdated entry needs CFO / Director approval'}
    if v.vdate > today():
        return {'role': 'CFO', 'why': f'Post-dated entry ({v.vdate:%d-%m-%Y}) needs CFO / Director approval'}
    role, why = None, ''
    accts = {x['account'] for x in v.built}
    for r in db.q('SELECT * FROM fin_approval_rules'):
        if r['vtype'] in ('*', v.vtype) and (not r['account'] or r['account'] in accts) and (not r['branch_id'] or r['branch_id'] == v.branch_id) \
                and Decimal(str(v.total)) > Decimal(str(r['above'] or 0)):
            if not role or _rank(r['role']) > _rank(role):
                role = r['role']
                why = (f"Rule: {'any voucher' if r['vtype'] == '*' else VTYPES[r['vtype']][0]}" + (f" touching {r['account']}" if r['account'] else '')
                       + (f" at {r['branch_id']}" if r['branch_id'] else '') + f" above ₹{r['above']:,.0f}")
    key = LIMIT_KEY.get(v.vtype, 'jv')
    always = v.vtype in cfg()['always_approve']
    if not role and (always or limit(u, key) < float(v.total)):
        role = db.val('''SELECT r.name FROM roles r JOIN approval_limits l ON l.role=r.name AND l.limit_key=:k WHERE r.active=1 AND r.approval_rank>=1
                         AND l.amount>=:a ORDER BY r.approval_rank, r.name LIMIT 1''', k=key, a=v.total) or 'Director'
        why = f'{VTYPES[v.vtype][0]} always needs a checker' if always else f'Above maker limit (₹{limit(u, key):,.0f})'
    return {'role': role, 'why': why} if role else None


def has_evidence(no):
    return bool(db.val("SELECT COUNT(*) FROM evidence WHERE ref=:r AND status='Active'", r=no))


def attach_needed(v):
    m = cfg()['attach'].get(v.vtype)
    return m is not None and float(v.total) >= float(m) and not has_evidence(v.no)


# ---------- life cycle ----------
def _log(v, action, note=''):
    u = getattr(g, 'user', None) or {}
    log = list((v.options or {}).get('log') or [])
    log.append([dt.datetime.now().isoformat(timespec='seconds'), u.get('name', 'System'), u.get('main_role', ''), action, note or ''])
    v.options = {**(v.options or {}), 'log': log}
    audit.write('Finance voucher · ' + action, v.no, new=f'{VTYPES[v.vtype][0]} ₹{float(v.total or 0):,.2f} · {v.status}', reason=note or None,
                category='finance', company=v.company, branch=v.branch_id)


def _apply(v, d):
    if 'vdate' in d:
        v.vdate = parse_date(d['vdate'], 'voucher date') if isinstance(d['vdate'], str) else d['vdate']
    for k in ('branch_id', 'cost_centre', 'dept', 'ref', 'narration'):
        if k in d:
            setattr(v, k, (d[k] or None) if k != 'narration' else (d[k] or ''))
    if 'party' in d or 'party_id' in d:
        p = d.get('party') or {'kind': d.get('party_kind'), 'id': d.get('party_id')}
        pp = party(v.company, p.get('kind') or VTYPES[v.vtype][2], p.get('id')) if p and p.get('id') else None
        v.party_kind, v.party_id, v.party_name, v.gstin = (pp['kind'], pp['id'], pp['name'], pp['gstin'] or None) if pp else (None, None, (p or {}).get('name'), None)
    if 'lines' in d:
        v.entered = [{k: ln.get(k) for k in ('account', 'branch', 'debit', 'credit', 'party', 'cost_centre', 'dept')} for ln in d['lines'] or []]
    if 'gst' in d:
        v.gst = d['gst'] or None
    if 'tds' in d:
        v.tds = d['tds'] or None
    if 'options' in d:
        keep = {k: x for k, x in (v.options or {}).items() if k == 'log'}
        v.options = {**keep, **(d['options'] or {})}


def create(d, system=False):
    u = g.user
    vt = d.get('vtype')
    if vt not in VTYPES:
        raise bad('Choose the voucher type.', 'invalid')
    co = d.get('company')
    if not system:
        check_company(u, co)
        if not can(u, 'add', 'fin'):
            raise denied('Your role cannot create finance vouchers.', 'forbidden')
    br = d.get('branch_id') or ho(co)
    if not system and br != ho(co):
        check_scope(u, br)
    series = (cfg().get('series') or {}).get(vt) or VTYPES[vt][1]
    v = FinVoucher(no=next_no(co, series), company=co, vtype=vt, vdate=today(), branch_id=br, narration='', entered=[], options={}, status='Draft',
                   maker=u['id'], maker_role=u['main_role'], total=0, taxable=0, tax=0, tds_amount=0, paid=0)
    _apply(v, d)
    build(v)
    db.add(v)
    db.session().flush()
    _log(v, 'Created', f'{VTYPES[vt][0]} · ₹{v.total:,.2f}')
    return v


def get(vid, lock=False):
    v = db.session().get(FinVoucher, vid, with_for_update=lock)
    if not v or v.company not in g.user['companies']:
        raise not_found('Voucher not found.')
    return v


def update(vid, d):
    v = get(vid, lock=True)
    if v.status not in EDITABLE:
        raise conflict(f'{v.no} is {v.status}; posted vouchers cannot be edited — use a reversal.', 'wrong_status')
    if v.maker != g.user['id'] and not can(g.user, 'edit', 'fin'):
        raise denied()
    before = json.dumps({'lines': v.entered, 'gst': v.gst, 'tds': v.tds, 'date': str(v.vdate)})[:300]
    _apply(v, d)
    build(v)
    _log(v, 'Edited', 'Before: ' + before)
    return v


def _norm(s):
    return re.sub(r'^0+', '', re.sub(r'[^A-Z0-9]', '', str(s or '').upper()))


def dup_bill(v):
    if v.vtype not in ('PI', 'EX') or not v.party_id or not v.ref:
        return {}
    r = _norm(v.ref)
    for x in db.q("SELECT no, ref FROM fin_vouchers WHERE company=:c AND vtype IN ('PI','EX') AND party_id=:p AND id<>:i AND status NOT IN :d",
                  c=v.company, p=v.party_id, i=v.id or 0, d=DEAD):
        if _norm(x['ref']) == r:
            return {'hard': True, 'msg': f'Duplicate vendor bill: {v.party_name} invoice {v.ref} is already booked ({x["no"]}).'}
    for x in db.q('SELECT bill_no FROM supplier_bills WHERE company=:c AND supplier_id=:p', c=v.company, p=v.party_id):
        if _norm(x['bill_no']) == r:
            return {'hard': True, 'msg': f'Duplicate vendor bill: {v.party_name} invoice {v.ref} is already booked in purchases.'}
    days = cfg()['dup_days']
    near = db.one('''SELECT no, vdate FROM fin_vouchers WHERE company=:c AND vtype IN ('PI','EX') AND party_id=:p AND id<>:i AND status NOT IN :d
                     AND ABS(total-:t) < 1 AND ABS(DATEDIFF(vdate, :dt)) <= :n LIMIT 1''', c=v.company, p=v.party_id, i=v.id or 0, d=DEAD, t=v.total, dt=v.vdate, n=days)
    return {'soft': True, 'msg': f"Possible duplicate: {near['no']} for the same supplier and amount on {near['vdate']:%d-%m-%Y}."} if near else {}


def three_way(v):
    po_no = (v.options or {}).get('po')
    po = db.one('SELECT id, no FROM purchase_orders WHERE no=:n AND company=:c', n=po_no, c=v.company)
    if not po:
        return {'ok': False, 'msg': f'Purchase order {po_no} not found.'}
    ls = db.q('SELECT qty, rate, received_qty, rejected_qty FROM purchase_order_lines WHERE po_id=:i', i=po['id'])
    po_val = float(r2(sum(Decimal(str(x['qty'])) * Decimal(str(x['rate'])) for x in ls)))
    grn_val = float(r2(sum(Decimal(str(x['received_qty'] - x['rejected_qty'])) * Decimal(str(x['rate'])) for x in ls)))
    inv = float(v.taxable)
    tol = cfg()['three_way_tolerance']
    if not grn_val:
        return {'ok': False, 'po': po_val, 'grn': 0, 'invoice': inv, 'msg': f'No goods receipt yet for {po_no}.'}
    ok = abs(inv - grn_val) / grn_val * 100 <= tol and (not po_val or abs(inv - po_val) / po_val * 100 <= tol)
    return {'ok': ok, 'po': po_val, 'grn': grn_val, 'invoice': inv,
            'msg': 'PO, GRN and invoice agree.' if ok else f'Invoice ₹{inv:,.0f} differs from GRN ₹{grn_val:,.0f} / PO ₹{po_val:,.0f} by more than {tol}%.'}


def submit(vid, direct=False, system=False):
    """Validate, then post at once (within the maker's limit, no rule hit) or send for approval."""
    v = get(vid, lock=True) if not isinstance(vid, FinVoucher) else vid
    u = g.user
    if v.status not in EDITABLE:
        raise conflict(f'{v.no} is {v.status}.', 'wrong_status')
    if not system and not can(u, 'submit', 'fin') and not can(u, 'add', 'fin'):
        raise denied()
    diff = build(v)
    err = validate(v, diff)
    if err:
        raise bad(' '.join(err), 'invalid_voucher')
    r = route(v, u)
    opts = v.options or {}
    dup = dup_bill(v)
    if dup.get('hard'):
        raise conflict(dup['msg'], 'duplicate_bill')
    if dup.get('soft') and not opts.get('dup_ok'):
        raise conflict(dup['msg'] + ' Tick “Not a duplicate” to continue.', 'possible_duplicate')
    if not system and attach_needed(v) and (not r or direct):
        m = cfg()['attach'][v.vtype]
        raise conflict(f"Attach the supporting document first: every {VTYPES[v.vtype][0]}{f' of ₹{m:,.0f} or more' if m else ''} needs one.", 'evidence_required')
    if v.vtype == 'PI' and opts.get('po'):
        m = three_way(v)
        v.options = {**opts, 'match': m}
        if not m['ok'] and not opts.get('match_ok'):
            raise conflict(m['msg'] + ' Tick “Accept variance” with a reason to continue.', 'three_way_mismatch')
    if not r or direct:
        return post(v)
    detail = f"{v.narration} · ₹{v.total:,.2f}" + (f" · GST ₹{v.tax:,.2f}" if v.tax else '') + (f" · TDS {v.tds['section']} ₹{v.tds_amount:,.0f}" if v.tds_amount else '') + f" · {r['why']}"
    ap = approval_request('finv', f"{VTYPES[v.vtype][0]} {v.no}" + (f' · {v.party_name}' if v.party_name else ''), {'id': v.id}, r['role'],
                          company=v.company, branch=v.branch_id, amount=v.total, reason=r['why'], ref=v.no, detail=detail[:500])
    if attach_needed(v):
        notify.alert('Missing evidence', f'{v.no}: attach the supporting document — it cannot be approved without it.', ref=v.no, company=v.company,
                     branch=v.branch_id, severity='warn')
    v.status, v.approval_no, v.approver_role = 'Pending approval', ap['no'], r['role']
    _log(v, 'Submitted for approval', f"{ap['no']} · {r['role']} · {r['why']}")
    return v


def post(v, approved=False):
    from . import ledger
    diff = build(v)
    err = validate(v, diff)
    if err:
        raise bad(' '.join(err), 'invalid_voucher')
    why = lock_reason(v.company, v.vdate)
    if why and not approved:
        raise conflict(why + '. A Director or CFO must approve this backdated entry.', 'books_locked')
    label = VTYPES[v.vtype][0].replace(' Voucher', '')[:30]
    lines = [{'account': x['account'], 'branch': x['branch'], 'debit': x['debit'] or None, 'credit': x['credit'] or None, 'party': x.get('party'),
              'cost_centre': x.get('cost_centre'), 'dept': x.get('dept')} for x in v.built]
    lines = [{k: val for k, val in ln.items() if val is not None} for ln in lines]
    j = ledger.post(v.company, v.vdate, label, v.no, ((v.narration or label) + (f' · {v.party_name}' if v.party_name else ''))[:300], lines,
                    force=bool(why and approved), approval_no=v.approval_no)
    v.journal_no, v.status, v.posted_at = j.no, 'Posted', dt.datetime.now()
    opts = v.options or {}
    if v.tds and v.tds.get('section') and v.party_id:
        db.insert('tds_entries', dict(company=v.company, voucher_no=v.no, ded_date=v.vdate, doc_date=parse_date(opts['doc_date'], 'bill date') if opts.get('doc_date') else v.vdate,
                                      party=v.party_id, name=v.party_name, pan=(fin_tds.party_tax(v.company, v.party_id) or {}).get('pan'), section=v.tds['section'],
                                      base=r2(v.tds.get('base')), rate=v.tds.get('rate') or 0, amount=r2(v.tds.get('amount')),
                                      kind='Receivable' if VTYPES[v.vtype][5] == 'recv' else 'Payable',
                                      status='Deducted' if (v.tds.get('amount') or 0) > 0 else 'No deduction', branch_id=v.branch_id, note=(v.tds.get('note') or '')[:200]))
    for no, amt in (opts.get('alloc') or {}).items():          # receipts against sales invoices / sales vouchers
        amt = r2(amt)
        if amt > 0:
            if not db.run('UPDATE invoices SET balance=GREATEST(0, balance-:a) WHERE no=:n AND company=:c', a=amt, n=no, c=v.company).rowcount:
                db.run('UPDATE fin_vouchers SET paid=paid+:a WHERE no=:n AND company=:c', a=amt, n=no, c=v.company)
    for no, amt in (opts.get('bill_alloc') or {}).items():     # payments against purchase bills / purchase vouchers
        amt = r2(amt)
        if amt > 0 and not db.run('UPDATE supplier_bills SET paid=paid+:a WHERE bill_no=:n AND company=:c', a=amt, n=no, c=v.company).rowcount:
            db.run('UPDATE fin_vouchers SET paid=paid+:a WHERE no=:n AND company=:c', a=amt, n=no, c=v.company)
    if opts.get('cheque') and v.vtype in ('PV', 'RV'):
        bank = next((x['account'] for x in v.built if is_bank(x['account'])), '1120')
        db.insert('cheques', dict(company=v.company, account=bank, cheque_no=str(opts['cheque'])[:20], cdate=v.vdate, party=v.party_name, amount=v.total,
                                  kind='Issued' if v.vtype == 'PV' else 'Received', status='Issued' if v.vtype == 'PV' else 'Deposited', voucher_no=v.no,
                                  log=json.dumps([[str(dt.datetime.now())[:19], g.user['name'], 'Issued' if v.vtype == 'PV' else 'Deposited']])))
    if v.reversal_of:
        t = db.session().get(FinVoucher, v.reversal_of)
        if t:
            t.status, t.reversed_by = 'Reversed', v.no
            _log(t, 'Reversed', 'by ' + v.no)
            db.run("UPDATE tds_entries SET status='Reversed' WHERE voucher_no=:n AND company=:c", n=t.no, c=t.company)
    orig = opts.get('reverses_journal') or (db.val('SELECT journal_no FROM fin_vouchers WHERE id=:i', i=v.reversal_of) if v.reversal_of else None)
    if orig:
        db.run('UPDATE journals SET reversed_by_no=:r WHERE no=:o AND company=:c', r=j.no, o=orig, c=v.company)
        j.reverses_no = orig
    hook = ON_POST.get(opts.get('on_post'))
    if hook:
        hook(v)
    _log(v, 'Approved and posted' if approved else 'Posted', j.no + (' · into closed / filed period with approval' if why else ''))
    _cash_alert(v)
    return v


def _cash_alert(v):
    for b in {x['branch'] for x in v.built if is_cash(x['account'])}:
        bal = db.val('''SELECT COALESCE(SUM(l.debit-l.credit), 0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                        WHERE j.company=:c AND l.account='1100' AND l.branch_id=:b AND j.txn_date<=CURDATE()''', c=v.company, b=b)
        if bal is not None and bal < -0.5:
            notify.alert('Negative cash', f'Cash at {b} is negative (₹{bal:,.0f}) after {v.no}.', ref=v.no, company=v.company, branch=b, severity='bad')


@handler('finv')
def _approve(p, apr):
    v = db.session().get(FinVoucher, p['id'], with_for_update=True)
    if not v:
        raise not_found('Voucher not found.')
    if v.status != 'Pending approval':
        raise conflict(f'{v.no} is {v.status}.', 'wrong_status')
    if v.maker == g.user['id']:
        raise denied('The maker cannot approve their own voucher.', 'segregation')
    if attach_needed(v):
        raise conflict(f'Supporting document missing for {v.no}. Attach it before approving.', 'evidence_required')
    v.approved_by, v.approved_at = g.user['id'], dt.datetime.now()
    v.approver_role = g.user['main_role']
    post(v, approved=True)
    return f'{v.no} approved and posted ({v.journal_no})'


def _drop(v, status, reason):
    v.status, v.reason = status, (reason or '')[:300]
    hook = ON_DROP.get((v.options or {}).get('on_post'))
    if hook and status != 'Returned':
        hook(v)
    _log(v, status, reason)


@on_reject('finv')
def _reject(p, apr, reason):
    v = db.session().get(FinVoucher, p['id'], with_for_update=True)
    if v:
        _drop(v, 'Rejected', reason)


@on_return('finv')
def _return(p, apr, reason):
    v = db.session().get(FinVoucher, p['id'], with_for_update=True)
    if v:
        v.approval_no = None
        _drop(v, 'Returned', reason)


def cancel(vid, why):
    v = get(vid, lock=True)
    if v.status not in ('Draft', 'Returned', 'Rejected'):
        raise conflict('Only draft, returned or rejected vouchers can be cancelled.', 'wrong_status')
    why = txt(why)
    if not why:
        raise bad('Give the reason.', 'reason_required')
    if v.maker != g.user['id'] and not can(g.user, 'cancel', 'fin'):
        raise denied()
    hook = ON_DROP.get((v.options or {}).get('on_post'))
    if hook and v.status != 'Rejected':
        hook(v)
    v.status, v.reason = 'Cancelled', why.strip()[:300]
    _log(v, 'Cancelled', why)
    return v


def reverse(vid=None, journal_no=None, why='', on=None):
    """A reversal voucher (RX) with every debit and credit swapped. Always goes through approval (RX is in 'always approve')."""
    why = txt(why)
    if not why:
        raise bad('Give the reason for the reversal.', 'reason_required')
    if not can(g.user, 'reverse', 'fin'):
        raise denied('Your role cannot reverse vouchers.', 'forbidden')
    src = get(vid, lock=True) if vid else None
    jno = src.journal_no if src else journal_no
    j = db.one('SELECT * FROM journals WHERE no=:n', n=jno) if jno else None
    if not j or j['company'] not in g.user['companies']:
        raise conflict('Only posted vouchers can be reversed.', 'wrong_status')
    if (src and src.status == 'Reversed') or j['reversed_by_no']:
        raise conflict('Already reversed.', 'duplicate')
    if db.one("SELECT 1 FROM fin_vouchers WHERE vtype='RX' AND status='Pending approval' AND ref=:r AND company=:c", r=src.no if src else jno, c=j['company']):
        raise conflict('A reversal is already waiting for approval.', 'duplicate')
    ls = db.q('SELECT * FROM journal_lines WHERE journal_id=:i ORDER BY line_no', i=j['id'])
    d = {'vtype': 'RX', 'company': j['company'], 'vdate': on or str(today()), 'branch_id': ls[0]['branch_id'] if ls else ho(j['company']),
         'ref': src.no if src else jno, 'narration': f"Reversal of {src.no if src else jno}: {why.strip()}"[:300],
         'lines': [{'account': x['account'], 'branch': x['branch_id'], 'debit': float(x['credit']), 'credit': float(x['debit']), 'party': x['party'],
                    'cost_centre': x.get('cost_centre'), 'dept': x.get('dept')} for x in ls],
         'options': {} if src else {'reverses_journal': jno}}
    v = create(d)
    v.reversal_of = src.id if src else None
    _log(v, 'Reversal requested', why)
    return submit(v.id)


# ---------- payroll ----------
def payroll(company, month):
    """Salary voucher for a month: gross to 5400, employer PF / ESI to 5420; PF, ESI, professional tax and TDS (s.192) withheld;
    net pay to salaries payable. Created as a draft for review."""
    m1 = parse_date(month[:7] + '-01', 'month') if isinstance(month, str) else month.replace(day=1)
    end = month_end(m1)
    es = db.q('SELECT * FROM employees WHERE company=:c AND active=1 AND joined<=:d ORDER BY id', c=company, d=end)
    if not es:
        raise conflict(f'No employees on the payroll for {company}.', 'no_employees')
    L, det = [], []
    for e in es:
        gross = Decimal(str(e['gross']))
        basic = int(round(gross * Decimal('0.5')))
        pf = int(round(min(basic, 15000) * Decimal('0.12'))) if e['pf'] else 0
        esi = int(-(-gross * Decimal('0.0075') // 1)) if e['esi'] else 0
        pt = 200 if gross > 20000 else 150 if gross > 15000 else 0
        tds = fin_tds.salary_tds(gross)
        er_pf = int(round(min(basic, 15000) * Decimal('0.13'))) if e['pf'] else 0
        er_esi = int(-(-gross * Decimal('0.0325') // 1)) if e['esi'] else 0
        net = gross - pf - esi - pt - tds
        det.append({'id': e['id'], 'name': e['name'], 'gross': float(gross), 'pf': pf, 'esi': esi, 'pt': pt, 'tds': tds, 'net': float(net), 'er_pf': er_pf, 'er_esi': er_esi})
        b = e['branch_id'] or ho(company)
        L += [{'account': '5400', 'debit': float(gross), 'branch': b}, {'account': '5420', 'debit': er_pf + er_esi, 'branch': b},
              {'account': '2320', 'credit': pf + esi + er_pf + er_esi, 'branch': b}, {'account': '2325', 'credit': pt, 'branch': b},
              {'account': '2310', 'credit': float(net), 'branch': b, 'party': e['id']}]
        if tds:
            L.append({'account': '2300', 'credit': tds, 'branch': b, 'party': e['id']})
    return create({'vtype': 'SAL', 'company': company, 'vdate': str(end), 'branch_id': ho(company), 'dept': 'D-ADM', 'ref': f'PAYROLL-{m1:%Y-%m}',
                   'narration': f'Salary for {m1:%B %Y} ({len(es)} employees)', 'lines': [x for x in L if x.get('debit') or x.get('credit')],
                   'options': {'on_post': 'payroll', 'payroll': det}})


def _payroll_posted(v):
    for d in (v.options or {}).get('payroll') or []:
        if d['tds']:
            db.insert('tds_entries', dict(company=v.company, voucher_no=v.no, ded_date=v.vdate, doc_date=v.vdate, party=d['id'], name=d['name'],
                                          pan=db.val('SELECT pan FROM employees WHERE id=:i', i=d['id']), section='192', base=d['gross'],
                                          rate=round(d['tds'] / d['gross'] * 100, 2), amount=d['tds'], kind='Payable', status='Deducted', branch_id=v.branch_id, note='Salary'))


ON_POST.update(payroll=_payroll_posted, tds_challan=fin_tds.on_challan_posted)
ON_DROP.update(tds_challan=fin_tds.on_challan_dropped)


# ---------- views ----------
def view(v, full=True):
    out = {c: getattr(v, c) for c in ('id', 'no', 'company', 'vtype', 'branch_id', 'cost_centre', 'dept', 'ref', 'party_kind', 'party_id', 'party_name', 'gstin',
                                      'narration', 'status', 'maker', 'maker_role', 'approval_no', 'approver_role', 'approved_by', 'journal_no', 'reversal_of',
                                      'reversed_by', 'reason')}
    out.update(vdate=str(v.vdate), type_label=VTYPES[v.vtype][0], total=float(v.total or 0), taxable=float(v.taxable or 0), tax=float(v.tax or 0),
               tds_amount=float(v.tds_amount or 0), paid=float(v.paid or 0), approved_at=str(v.approved_at or '') or None, posted_at=str(v.posted_at or '') or None)
    if full:
        names = {r['id']: r['name'] for r in db.q('SELECT id, name FROM users WHERE id IN :i', i=(v.maker, v.approved_by or ''))}
        opts = dict(v.options or {})
        out.update(entered=v.entered, gst=v.gst, tds=v.tds, built=v.built, options={k: x for k, x in opts.items() if k != 'log'}, log=opts.get('log') or [],
                   maker_name=names.get(v.maker), approved_by_name=names.get(v.approved_by), evidence=has_evidence(v.no), lock=lock_reason(v.company, v.vdate),
                   can={'edit': v.status in EDITABLE and (v.maker == g.user['id'] or can(g.user, 'edit', 'fin')),
                        'submit': v.status in EDITABLE and can(g.user, 'add', 'fin'),
                        'cancel': v.status in ('Draft', 'Returned', 'Rejected') and (v.maker == g.user['id'] or can(g.user, 'cancel', 'fin')),
                        'reverse': v.status == 'Posted' and can(g.user, 'reverse', 'fin')})
    return out


def search(args, u):
    cos = [c for c in u['companies'] if not args.get('company') or c == args['company']] or ['']
    rows = db.q('''SELECT id FROM fin_vouchers WHERE company IN :c AND (:t IS NULL OR vtype=:t) AND (:s IS NULL OR status=:s)
                   AND (:f IS NULL OR vdate>=:f) AND (:to IS NULL OR vdate<=:to) AND (:p IS NULL OR party_id=:p)
                   AND (:q IS NULL OR no LIKE :ql OR ref LIKE :ql OR narration LIKE :ql OR party_name LIKE :ql) ORDER BY vdate DESC, id DESC LIMIT 500''',
                c=tuple(cos), t=args.get('vtype') or None, s=args.get('status') or None, f=args.get('from') or None, to=args.get('to') or None,
                p=args.get('party') or None, q=args.get('q') or None, ql=f"%{args.get('q') or ''}%")
    return [view(db.session().get(FinVoucher, r['id']), False) for r in rows]


def is_closer(u):
    return any(r in CLOSERS for r in u['roles'])
