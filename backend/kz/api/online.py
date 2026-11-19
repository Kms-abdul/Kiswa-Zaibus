"""Staff side of the online store: fulfilment queue, steps, collection, requests, refunds."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core.rbac import body, require
from ..errors import not_found
from ..models import OnlineOrder
from ..services import online as svc

bp = Blueprint('online', __name__, url_prefix='/api/online')


def order_json(o):
    out = o.as_dict('collect_otp_hash')
    out['student_name'] = db.val('SELECT name FROM students WHERE adm_no=:a', a=o.adm_no)
    out['collect_point'] = db.val('SELECT COALESCE(public_name, name) FROM locations WHERE code=:c', c=o.collect_at)
    out['lines'] = [dict(ln.as_dict(), name=db.val('SELECT name FROM items WHERE id=:i', i=ln.item_id),
                         size=db.val('SELECT size FROM items WHERE id=:i', i=ln.item_id),
                         bin=db.val('SELECT bin_location FROM items WHERE id=:i', i=ln.item_id)) for ln in o.lines]
    out['requests'] = db.clean(db.q('SELECT * FROM order_requests WHERE order_id=:o ORDER BY id', o=o.id))
    out['refunds'] = db.clean(db.q('SELECT * FROM refunds WHERE order_id=:o ORDER BY id', o=o.id))
    if g.get('dev_collect_code'):
        out['dev_collect_code'] = g.dev_collect_code
    return out


@bp.get('/orders')
@require('view', 'onl')
def orders():
    sql = '''SELECT o.id, o.no, o.company, o.adm_no, s.name AS student_name, o.collect_at, o.total, o.status, o.placed_at, o.invoice_no
             FROM online_orders o LEFT JOIN students s ON s.adm_no=o.adm_no WHERE o.company IN :c'''
    p = {'c': tuple(g.user['companies']) or ('',)}
    if request.args.get('status'):
        sql += ' AND o.status IN :st'
        p['st'] = tuple(request.args['status'].split(','))
    if request.args.get('q'):
        sql += ' AND (o.no LIKE :q OR o.adm_no LIKE :q OR s.name LIKE :q)'
        p['q'] = f"%{request.args['q']}%"
    if g.user['branches'] != '*' and request.args.get('mine') == '1':
        locs = tuple({b.split('-', 1)[1] for b in g.user['branches']}) or ('',)
        sql += ' AND o.collect_at IN :l'
        p['l'] = locs
    return jsonify(db.clean(db.q(sql + ' ORDER BY o.id DESC LIMIT 300', **p)))


@bp.get('/orders/<int:oid>')
@require('view', 'onl')
def order(oid):
    o = db.session().get(OnlineOrder, oid)
    if not o:
        raise not_found('Order not found.', 'unknown_order')
    return jsonify(order_json(o))


@bp.post('/orders/<int:oid>/step/<step>')
@require('edit', 'onl')
def step(oid, step):
    return jsonify(order_json(svc.advance(oid, step, body())))


@bp.post('/orders/<int:oid>/collect')
@require('edit', 'onl')
def collect(oid):
    return jsonify(order_json(svc.collect(oid, body().get('code'))))


@bp.get('/requests')
@require('view', 'onl')
def requests_():
    return jsonify(db.clean(db.q('''SELECT r.*, o.no AS order_no, o.company, o.status AS order_status FROM order_requests r JOIN online_orders o ON o.id=r.order_id
                                    WHERE o.company IN :c ORDER BY r.id DESC LIMIT 300''', c=tuple(g.user['companies']) or ('',))))


@bp.post('/requests/<int:rid>/decide')
@require('edit', 'onl')       # who may decide is checked by role in services/online.need_step('manage')
def decide(rid):
    r = svc.decide_request(rid, body())
    return jsonify({'no': r.no, 'status': r.status, 'credit_note_no': r.credit_note_no})


@bp.get('/refunds')
@require('view', 'onl')
def refunds():
    return jsonify(db.clean(db.q('''SELECT f.*, o.no AS order_no, o.company FROM refunds f JOIN online_orders o ON o.id=f.order_id
                                    WHERE o.company IN :c ORDER BY f.id DESC LIMIT 300''', c=tuple(g.user['companies']) or ('',))))


@bp.post('/refunds/<int:fid>/process')
@require('fin', 'onl')
def process_refund(fid):
    return jsonify(svc.process_refund(fid).as_dict())
