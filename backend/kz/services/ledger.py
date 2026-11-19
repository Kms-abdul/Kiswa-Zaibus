"""Double-entry posting. Everything that moves money calls post(); nothing writes journals directly.

Posting rules per transaction are listed in docs/DATABASE.md ("Posting rules").
"""
from decimal import Decimal

from .. import db
from ..core.numbering import next_no
from ..errors import bad, conflict
from ..models import Journal, JournalLine
from .common import check_lock, money, today, user_id

CONTROL_ACCOUNTS = {'1105', '1200', '1210', '1300', '1400', '2000', '2050', '2200', '2400'}   # sub-ledger only


def post(company, date, jtype, ref, narration, lines, *, force=False, approval_no=None, allow_control=True, skip_if_empty=False):
    """lines: [{'account', 'branch', 'debit' | 'credit', 'party'?, 'cost_centre'?, 'dept'?}]. Returns the Journal.

    Raises unbalanced_voucher / empty_voucher / books_locked / unknown_account / control_account.
    `force` skips the lock-date check (opening balances only); `skip_if_empty` returns None instead of raising when every amount is zero."""
    clean = []
    for ln in lines:
        d, c = money(ln.get('debit')), money(ln.get('credit'))
        if d < 0:                      # a negative debit is a credit, and the other way round
            d, c = Decimal(0), c - d
        if c < 0:
            d, c = d - c, Decimal(0)
        if d and c:                    # keep one side only
            d, c = (d - c, Decimal(0)) if d > c else (Decimal(0), c - d)
        if d == 0 and c == 0:
            continue
        clean.append({'account': str(ln['account']), 'branch': ln.get('branch') or f'{company}-HO', 'party': ln.get('party'), 'debit': d, 'credit': c,
                      'cc': ln.get('cost_centre') or None, 'dept': ln.get('dept') or None})
    if not clean:
        if skip_if_empty:          # e.g. moving stock whose cost is still zero: nothing to record in the books
            return None
        raise bad('The voucher has no amounts.', 'empty_voucher')
    dr = sum(x['debit'] for x in clean)
    cr = sum(x['credit'] for x in clean)
    if dr != cr:
        raise bad(f'Voucher does not balance: debit {dr:.2f}, credit {cr:.2f}.', 'unbalanced_voucher')
    accounts = {r['code'] for r in db.q('SELECT code FROM accounts WHERE company=:c AND active=1', c=company)}
    for x in clean:
        if x['account'] not in accounts:
            raise bad(f"Account {x['account']} does not exist for {company}.", 'unknown_account')
        if not allow_control and x['account'] in CONTROL_ACCOUNTS:
            raise bad(f"Account {x['account']} is a control account; post it through its own document (invoice, bill, receipt).", 'control_account')
    if not force:
        check_lock(company, date)
    j = Journal(no=next_no(company, 'JV', date), company=company, txn_date=date, posted_on=today(), type=jtype, ref=ref,
                narration=narration[:500], created_by=user_id(), approval_no=approval_no)
    for n, x in enumerate(clean, 1):
        j.lines.append(JournalLine(line_no=n, account=x['account'], branch_id=x['branch'], party=x['party'], debit=x['debit'], credit=x['credit'],
                                   cost_centre=x['cc'], dept=x['dept']))
    db.add(j)
    return j


def reverse(journal_no, company, reason):
    j = db.session().query(Journal).filter_by(company=company, no=journal_no).with_for_update().first()
    if not j:
        raise bad('Voucher not found.', 'unknown_voucher')
    if j.type not in ('Journal', 'Expense', 'Contra'):
        raise conflict('Reverse this entry through its source document (invoice, GRN, receipt, payment).', 'reverse_via_document')
    if j.reversed_by_no:
        raise conflict(f'This voucher is already reversed by {j.reversed_by_no}.', 'already_reversed')
    if not (reason or '').strip():
        raise bad('A reason is required to reverse a voucher.', 'reason_required')
    r = post(company, today(), 'Reversal', j.no, f'Reversal of {j.no}: {reason}',
             [{'account': ln.account, 'branch': ln.branch_id, 'party': ln.party, 'debit': ln.credit, 'credit': ln.debit} for ln in j.lines])
    r.reverses_no = j.no
    j.reversed_by_no = r.no
    return r


def balance(company, account, *, branch=None, party=None, to=None):
    sql = 'SELECT COALESCE(SUM(l.debit - l.credit), 0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id WHERE j.company=:c AND l.account=:a'
    p = {'c': company, 'a': account}
    if branch:
        sql += ' AND l.branch_id=:b'
        p['b'] = branch
    if party:
        sql += ' AND l.party=:p'
        p['p'] = party
    if to:
        sql += ' AND j.txn_date <= :t'
        p['t'] = to
    return money(db.val(sql, **p))
