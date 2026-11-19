"""Accounting entries: customer receipts, expenses, contra (cash <-> bank), journal vouchers, reversals, inter-company funds.

Receipt:   Dr cash/bank/UPI / Cr receivable (oldest open invoices first, party = customer); any excess Cr 2200 wallet.
Expense:   Dr <expense account> / Cr 1100 cash or 1120 bank. Above the user's 'exp' limit -> approval.
Contra:    deposit Dr 1120 / Cr 1100; withdraw Dr 1100 / Cr 1120; settle Dr 1120 / Cr 1110 (UPI & card settlement).
Journal:   free lines, not on control accounts (sub-ledger accounts must be posted by their documents). Above 'jv' limit -> approval.
Inter-company fund transfer: payer Dr 1400 (party = other company) / Cr 1120; receiver Dr 1120 / Cr 2400 (party = payer).
"""
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit
from ..core.approvals import handler, on_reject, request as approval_request, role_for_limit
from ..core.numbering import next_no
from ..errors import bad, conflict, not_found
from ..models import Customer, InterCompany, Invoice, Receipt
from . import ledger
from .common import PAY_ACCOUNT, branch, check_company, check_lock, check_scope, ho_branch, money, parse_date, today

CONTRA = {'deposit': ('1120', '1100', 'Cash deposited to bank'), 'withdraw': ('1100', '1120', 'Cash withdrawn from bank'),
          'settle': ('1120', '1110', 'UPI & card settlement to bank')}


def _post_branch(company, branch_id):
    if branch_id in (None, '', 'HO', ho_branch(company)):
        return ho_branch(company)
    b = branch(branch_id)
    if b['company'] != company:
        raise bad('That branch belongs to the other company.', 'cross_company')
    check_scope(g.user, b['id'])
    return b['id']


def post_receipt(d):
    u = g.user
    co = d.get('company')
    check_company(u, co)
    cust = db.session().get(Customer, d.get('customer_id'))
    if not cust or cust.company != co:
        raise bad('Choose the customer.', 'unknown_customer')
    amt = money(d.get('amount'))
    if amt <= 0:
        raise bad('Enter an amount greater than zero.', 'bad_amount')
    mode = d.get('mode')
    if mode not in ('cash', 'upi', 'card', 'bank'):
        raise bad('Mode must be cash, UPI, card or bank.', 'bad_payment_mode')
    if mode != 'cash' and not (d.get('reference') or d.get('utr') or '').strip():
        raise bad('Enter the transaction reference / UTR.', 'payment_ref_required')
    br = _post_branch(co, d.get('branch_id'))
    date = parse_date(d.get('date'))
    check_lock(co, date)
    shift = None
    if mode == 'cash' and br != ho_branch(co):
        loc = db.val('SELECT location FROM branches WHERE id=:b', b=br)
        shift = db.one("SELECT id FROM cash_shifts WHERE user_id=:u AND location=:l AND status='Open'", u=u['id'], l=loc)
    return record_receipt(co, cust, amt, mode, br, date, reference=d.get('reference'), utr=d.get('utr'), against_invoice=d.get('against_invoice'),
                          advance=bool(d.get('advance')), created_by=u['id'], shift_id=shift['id'] if shift else None)


def record_receipt(co, cust, amt, mode, br, date, *, reference=None, utr=None, against_invoice=None, advance=False, created_by=None, shift_id=None):
    """Money received from a customer: applied to the named bill first, then the oldest open bills; any remainder goes to the
    customer's wallet (2200). Used by counter / HO receipts and by paid payment links (mode 'online', Dr 1115)."""
    invs = (db.session().query(Invoice).filter(Invoice.company == co, Invoice.customer_id == cust.id, Invoice.balance > 0)
            .order_by(Invoice.inv_date, Invoice.id).with_for_update().all())
    if against_invoice:
        invs.sort(key=lambda i: 0 if i.no == against_invoice else 1)
    lines = [{'account': PAY_ACCOUNT[mode], 'branch': br, 'debit': amt}]
    rem, applied = amt, []
    if not advance:
        for inv in invs:
            if rem <= 0:
                break
            x = min(rem, money(inv.balance))
            lines.append({'account': cust.receivable_account, 'branch': inv.branch_id, 'credit': x, 'party': cust.id})
            inv.balance = money(inv.balance) - x
            applied.append([inv.no, float(x)])
            rem -= x
    if rem > 0:
        lines.append({'account': '2200', 'branch': br, 'credit': rem, 'party': cust.id})
    r = Receipt(no=next_no(co, 'RV', date), company=co, branch_id=br, customer_id=cust.id, rcpt_date=date, amount=amt, mode=mode,
                reference=reference, utr=utr, applied=applied, to_wallet=money(rem), shift_id=shift_id, created_by=created_by or 'system')
    j = ledger.post(co, date, 'Receipt', r.no, f'Receipt from {cust.name}' + (f' (Rs {rem} to wallet)' if rem else ''), lines)
    r.journal_no = j.no
    db.add(r)
    audit.write('Receipt', r.no, new=f'Rs {amt} {mode} from {cust.name}', category='accounts', company=co, branch=br)
    return r


def _expense_account(co, acct):
    a = db.one('SELECT * FROM accounts WHERE company=:c AND code=:a AND active=1', c=co, a=acct)
    if not a or a['grp'] != 'X':
        raise bad('Choose an expense account.', 'unknown_account')
    return a


def create_expense(d):
    u = g.user
    co = d.get('company')
    check_company(u, co)
    acct = _expense_account(co, d.get('account'))
    amt = money(d.get('amount'))
    if amt <= 0:
        raise bad('Enter an amount greater than zero.', 'bad_amount')
    mode = d.get('mode') or 'bank'
    if mode not in ('cash', 'bank'):
        raise bad('Pay expenses in cash or from the bank.', 'bad_payment_mode')
    narr = str(d.get('narration') or '').strip()
    if not narr:
        raise bad('Describe the expense.', 'reason_required')
    br = _post_branch(co, d.get('branch_id'))
    payload = {'company': co, 'account': acct['code'], 'amount': str(amt), 'mode': mode, 'narration': narr, 'branch_id': br,
               'date': str(parse_date(d.get('date'))), 'ref': next_no(co, 'EXP')}
    role = role_for_limit(u, 'exp', amt, txn_type='exp', branch=br if br != ho_branch(co) else None)
    if role:
        a = approval_request('exp', f"Expense {payload['ref']}: {acct['name']} Rs {amt} ({narr})", payload, role, company=co,
                             branch=None if br == ho_branch(co) else br, amount=amt, reason=narr, ref=payload['ref'])
        return None, a
    return post_expense(payload), None


def post_expense(p, approval_no=None):
    co, amt, br = p['company'], money(p['amount']), p['branch_id']
    date = parse_date(p.get('date'))
    if p['mode'] == 'cash':
        have = ledger.balance(co, '1100', branch=br)
        if have < amt:
            raise conflict(f'Cash in hand at {br} is only Rs {have}.', 'insufficient_cash')
    j = ledger.post(co, date, 'Expense', p['ref'], p['narration'], [{'account': p['account'], 'branch': br, 'debit': amt},
                    {'account': '1100' if p['mode'] == 'cash' else '1120', 'branch': br, 'credit': amt}], approval_no=approval_no)
    audit.write('Expense', p['ref'], new=f"Rs {amt} {p['account']} ({p['mode']})", reason=p['narration'], category='accounts', company=co, branch=br)
    return j


@handler('exp')
def _approve_exp(p, apr):
    j = post_expense(p, apr['no'])
    return f"Expense {p['ref']} posted ({j.no})"


def contra(d):
    u = g.user
    co = d.get('company')
    check_company(u, co)
    kind = d.get('kind')
    if kind not in CONTRA:
        raise bad('Choose deposit, withdraw or settle.', 'invalid')
    amt = money(d.get('amount'))
    if amt <= 0:
        raise bad('Enter an amount greater than zero.', 'bad_amount')
    dr, cr, label = CONTRA[kind]
    br = _post_branch(co, d.get('branch_id'))
    date = parse_date(d.get('date'))
    check_lock(co, date)
    have = ledger.balance(co, cr, branch=br)
    if have < amt:
        raise conflict(f'Balance of {cr} at {br} is only Rs {have}.', 'insufficient_cash')
    no = next_no(co, 'CV')
    j = ledger.post(co, date, 'Contra', no, f"{label} · {br}" + (f" · ref {d['reference']}" if d.get('reference') else ''),
                    [{'account': dr, 'branch': br, 'debit': amt}, {'account': cr, 'branch': br, 'credit': amt}])
    audit.write('Contra entry', no, new=f'Rs {amt} {label}', category='accounts', company=co, branch=br)
    return j


def create_journal(d):
    u = g.user
    co = d.get('company')
    check_company(u, co)
    narr = str(d.get('narration') or '').strip()
    if not narr:
        raise bad('Narration is required for a journal voucher.', 'reason_required')
    lines = []
    for ln in d.get('lines') or []:
        x = {'account': ln.get('account'), 'branch': _post_branch(co, ln.get('branch_id')), 'debit': money(ln.get('debit')), 'credit': money(ln.get('credit'))}
        if x['debit'] < 0 or x['credit'] < 0:
            raise bad('Amounts cannot be negative: put the amount on the other side instead.', 'negative_amount')
        if x['debit'] and x['credit']:
            raise bad('A line has either a debit or a credit, not both.', 'invalid')
        lines.append(x)
    jdate = parse_date(d.get('date'))
    if jdate > today():
        raise bad('A journal cannot be dated in the future.', 'future_date')
    total = sum((x['debit'] for x in lines), Decimal(0))
    ref = next_no(co, 'JVR')
    payload = {'company': co, 'narration': narr, 'lines': [{**x, 'debit': str(x['debit']), 'credit': str(x['credit'])} for x in lines],
               'date': str(jdate), 'ref': ref}
    # validate now so the approver never sees an unbalanced voucher
    if sum((x['credit'] for x in lines), Decimal(0)) != total or not total:
        raise bad('Voucher does not balance (debit must equal credit and be more than zero).', 'unbalanced_voucher')
    for x in lines:
        if x['account'] in ledger.CONTROL_ACCOUNTS:
            raise bad(f"Account {x['account']} is a control account; post it through its own document.", 'control_account')
    role = role_for_limit(u, 'jv', total, txn_type='jv')
    if role:
        return None, approval_request('jv', f'Journal {ref}: {narr} (Rs {total})', payload, role, company=co, amount=total, reason=narr, ref=ref)
    return post_journal(payload), None


def post_journal(p, approval_no=None):
    j = ledger.post(p['company'], parse_date(p['date']), 'Journal', p['ref'], p['narration'], p['lines'], approval_no=approval_no, allow_control=False)
    audit.write('Journal voucher', j.no, new=f"Rs {sum(Decimal(x['debit']) for x in p['lines'])}", reason=p['narration'], category='accounts', company=p['company'])
    return j


@handler('jv')
def _approve_jv(p, apr):
    return f"Journal {post_journal(p, apr['no']).no} posted"


def reverse(d):
    u = g.user
    co = d.get('company')
    check_company(u, co)
    r = ledger.reverse(d.get('journal_no'), co, d.get('reason'))
    audit.write('Voucher reversed', d.get('journal_no'), new='Reversed by ' + r.no, reason=d.get('reason'), category='accounts', company=co)
    return r


# ---------------------------------------------------------------- inter-company
def create_intercompany(d):
    u = g.user
    a, b = d.get('from_company'), d.get('to_company')
    if not a or not b or a == b:
        raise bad('Choose the paying and the receiving company.', 'invalid')
    check_company(u, a)
    check_company(u, b)
    amt = money(d.get('amount'))
    if amt <= 0:
        raise bad('Enter the amount.', 'bad_amount')
    if not str(d.get('reason') or '').strip():
        raise bad('Give the reason.', 'reason_required')
    ic = InterCompany(no=next_no(a, 'IC'), from_company=a, to_company=b, amount=amt, reason=d['reason'].strip(), status='Pending approval', created_by=u['id'])
    db.add(ic)
    ap = approval_request('ic', f'Inter-company funds {a} -> {b} Rs {amt}', {'id': ic.id}, 'CFO', company=a, amount=amt, reason=ic.reason, ref=ic.no)
    return ic, ap


@handler('ic')
def _approve_ic(p, apr):
    ic = db.session().get(InterCompany, p['id'], with_for_update=True)
    if ic.status != 'Pending approval':
        raise conflict(f'{ic.no} is {ic.status}.', 'wrong_status')
    a, b, amt = ic.from_company, ic.to_company, money(ic.amount)
    have = ledger.balance(a, '1120')
    if have < amt:
        raise conflict(f'{a} bank balance is Rs {have}.', 'insufficient_cash')
    ledger.post(a, today(), 'Inter-company', ic.no, f'Funds to {b}: {ic.reason}',
                [{'account': '1400', 'branch': ho_branch(a), 'party': b, 'debit': amt}, {'account': '1120', 'branch': ho_branch(a), 'credit': amt}],
                approval_no=apr['no'])
    ledger.post(b, today(), 'Inter-company', ic.no, f'Funds from {a}: {ic.reason}',
                [{'account': '1120', 'branch': ho_branch(b), 'debit': amt}, {'account': '2400', 'branch': ho_branch(b), 'party': a, 'credit': amt}],
                approval_no=apr['no'])
    ic.status = 'Posted'
    audit.write('Inter-company posted', ic.no, new=f'Rs {amt} {a} -> {b}', reason=ic.reason, category='accounts')
    return f'{ic.no} posted in both companies'


@on_reject('ic')
def _reject_ic(p, apr, reason):
    ic = db.session().get(InterCompany, p['id'], with_for_update=True)
    ic.status = 'Rejected'


def receipt(no, company):
    r = db.session().query(Receipt).filter_by(no=no, company=company).first()
    if not r:
        raise not_found('Receipt not found.', 'unknown_receipt')
    return r
