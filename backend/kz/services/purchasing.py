"""Purchasing.

Requisition (PO in status Requisition) -> quotes from suppliers -> choose supplier and submit -> approval when the value
is above the requester's 'po' limit -> PO approved -> goods receipt (one or more GRNs) -> supplier bill -> payment.

GRN:      landed rate = rate + freight shared by value; moving-average cost updated; stock in.
          Dr 1300 Inventory / Cr 2050 Goods received not invoiced.
Bill:     Dr 2050 (received value) + Dr 1310 GST input / Cr 2000 Trade payables (party = supplier).
Payment:  Dr 2000 (oldest bills first) / Cr 1120 bank or 1100 cash.  Above the 'pay' limit -> approval.
Return:   debit note. Billed: Dr 2000 (rate incl. GST) / Cr 1300 (landed) / Cr 1310 GST / +-5100 difference.
          Not billed yet: Dr 2050 / Cr 1300 at landed rate.
"""
import datetime as dt
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit
from ..core.approvals import handler, on_reject, request as approval_request, role_for_limit
from ..core.numbering import next_no
from ..errors import bad, conflict, not_found
from ..models import DebitNote, GoodsReceipt, PurchaseOrder, PurchaseOrderLine, PurchaseQuote, Supplier, SupplierBill, SupplierPayment
from . import ledger, stock
from .common import branch, check_company, check_lock, check_scope, item, money, parse_date, positive_int, today, whole

OPEN_FOR_RECEIPT = ('PO approved', 'Partly received')


def _po(po_id, lock=True):
    po = db.session().get(PurchaseOrder, po_id, with_for_update=lock)
    if not po:
        raise not_found('Purchase order not found.', 'unknown_po')
    check_company(g.user, po.company)
    return po


def _supplier(sid, company):
    s = db.session().get(Supplier, sid)
    if not s or s.company != company or not s.active:
        raise bad('Choose an active supplier of this company.', 'unknown_supplier')
    return s


def create_requisition(d):
    u = g.user
    b = branch(d.get('branch_id'))
    check_scope(u, b['id'])
    lines = d.get('lines') or []
    if not lines:
        raise bad('Add at least one item.', 'no_lines')
    po = PurchaseOrder(no=next_no(b['company'], 'PO'), company=b['company'], branch_id=b['id'], status='Requisition',
                       required_by=parse_date(d.get('required_by'), today() + dt.timedelta(days=14)), notes=(d.get('notes') or '')[:300] or None,
                       created_by=u['id'], freight=money(d.get('freight')))
    value = Decimal(0)
    for n, ln in enumerate(lines, 1):
        it = item(ln.get('item_id'), b['company'])
        q = positive_int(ln.get('qty'), f'Quantity for {it.name}')
        rate = money(ln.get('rate') if ln.get('rate') not in (None, '') else it.cost)
        po.lines.append(PurchaseOrderLine(line_no=n, item_id=it.id, qty=q, rate=rate))
        value += rate * q
    po.value = money(value)
    if d.get('supplier_id'):
        po.supplier_id = _supplier(d['supplier_id'], po.company).id
    db.add(po)
    audit.write('Requisition raised', po.no, new=f'{len(po.lines)} line(s), Rs {po.value}', category='purchase', company=po.company, branch=po.branch_id)
    return po


def add_quote(po_id, d):
    po = _po(po_id)
    if po.status not in ('Requisition', 'Quotes'):
        raise conflict(f'{po.no} is {po.status}; quotes are taken before submission.', 'wrong_status')
    s = _supplier(d.get('supplier_id'), po.company)
    rates = [money(x) for x in (d.get('rates') or [])]
    if len(rates) != len(po.lines) or any(r <= 0 for r in rates):
        raise bad('Enter a rate for every line.', 'bad_rate')
    for q in po.quotes:
        if q.supplier_id == s.id:
            q.rates, q.lead_days = [str(r) for r in rates], d.get('lead_days')
            break
    else:
        po.quotes.append(PurchaseQuote(supplier_id=s.id, rates=[str(r) for r in rates], lead_days=d.get('lead_days')))
    po.status = 'Quotes'
    audit.write('Quote recorded', po.no, new=f'{s.name}: Rs {sum(r * ln.qty for r, ln in zip(rates, po.lines))}', category='purchase', company=po.company)
    return po


def submit(po_id, d):
    u = g.user
    po = _po(po_id)
    if po.status not in ('Requisition', 'Quotes'):
        raise conflict(f'{po.no} is {po.status}.', 'wrong_status')
    sid = d.get('supplier_id') or po.supplier_id
    s = _supplier(sid, po.company)
    q = next((x for x in po.quotes if x.supplier_id == s.id), None)
    if q:
        for ln, r in zip(po.lines, q.rates):
            ln.rate = money(r)
        for x in po.quotes:
            x.selected = x.supplier_id == s.id
        cheapest = min(po.quotes, key=lambda x: sum(Decimal(r) * ln.qty for r, ln in zip(x.rates, po.lines)))
        if cheapest.supplier_id != s.id and not str(d.get('reason') or '').strip():
            raise bad('You chose a supplier who is not the cheapest. Give the reason.', 'reason_required')
    po.supplier_id = s.id
    po.value = money(sum(Decimal(str(ln.rate)) * ln.qty for ln in po.lines))
    role = role_for_limit(u, 'po', po.value, txn_type='po', branch=po.branch_id)
    if role:
        po.status = 'Pending approval'
        a = approval_request('po', f'{po.no}: {s.name} Rs {po.value:,.2f}', {'id': po.id}, role, company=po.company, branch=po.branch_id,
                             amount=po.value, reason=d.get('reason'), ref=po.no)
        audit.write('PO submitted', po.no, new=f'{s.name} Rs {po.value}, needs {role}', category='purchase', company=po.company)
        return po, a
    po.status = 'PO approved'
    audit.write('PO approved (within limit)', po.no, new=f'{s.name} Rs {po.value}', category='purchase', company=po.company)
    return po, None


@handler('po')
def _approve_po(p, apr):
    po = _po(p['id'])
    if po.status != 'Pending approval':
        raise conflict(f'{po.no} is {po.status}.', 'wrong_status')
    po.status = 'PO approved'
    return f'{po.no} approved'


@on_reject('po')
def _reject_po(p, apr, reason):
    po = _po(p['id'])
    po.status = 'Rejected'


def cancel_po(po_id, reason):
    po = _po(po_id)
    if po.status not in ('Requisition', 'Quotes', 'Pending approval', 'PO approved'):
        raise conflict('Goods were already received against this PO.', 'wrong_status')
    if not (reason or '').strip():
        raise bad('Give a reason.', 'reason_required')
    po.status = 'Cancelled'
    db.run("UPDATE approvals SET status='Cancelled', result=:r WHERE kind='po' AND ref=:n AND status='Pending'", r=reason, n=po.no)
    audit.write('PO cancelled', po.no, reason=reason, category='purchase', company=po.company)
    return po


def receive(po_id, d):
    u = g.user
    po = _po(po_id)
    check_scope(u, po.branch_id)
    branch(po.branch_id)
    if po.status not in OPEN_FOR_RECEIPT:
        raise conflict(f'{po.no} is {po.status}; goods can be received only against an approved PO.', 'wrong_status')
    date = parse_date(d.get('date'))
    check_lock(po.company, date)
    rec = {int(x['line_no']): x for x in (d.get('lines') or [])}
    accepted_value = Decimal(0)
    work = []
    for ln in po.lines:
        x = rec.get(ln.line_no, {})
        rq, rj = whole(x.get('received'), 'Received quantity', 0), whole(x.get('rejected'), 'Rejected quantity', 0)
        left = ln.qty - ln.received_qty
        if rq < 0 or rq > left or rj < 0 or rj > rq:
            raise bad(f'Line {ln.line_no}: receive 0-{left}; rejected up to the received quantity.', 'bad_quantity')
        acc = rq - rj
        work.append((ln, rq, rj, acc))
        accepted_value += acc * Decimal(str(ln.rate))
    if accepted_value <= 0:
        raise bad('Enter accepted quantities before posting the GRN.', 'nothing_received')
    freight = money(d.get('freight', po.freight if not po.grn_value else 0))
    grn = GoodsReceipt(no=next_no(po.company, 'GRN', date), po_id=po.id, txn_date=date, value=0, line_items=[], received_by=u['id'])
    total = Decimal(0)
    for ln, rq, rj, acc in work:
        if not acc:
            ln.received_qty += rq
            ln.rejected_qty += rj
            continue
        share = freight * (acc * Decimal(str(ln.rate))) / accepted_value if freight else Decimal(0)
        landed = (Decimal(str(ln.rate)) + share / acc).quantize(Decimal('0.0001'))
        stock.receive_cost(po.company, ln.item_id, acc, landed)
        stock.move(po.company, po.branch_id, ln.item_id, acc, 'GRN', grn.no, date, landed)
        ln.received_qty += rq
        ln.rejected_qty += rj
        ln.landed_rate = landed
        total += landed * acc
        grn.line_items.append({'line_no': ln.line_no, 'item_id': ln.item_id, 'received': rq, 'rejected': rj, 'landed_rate': str(landed)})
    total = money(total)
    grn.value = total
    db.add(grn)
    ledger.post(po.company, date, 'GRN', grn.no, f'GRN {grn.no} against {po.no}', [{'account': '1300', 'branch': po.branch_id, 'debit': total},
                {'account': '2050', 'branch': po.branch_id, 'credit': total}])
    po.grn_value = money(po.grn_value) + total
    po.status = 'Goods received' if all(ln.received_qty >= ln.qty for ln in po.lines) else 'Partly received'
    audit.write('GRN posted', grn.no, new=f'Rs {total} against {po.no}', category='purchase', company=po.company, branch=po.branch_id)
    return grn


def book_bill(po_id, d):
    po = _po(po_id)
    bill_no = str(d.get('bill_no') or '').strip()
    if not bill_no:
        raise bad("Enter the supplier's invoice number.", 'bill_no_required')
    if db.val('SELECT COUNT(*) FROM supplier_bills WHERE company=:c AND supplier_id=:s AND bill_no=:b', c=po.company, s=po.supplier_id, b=bill_no):
        raise conflict('This supplier invoice number is already booked.', 'duplicate_bill')
    if db.val('SELECT COUNT(*) FROM supplier_bills WHERE po_id=:p', p=po.id):
        raise conflict(f'A bill is already booked against {po.no}.', 'duplicate_bill')
    if po.status not in ('Goods received', 'Partly received'):
        raise conflict('Book the supplier bill after the goods are received.', 'wrong_status')
    date = parse_date(d.get('bill_date'))
    check_lock(po.company, date)
    gst = Decimal(0)
    for ln in po.lines:
        acc = ln.received_qty - ln.rejected_qty
        gst_rate = Decimal(str(db.val('SELECT gst_rate FROM items WHERE id=:i', i=ln.item_id) or 0))
        gst += acc * Decimal(str(ln.rate)) * gst_rate / 100
    gst = money(gst)
    taxable = money(po.grn_value)
    total = taxable + gst
    if d.get('bill_total') not in (None, '') and abs(money(d['bill_total']) - total) > Decimal('1.00'):
        raise conflict(f"Supplier's total Rs {money(d['bill_total'])} does not match the received value Rs {total} (taxable {taxable} + GST {gst}). "
                       'Check the GRN or the supplier invoice.', 'bill_mismatch', expected=float(total))
    s = db.session().get(Supplier, po.supplier_id)
    no = next_no(po.company, 'PI', date)
    j = ledger.post(po.company, date, 'Purchase invoice', no, f'Supplier invoice {bill_no} · {s.name} · {po.no}',
                    [{'account': '2050', 'branch': po.branch_id, 'debit': taxable}, {'account': '1310', 'branch': po.branch_id, 'debit': gst},
                     {'account': '2000', 'branch': po.branch_id, 'credit': total, 'party': s.id}])
    bill = SupplierBill(company=po.company, supplier_id=s.id, branch_id=po.branch_id, po_id=po.id, bill_no=bill_no, internal_no=no, bill_date=date,
                        due_date=date + dt.timedelta(days=s.terms_days or 30), taxable=taxable, gst=gst, amount=total, journal_no=j.no)
    db.add(bill)
    po.status = 'Invoiced'
    audit.write('Purchase invoice booked', no, new=f'Rs {total} · {s.name} · bill {bill_no}', category='purchase', company=po.company, branch=po.branch_id)
    return bill


def outstanding(bill):
    return money(bill.amount) - money(bill.paid) - money(bill.adjusted)


def pay_supplier(d):
    u = g.user
    co = d.get('company')
    check_company(u, co)
    s = _supplier(d.get('supplier_id'), co)
    amt = money(d.get('amount'))
    mode = d.get('mode') or 'bank'
    if amt <= 0:
        raise bad('Enter an amount greater than zero.', 'bad_amount')
    if mode not in ('bank', 'cash'):
        raise bad('Pay suppliers from the bank or in cash.', 'bad_payment_mode')
    if mode == 'bank' and not str(d.get('reference') or '').strip():
        raise bad('Enter the bank reference (UTR / cheque number).', 'payment_ref_required')
    bills = db.session().query(SupplierBill).filter_by(company=co, supplier_id=s.id).order_by(SupplierBill.bill_date, SupplierBill.id).all()
    out = sum((outstanding(b) for b in bills), Decimal(0))
    if amt > out:
        raise bad(f'Payment is more than the Rs {out} outstanding to this supplier.', 'overpayment')
    payload = {'company': co, 'supplier_id': s.id, 'amount': str(amt), 'mode': mode, 'reference': d.get('reference'),
               'date': str(parse_date(d.get('date'))), 'no': next_no(co, 'PV')}
    role = role_for_limit(u, 'pay', amt, txn_type='pay')
    if role:
        return None, approval_request('pay', f"Supplier payment {payload['no']}: {s.name} Rs {amt}", payload, role, company=co, amount=amt,
                                      reason=d.get('reason'), ref=payload['no'])
    return post_payment(payload), None


def post_payment(p, approval_no=None):
    co, amt = p['company'], money(p['amount'])
    date = parse_date(p['date'])
    s = db.session().get(Supplier, p['supplier_id'])
    bills = (db.session().query(SupplierBill).filter_by(company=co, supplier_id=s.id).order_by(SupplierBill.bill_date, SupplierBill.id)
             .with_for_update().all())
    lines, rem, applied = [], amt, []
    for b in bills:
        o = outstanding(b)
        if rem <= 0 or o <= 0:
            continue
        x = min(rem, o)
        lines.append({'account': '2000', 'branch': b.branch_id, 'debit': x, 'party': s.id})
        b.paid = money(b.paid) + x
        applied.append([b.id, float(x)])
        rem -= x
    if rem > 0:
        raise bad(f'Payment is more than the outstanding amount by Rs {rem}.', 'overpayment')
    pay_branch = f'{co}-HO'
    lines.append({'account': '1100' if p['mode'] == 'cash' else '1120', 'branch': pay_branch, 'credit': amt})
    j = ledger.post(co, date, 'Payment', p['no'], f"Payment to {s.name}" + (f" · ref {p['reference']}" if p.get('reference') else ''), lines,
                    approval_no=approval_no)
    sp = SupplierPayment(no=p['no'], company=co, supplier_id=s.id, pay_date=date, amount=amt, mode=p['mode'], reference=p.get('reference'),
                         applied=applied, journal_no=j.no, created_by=g.user['id'])
    db.add(sp)
    audit.write('Supplier payment', sp.no, new=f'Rs {amt} to {s.name}', category='purchase', company=co)
    return sp


@handler('pay')
def _approve_pay(p, apr):
    return f"Payment {post_payment(p, apr['no']).no} posted"


def purchase_return(po_id, d):
    u = g.user
    po = _po(po_id)
    check_scope(u, po.branch_id)
    ln = next((x for x in po.lines if x.line_no == int(d.get('line_no') or 0)), None)
    if not ln:
        raise bad('Choose the PO line.', 'bad_line')
    q = positive_int(d.get('qty'), 'Quantity')
    reason = str(d.get('reason') or '').strip()
    if not reason:
        raise bad('Give a reason for the purchase return.', 'reason_required')
    returnable = ln.received_qty - ln.rejected_qty - ln.returned_qty
    if q > returnable:
        raise bad(f'Return between 1 and {returnable} units.', 'bad_quantity')
    check_lock(po.company, today())
    landed = Decimal(str(ln.landed_rate or ln.rate))
    gst_rate = Decimal(str(db.val('SELECT gst_rate FROM items WHERE id=:i', i=ln.item_id) or 0))
    inv_value = money(landed * q)
    no = next_no(po.company, 'DN')
    stock.move(po.company, po.branch_id, ln.item_id, -q, 'Purchase return', no, today(), landed)
    bill = db.session().query(SupplierBill).filter_by(po_id=po.id).with_for_update().first()
    if bill:
        gst = money(Decimal(str(ln.rate)) * q * gst_rate / 100)
        ap = money(Decimal(str(ln.rate)) * q) + gst
        diff = ap - inv_value - gst
        lines = [{'account': '2000', 'branch': po.branch_id, 'debit': ap, 'party': po.supplier_id}, {'account': '1300', 'branch': po.branch_id, 'credit': inv_value},
                 {'account': '1310', 'branch': po.branch_id, 'credit': gst}]
        if diff:
            lines.append({'account': '5100', 'branch': po.branch_id, 'credit': diff})
        bill.adjusted = money(bill.adjusted) + ap
        amount = ap
    else:
        lines = [{'account': '2050', 'branch': po.branch_id, 'debit': inv_value}, {'account': '1300', 'branch': po.branch_id, 'credit': inv_value}]
        po.grn_value = money(po.grn_value) - inv_value
        amount = inv_value
    ledger.post(po.company, today(), 'Debit note', no, f'Purchase return {no} · {po.no} · {reason}', lines)
    ln.returned_qty += q
    dn = DebitNote(no=no, company=po.company, po_id=po.id, line_no=ln.line_no, item_id=ln.item_id, qty=q, amount=amount, reason=reason, created_by=u['id'])
    db.add(dn)
    audit.write('Purchase return / debit note', no, new=f'{q} x {ln.item_id}, Rs {amount}', reason=reason, category='purchase', company=po.company)
    return dn
