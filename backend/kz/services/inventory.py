"""Branch transfers and stock adjustments.

Transfer:  request -> (approval when value > requester's 'transfer' limit) -> Approved -> dispatch (stock leaves the
           sending branch) -> receive (accepted qty enters the receiving branch; shortage/damage written off)
           Journal at receipt: Dr 1300 Inventory (to, accepted) + Dr 5600 Shrinkage (to, short/damaged) / Cr 1300 (from, sent).
Adjustment: lines of +/- qty at moving-average cost -> posted directly when within the user's 'adj' limit, else approval.
           Loss: Dr 5600 / Cr 1300.  Gain: Dr 1300 / Cr 5600.
"""
import datetime as dt
from decimal import Decimal

from flask import g

from .. import db
from ..core import audit, notify
from ..core.approvals import handler, on_reject, request as approval_request, role_for_limit
from ..core.numbering import next_no
from ..errors import bad, conflict, denied, not_found
from ..models import StockAdjustment, StockAdjustmentLine, Transfer, TransferLine
from . import ledger, stock
from .common import branch, check_lock, check_scope, item, money, positive_int, today, whole

ADJ_TYPES = ('Damaged', 'Write-off', 'Obsolete', 'Lost / missing', 'Found excess', 'Physical count')


# ---------------------------------------------------------------- transfers
def create_transfer(d):
    u = g.user
    src, dst = branch(d.get('from_branch')), branch(d.get('to_branch'))
    if src['id'] == dst['id']:
        raise bad('Choose two different locations.', 'same_branch')
    if src['company'] != dst['company']:
        raise bad('Transfers stay within one company. Use an inter-company entry between Kiswa and Zaibus.', 'cross_company')
    if u['branches'] != '*' and src['id'] not in u['branches'] and dst['id'] not in u['branches']:
        raise denied('You can only request transfers into or out of your own locations.', 'out_of_scope')
    lines = d.get('lines') or []
    if not lines:
        raise bad('Add at least one item.', 'no_lines')
    t = Transfer(no=next_no(src['company'], 'ST'), company=src['company'], from_branch=src['id'], to_branch=dst['id'], txn_date=today(),
                 status='Pending approval', reason=str(d.get('reason') or '').strip() or None, requested_by=u['id'])
    value = Decimal(0)
    for n, ln in enumerate(lines, 1):
        it = item(ln.get('item_id'), src['company'])
        q = positive_int(ln.get('qty'), f'Quantity for {it.name}')
        if stock.available(src['id'], it.id) < q:
            raise conflict(f'{it.name}: only {stock.available(src["id"], it.id)} free at {src["name"]}.', 'negative_stock', item=it.id)
        t.lines.append(TransferLine(line_no=n, item_id=it.id, qty=q, unit_cost=it.cost))
        value += Decimal(str(it.cost)) * q
    t.value = money(value)
    db.add(t)
    role = role_for_limit(u, 'transfer', t.value, txn_type='transfer', branch=src['id'])
    if role:
        a = approval_request('transfer', f'Transfer {t.no}: {src["name"]} -> {dst["name"]} (Rs {t.value:,.2f})', {'id': t.id}, role,
                             company=t.company, branch=src['id'], amount=t.value, reason=t.reason, ref=t.no)
        audit.write('Transfer requested', t.no, new=f'Rs {t.value} at cost, needs {role}', category='inventory', company=t.company, branch=src['id'])
        return t, a
    t.status = 'Approved'
    audit.write('Transfer requested and approved (within limit)', t.no, new=f'Rs {t.value} at cost', category='inventory', company=t.company, branch=src['id'])
    return t, None


@handler('transfer')
def _approve_transfer(p, apr):
    t = db.session().get(Transfer, p['id'], with_for_update=True)
    if t.status != 'Pending approval':
        raise conflict(f'Transfer {t.no} is {t.status}.', 'wrong_status')
    t.status = 'Approved'
    return f'{t.no} approved for dispatch'


@on_reject('transfer')
def _reject_transfer(p, apr, reason):
    t = db.session().get(Transfer, p['id'], with_for_update=True)
    t.status = 'Rejected'


def _transfer(tid):
    t = db.session().get(Transfer, tid, with_for_update=True)
    if not t:
        raise not_found('Transfer not found.', 'unknown_transfer')
    return t


def dispatch_transfer(tid):
    t = _transfer(tid)
    check_scope(g.user, t.from_branch)
    if t.status != 'Approved':
        raise conflict('Only approved transfers can be dispatched.', 'wrong_status')
    branch(t.from_branch), branch(t.to_branch)
    for ln in t.lines:
        stock.move(t.company, t.from_branch, ln.item_id, -ln.qty, 'Transfer out', t.no, today(), ln.unit_cost, respect_reserved=True)
    t.status, t.dispatched_by, t.dispatched_at = 'In transit', g.user['id'], dt.datetime.now()
    audit.write('Transfer dispatched', t.no, new=f'To {t.to_branch}', category='inventory', company=t.company, branch=t.from_branch)
    return t


def receive_transfer(tid, received):
    """received: [{line_no, received_qty, damaged_qty}] (missing lines = received in full, nothing damaged)."""
    t = _transfer(tid)
    check_scope(g.user, t.to_branch)
    if t.status != 'In transit':
        raise conflict('This transfer is not in transit.', 'wrong_status')
    check_lock(t.company, today())
    by_line = {whole(r.get('line_no'), 'Line number'): r for r in (received or []) if isinstance(r, dict)}
    acc = loss = sent = Decimal(0)
    notes = []
    for ln in t.lines:
        r = by_line.get(ln.line_no, {})
        rq = whole(r.get('received_qty', ln.qty), 'Received quantity')
        dm = whole(r.get('damaged_qty'), 'Damaged quantity', 0)
        if rq < 0 or rq > ln.qty or dm < 0 or dm > rq:
            raise bad(f'Line {ln.line_no}: received must be 0-{ln.qty} and damaged at most the received quantity.', 'bad_quantity')
        ln.received_qty, ln.damaged_qty = rq, dm
        good = rq - dm
        cost = Decimal(str(ln.unit_cost))
        if good:
            stock.move(t.company, t.to_branch, ln.item_id, good, 'Transfer in', t.no, today(), cost)
        acc += good * cost
        loss += (ln.qty - good) * cost
        sent += ln.qty * cost
        if ln.qty - rq:
            notes.append(f'{ln.item_id}: {ln.qty - rq} short')
        if dm:
            notes.append(f'{ln.item_id}: {dm} damaged')
    ledger.post(t.company, today(), 'Stock transfer', t.no, f'Transfer {t.no}: {t.from_branch} -> {t.to_branch}',
                [{'account': '1300', 'branch': t.to_branch, 'debit': acc}, {'account': '5600', 'branch': t.to_branch, 'debit': loss},
                 {'account': '1300', 'branch': t.from_branch, 'credit': sent}], skip_if_empty=True)
    t.status, t.received_by, t.received_at = 'Received', g.user['id'], dt.datetime.now()
    t.receipt_note = '; '.join(notes) or 'Received in full'
    if loss > 0:
        notify.alert('Transfer shortage', f'{t.no}: {t.receipt_note} (Rs {money(loss)} at cost)', ref=t.no, company=t.company, branch=t.to_branch)
    audit.write('Transfer received', t.no, new=t.receipt_note, category='inventory', company=t.company, branch=t.to_branch)
    return t


def cancel_transfer(tid, reason):
    t = _transfer(tid)
    if t.status not in ('Pending approval', 'Approved'):
        raise conflict('Only a transfer that has not left the branch can be cancelled.', 'wrong_status')
    if not (reason or '').strip():
        raise bad('Give a reason.', 'reason_required')
    t.status = 'Cancelled'
    db.run("UPDATE approvals SET status='Cancelled', result=:r WHERE kind='transfer' AND ref=:n AND status='Pending'", r=reason, n=t.no)
    audit.write('Transfer cancelled', t.no, reason=reason, category='inventory', company=t.company)
    return t


# ---------------------------------------------------------------- adjustments
def create_adjustment(d):
    u = g.user
    b = branch(d.get('branch_id'))
    check_scope(u, b['id'])
    adj_type = d.get('adj_type')
    if adj_type not in ADJ_TYPES:
        raise bad('Choose the adjustment type.', 'bad_adj_type')
    reason = str(d.get('reason') or '').strip()
    if not reason:
        raise bad('A reason is required for every stock adjustment.', 'reason_required')
    lines = d.get('lines') or []
    if not lines:
        raise bad('Add at least one item.', 'no_lines')
    a = StockAdjustment(no=next_no(b['company'], 'ADJ'), company=b['company'], branch_id=b['id'], txn_date=today(), adj_type=adj_type,
                        reason=reason, status='Pending approval', created_by=u['id'], value=0)
    value = Decimal(0)
    for n, ln in enumerate(lines, 1):
        it = item(ln.get('item_id'), b['company'])
        on_hand, _ = stock.on_hand(b['id'], it.id)
        if adj_type == 'Physical count':
            counted = whole(ln.get('counted_qty'), f'Counted quantity for {it.name}')
            if counted < 0:
                raise bad('Counted quantity cannot be negative.', 'bad_quantity')
            delta = counted - on_hand
        else:
            delta = whole(ln.get('qty'), f'Quantity for {it.name}', 0)
            if adj_type == 'Found excess':
                delta = abs(delta)
            else:
                delta = -abs(delta)
        if not delta:
            continue
        if on_hand + delta < 0:
            raise conflict(f'{it.name}: only {on_hand} in stock; the adjustment would make stock negative.', 'negative_stock', item=it.id)
        a.lines.append(StockAdjustmentLine(line_no=n, item_id=it.id, qty=delta, unit_cost=it.cost))
        value += abs(delta) * Decimal(str(it.cost))
    if not a.lines:
        raise bad('Nothing to adjust: the counted quantities match the system.', 'nothing_to_adjust')
    a.value = money(value)
    db.add(a)
    role = role_for_limit(u, 'adj', a.value, txn_type='adj', branch=b['id'])
    if role:
        ap = approval_request('adj', f'Stock adjustment {a.no} ({adj_type}) Rs {a.value:,.2f} at {b["name"]}', {'id': a.id}, role,
                              company=a.company, branch=b['id'], amount=a.value, reason=reason, ref=a.no)
        audit.write('Stock adjustment requested', a.no, new=f'Rs {a.value} needs {role}', reason=reason, category='inventory', company=a.company, branch=b['id'])
        return a, ap
    _post_adjustment(a)
    return a, None


def _post_adjustment(a):
    check_lock(a.company, today())
    loss = gain = Decimal(0)
    for ln in a.lines:
        stock.move(a.company, a.branch_id, ln.item_id, ln.qty, f'Adjustment · {a.adj_type}', a.no, today(), ln.unit_cost)
        v = abs(ln.qty) * Decimal(str(ln.unit_cost))
        if ln.qty < 0:
            loss += v
        else:
            gain += v
    lines = []
    if loss:
        lines += [{'account': '5600', 'branch': a.branch_id, 'debit': loss}, {'account': '1300', 'branch': a.branch_id, 'credit': loss}]
    if gain:
        lines += [{'account': '1300', 'branch': a.branch_id, 'debit': gain}, {'account': '5600', 'branch': a.branch_id, 'credit': gain}]
    j = ledger.post(a.company, today(), 'Stock adjustment', a.no, f'{a.adj_type}: {a.reason}', lines, skip_if_empty=True)
    a.status, a.journal_no = 'Posted', j.no if j else None
    if a.adj_type == 'Physical count':
        notify.alert('Stock variance', f'{a.no}: physical count variance Rs {a.value} at {a.branch_id}', ref=a.no, company=a.company, branch=a.branch_id)
    audit.write('Stock adjustment posted', a.no, new=f'{len(a.lines)} line(s), Rs {a.value}', reason=a.reason, category='inventory', company=a.company, branch=a.branch_id)


@handler('adj')
def _approve_adj(p, apr):
    a = db.session().get(StockAdjustment, p['id'], with_for_update=True)
    if a.status != 'Pending approval':
        raise conflict(f'{a.no} is {a.status}.', 'wrong_status')
    _post_adjustment(a)
    return f'Adjustment {a.no} posted'


@on_reject('adj')
def _reject_adj(p, apr, reason):
    a = db.session().get(StockAdjustment, p['id'], with_for_update=True)
    a.status = 'Rejected'
