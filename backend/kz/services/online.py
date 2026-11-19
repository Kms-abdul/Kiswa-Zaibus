"""Online store: parent ordering, payment, central-warehouse fulfilment, branch collection, cancellations and returns.

Flow (status on each order):
  Order Placed -> Payment Confirmed (invoice posted, paid by 'online' = 1115 Razorpay clearing; stock reserved at the
  central warehouse) -> Picking -> Quality Check -> Packed (stock leaves the warehouse: COGS posted, reservation released)
  -> Dispatched to Branch -> Received at Branch -> Ready for Collection (one-time collection code sent to the parent)
  -> Collected (parent shows the code).
Requests: Cancellation (before packing) and Return (within the return window after collection) are approved by the
warehouse / branch manager; the refund goes back through Razorpay and Finance confirms it.
"""
import datetime as dt
import hashlib
import secrets
import uuid
from decimal import Decimal

from flask import g

from .. import db
from ..config import Config
from ..core import audit, notify
from ..core.numbering import next_no
from ..core.security import setting
from ..errors import ApiError, bad, conflict, denied, not_found
from ..models import Invoice, InvoiceLine, InvoicePayment, Item, OnlineOrder, OnlineOrderLine, OrderRequest, Refund
from . import ledger, razorpay, sales, stock
from .common import HOME_STATE, money, today, whole

STEP_ROLES = {
    'pick': {'Warehouse Picker', 'Inventory Manager', 'Warehouse Manager'},
    'qc': {'Warehouse Quality Checker', 'Inventory Manager', 'Warehouse Manager'},
    'pack': {'Warehouse Packer', 'Inventory Manager', 'Warehouse Manager'},
    'dispatch': {'Warehouse Dispatcher', 'Inventory Manager', 'Warehouse Manager'},
    'branch': {'Branch Collection Staff', 'Branch In-Charge', 'Store In-Charge', 'Branch Manager', 'Store Cashier'},
    'manage': {'Inventory Manager', 'Warehouse Manager', 'Accounts Manager', 'CFO', 'Branch Manager', 'Store In-Charge'},
    'finance': {'Finance', 'Finance Head', 'Accounts Manager', 'CFO'},
}
ALL_STEPS = {'Director', 'Owner', 'Super Admin', 'Chairman'}
SYSTEM = {'id': None, 'name': 'System (online store)', 'main_role': 'System'}
CATEGORY_LIMIT_KEY = {'Shoes': 'shoes', 'Book': 'book', 'Textbook': 'book', 'Workbook': 'book'}
UNIFORM = {'Shirt', 'Trouser', 'Skirt', 'Tie', 'Belt', 'Socks', 'Blazer', 'Pinafore', 'Tunic', 'Sweater', 'Jacket', 'T-shirt', 'Shorts'}


def cfg():
    return setting('online', {}) or {}


def need_step(step, order=None):
    u = g.user
    if u['read_only'] or not (set(u['roles']) & (STEP_ROLES[step] | ALL_STEPS)):
        raise denied('Your role cannot do this step for online orders.', 'wrong_step_role')
    if step == 'branch' and order is not None and u['branches'] != '*':
        if not any(b.endswith('-' + order.collect_at) for b in u['branches']):
            raise denied('This parcel is for another collection branch.', 'out_of_scope')


def _event(o, text, by=None):
    ev = list(o.events or [])
    ev.append([dt.datetime.now().isoformat(sep=' ', timespec='seconds'), by or (g.user['name'] if g.get('user') else 'Parent'), text])
    o.events = ev


def warehouse(company):
    wh = (cfg().get('wh') or {}).get(company)
    b = db.one('SELECT b.id FROM branches b JOIN locations l ON l.code=b.location WHERE b.id=:i AND b.active=1 AND l.active=1', i=wh) if wh else None
    if not b:
        b = db.one('''SELECT b.id FROM branches b JOIN locations l ON l.code=b.location WHERE b.company=:c AND l.is_central_wh=1 AND b.active=1 AND l.active=1''', c=company)
    if not b:
        raise conflict(f'No central warehouse is set up for {company}.', 'no_warehouse')
    return b['id']


def collection_points():
    pts = cfg().get('points') or []
    return db.clean(db.q('SELECT code, COALESCE(public_name, name) AS name, address FROM locations WHERE code IN :p AND active=1', p=tuple(pts) or ('',)))


def _limit_key(cat):
    if cat in CATEGORY_LIMIT_KEY:
        return CATEGORY_LIMIT_KEY[cat]
    return 'uniform' if cat in UNIFORM else 'other'


def bought_qty(adm_no):
    """Items already bought by the student (counter + online, net of returns), by category."""
    rows = db.q('''SELECT it.category, SUM(l.qty - l.returned_qty) q FROM invoice_lines l JOIN invoices i ON i.id=l.invoice_id JOIN items it ON it.id=l.item_id
                   WHERE i.adm_no=:a AND i.status <> 'Cancelled' GROUP BY it.category''', a=adm_no)
    out = {r['category']: int(r['q'] or 0) for r in rows}
    for r in db.q('''SELECT it.category, SUM(l.qty) q FROM online_order_lines l JOIN online_orders o ON o.id=l.order_id JOIN items it ON it.id=l.item_id
                     WHERE o.adm_no=:a AND o.status='Order Placed' GROUP BY it.category''', a=adm_no):
        out[r['category']] = out.get(r['category'], 0) + int(r['q'] or 0)
    return out


def catalogue(student):
    sponsored = {c[0] for c in (setting('student_categories', []) or []) if len(c) > 1 and c[1]}
    if student['category'] in sponsored:
        return {'sponsored': True, 'message': 'Sponsored students receive their items at the school counter against the MSEA voucher.', 'items': []}
    gender = ['Girls' if student['gender'] == 'F' else 'Boys', 'Unisex', None, '']
    limits = cfg().get('limits') or {}
    had = bought_qty(student['adm_no'])
    from . import schemes as sch
    out = []
    for co in ('KG', 'ZE'):
        try:
            wh = warehouse(co)
        except ApiError:
            continue
        if co == 'KG':
            rows = db.q('''SELECT * FROM items WHERE company='KG' AND active=1 AND unpriced=0 AND price > 0 AND school_id=:s''', s=student['school_id'])
            rows = [r for r in rows if r['gender'] in gender]
        else:
            rows = db.q('''SELECT * FROM items WHERE company='ZE' AND active=1 AND unpriced=0 AND price > 0 AND (class_name=:c OR class_name IS NULL OR class_name='')''',
                        c=student['class_name'])
        active = sch.active_for(co, online=True)
        for r in rows:
            free = stock.available(wh, r['id'])
            dpct, dname = sch.best(r, active)
            per = int(limits.get(_limit_key(r['category']), 6))
            left = max(0, per - had.get(r['category'], 0))
            out.append({'item_id': r['id'], 'company': co, 'name': r['name'], 'category': r['category'], 'size': r['size'], 'class_name': r['class_name'],
                        'price': float(r['price']), 'gst_rate': float(r['gst_rate']), 'in_stock': free, 'max_qty': min(left, free),
                        'disc_pct': float(dpct), 'scheme': dname, 'net_price': float(money(Decimal(str(r['price'])) * (1 - dpct / 100)))})
    return {'sponsored': False, 'items': out, 'collection_points': collection_points(), 'return_days': int((setting('controls', {}) or {}).get('returnDays') or 7)}


def place_order(parent, d):
    adm = d.get('adm_no')
    if adm not in (parent['adm_nos'] or []):
        raise denied('This student is not linked to your mobile number.', 'not_your_student')
    st = db.one('SELECT * FROM students WHERE adm_no=:a', a=adm)
    if not st or st['status'] != 'Active':
        raise conflict('Online ordering is not available for this student.', 'student_blocked')
    point = d.get('collect_at')
    if point not in [p['code'] for p in collection_points()]:
        raise bad('Choose where you will collect the order.', 'bad_collection_point')
    cat = {x['item_id']: x for x in catalogue(st)['items']}
    by_co = {}
    for ln in d.get('lines') or []:
        x = cat.get(ln.get('item_id'))
        q = whole(ln.get('qty'), 'Quantity', 0)
        if not x:
            raise bad('An item in the cart is not available for this student.', 'unknown_item')
        if q <= 0:
            continue
        if q > x['max_qty']:
            raise conflict(f"{x['name']}: you can order up to {x['max_qty']}.", 'over_limit', item=x['item_id'])
        by_co.setdefault(x['company'], []).append((x, q))
    if not by_co:
        raise bad('Your cart is empty.', 'no_lines')
    grp = uuid.uuid4().hex
    orders = []
    for co, lines in by_co.items():
        wh = warehouse(co)
        o = OnlineOrder(no=next_no(co, 'ONL'), grp=grp, company=co, adm_no=adm, mobile=parent['mobile'], collect_at=point, warehouse_branch=wh,
                        subtotal=0, total=0, status='Order Placed', events=[])
        total = Decimal(0)
        for n, (x, q) in enumerate(lines, 1):
            stock.reserve(wh, x['item_id'], q)
            dpct = Decimal(str(x.get('disc_pct') or 0))
            amt = money(Decimal(str(x['price'])) * q * (1 - dpct / 100))
            o.lines.append(OnlineOrderLine(line_no=n, item_id=x['item_id'], qty=q, rate=money(x['price']), disc_pct=dpct, amount=amt))
            total += amt
        o.subtotal = money(total)
        o.total = sales.rupee(total)
        pay = razorpay.create_order(co, o.total, o.no, {'ref': o.no, 'ref_type': 'online_order', 'adm_no': adm})
        o.rzp_order_id = pay['rzp_order_id']
        _event(o, f'Order placed ({len(lines)} item lines, Rs {o.total}); stock reserved at the warehouse', by='Parent')
        db.add(o)
        orders.append({'id': o.id, 'no': o.no, 'company': co, 'total': float(o.total), 'payment': pay})
    audit.write('Online order placed', grp, new=', '.join(x['no'] for x in orders), category='online', user={'id': None, 'name': f'Parent {parent["mobile"][-4:]}', 'main_role': 'Parent'})
    return {'group': grp, 'orders': orders}


def _order(oid, lock=True):
    o = db.session().get(OnlineOrder, oid, with_for_update=lock)
    if not o:
        raise not_found('Order not found.', 'unknown_order')
    return o


def confirm_payment(o, payment_id, *, verified, method='upi'):
    """Mark the order paid and post its invoice (idempotent)."""
    if o.payment_id:
        return o
    if o.status != 'Order Placed':
        raise conflict(f'Order {o.no} is {o.status}.', 'wrong_status')
    st = db.one('SELECT * FROM students WHERE adm_no=:a', a=o.adm_no)
    cust = sales.student_customer(o.company, st)
    inv = _post_online_invoice(o, cust, st, payment_id)
    o.payment_id, o.invoice_no, o.status = payment_id, inv.no, 'Payment Confirmed'
    db.run('UPDATE rzp_payments SET app_ref=:r WHERE id=:i', r=inv.no, i=payment_id)
    _event(o, f'Payment confirmed ({payment_id}); invoice {inv.no}', by='System')
    notify.queue('sms', o.mobile, None, f'Kiswa-Zaibus: payment received for order {o.no} (Rs {o.total}). We will tell you when it is ready to collect.', None, 'Order')
    audit.write('Online payment confirmed', o.no, new=f'{payment_id} · invoice {inv.no}', category='online', company=o.company, user=SYSTEM)
    return o


def _post_online_invoice(o, cust, st, payment_id):
    co, br = o.company, o.warehouse_branch
    lines = []
    for ln in o.lines:
        it = db.session().get(Item, ln.item_id)
        lines.append(sales.price_line(it, ln.qty, ln.disc_pct or 0))
    tot = sales.totals(lines)
    now = dt.datetime.now()
    inv = Invoice(no=next_no(co, 'INV:ONL'), company=co, branch_id=br, inv_date=today(), inv_time=now.time().replace(microsecond=0), customer_id=cust.id,
                  adm_no=o.adm_no, student_snapshot={k: st.get(k) for k in ('adm_no', 'name', 'class_name', 'section', 'category')}, channel='online',
                  place_of_supply=HOME_STATE, gross=tot['gross'], discount=tot['discount'], taxable=tot['taxable'], round_off=tot['round_off'], total=tot['total'],
                  balance=0, status='Posted', pending_reason='Online order: delivered at collection', online_order_no=o.no)
    inv.cgst = money(tot['tax'] / 2)
    inv.sgst = money(tot['tax'] - inv.cgst)
    for n, (ln, p) in enumerate(zip(o.lines, lines), 1):
        it = db.session().get(Item, ln.item_id)
        inv.lines.append(InvoiceLine(line_no=n, item_id=it.id, description=p['description'], hsn=p['hsn'], qty=p['qty'], rate=p['rate'], disc_pct=p['disc_pct'],
                                     gst_rate=p['gst_rate'], taxable=p['taxable'], tax=p['tax'], amount=p['amount'], unit_cost=it.cost, delivered_qty=0))
    inv.payments.append(InvoicePayment(line_no=1, mode='online', amount=tot['total'], ref=payment_id))
    db.add(inv)
    jl = [{'account': '1115', 'branch': br, 'debit': tot['total']}, {'account': '4000', 'branch': br, 'credit': tot['taxable']},
          {'account': '2100', 'branch': br, 'credit': inv.cgst}, {'account': '2110', 'branch': br, 'credit': inv.sgst}]
    if tot['round_off']:
        jl.append({'account': '5950', 'branch': br, 'credit': tot['round_off']})
    j = ledger.post(co, today(), 'Sale', inv.no, f'Online sale {inv.no} · order {o.no}', jl)
    inv.journal_no = j.no
    return inv


def confirm_from_checkout(parent, oid, d):
    o = _order(oid)
    if o.mobile != parent['mobile']:
        raise denied('This order belongs to another account.', 'not_your_order')
    if o.payment_id:
        return o
    if razorpay.simulated(o.company):
        if not d.get('simulate'):
            raise bad('This store is in test mode. Confirm the simulated payment.', 'simulated_mode')
        pid = 'pay_SIM' + secrets.token_hex(7)
        razorpay.record_payment(o.company, {'id': pid, 'order_id': o.rzp_order_id, 'amount': int(o.total * 100), 'status': 'captured', 'method': d.get('method') or 'upi',
                                            'notes': {'ref': o.no, 'adm_no': o.adm_no}, 'created_at': int(dt.datetime.now().timestamp())}, o.no, 'online_order', False)
        return confirm_payment(o, pid, verified=False)
    pid, sig = d.get('razorpay_payment_id'), d.get('razorpay_signature')
    if not razorpay.checkout_signature_ok(o.company, o.rzp_order_id, pid, sig):
        audit.security_event('Payment signature mismatch', f'Order {o.no}: checkout signature did not verify')
        raise bad('We could not verify this payment. If money was taken it will be confirmed automatically within a few minutes.', 'bad_payment_signature')
    ent = razorpay.fetch_payment(o.company, pid)
    razorpay.check_amount(o.total, money((ent.get('amount') or 0) / 100))
    razorpay.record_payment(o.company, ent, o.no, 'online_order', True)
    if ent.get('status') != 'captured':
        raise conflict('The payment is not captured yet. It will be confirmed automatically.', 'payment_pending')
    return confirm_payment(o, pid, verified=True)


def confirm_by_gateway(company, rzp_order_id, payment_id):
    if not rzp_order_id:
        return
    o = db.session().query(OnlineOrder).filter_by(rzp_order_id=rzp_order_id).with_for_update().first()
    if o and not o.payment_id and o.status == 'Order Placed':
        prev = g.get('user')
        g.user = SYSTEM
        try:
            confirm_payment(o, payment_id, verified=True)
        finally:
            g.user = prev


def expire_unpaid(hours=2):
    """Release stock held by orders never paid (scheduler)."""
    n = 0
    cutoff = dt.datetime.now() - dt.timedelta(hours=hours)
    for o in db.session().query(OnlineOrder).filter(OnlineOrder.status == 'Order Placed', OnlineOrder.placed_at < cutoff).with_for_update().all():
        for ln in o.lines:
            stock.reserve(o.warehouse_branch, ln.item_id, -ln.qty)
        o.status = 'Payment failed'
        _event(o, 'Not paid in time; reservation released', by='System')
        n += 1
    return n


# ---------------------------------------------------------------- fulfilment
def advance(oid, step, d):
    o = _order(oid)
    flow = {'pick': ('Payment Confirmed', 'Picking'), 'picked': ('Picking', 'Quality Check'), 'qc': ('Quality Check', 'Packed'),
            'dispatch': ('Packed', 'Dispatched to Branch'), 'receive': ('Dispatched to Branch', 'Ready for Collection')}
    if step not in flow:
        raise bad('Unknown step.', 'invalid')
    want, nxt = flow[step]
    need_step({'pick': 'pick', 'picked': 'pick', 'qc': 'qc', 'dispatch': 'dispatch', 'receive': 'branch'}[step], o)
    if o.status != want:
        raise conflict(f'Order {o.no} is {o.status}; this step needs {want}.', 'wrong_status')
    if db.val("SELECT COUNT(*) FROM order_requests WHERE order_id=:o AND type='Cancellation' AND status='Requested'", o=o.id):
        raise conflict('A cancellation request is waiting on this order. Decide it first.', 'cancellation_pending')
    if step == 'picked':
        for ln in o.lines:
            ln.picked_qty = ln.qty
    if step == 'qc':
        if d.get('ok') is False:
            o.status = 'Picking'
            _event(o, 'Quality check failed: ' + (d.get('note') or 'repick'))
            return o
        _pack(o)
    if step == 'receive':
        code = f'{secrets.randbelow(1_000_000):06d}'
        o.collect_otp_hash = hashlib.sha256(f'{o.no}:{code}:{Config.SECRET_KEY}'.encode()).hexdigest()
        point = db.val('SELECT COALESCE(public_name, name) FROM locations WHERE code=:c', c=o.collect_at)
        notify.queue('sms', o.mobile, None, f'Kiswa-Zaibus: order {o.no} is ready at {point}. Collection code {code}. Show it at the counter.', None, 'OTP')
        if Config.OTP_DEMO:
            g.dev_collect_code = code
    o.status = nxt
    _event(o, nxt)
    audit.write(f'Online order: {nxt}', o.no, category='online', company=o.company)
    return o


def _pack(o):
    """Stock leaves the warehouse when the parcel is packed: COGS posted, reservation released."""
    inv = db.session().query(Invoice).filter_by(no=o.invoice_no).with_for_update().one()
    cost = Decimal(0)
    for ln, il in zip(o.lines, inv.lines):
        stock.reserve(o.warehouse_branch, ln.item_id, -ln.qty)
        stock.move(o.company, o.warehouse_branch, ln.item_id, -ln.qty, 'Online sale', o.no, today(), il.unit_cost)
        il.delivered_qty = il.qty
        cost += Decimal(str(il.unit_cost)) * il.qty
    ledger.post(o.company, today(), 'Delivery', o.no, f'Online order {o.no} packed at the warehouse',
                [{'account': '5000', 'branch': o.warehouse_branch, 'debit': cost}, {'account': '1300', 'branch': o.warehouse_branch, 'credit': cost}],
                skip_if_empty=True)
    _event(o, 'Packed; stock issued from the warehouse')


def collect(oid, code):
    o = _order(oid)
    need_step('branch', o)
    if o.status != 'Ready for Collection':
        raise conflict(f'Order {o.no} is {o.status}.', 'wrong_status')
    h = hashlib.sha256(f'{o.no}:{(code or "").strip()}:{Config.SECRET_KEY}'.encode()).hexdigest()
    if not o.collect_otp_hash or h != o.collect_otp_hash:
        audit.security_event('Wrong collection code', f'Order {o.no}')
        raise bad('The collection code is not correct.', 'bad_otp')
    for ln in o.lines:
        ln.collected_qty = ln.qty
    o.status = 'Collected'
    _event(o, 'Collected by the parent')
    audit.write('Online order collected', o.no, category='online', company=o.company)
    return o


# ---------------------------------------------------------------- cancellations and returns
def create_request(parent, oid, d):
    o = _order(oid)
    if o.mobile != parent['mobile']:
        raise denied('This order belongs to another account.', 'not_your_order')
    kind = d.get('type')
    reason = str(d.get('reason') or '').strip()
    if kind not in ('Cancellation', 'Return'):
        raise bad('Choose cancellation or return.', 'invalid')
    if not reason:
        raise bad('Tell us the reason.', 'reason_required')
    if db.val("SELECT COUNT(*) FROM order_requests WHERE order_id=:o AND status='Requested'", o=o.id):
        raise conflict('A request for this order is already waiting.', 'duplicate_request')
    if kind == 'Cancellation':
        if o.status not in ('Payment Confirmed', 'Picking', 'Quality Check'):
            raise conflict('This order can no longer be cancelled; ask for a return after collecting it.', 'too_late_to_cancel')
        items = [{'line_no': ln.line_no, 'qty': ln.qty} for ln in o.lines]
    else:
        if o.status != 'Collected':
            raise conflict('Returns are accepted after collection.', 'wrong_status')
        days = int((setting('controls', {}) or {}).get('returnDays') or 7)
        last = max((e[0] for e in (o.events or []) if e[2] == 'Collected by the parent'), default=None)
        if last and (dt.datetime.now() - dt.datetime.fromisoformat(last)).days > days:
            raise conflict(f'The return window is {days} days from collection.', 'return_window_passed')
        items = []
        for x in d.get('lines') or []:
            ln = next((l for l in o.lines if l.line_no == int(x.get('line_no') or 0)), None)
            q = whole(x.get('qty'), 'Quantity', 0)
            if ln and 0 < q <= ln.collected_qty - ln.returned_qty:
                items.append({'line_no': ln.line_no, 'qty': q})
        if not items:
            raise bad('Choose the items and quantities to return.', 'nothing_to_return')
    r = OrderRequest(no=next_no(o.company, 'ORQ'), order_id=o.id, type=kind, line_items=items, reason=reason, status='Requested')
    db.add(r)
    _event(o, f'{kind} requested: {reason}', by='Parent')
    notify.alert('Online request', f'{r.no}: {kind} requested for {o.no}', ref=r.no, company=o.company, severity='info')
    return r


def decide_request(rid, d):
    r = db.session().get(OrderRequest, rid, with_for_update=True)
    if not r:
        raise not_found('Request not found.', 'unknown_request')
    o = _order(r.order_id)
    need_step('manage')
    if r.status != 'Requested':
        raise conflict(f'This request is {r.status}.', 'wrong_status')
    note = str(d.get('note') or '').strip()
    if not d.get('approve'):
        if not note:
            raise bad('Give the reason.', 'reason_required')
        r.status, r.decided_by, r.decision_note = 'Rejected', g.user['id'], note
        _event(o, f'{r.type} request rejected: {note}')
        notify.queue('sms', o.mobile, None, f'Kiswa-Zaibus: your {r.type.lower()} request for {o.no} was not accepted: {note}', None, 'Order')
        return r
    inv = db.session().query(Invoice).filter_by(no=o.invoice_no).with_for_update().one()
    if r.type == 'Cancellation':
        if o.status not in ('Payment Confirmed', 'Picking', 'Quality Check'):
            raise conflict('The order was already packed; it can only be returned after collection.', 'too_late_to_cancel')
        for ln in o.lines:
            stock.reserve(o.warehouse_branch, ln.item_id, -ln.qty)
        o.status = 'Cancelled'
    else:
        for x in r.line_items:
            ln = next(l for l in o.lines if l.line_no == x['line_no'])
            ln.returned_qty += x['qty']
        o.status = 'Returned' if all(ln.returned_qty >= ln.qty for ln in o.lines) else 'Partly returned'
    cn = sales.execute_return(inv, {'lines': r.line_items, 'refund_mode': 'online', 'reason': f'{r.type} {r.no}: {r.reason}',
                                    'condition': d.get('condition') or 'Good', 'kind': 'Cancellation' if r.type == 'Cancellation' else 'Return', '_online': True})
    r.status, r.decided_by, r.decision_note, r.credit_note_no = 'Approved', g.user['id'], note or None, cn.no
    f = Refund(no=next_no(o.company, 'RFD'), order_id=o.id, request_no=r.no, credit_note_no=cn.no, amount=cn.amount, original_payment=o.payment_id, status='Pending')
    db.add(f)
    _event(o, f'{r.type} approved; credit note {cn.no}; refund {f.no} of Rs {cn.amount} pending with Finance')
    return r


def process_refund(fid):
    f = db.session().get(Refund, fid, with_for_update=True)
    if not f:
        raise not_found('Refund not found.', 'unknown_refund')
    need_step('finance')
    if f.status != 'Pending':
        raise conflict(f'This refund is {f.status}.', 'wrong_status')
    o = _order(f.order_id)
    res = razorpay.refund(o.company, f.original_payment, f.amount, {'refund': f.no, 'order': o.no})
    f.gateway_refund_id = res.get('id')
    if res.get('status') == 'processed':
        f.status, f.completed_at = 'Refunded', dt.datetime.now()
    db.run('UPDATE rzp_payments SET refunded = LEAST(amount, refunded + :a), status = IF(refunded + :a >= amount, "Refunded", "Partially refunded") WHERE id=:i',
           a=f.amount, i=f.original_payment)
    _event(o, f'Refund {f.no} sent to the gateway ({f.gateway_refund_id})')
    notify.queue('sms', o.mobile, None, f'Kiswa-Zaibus: refund of Rs {f.amount} for {o.no} has been initiated. It reaches your account in 5-7 working days.', None, 'Order')
    audit.write('Online refund processed', f.no, new=f'Rs {f.amount} via {f.gateway_refund_id}', category='online', company=o.company)
    return f
