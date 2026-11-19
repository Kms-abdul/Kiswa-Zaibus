"""Cash custody.

Counter shift   open (float per company) -> sell -> close (count by denomination). Expected cash = opening + cash sales
                + cash receipts - cash refunds - cash handed to the Outdoor Clerk. A difference within the closer's 'cash'
                limit posts at once (Dr/Cr 5700 Cash short/excess vs 1100); a larger one waits for approval.
Handover        counter -> Outdoor Clerk: Dr 1105 Cash with Outdoor Clerk (party = clerk) / Cr 1100 Cash in hand (branch).
Owner instruction (deposit / payment / transfer to owner) approved by CFO or Director, executed by the clerk,
                verified by Finance:  deposit Dr 1120 Bank; payment Dr <expense>; to owner Dr 3000 Capital  /  Cr 1105 (clerk).
"""
import datetime as dt
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit, notify
from ..core.approvals import handler, on_reject, request as approval_request, role_for_limit
from ..core.numbering import next_no
from ..errors import bad, conflict, denied, not_found
from ..models import CashHandover, CashShift, OwnerInstruction
from . import ledger
from .common import branch, check_scope, money, today

DENOMS = (2000, 500, 200, 100, 50, 20, 10, 5, 2, 1)
INSTR_TYPES = ('Bank deposit', 'Cash payment', 'Transfer to owner')
OWNER_ROLES = {'Director', 'Owner', 'Chairman', 'Super Admin', 'CFO'}


def denom_total(den):
    total = Decimal(0)
    for k, v in (den or {}).items():
        if int(k) not in DENOMS:
            raise bad(f'Unknown denomination {k}.', 'bad_denomination')
        n = int(v or 0)
        if n < 0:
            raise bad('Note counts cannot be negative.', 'bad_denomination')
        total += int(k) * n
    return money(total)


# ---------------------------------------------------------------- shifts
def location_branches(location):
    return {r['company']: r['id'] for r in db.q('SELECT id, company FROM branches WHERE location=:l AND active=1', l=location)}


def open_shift(d):
    u = g.user
    loc = d.get('location')
    brs = location_branches(loc)
    if not brs:
        raise bad('Choose your location.', 'unknown_location')
    for b in brs.values():
        check_scope(u, b)
    if db.val("SELECT COUNT(*) FROM cash_shifts WHERE user_id=:u AND status IN ('Open','Pending approval')", u=u['id']):
        raise conflict('You already have a shift open. Close it first.', 'shift_open')
    opening = {co: float(money((d.get('opening') or {}).get(co, 0))) for co in brs}
    sh = CashShift(no=next_no('HO', 'SH'), location=loc, counter=d.get('counter'), user_id=u['id'], shift_date=today(), opened_at=dt.datetime.now(),
                   status='Open', opening=opening)
    db.add(sh)
    audit.write('Shift opened', sh.no, new={'location': loc, 'opening': opening}, category='cash')
    return sh


def shift_figures(sh):
    brs = location_branches(sh.location)
    out = {}
    for co, br in brs.items():
        sales = money(db.val('''SELECT COALESCE(SUM(p.amount),0) FROM invoice_payments p JOIN invoices i ON i.id=p.invoice_id
                                WHERE i.shift_id=:s AND i.company=:c AND p.mode='cash' ''', s=sh.id, c=co))
        refunds = money(db.val("SELECT COALESCE(SUM(amount),0) FROM credit_notes WHERE shift_id=:s AND company=:c AND refund_mode='cash'", s=sh.id, c=co))
        receipts = money(db.val("SELECT COALESCE(SUM(amount),0) FROM receipts WHERE shift_id=:s AND company=:c AND mode='cash'", s=sh.id, c=co))
        handed = money(db.val('''SELECT COALESCE(SUM(amount),0) FROM cash_handovers WHERE from_user=:u AND branch_id=:b AND created_at >= :o
                                 AND status <> 'Rejected' ''', u=sh.user_id, b=br, o=sh.opened_at))
        digital = db.q('''SELECT p.mode, COALESCE(SUM(p.amount),0) amt FROM invoice_payments p JOIN invoices i ON i.id=p.invoice_id
                          WHERE i.shift_id=:s AND i.company=:c AND p.mode<>'cash' GROUP BY p.mode''', s=sh.id, c=co)
        bills = db.val('SELECT COUNT(*) FROM invoices WHERE shift_id=:s AND company=:c', s=sh.id, c=co)
        opening = money((sh.opening or {}).get(co, 0))
        out[co] = {'branch_id': br, 'opening': float(opening), 'cash_sales': float(sales), 'cash_receipts': float(receipts),
                   'cash_refunds': float(refunds), 'handed_over': float(handed), 'expected': float(opening + sales + receipts - refunds - handed),
                   'other_modes': {r['mode']: float(r['amt']) for r in digital}, 'bills': bills}
    return out


def close_shift(shift_id, d):
    u = g.user
    sh = db.session().get(CashShift, shift_id, with_for_update=True)
    if not sh:
        raise not_found('Shift not found.', 'unknown_shift')
    if sh.user_id != u['id'] and not d.get('_supervisor'):
        raise denied('Only the cashier who opened the shift can close it.', 'not_shift_owner')
    if sh.status != 'Open':
        raise conflict(f'This shift is {sh.status}.', 'wrong_status')
    fig = shift_figures(sh)
    counted = d.get('counted') or {}
    dens = d.get('denominations') or {}
    total_diff = Decimal(0)
    for co, f in fig.items():
        c = denom_total(dens.get(co)) if dens.get(co) else money(counted.get(co))
        f['counted'] = float(c)
        f['difference'] = float(money(c - Decimal(str(f['expected']))))
        total_diff += abs(Decimal(str(f['difference'])))
    if total_diff and not str(d.get('reason') or '').strip():
        raise bad('Explain the difference between counted and expected cash.', 'reason_required')
    sh.figures, sh.denominations, sh.difference_reason = fig, dens, str(d.get('reason') or '').strip() or None
    sh.closed_at = dt.datetime.now()
    role = role_for_limit(u, 'cash', total_diff, txn_type='cash') if total_diff else None
    if role:
        sh.status = 'Pending approval'
        a = approval_request('cash', f'Shift {sh.no} closed with a difference of Rs {total_diff}', {'id': sh.id}, role, amount=total_diff,
                             reason=sh.difference_reason, ref=sh.no, branch=next(iter(fig.values()))['branch_id'])
        sh.approval_no = a['no']
        notify.alert('Cash difference', f'{sh.no}: Rs {total_diff} difference at close, needs {role}', ref=sh.no, severity='warn')
        audit.write('Shift closed (difference pending approval)', sh.no, new=fig, reason=sh.difference_reason, category='cash')
        return sh, a
    _post_shift_difference(sh)
    audit.write('Shift closed', sh.no, new=fig, reason=sh.difference_reason, category='cash')
    return sh, None


def _post_shift_difference(sh, approval_no=None):
    for co, f in (sh.figures or {}).items():
        diff = money(f.get('difference'))
        if not diff:
            continue
        br = f['branch_id']
        lines = ([{'account': '5700', 'branch': br, 'debit': -diff}, {'account': '1100', 'branch': br, 'credit': -diff}] if diff < 0 else
                 [{'account': '1100', 'branch': br, 'debit': diff}, {'account': '5700', 'branch': br, 'credit': diff}])
        ledger.post(co, sh.shift_date, 'Cash difference', sh.no, f"Cash {'shortage' if diff < 0 else 'excess'} at shift close {sh.no}", lines,
                    approval_no=approval_no)
    sh.status = 'Closed'


@handler('cash')
def _approve_shift(p, apr):
    sh = db.session().get(CashShift, p['id'], with_for_update=True)
    if sh.status != 'Pending approval':
        raise conflict(f'{sh.no} is {sh.status}.', 'wrong_status')
    _post_shift_difference(sh, apr['no'])
    return f'{sh.no} closed; difference posted'


@on_reject('cash')
def _reject_shift(p, apr, reason):
    sh = db.session().get(CashShift, p['id'], with_for_update=True)
    sh.status = 'Open'            # recount requested
    sh.closed_at = None


# ---------------------------------------------------------------- handovers to the Outdoor Clerk
def clerk_balance(clerk_id, company=None):
    sql = "SELECT j.company, COALESCE(SUM(l.debit-l.credit),0) bal FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE l.account='1105' AND l.party=:p"
    p = {'p': clerk_id}
    if company:
        sql += ' AND j.company=:c'
        p['c'] = company
    return {r['company']: money(r['bal']) for r in db.q(sql + ' GROUP BY j.company', **p)}


def create_handover(d):
    u = g.user
    b = branch(d.get('branch_id'))
    check_scope(u, b['id'])
    clerk = db.one("SELECT id, name FROM users WHERE id=:i AND main_role='Outdoor Clerk' AND status='Active'", i=d.get('clerk_id'))
    if not clerk:
        raise bad('Choose the Outdoor Clerk receiving the cash.', 'unknown_clerk')
    den = d.get('denominations') or {}
    amount = money(d.get('amount'))
    if den and denom_total(den) != amount:
        raise bad(f'Notes add up to Rs {denom_total(den)}, not Rs {amount}.', 'denomination_mismatch')
    if amount <= 0:
        raise bad('Enter the amount handed over.', 'bad_amount')
    have = ledger.balance(b['company'], '1100', branch=b['id'])
    if amount > have:
        raise conflict(f'Cash in hand at {b["name"]} is only Rs {have}.', 'insufficient_cash')
    h = CashHandover(no=next_no(b['company'], 'HOV'), company=b['company'], branch_id=b['id'], txn_date=today(), clerk_id=clerk['id'], from_user=u['id'],
                     amount=amount, denominations=den, status='Awaiting clerk')
    db.add(h)
    audit.write('Cash handover created', h.no, new=f'Rs {amount} to {clerk["name"]}', category='cash', company=h.company, branch=h.branch_id)
    return h


def accept_handover(hid, d):
    u = g.user
    h = db.session().get(CashHandover, hid, with_for_update=True)
    if not h:
        raise not_found('Handover not found.', 'unknown_handover')
    if h.clerk_id != u['id']:
        raise denied('Only the receiving Outdoor Clerk can accept this handover.', 'not_clerk')
    if h.status != 'Awaiting clerk':
        raise conflict(f'This handover is {h.status}.', 'wrong_status')
    counted = money(d.get('counted', h.amount))
    if counted < 0 or counted > h.amount:
        raise bad('Counted cash must be between 0 and the amount handed over.', 'bad_amount')
    h.counted, h.difference = counted, money(h.amount - counted)
    if h.difference and not str(d.get('reason') or '').strip():
        raise bad('Explain why the counted cash is less.', 'reason_required')
    h.reason = str(d.get('reason') or '').strip() or None
    j = ledger.post(h.company, today(), 'Contra', h.no, f'Cash handed to Outdoor Clerk {u["name"]} ({h.no})',
                    [{'account': '1105', 'branch': h.branch_id, 'party': u['id'], 'debit': counted},
                     {'account': '1100', 'branch': h.branch_id, 'credit': counted}])
    h.journal_no, h.status, h.accepted_at = j.no, 'Accepted' if not h.difference else 'Disputed', dt.datetime.now()
    if h.difference:
        notify.alert('Handover difference', f'{h.no}: clerk counted Rs {counted} of Rs {h.amount}', ref=h.no, company=h.company, branch=h.branch_id, severity='bad')
    audit.write('Cash handover accepted', h.no, new=f'Rs {counted} counted', reason=h.reason, category='cash', company=h.company, branch=h.branch_id)
    return h


def reject_handover(hid, reason):
    u = g.user
    h = db.session().get(CashHandover, hid, with_for_update=True)
    if not h or h.clerk_id != u['id']:
        raise denied('Only the receiving Outdoor Clerk can refuse this handover.', 'not_clerk')
    if h.status != 'Awaiting clerk':
        raise conflict(f'This handover is {h.status}.', 'wrong_status')
    if not (reason or '').strip():
        raise bad('Give a reason.', 'reason_required')
    h.status, h.reason = 'Rejected', reason
    audit.write('Cash handover refused', h.no, reason=reason, category='cash', company=h.company, branch=h.branch_id)
    return h


# ---------------------------------------------------------------- owner instructions
def create_instruction(d):
    u = g.user
    if not OWNER_ROLES & set(u['roles']):
        raise denied('Only the owner, Director, CFO or Super Admin issues instructions to the Outdoor Clerk.', 'not_owner')
    co = d.get('company')
    if co not in u['companies']:
        raise bad('Choose the company whose cash is used.', 'company_denied')
    if d.get('type') not in INSTR_TYPES:
        raise bad('Type must be Bank deposit, Cash payment or Transfer to owner.', 'bad_instruction_type')
    clerk = db.one("SELECT id, name FROM users WHERE id=:i AND main_role='Outdoor Clerk' AND status='Active'", i=d.get('clerk_id'))
    if not clerk:
        raise bad('Choose the Outdoor Clerk.', 'unknown_clerk')
    amount = money(d.get('amount'))
    if amount <= 0:
        raise bad('Enter the amount.', 'bad_amount')
    if not str(d.get('purpose') or '').strip():
        raise bad('State the purpose.', 'reason_required')
    acct = d.get('expense_account')
    if d['type'] == 'Cash payment':
        if not acct or not db.val("SELECT COUNT(*) FROM accounts WHERE company=:c AND code=:a AND grp='X'", c=co, a=acct):
            raise bad('Choose the expense account for a cash payment.', 'unknown_account')
    i = OwnerInstruction(no=next_no(co, 'OI'), company=co, clerk_id=clerk['id'], type=d['type'], amount=amount, recipient=d.get('recipient'),
                         purpose=d['purpose'].strip(), expense_account=acct, status='Pending approval', created_by=u['id'])
    db.add(i)
    limit = Decimal(str(db.val("SELECT amount FROM approval_limits WHERE role='CFO' AND limit_key='cash'") or 0))
    role = 'CFO' if amount <= max(limit, Decimal('50000')) else 'Director'
    a = approval_request('ocinstr', f'{i.type} Rs {amount} by {clerk["name"]}: {i.purpose}', {'id': i.id}, role, company=co, amount=amount,
                         reason=i.purpose, ref=i.no)
    i.approval_no = a['no']
    audit.write('Owner instruction raised', i.no, new=f'{i.type} Rs {amount}', reason=i.purpose, category='cash', company=co)
    return i, a


@handler('ocinstr')
def _approve_instr(p, apr):
    i = db.session().get(OwnerInstruction, p['id'], with_for_update=True)
    if i.status != 'Pending approval':
        raise conflict(f'{i.no} is {i.status}.', 'wrong_status')
    i.status = 'Owner Approved'
    notify.alert('Instruction approved', f'{i.no}: {i.type} Rs {i.amount} ready for the Outdoor Clerk', ref=i.no, company=i.company, severity='info')
    return f'{i.no} approved'


@on_reject('ocinstr')
def _reject_instr(p, apr, reason):
    i = db.session().get(OwnerInstruction, p['id'], with_for_update=True)
    i.status = 'Rejected'


def execute_instruction(iid, d):
    u = g.user
    i = db.session().get(OwnerInstruction, iid, with_for_update=True)
    if not i:
        raise not_found('Instruction not found.', 'unknown_instruction')
    if i.clerk_id != u['id']:
        raise denied('Only the assigned Outdoor Clerk executes this instruction.', 'not_clerk')
    if i.status != 'Owner Approved':
        raise conflict(f'This instruction is {i.status}.', 'wrong_status')
    have = clerk_balance(u['id'], i.company).get(i.company, Decimal(0))
    if i.amount > have:
        raise conflict(f'You hold Rs {have} of {i.company} cash; the instruction is Rs {i.amount}.', 'insufficient_cash')
    if i.type == 'Bank deposit' and not str(d.get('slip_no') or '').strip():
        raise bad('Enter the bank deposit slip number.', 'payment_ref_required')
    debit = {'Bank deposit': '1120', 'Cash payment': i.expense_account, 'Transfer to owner': '3000'}[i.type]
    j = ledger.post(i.company, today(), 'Contra' if i.type == 'Bank deposit' else 'Payment', i.no, f'{i.type}: {i.purpose} ({i.no})',
                    [{'account': debit, 'branch': f'{i.company}-HO', 'debit': i.amount},
                     {'account': '1105', 'branch': f'{i.company}-HO', 'party': u['id'], 'credit': i.amount}], approval_no=i.approval_no)
    i.status, i.executed_at, i.journal_no = 'Executed', dt.datetime.now(), j.no
    i.execution = {k: d.get(k) for k in ('slip_no', 'paid_to', 'notes') if d.get(k)}
    audit.write('Owner instruction executed', i.no, new=i.execution or 'done', category='cash', company=i.company)
    return i


def verify_instruction(iid, d):
    u = g.user
    i = db.session().get(OwnerInstruction, iid, with_for_update=True)
    if not i:
        raise not_found('Instruction not found.', 'unknown_instruction')
    if i.status != 'Executed':
        raise conflict('Only executed instructions can be verified.', 'wrong_status')
    if i.clerk_id == u['id'] or i.created_by == u['id']:
        raise denied('Verification must be done by Finance, not by the clerk or the person who raised it.', 'cannot_decide')
    if d.get('ok') is False:
        if not str(d.get('remarks') or '').strip():
            raise bad('Give the reason.', 'reason_required')
        notify.alert('Instruction query', f'{i.no}: Finance queried the execution: {d["remarks"]}', ref=i.no, company=i.company, severity='bad')
        audit.write('Owner instruction queried', i.no, reason=d['remarks'], category='cash', company=i.company)
        return i
    i.status, i.verified_by, i.verified_at = 'Closed', u['id'], dt.datetime.now()
    audit.write('Owner instruction verified and closed', i.no, reason=d.get('remarks'), category='cash', company=i.company)
    return i
