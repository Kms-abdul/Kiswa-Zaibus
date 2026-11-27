"""Counter sales (POS), sponsored issues, held bills, pending delivery, returns / cancellations (credit notes).

Pricing (same as the prototype): prices include GST.
    net      = rate x qty x (1 - disc%)
    taxable  = net / (1 + gst%)            tax = net - taxable
    total    = round(sum taxable + sum tax) to the rupee; the difference is round-off (5950)
Sale journal:   Dr payment accounts (cash 1100, UPI/card 1110, bank 1120, credit -> customer's receivable 1200/1210, wallet 2200)
                Cr 4000 Sales (taxable)   Cr 2100 CGST + 2110 SGST (intra-state) or 2120 IGST   +/- 5950 round off
                Dr 5000 COGS / Cr 1300 Inventory for the quantity delivered now (at moving-average cost)
Credit note:    the mirror image: Dr 4010 Sales returns, Dr GST, Cr refund account; cost back Dr 1300 (or 5600 if damaged) / Cr 5000
"""
import datetime as dt
import uuid
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit, notify
from ..core.approvals import handler, on_reject, request as approval_request, role_for_limit
from ..core.numbering import next_no
from ..core.security import setting
from ..errors import bad, conflict, not_found
from ..models import CreditNote, CreditNoteLine, Customer, HeldBill, Invoice, InvoiceLine, InvoicePayment, MseaVoucher
from . import ledger, stock
from .common import HOME_STATE, PAY_ACCOUNT, branch, check_company, check_lock, check_scope, item, money, rupee, today, whole

CASH_LIMIT_DEFAULT = 199999        # Income-tax Act s.269ST: no cash receipt of Rs 2,00,000 or more in one transaction
BLOCKED_STUDENT_STATUSES = ('Transferred', 'Left', 'Inactive', 'Cancelled')


# ---------------------------------------------------------------- pricing
def price_line(it, qty, disc_pct=0):
    rate = Decimal(str(it.price))
    gst = Decimal(str(it.gst_rate))
    net = money(rate * qty * (1 - Decimal(str(disc_pct or 0)) / 100))
    taxable = money(net / (1 + gst / 100))
    return {'item_id': it.id, 'description': it.name + (f' ({it.size})' if it.size and it.size != 'Std' else ''), 'hsn': it.hsn, 'qty': qty,
            'rate': money(rate), 'disc_pct': Decimal(str(disc_pct or 0)), 'gst_rate': gst, 'taxable': taxable, 'tax': money(net - taxable),
            'amount': net, 'gross': money(rate * qty)}


def totals(lines):
    gross = sum((ln['gross'] for ln in lines), Decimal(0))
    net = sum((ln['amount'] for ln in lines), Decimal(0))
    taxable = sum((ln['taxable'] for ln in lines), Decimal(0))
    tax = sum((ln['tax'] for ln in lines), Decimal(0))
    total = rupee(taxable + tax)
    return {'gross': money(gross), 'discount': money(gross - net), 'taxable': money(taxable), 'tax': money(tax),
            'round_off': money(total - taxable - tax), 'total': money(total)}


def quote(company, lines, schemes=True):
    """Price a cart without posting (used by the POS screen). With schemes=True (not for MSEA-sponsored issues) each
    line that is not part of a package gets the best active discount scheme; the cashier may give more, which then
    follows the discount limits (see post_invoice)."""
    from . import schemes as sch
    active = sch.active_for(company) if schemes else []
    priced = []
    if not isinstance(lines, list) or not all(isinstance(x, dict) for x in lines):
        raise bad('Bill lines must be a list of items.', 'invalid_input')
    for ln in lines:
        it = item(ln.get('item_id'), company)
        if it.unpriced or not it.price:
            raise bad(f'{it.name} has no selling price yet. Ask for a price to be set.', 'unpriced_item', item=it.id)
        q = whole(ln.get('qty'), f'Quantity for {it.name}', 0)
        if q <= 0:
            raise bad(f'Quantity for {it.name} must be more than zero.', 'bad_quantity')
        d = Decimal(str(ln.get('disc_pct') or 0))
        if d < 0 or d > 100:
            raise bad('Discount must be between 0 and 100%.', 'bad_discount')
        if ln.get('package_name'):      # a package's own discount is pre-approved like a scheme
            pd = db.val('SELECT disc_pct FROM packages WHERE company=:c AND name=:n AND active=1', c=company, n=ln['package_name'])
            spct, sname = Decimal(str(pd or 0)), (f"{ln['package_name']} package" if pd else None)
        else:
            spct, sname = sch.best(it, active) if active else (Decimal(0), None)
        priced.append(dict(price_line(it, q, max(d, spct)), package_name=ln.get('package_name'), scheme=sname, scheme_pct=spct))
    return priced, totals(priced)


# ---------------------------------------------------------------- customers
def student_customer(company, st):
    cid = f'{company}-STU-{st["adm_no"]}'
    c = db.session().get(Customer, cid)
    if not c:
        c = Customer(id=cid, company=company, type='Student', name=st['name'], mobile=st.get('parent_mobile'), adm_no=st['adm_no'],
                     receivable_account='1200', credit_limit=0)
        db.add(c)
    return c


def msea_customer(company):
    """MS Education Academy, billed for voucher issues (receivable 1210). Made on first use, so a database set up from empty works too."""
    cid = f'{company}-MSEA'
    c = db.session().get(Customer, cid)
    if not c:
        c = Customer(id=cid, company=company, type='Institution', name='MS Education Academy (sponsorships)', receivable_account='1210',
                     credit_limit=0)
        db.add(c)
        db.session().flush()
    return c


def _student(adm_no, branch_loc=None):
    st = db.one('SELECT * FROM students WHERE adm_no=:a', a=adm_no)
    if not st:
        raise not_found('Student not found. Check the admission number or run the ERP sync.', 'unknown_student')
    if st['status'] in BLOCKED_STUDENT_STATUSES:
        raise conflict(f"{st['name']} is marked {st['status']} in the ERP. Billing is blocked.", 'student_blocked')
    return st


def _snapshot(st):
    return {k: st.get(k) for k in ('adm_no', 'erp_ref', 'name', 'class_name', 'section', 'category', 'campus', 'school_id', 'academic_year')}


def _open_shift(user_id, location):
    return db.one("SELECT * FROM cash_shifts WHERE user_id=:u AND location=:l AND status='Open' ORDER BY id DESC LIMIT 1", u=user_id, l=location)


# ---------------------------------------------------------------- voucher checks (sponsored issue)
def voucher_checks(v, company, lines, total):
    """Problems that stop a bill against voucher v. MSEA's part is msea.share(); the amount limit only caps that part (the parent pays the
    rest), and the categories listed on the voucher say what it may be used for (their quantities are a guide, not a limit)."""
    problems = []
    if v.company != company:
        problems.append('voucher is for the other company')
    if v.status not in ('Approved', 'Partly used'):
        problems.append(f'voucher status is {v.status}')
    if v.valid_to < today():
        problems.append(f'voucher expired on {v.valid_to}')
    if v.max_value is not None and money(v.used_value) >= money(v.max_value):
        problems.append('nothing is left on the voucher')
    allowed = set(v.entitlement or {})
    if allowed:
        for ln in lines:
            cat = db.val('SELECT category FROM items WHERE id=:i', i=ln['item_id'])
            if cat not in allowed:
                problems.append(f"{ln['description']} ({cat}) is not covered by the voucher (it is for {', '.join(sorted(allowed))})")
    return problems


# ---------------------------------------------------------------- post a sale
def post_invoice(d, *, approved=None):
    """d: {branch_id, adm_no?, customer_id?, lines:[{item_id, qty, disc_pct?, delivered_qty?, package_name?}],
           payments:[{mode, amount, ref?}], voucher_no?, pending_reason?, place_of_supply?, counter?,
           stock_override_reason?}
    approved: {'by': name, 'approval_no': ...} when called from an approval handler (skips limit checks).
    Returns (invoice, None) or (None, held-bill dict) when a supervisor must approve first."""
    u = g.user
    b = branch(d.get('branch_id'))
    check_scope(u, b['id'])
    check_company(u, b['company'])
    co = b['company']
    check_lock(co, today())
    controls = setting('controls', {}) or {}

    # who is buying
    st = None
    voucher = None
    if d.get('voucher_no'):
        voucher = db.session().get(MseaVoucher, d['voucher_no'], with_for_update=True)
        if not voucher:
            raise not_found('Voucher not found.', 'unknown_voucher')
        st = _student(voucher.adm_no)
        cust = msea_customer(co)
    elif d.get('adm_no'):
        st = _student(d['adm_no'])
        cust = student_customer(co, st)
    elif d.get('customer_id'):
        cust = db.session().get(Customer, d['customer_id'])
        if not cust or cust.company != co or cust.type == 'Student':
            raise bad('Choose a valid customer for this company.', 'unknown_customer')
        if cust.type == 'Walk-in' and not controls.get('allowWalkIn'):
            raise bad("Select the student. Every counter bill must carry the student's admission number.", 'student_required')
    else:
        raise bad("Select the student. Every counter bill must carry the student's admission number.", 'student_required')

    lines, tot = quote(co, d.get('lines') or [], schemes=voucher is None)
    if not lines:
        raise bad('Add at least one item.', 'no_lines')
    delivered = []
    for ln, raw in zip(lines, d['lines']):
        dq = raw.get('delivered_qty')
        dq = ln['qty'] if dq is None else int(dq)
        if dq < 0 or dq > ln['qty']:
            raise bad('Delivered quantity must be between 0 and the billed quantity.', 'bad_quantity')
        delivered.append(dq)
    if any(dq < ln['qty'] for dq, ln in zip(delivered, lines)) and not str(d.get('pending_reason') or '').strip():
        raise bad('Give the reason for the pending delivery.', 'pending_reason_required')

    # payments
    pays = []
    msea_part = Decimal(0)
    if voucher:
        from .msea import share
        problems = voucher_checks(voucher, co, lines, tot['total'])
        if problems:
            raise conflict('Voucher check failed: ' + '; '.join(problems), 'voucher_check_failed', problems=problems)
        msea_part = share(voucher, tot['total'])
        if msea_part > 0:
            pays = [{'mode': 'credit', 'amount': msea_part, 'ref': voucher.no}]
    # the parent's payments: all of a normal bill, the rest of a voucher bill
    for p in d.get('payments') or []:
        mode = p.get('mode')
        if mode not in PAY_ACCOUNT or mode == 'online':
            raise bad(f'Unknown payment mode {mode}.', 'bad_payment_mode')
        if voucher and mode in ('credit', 'wallet'):
            raise bad("The parent's part of a voucher bill is paid now: cash, UPI, card or bank.", 'bad_payment_mode')
        amt = money(p.get('amount'))
        if amt <= 0:
            continue
        if mode in ('upi', 'card', 'bank') and not str(p.get('ref') or '').strip():
            raise bad(f'Enter the {mode.upper()} reference number.', 'payment_ref_required')
        ref = str(p.get('ref') or '').strip() or None
        if ref and mode in ('upi', 'card', 'bank'):
            used = db.one('''SELECT i.no FROM invoice_payments p JOIN invoices i ON i.id=p.invoice_id
                             WHERE p.mode=:m AND p.ref=:r AND i.company=:c AND i.status<>'Cancelled' LIMIT 1''', m=mode, r=ref, c=co)
            if used or any(x['mode'] == mode and x['ref'] == ref for x in pays):
                raise conflict(f'{mode.upper()} reference {ref} is already used on bill {used["no"] if used else "this bill"}. '
                               'Check the payment; one payment cannot settle two bills.', 'duplicate_payment_ref')
        pays.append({'mode': mode, 'amount': amt, 'ref': ref})
    paid = sum((p['amount'] for p in pays), Decimal(0))
    if paid != tot['total']:
        if voucher:
            raise bad(f'MSEA pays Rs {msea_part} of this Rs {tot["total"]} bill; the parent pays Rs {tot["total"] - msea_part} '
                      f'(payments entered: Rs {paid - msea_part}).', 'payment_mismatch')
        raise bad(f'Payments (Rs {paid}) do not add up to the bill total (Rs {tot["total"]}).', 'payment_mismatch')
    cash_in = sum((p['amount'] for p in pays if p['mode'] == 'cash'), Decimal(0))
    cash_max = Decimal(str(controls.get('cashBillMax') or CASH_LIMIT_DEFAULT))
    if cash_in > cash_max:
        raise bad(f'Cash of Rs {cash_in} in one bill is above the Rs {cash_max:,.0f} cash limit (Income-tax Act s.269ST). Take the balance by UPI, card or bank.',
                  'cash_limit')
    credit = sum((p['amount'] for p in pays if p['mode'] == 'credit'), Decimal(0))
    if credit and not voucher and cust.type == 'Student':
        raise bad('Students cannot buy on credit. Use a sponsorship voucher or take payment.', 'credit_not_allowed')
    if any(p['mode'] == 'wallet' for p in pays):
        have = -ledger.balance(co, '2200', party=cust.id)
        need_w = sum((p['amount'] for p in pays if p['mode'] == 'wallet'), Decimal(0))
        if need_w > have:
            raise bad(f'Wallet balance is Rs {have}.', 'wallet_insufficient')

    # supervisor checks: discount limit and stock override -> held bill + approval
    shift = _open_shift(u['id'], b['location'])
    if cash_in and not shift and not approved:
        raise conflict('Open your cash shift before taking cash.', 'shift_required')
    # a discount up to the line's scheme percentage is pre-approved; only discounts above it go through the limits
    max_disc = max((ln['disc_pct'] for ln in lines if ln['disc_pct'] > ln.get('scheme_pct', 0)), default=Decimal(0))
    short = []
    for ln, dq in zip(lines, delivered):
        if dq and stock.available(b['id'], ln['item_id']) < dq:
            short.append(f"{ln['description']}: {stock.available(b['id'], ln['item_id'])} available, {dq} needed")
    if not approved:
        reasons = []
        role = None
        if max_disc > 0:
            role = role_for_limit(u, 'disc', max_disc, txn_type='sale', branch=b['id'])
            if role:
                reasons.append(f'discount {max_disc}% above your limit')
        if short:
            if not str(d.get('stock_override_reason') or '').strip():
                notify.alert('Negative stock attempt', f"{b['name']}: " + '; '.join(short), company=co, branch=b['id'], severity='bad')
                raise conflict('Not enough stock: ' + '; '.join(short) + '. Negative stock is blocked.', 'negative_stock', lines=short)
            role = role or 'Branch Manager'
            reasons.append('stock override: ' + d['stock_override_reason'].strip())
        if reasons:
            return None, hold_bill(d, b, ' and '.join(reasons), role or 'Branch Manager', tot['total'])
    elif short and not d.get('stock_override_reason'):
        raise conflict('Not enough stock: ' + '; '.join(short), 'negative_stock', lines=short)

    # post
    now = dt.datetime.now()
    inv = Invoice(no=next_no(co, f"INV:{b['code']}"), company=co, branch_id=b['id'], counter=d.get('counter'), inv_date=today(), inv_time=now.time().replace(microsecond=0),
                  customer_id=cust.id, adm_no=st['adm_no'] if st else None, student_snapshot=_snapshot(st) if st else None, cashier_id=u['id'],
                  shift_id=shift['id'] if shift else None, channel='sponsored' if voucher else 'counter',
                  place_of_supply=(d.get('place_of_supply') or HOME_STATE), gross=tot['gross'], discount=tot['discount'], taxable=tot['taxable'],
                  round_off=tot['round_off'], total=tot['total'], balance=credit, status='Posted',
                  pending_reason=str(d.get('pending_reason') or '').strip() or None, msea_voucher_no=voucher.no if voucher else None)
    if inv.place_of_supply == HOME_STATE:
        inv.cgst = money(tot['tax'] / 2)
        inv.sgst = money(tot['tax'] - inv.cgst)
    else:
        inv.igst = tot['tax']
    if approved and short:
        inv.override_by, inv.override_reason = approved.get('by'), d.get('stock_override_reason')
    cost = Decimal(0)
    for n, (ln, dq) in enumerate(zip(lines, delivered), 1):
        it = item(ln['item_id'])
        inv.lines.append(InvoiceLine(line_no=n, item_id=it.id, description=ln['description'], hsn=ln['hsn'], qty=ln['qty'], rate=ln['rate'],
                                     disc_pct=ln['disc_pct'], gst_rate=ln['gst_rate'], taxable=ln['taxable'], tax=ln['tax'], amount=ln['amount'],
                                     unit_cost=it.cost, delivered_qty=dq, package_name=ln.get('package_name')))
        cost += Decimal(str(it.cost)) * dq
    for n, p in enumerate(pays, 1):
        inv.payments.append(InvoicePayment(line_no=n, mode=p['mode'], amount=p['amount'], ref=p['ref']))
    if any(delivered):
        inv.delivery_no = next_no(co, 'DLV')
    if voucher:
        inv.issue_no = next_no(co, 'SIV')
    db.add(inv)

    jl = []
    for p in pays:
        acct = cust.receivable_account if p['mode'] == 'credit' else PAY_ACCOUNT[p['mode']]
        jl.append({'account': acct, 'branch': b['id'], 'debit': p['amount'], 'party': cust.id if p['mode'] in ('credit', 'wallet') else None})
    jl.append({'account': '4000', 'branch': b['id'], 'credit': tot['taxable']})
    if inv.igst:
        jl.append({'account': '2120', 'branch': b['id'], 'credit': inv.igst})
    else:
        jl += [{'account': '2100', 'branch': b['id'], 'credit': inv.cgst}, {'account': '2110', 'branch': b['id'], 'credit': inv.sgst}]
    if tot['round_off']:
        jl.append({'account': '5950', 'branch': b['id'], 'credit': tot['round_off']})   # negative becomes a debit
    if cost:
        jl += [{'account': '5000', 'branch': b['id'], 'debit': cost}, {'account': '1300', 'branch': b['id'], 'credit': cost}]
    j = ledger.post(co, today(), 'Sale', inv.no, f"Sale {inv.no} · {cust.name}", jl, approval_no=(approved or {}).get('approval_no'))
    inv.journal_no = j.no
    for ln, dq in zip(inv.lines, delivered):
        if dq:
            stock.move(co, b['id'], ln.item_id, -dq, 'Sale', inv.no, today(), ln.unit_cost, allow_negative=bool(approved and short))
    if voucher:
        used = dict(voucher.used_qty or {})
        for ln in lines:
            cat = db.val('SELECT category FROM items WHERE id=:i', i=ln['item_id'])
            used[cat] = int(used.get(cat, 0)) + ln['qty']
        voucher.used_qty = used
        voucher.used_value = money(voucher.used_value) + msea_part
        from .msea import close_if_used
        close_if_used(voucher)
    audit.write('Invoice posted', inv.no, new=f"Rs {inv.total} · " + ' + '.join(f"{p['mode']} {p['amount']}" for p in pays),
                reason=('Pending delivery: ' + inv.pending_reason) if inv.pending_reason else None, category='sales', company=co, branch=b['id'])
    if inv.override_reason:
        audit.write('Stock override used', inv.no, new=inv.override_reason, status='Approved by ' + str(inv.override_by), category='sales', company=co, branch=b['id'])
    return inv, None


def hold_bill(d, b, why, role, total):
    u = g.user
    h = HeldBill(no=next_no(b['company'], 'HOLD'), company=b['company'], branch_id=b['id'], draft=d, reason=why, status='Held', created_by=u['id'])
    db.add(h)
    a = approval_request('sale', f"Held bill {h.no} at {b['name']}: {why}", {'hold_id': h.id}, role, company=b['company'], branch=b['id'],
                         amount=total, reason=why, ref=h.no)
    h.approval_no = a['no']
    audit.write('Bill held for approval', h.no, new=why, category='sales', company=b['company'], branch=b['id'])
    return {'held': True, 'hold_no': h.no, 'approval': a, 'message': f'Bill held: {why}. Sent to {role} ({a["no"]}).'}


@handler('sale')
def _approve_hold(p, apr):
    h = db.session().get(HeldBill, p['hold_id'], with_for_update=True)
    if not h or h.status != 'Held':
        raise conflict('This held bill is no longer waiting.', 'wrong_status')
    # the bill is posted in the name of the cashier who made it
    maker = g.user
    from ..core.rbac import load_user
    g.user = load_user(h.created_by)
    try:
        inv, _ = post_invoice(dict(h.draft), approved={'by': maker['name'], 'approval_no': apr['no']})
    finally:
        g.user = maker
    h.status, h.invoice_no = 'Released', inv.no
    return f'Invoice {inv.no} posted'


@on_reject('sale')
def _reject_hold(p, apr, reason):
    h = db.session().get(HeldBill, p['hold_id'], with_for_update=True)
    if h:
        h.status = 'Discarded'


# ---------------------------------------------------------------- pending delivery
def deliver_pending(invoice_id, qtys):
    inv = _invoice(invoice_id)
    check_scope(g.user, inv.branch_id)
    if inv.status == 'Cancelled':
        raise conflict('This invoice is cancelled.', 'wrong_status')
    by_line = {int(x['line_no']): int(x['qty']) for x in (qtys or [])}
    cost = Decimal(0)
    moved = False
    for ln in inv.lines:
        q = by_line.get(ln.line_no, 0)
        pending = ln.qty - ln.delivered_qty - max(0, ln.returned_qty - ln.delivered_qty)
        if q < 0 or q > pending:
            raise bad(f'Line {ln.line_no}: deliver between 0 and {pending}.', 'bad_quantity')
        if q:
            stock.move(inv.company, inv.branch_id, ln.item_id, -q, 'Sale (delivery)', inv.no, today(), ln.unit_cost)
            ln.delivered_qty += q
            cost += Decimal(str(ln.unit_cost)) * q
            moved = True
    if not moved:
        raise bad('Enter the quantity being handed over now.', 'nothing_to_deliver')
    no = next_no(inv.company, 'DLV')
    ledger.post(inv.company, today(), 'Delivery', no, f'Pending items delivered against {inv.no}',
                [{'account': '5000', 'branch': inv.branch_id, 'debit': cost}, {'account': '1300', 'branch': inv.branch_id, 'credit': cost}], skip_if_empty=True)
    audit.write('Pending items delivered', inv.no, new=no, category='sales', company=inv.company, branch=inv.branch_id)
    return no


def _invoice(invoice_id, lock=True):
    inv = db.session().get(Invoice, invoice_id, with_for_update=lock)
    if not inv:
        raise not_found('Invoice not found.', 'unknown_invoice')
    return inv


# ---------------------------------------------------------------- returns and cancellations
def return_value(inv, qty_by_line):
    taxable = tax = Decimal(0)
    for ln in inv.lines:
        q = qty_by_line.get(ln.line_no, 0)
        if q:
            taxable += Decimal(str(ln.taxable)) / ln.qty * q
            tax += Decimal(str(ln.tax)) / ln.qty * q
    taxable, tax = money(taxable), money(tax)
    return taxable, tax, rupee(taxable + tax)


def request_return(invoice_id, d):
    """d: {lines:[{line_no, qty}], refund_mode, reason, condition: Good|Damaged, kind: Return|Cancellation|Exchange}"""
    u = g.user
    inv = _invoice(invoice_id)
    check_scope(u, inv.branch_id)
    if inv.channel == 'online' and not d.get('_online'):
        raise conflict('Online orders are cancelled or returned through the online request workflow.', 'use_online_request')
    kind = d.get('kind') or 'Return'
    if kind not in ('Return', 'Cancellation', 'Exchange'):
        raise bad('Kind must be Return, Cancellation or Exchange.', 'invalid')
    reason = str(d.get('reason') or '').strip()
    if not reason:
        raise bad('Give the reason for the return.', 'reason_required')
    controls = setting('controls', {}) or {}
    if kind == 'Cancellation':
        hours = int(controls.get('cancelHours') or 24)
        made = dt.datetime.combine(inv.inv_date, inv.inv_time)
        if dt.datetime.now() - made > dt.timedelta(hours=hours) and not d.get('_approved'):
            raise conflict(f'Bills can be cancelled within {hours} hours. Use a return instead.', 'cancel_window_passed')
        qty_by_line = {ln.line_no: ln.qty - ln.returned_qty for ln in inv.lines}
    else:
        days = int(controls.get('returnDays') or 7)
        if (today() - inv.inv_date).days > days and not d.get('_approved'):
            raise conflict(f'The return window is {days} days from the bill date.', 'return_window_passed')
        qty_by_line = {whole(x.get('line_no'), 'Line number'): whole(x.get('qty'), 'Return quantity') for x in (d.get('lines') or []) if isinstance(x, dict) and whole(x.get('qty'), 'Return quantity', 0)}
    if not any(qty_by_line.values()):
        raise bad('Enter at least one quantity to return.', 'nothing_to_return')
    for ln in inv.lines:
        q = qty_by_line.get(ln.line_no, 0)
        if q < 0 or q > ln.qty - ln.returned_qty:
            raise bad(f'Return quantity for {ln.description} must be between 0 and {ln.qty - ln.returned_qty}.', 'bad_quantity')
    mode = d.get('refund_mode') or ('credit' if inv.msea_voucher_no and not _parent_share(inv) else 'cash')
    if inv.msea_voucher_no and not _parent_share(inv):
        mode = 'credit'                                  # all of it was MSEA's: it goes back to the voucher
    elif inv.msea_voucher_no and mode in ('credit', 'wallet'):
        raise bad("MSEA's part goes back to the voucher; choose how the parent's part is paid back: cash, UPI, card or bank.", 'sponsored_return_mode')
    if mode not in PAY_ACCOUNT or mode == 'online' and not d.get('_online'):
        raise bad('Choose how the money goes back: cash, UPI, card, bank, wallet or against the account.', 'bad_payment_mode')
    taxable, tax, refund = return_value(inv, qty_by_line)
    if mode == 'credit' and refund > money(inv.balance) + Decimal('0.01') and not inv.msea_voucher_no:
        raise bad(f'Adjusting against the account is limited to the invoice balance of Rs {inv.balance}.', 'credit_exceeds_balance')
    payload = {'invoice_id': inv.id, 'lines': [{'line_no': k, 'qty': v} for k, v in qty_by_line.items() if v], 'refund_mode': mode,
               'reason': reason, 'condition': d.get('condition') or 'Good', 'kind': kind}
    if not d.get('_approved') and not d.get('_online'):
        role = role_for_limit(u, 'refund', refund, txn_type='ret', branch=inv.branch_id)
        if role:
            a = approval_request('ret', f'{kind} against {inv.no}: refund Rs {refund}', payload, role, company=inv.company, branch=inv.branch_id,
                                 amount=refund, reason=reason, ref=inv.no, signature=f'ret:{inv.no}:{payload["lines"]}')
            return None, a
    return execute_return(inv, payload), None


@handler('ret')
def _approve_return(p, apr):
    inv = _invoice(p['invoice_id'])
    cn = execute_return(inv, dict(p, _approved=True), approval_no=apr['no'])
    return f'Credit note {cn.no} issued'


def _parent_share(inv):
    """Part of a voucher bill the parent paid (the rest was MSEA's, against the voucher)."""
    msea = db.val("SELECT COALESCE(SUM(amount), 0) FROM invoice_payments WHERE invoice_id=:i AND mode='credit'", i=inv.id) or 0
    return max(Decimal(0), money(inv.total) - money(msea))


def execute_return(inv, p, approval_no=None):
    co, br = inv.company, inv.branch_id
    check_lock(co, today())
    qty_by_line = {int(x['line_no']): int(x['qty']) for x in p['lines']}
    taxable, tax, refund = return_value(inv, qty_by_line)
    damaged = p.get('condition') == 'Damaged'
    mode = p['refund_mode']
    cust = db.session().get(Customer, inv.customer_id)
    # a voucher bill is refunded in the same proportion it was paid: MSEA's part back to the voucher, the parent's part in `mode`
    msea_back = refund
    if inv.msea_voucher_no:
        parent = _parent_share(inv)
        msea_back = money(refund * (money(inv.total) - parent) / money(inv.total)) if money(inv.total) else refund
        if parent <= 0:
            mode = 'credit'
    parent_back = refund - msea_back if inv.msea_voucher_no else Decimal(0)
    if (mode == 'cash' and not p.get('_online')) and (not inv.msea_voucher_no or parent_back > 0):
        shift = _open_shift(g.user['id'], db.val('SELECT location FROM branches WHERE id=:b', b=br))
    else:
        shift = None
    cn = CreditNote(no=next_no(co, 'CN'), invoice_id=inv.id, company=co, cn_date=today(), kind=p.get('kind') or 'Return',
                    condition='Damaged' if damaged else 'Good', reason=p['reason'], taxable=taxable, tax=tax, amount=refund, refund_mode=mode,
                    shift_id=shift['id'] if shift else None, approval_no=approval_no, created_by=g.user['id'])
    cost = Decimal(0)
    n = 0
    for ln in inv.lines:
        q = qty_by_line.get(ln.line_no, 0)
        if not q:
            continue
        n += 1
        undelivered = max(0, ln.qty - ln.delivered_qty - max(0, ln.returned_qty - ln.delivered_qty))
        from_pending = min(q, undelivered)
        from_delivered = q - from_pending
        restock = from_delivered and not damaged
        if restock:
            stock.move(co, br, ln.item_id, from_delivered, 'Cancellation' if cn.kind == 'Cancellation' else 'Sales return', cn.no, today(), ln.unit_cost)
        cost += Decimal(str(ln.unit_cost)) * from_delivered
        ln.returned_qty += q
        cn.lines.append(CreditNoteLine(line_no=n, invoice_line_no=ln.line_no, item_id=ln.item_id, qty=q,
                                       amount=money(Decimal(str(ln.amount)) / ln.qty * q), restocked=bool(restock)))
    db.add(cn)
    party = inv.customer_id if mode in ('credit', 'wallet') else None
    refund_acct = (cust.receivable_account if cust else '1200') if mode == 'credit' else PAY_ACCOUNT[mode]
    jl = [{'account': '4010', 'branch': br, 'debit': taxable}]
    if inv.igst:
        jl.append({'account': '2120', 'branch': br, 'debit': tax})
    else:
        cg = money(tax / 2)
        jl += [{'account': '2100', 'branch': br, 'debit': cg}, {'account': '2110', 'branch': br, 'debit': tax - cg}]
    if inv.msea_voucher_no and parent_back > 0:
        if msea_back:
            jl.append({'account': cust.receivable_account if cust else '1210', 'branch': br, 'credit': msea_back, 'party': inv.customer_id})
        jl.append({'account': PAY_ACCOUNT[mode], 'branch': br, 'credit': parent_back, 'party': None})
    else:
        jl.append({'account': refund_acct, 'branch': br, 'credit': refund, 'party': party})
    ro = refund - taxable - tax
    if ro:
        jl.append({'account': '5950', 'branch': br, 'debit': ro})
    if cost:
        jl += [{'account': '5600' if damaged else '1300', 'branch': br, 'debit': cost}, {'account': '5000', 'branch': br, 'credit': cost}]
    j = ledger.post(co, today(), 'Credit note', cn.no, f"{cn.kind} of {inv.no}: {p['reason']}", jl, approval_no=approval_no)
    cn.journal_no = j.no
    if inv.msea_voucher_no:
        inv.balance = max(Decimal(0), money(inv.balance) - msea_back)
    elif mode == 'credit':
        inv.balance = max(Decimal(0), money(inv.balance) - refund)
    if inv.msea_voucher_no:
        v = db.session().get(MseaVoucher, inv.msea_voucher_no, with_for_update=True)
        if v:
            used = dict(v.used_qty or {})
            for ln in inv.lines:
                q = qty_by_line.get(ln.line_no, 0)
                if q:
                    cat = db.val('SELECT category FROM items WHERE id=:i', i=ln.item_id)
                    used[cat] = max(0, int(used.get(cat, 0)) - q)
            v.used_qty = used
            v.used_value = max(Decimal(0), money(v.used_value) - msea_back)
            if v.status in ('Partly used', 'Fully used', 'Approved'):
                from .msea import close_if_used
                close_if_used(v)
    full = all(ln.returned_qty >= ln.qty for ln in inv.lines)
    inv.status = ('Cancelled' if cn.kind == 'Cancellation' else 'Returned') if full else 'Partly returned'
    audit.write('Invoice cancelled (credit note)' if cn.kind == 'Cancellation' else 'Sales return', cn.no, old=inv.no,
                new=f'Refund Rs {refund} via {mode}' + (' · damaged, written off' if damaged and cost else ''), reason=p['reason'],
                category='sales', company=co, branch=br)
    refund_alert = Decimal(str(((setting('controls', {}) or {}).get('refundAlert')) or 2000))
    if refund >= refund_alert:
        notify.alert('High-value refund', f'{cn.no}: Rs {refund} refunded against {inv.no} ({p["reason"]})', ref=cn.no, company=co, branch=br)
    return cn


def reprint(invoice_id):
    inv = _invoice(invoice_id)
    check_scope(g.user, inv.branch_id)
    inv.reprints += 1
    mx = int(((setting('controls', {}) or {}).get('reprintMax')) or 2)
    audit.write('Invoice reprinted', inv.no, new=f'copy {inv.reprints}', category='sales', company=inv.company, branch=inv.branch_id,
                status='Above limit' if inv.reprints > mx else None)
    if inv.reprints > mx:
        notify.alert('Repeated reprint', f'{inv.no} reprinted {inv.reprints} times', ref=inv.no, company=inv.company, branch=inv.branch_id)
    return inv.reprints


def new_group_id():
    return uuid.uuid4().hex
