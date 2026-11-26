"""MSEA sponsorship vouchers."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core.rbac import body, require
from ..errors import not_found
from ..models import MseaVoucher
from ..services import msea as svc

bp = Blueprint('msea', __name__, url_prefix='/api/msea')


@bp.get('/vouchers')
@require('view', 'msea')
def vouchers():
    sql = '''SELECT v.*, s.name AS student_name, s.class_name, s.category FROM msea_vouchers v LEFT JOIN students s ON s.adm_no=v.adm_no
             WHERE v.company IN :c'''
    p = {'c': tuple(g.user['companies']) or ('',)}
    if request.args.get('status'):
        sql += ' AND v.status=:s'
        p['s'] = request.args['status']
    if request.args.get('adm_no'):
        sql += ' AND v.adm_no=:a'
        p['a'] = request.args['adm_no']
    return jsonify(db.clean(db.q(sql + ' ORDER BY v.created_at DESC LIMIT 300', **p)))


@bp.get('/vouchers/<path:no>')
@require('view', 'msea')
def voucher(no):
    v = db.session().get(MseaVoucher, no)
    if not v:
        raise not_found('Voucher not found.', 'unknown_voucher')
    out = v.as_dict()
    out['student'] = db.clean(db.one('SELECT * FROM students WHERE adm_no=:a', a=v.adm_no))
    out['issues'] = db.clean(db.q('SELECT id, no, inv_date, total, status FROM invoices WHERE msea_voucher_no=:v ORDER BY id', v=no))
    return jsonify(out)


@bp.post('/vouchers')
@require('add', 'msea')
def create():
    v, a = svc.create_voucher(body())
    return jsonify({'no': v.no, 'status': v.status, 'approval': a}), 201


@bp.post('/vouchers/<path:no>/cancel')
@require('cancel', 'msea')
def cancel(no):
    v = svc.cancel_voucher(no, body().get('reason'))
    return jsonify({'no': v.no, 'status': v.status})


@bp.get('/billing')
@require('view', 'msea')
def billing():
    """What MSEA owes each company for sponsored issues (receivable 1210), with the invoices behind it."""
    out = []
    for co in g.user['companies']:
        bal = db.val('''SELECT COALESCE(SUM(l.debit-l.credit),0) FROM journal_lines l JOIN journals j ON j.id=l.journal_id
                        WHERE j.company=:c AND l.account='1210' ''', c=co)
        invs = db.clean(db.q('''SELECT no, inv_date, adm_no, total, balance, msea_voucher_no FROM invoices WHERE company=:c AND customer_id=:m
                                AND balance > 0 ORDER BY inv_date''', c=co, m=f'{co}-MSEA'))
        out.append({'company': co, 'outstanding': float(bal or 0), 'invoices': invs})
    return jsonify(out)
