"""Inventory: stock enquiry, stock ledger, low stock, transfers, adjustments."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core.rbac import body, can, in_scope, require, scope_sql
from ..errors import denied, not_found
from ..models import StockAdjustment, Transfer
from ..services import inventory as svc

bp = Blueprint('inventory', __name__, url_prefix='/api/inventory')


def _cos():
    return tuple(g.user['companies']) or ('',)


@bp.get('/stock')
@require('view', 'inv')
def stock_list():
    u = g.user
    br = request.args.get('branch')
    if br and not in_scope(u, br):
        raise denied('This location is outside your access.', 'out_of_scope')
    where, p = scope_sql(u, 's.branch_id')
    cost = ', i.cost, ROUND(s.qty*i.cost,2) AS value' if can(u, 'cost', 'inv') else ''
    sql = f'''SELECT s.branch_id, s.item_id, i.name, i.category, i.size, i.class_name, s.qty, s.reserved, s.qty - s.reserved AS free,
                     i.reorder_level, i.price{cost}
              FROM stock_balance s JOIN items i ON i.id=s.item_id WHERE {where} AND i.company IN :c'''
    p['c'] = _cos()
    if br:
        sql += ' AND s.branch_id=:b'
        p['b'] = br
    qs = (request.args.get('q') or '').strip()
    if qs:
        sql += ' AND (i.name LIKE :q OR i.id LIKE :q OR i.barcode=:e OR i.isbn13=:e)'
        p['q'], p['e'] = f'%{qs}%', qs
    if request.args.get('low') == '1':
        sql += ' AND i.reorder_level > 0 AND s.qty <= i.reorder_level'
    if request.args.get('nonzero', '1') == '1':
        sql += ' AND (s.qty <> 0 OR s.reserved <> 0)'
    return jsonify(db.clean(db.q(sql + ' ORDER BY s.branch_id, i.category, i.name LIMIT 2000', **p)))


@bp.get('/ledger')
@require('view', 'inv')
def stock_ledger():
    br, iid = request.args.get('branch'), request.args.get('item')
    if not br or not iid:
        return jsonify([])
    if not in_scope(g.user, br):
        raise denied('This location is outside your access.', 'out_of_scope')
    rows = db.q('''SELECT txn_date, txn_type, ref, qty, unit_cost, created_at FROM stock_ledger WHERE branch_id=:b AND item_id=:i ORDER BY id''', b=br, i=iid)
    bal = 0
    for r in rows:
        bal += r['qty']
        r['balance'] = bal
    return jsonify(db.clean(rows[-500:]))


@bp.get('/transfers')
@require('view', 'inv')
def transfers():
    u = g.user
    rows = db.clean(db.q('SELECT * FROM transfers WHERE company IN :c ORDER BY id DESC LIMIT 300', c=_cos()))
    if u['branches'] != '*':
        rows = [t for t in rows if t['from_branch'] in u['branches'] or t['to_branch'] in u['branches']]
    st = request.args.get('status')
    return jsonify([t for t in rows if not st or t['status'] == st])


@bp.get('/transfers/<int:tid>')
@require('view', 'inv')
def transfer(tid):
    t = db.session().get(Transfer, tid)
    if not t:
        raise not_found('Transfer not found.', 'unknown_transfer')
    out = t.as_dict()
    out['lines'] = [dict(ln.as_dict(), name=db.val('SELECT name FROM items WHERE id=:i', i=ln.item_id)) for ln in t.lines]
    return jsonify(out)


@bp.post('/transfers')
@require('add', 'inv')
def create_transfer():
    t, a = svc.create_transfer(body())
    return jsonify({'id': t.id, 'no': t.no, 'status': t.status, 'value': float(t.value), 'approval': a}), 201


@bp.post('/transfers/<int:tid>/dispatch')
@require('edit', 'inv')
def dispatch(tid):
    t = svc.dispatch_transfer(tid)
    return jsonify({'no': t.no, 'status': t.status})


@bp.post('/transfers/<int:tid>/receive')
@require('edit', 'inv')
def receive(tid):
    t = svc.receive_transfer(tid, body().get('lines'))
    return jsonify({'no': t.no, 'status': t.status, 'note': t.receipt_note})


@bp.post('/transfers/<int:tid>/cancel')
@require('cancel', 'inv')
def cancel(tid):
    t = svc.cancel_transfer(tid, body().get('reason'))
    return jsonify({'no': t.no, 'status': t.status})


@bp.get('/adjustments')
@require('view', 'inv')
def adjustments():
    where, p = scope_sql(g.user, 'branch_id')
    return jsonify(db.clean(db.q(f'SELECT * FROM stock_adjustments WHERE {where} AND company IN :c ORDER BY id DESC LIMIT 300', c=_cos(), **p)))


@bp.get('/adjustments/<int:aid>')
@require('view', 'inv')
def adjustment(aid):
    a = db.session().get(StockAdjustment, aid)
    if not a or not in_scope(g.user, a.branch_id):
        raise not_found('Adjustment not found.', 'unknown_adjustment')
    out = a.as_dict()
    out['lines'] = [dict(ln.as_dict(), name=db.val('SELECT name FROM items WHERE id=:i', i=ln.item_id)) for ln in a.lines]
    return jsonify(out)


@bp.post('/adjustments')
@require('add', 'inv')
def create_adjustment():
    a, ap = svc.create_adjustment(body())
    return jsonify({'id': a.id, 'no': a.no, 'status': a.status, 'value': float(a.value), 'approval': ap}), 201
