"""Point of sale: quote a cart, post a bill, held bills, invoices, pending delivery, returns / cancellations, reprint, shifts."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core.rbac import body, can, in_scope, login_required, require, scope_sql
from ..errors import denied, not_found
from ..models import CashShift, CreditNote, Invoice
from ..services import cash as cash_svc
from ..services import sales

bp = Blueprint('pos', __name__, url_prefix='/api/pos')


def invoice_json(inv, with_cost=False):
    out = inv.as_dict()
    out['lines'] = [ln.as_dict(*(() if with_cost else ('unit_cost',))) for ln in inv.lines]
    out['payments'] = [p.as_dict() for p in inv.payments]
    out['credit_notes'] = db.clean(db.q('SELECT no, cn_date, kind, amount, refund_mode, reason FROM credit_notes WHERE invoice_id=:i ORDER BY id', i=inv.id))
    out['company_info'] = db.clean(db.one('SELECT name, gstin, address, phone, email FROM companies WHERE code=:c', c=inv.company))
    out['branch_name'] = db.val('SELECT l.name FROM branches b JOIN locations l ON l.code=b.location WHERE b.id=:b', b=inv.branch_id)
    out['cashier_name'] = db.val('SELECT name FROM users WHERE id=:i', i=inv.cashier_id)
    return out


@bp.post('/quote')
@require('view', 'pos')
def quote():
    d = body()
    lines, tot = sales.quote(d.get('company'), d.get('lines') or [], schemes=not d.get('sponsored'))
    return jsonify({'lines': db.clean(lines), 'totals': db.clean(tot)})


@bp.post('/invoices')
@require('add', 'pos')
def create_invoice():
    inv, held = sales.post_invoice(body())
    if held:
        return jsonify(held), 202
    return jsonify(invoice_json(inv)), 201


@bp.get('/invoices')
@require('view', 'pos')
def invoices():
    a = request.args
    where, p = scope_sql(g.user, 'branch_id')
    sql = f'SELECT id, no, company, branch_id, inv_date, inv_time, adm_no, customer_id, total, balance, status, channel FROM invoices WHERE {where} AND company IN :c'
    p['c'] = tuple(g.user['companies']) or ('',)
    if a.get('q'):
        sql += ' AND (no LIKE :q OR adm_no LIKE :q)'
        p['q'] = f"%{a['q']}%"
    if a.get('date'):
        sql += ' AND inv_date=:d'
        p['d'] = a['date']
    if a.get('branch'):
        sql += ' AND branch_id=:b'
        p['b'] = a['branch']
    if a.get('mine') == '1':
        sql += ' AND cashier_id=:me'
        p['me'] = g.user['id']
    if a.get('pending') == '1':
        sql += ' AND id IN (SELECT invoice_id FROM invoice_lines WHERE delivered_qty < qty - returned_qty)'
    return jsonify(db.clean(db.q(sql + ' ORDER BY id DESC LIMIT 300', **p)))


def _inv(iid):
    inv = db.session().get(Invoice, iid)
    if not inv or not in_scope(g.user, inv.branch_id) or inv.company not in g.user['companies']:
        raise not_found('Invoice not found.', 'unknown_invoice')
    return inv


@bp.get('/invoices/<int:iid>')
@require('view', 'pos')
def invoice(iid):
    return jsonify(invoice_json(_inv(iid), can(g.user, 'cost', 'pos')))


@bp.get('/invoices/by-no')
@require('view', 'pos')
def invoice_by_no():
    inv = db.session().query(Invoice).filter_by(no=request.args.get('no', '')).first()
    if not inv:
        raise not_found('Invoice not found.', 'unknown_invoice')
    return invoice(inv.id)


@bp.post('/invoices/<int:iid>/deliver')
@require('edit', 'pos')
def deliver(iid):
    _inv(iid)
    return jsonify({'delivery_no': sales.deliver_pending(iid, body().get('lines'))})


@bp.post('/invoices/<int:iid>/return')
@require('cancel', 'pos')
def ret(iid):
    _inv(iid)
    cn, approval = sales.request_return(iid, body())
    if approval:
        return jsonify({'approval': approval, 'message': f"Refund needs {approval['required_role']} approval ({approval['no']})."}), 202
    return jsonify({'credit_note': cn.no, 'amount': float(cn.amount)}), 201


@bp.post('/invoices/<int:iid>/reprint')
@require('print', 'pos')
def reprint(iid):
    _inv(iid)
    return jsonify({'reprints': sales.reprint(iid)})


@bp.get('/credit-notes/<int:cid>')
@require('view', 'pos')
def credit_note(cid):
    cn = db.session().get(CreditNote, cid)
    if not cn:
        raise not_found('Credit note not found.', 'unknown_credit_note')
    out = cn.as_dict()
    out['lines'] = [ln.as_dict() for ln in cn.lines]
    return jsonify(out)


@bp.get('/held')
@require('view', 'pos')
def held():
    where, p = scope_sql(g.user, 'branch_id')
    return jsonify(db.clean(db.q(f"SELECT id, no, company, branch_id, reason, approval_no, status, invoice_no, created_at FROM held_bills WHERE {where} ORDER BY id DESC LIMIT 100", **p)))


# ---------------------------------------------------------------- shifts
@bp.get('/shift')
@login_required
def my_shift():
    sh = db.one("SELECT * FROM cash_shifts WHERE user_id=:u AND status IN ('Open','Pending approval') ORDER BY id DESC LIMIT 1", u=g.user['id'])
    if not sh:
        return jsonify(None)
    obj = db.session().get(CashShift, sh['id'])
    out = obj.as_dict()
    out['live'] = cash_svc.shift_figures(obj)
    return jsonify(out)


@bp.post('/shift/open')
@require('add', 'cash')
def open_shift():
    return jsonify(cash_svc.open_shift(body()).as_dict()), 201


@bp.post('/shift/<int:sid>/close')
@require('add', 'cash')
def close_shift(sid):
    sh, a = cash_svc.close_shift(sid, body())
    return jsonify({'shift': sh.as_dict(), 'approval': a})


@bp.get('/shifts')
@require('view', 'cash')
def shifts():
    rows = db.clean(db.q('SELECT s.*, u.name AS user_name FROM cash_shifts s JOIN users u ON u.id=s.user_id ORDER BY s.id DESC LIMIT 200'))
    if g.user['branches'] != '*':
        locs = {db.val('SELECT location FROM branches WHERE id=:b', b=b) for b in g.user['branches']}
        rows = [r for r in rows if r['location'] in locs or r['user_id'] == g.user['id']]
    return jsonify(rows)


def _cannot():
    raise denied()
