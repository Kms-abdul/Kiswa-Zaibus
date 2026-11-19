"""Purchasing: requisitions, quotes, purchase orders, goods receipts, supplier bills, payments, returns."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core.rbac import body, require, scope_sql
from ..services import purchasing as svc

bp = Blueprint('purchasing', __name__, url_prefix='/api/purchasing')


def po_json(po):
    out = po.as_dict()
    out['supplier_name'] = db.val('SELECT name FROM suppliers WHERE id=:s', s=po.supplier_id) if po.supplier_id else None
    out['lines'] = [dict(ln.as_dict(), name=db.val('SELECT name FROM items WHERE id=:i', i=ln.item_id)) for ln in po.lines]
    out['quotes'] = [dict(q.as_dict(), supplier_name=db.val('SELECT name FROM suppliers WHERE id=:s', s=q.supplier_id)) for q in po.quotes]
    out['grns'] = db.clean(db.q('SELECT no, txn_date, value, line_items FROM goods_receipts WHERE po_id=:p ORDER BY id', p=po.id))
    out['bill'] = db.clean(db.one('SELECT * FROM supplier_bills WHERE po_id=:p', p=po.id))
    out['debit_notes'] = db.clean(db.q('SELECT no, qty, amount, reason, created_at FROM debit_notes WHERE po_id=:p ORDER BY id', p=po.id))
    return out


@bp.get('/orders')
@require('view', 'pur')
def orders():
    where, p = scope_sql(g.user, 'o.branch_id')
    sql = f'''SELECT o.*, s.name AS supplier_name FROM purchase_orders o LEFT JOIN suppliers s ON s.id=o.supplier_id
              WHERE {where} AND o.company IN :c'''
    p['c'] = tuple(g.user['companies']) or ('',)
    if request.args.get('status'):
        sql += ' AND o.status=:s'
        p['s'] = request.args['status']
    return jsonify(db.clean(db.q(sql + ' ORDER BY o.id DESC LIMIT 300', **p)))


@bp.get('/orders/<int:pid>')
@require('view', 'pur')
def order(pid):
    return jsonify(po_json(svc._po(pid, lock=False)))


@bp.post('/orders')
@require('add', 'pur')
def create():
    return jsonify(po_json(svc.create_requisition(body()))), 201


@bp.post('/orders/<int:pid>/quotes')
@require('edit', 'pur')
def quote(pid):
    return jsonify(po_json(svc.add_quote(pid, body())))


@bp.post('/orders/<int:pid>/submit')
@require('submit', 'pur')
def submit(pid):
    po, a = svc.submit(pid, body())
    return jsonify(dict(po_json(po), approval=a))


@bp.post('/orders/<int:pid>/cancel')
@require('cancel', 'pur')
def cancel(pid):
    return jsonify(po_json(svc.cancel_po(pid, body().get('reason'))))


@bp.post('/orders/<int:pid>/receive')
@require('edit', 'pur')
def receive(pid):
    grn = svc.receive(pid, body())
    return jsonify({'grn': grn.no, 'value': float(grn.value)}), 201


@bp.post('/orders/<int:pid>/bill')
@require('add', 'acc')
def bill(pid):
    b = svc.book_bill(pid, body())
    return jsonify(b.as_dict()), 201


@bp.post('/orders/<int:pid>/return')
@require('edit', 'pur')
def ret(pid):
    dn = svc.purchase_return(pid, body())
    return jsonify(dn.as_dict()), 201


@bp.get('/bills')
@require('view', 'pur')
def bills():
    return jsonify(db.clean(db.q('''SELECT b.*, s.name AS supplier_name, b.amount - b.paid - b.adjusted AS outstanding FROM supplier_bills b
                                    JOIN suppliers s ON s.id=b.supplier_id WHERE b.company IN :c ORDER BY b.bill_date DESC LIMIT 300''',
                                 c=tuple(g.user['companies']) or ('',))))


@bp.post('/payments')
@require('add', 'acc')
def pay():
    sp, a = svc.pay_supplier(body())
    if a:
        return jsonify({'approval': a, 'message': f"Payment needs {a['required_role']} approval ({a['no']})."}), 202
    return jsonify(sp.as_dict()), 201


@bp.get('/payments')
@require('view', 'pur')
def payments():
    return jsonify(db.clean(db.q('''SELECT p.*, s.name AS supplier_name FROM supplier_payments p JOIN suppliers s ON s.id=p.supplier_id
                                    WHERE p.company IN :c ORDER BY p.id DESC LIMIT 300''', c=tuple(g.user['companies']) or ('',))))
