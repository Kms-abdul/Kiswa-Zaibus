"""Accounts: receipts, expenses, contra, journal vouchers, reversals, inter-company, vouchers list."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core.rbac import body, require
from ..errors import not_found
from ..services import accounts as svc

bp = Blueprint('accounts', __name__, url_prefix='/api/accounts')


@bp.post('/receipts')
@require('add', 'acc')
def receipt():
    r = svc.post_receipt(body())
    return jsonify(r.as_dict()), 201


@bp.get('/receipts')
@require('view', 'acc')
def receipts():
    return jsonify(db.clean(db.q('''SELECT r.*, c.name AS customer_name FROM receipts r LEFT JOIN customers c ON c.id=r.customer_id
                                    WHERE r.company IN :c ORDER BY r.id DESC LIMIT 300''', c=tuple(g.user['companies']) or ('',))))


@bp.post('/expenses')
@require('add', 'acc')
def expense():
    j, a = svc.create_expense(body())
    if a:
        return jsonify({'approval': a, 'message': f"Expense needs {a['required_role']} approval ({a['no']})."}), 202
    return jsonify({'journal_no': j.no}), 201


@bp.post('/contra')
@require('add', 'acc')
def contra():
    return jsonify({'journal_no': svc.contra(body()).no}), 201


@bp.post('/journals')
@require('add', 'acc')
def journal():
    j, a = svc.create_journal(body())
    if a:
        return jsonify({'approval': a, 'message': f"Journal needs {a['required_role']} approval ({a['no']})."}), 202
    return jsonify({'journal_no': j.no}), 201


@bp.post('/reverse')
@require('reverse', 'acc')
def reverse():
    return jsonify({'reversal_no': svc.reverse(body()).no})


@bp.post('/intercompany')
@require('add', 'acc')
def intercompany():
    ic, a = svc.create_intercompany(body())
    return jsonify({'no': ic.no, 'status': ic.status, 'approval': a}), 201


@bp.get('/vouchers')
@require('view', 'acc')
def vouchers():
    a = request.args
    sql = 'SELECT id, no, company, txn_date, type, ref, narration, created_by, reverses_no, reversed_by_no FROM journals WHERE company IN :c'
    p = {'c': tuple([a['company']] if a.get('company') in g.user['companies'] else g.user['companies']) or ('',)}
    for k, col in (('type', 'type'), ('ref', 'ref')):
        if a.get(k):
            sql += f' AND {col}=:{k}'
            p[k] = a[k]
    if a.get('from'):
        sql += ' AND txn_date >= :f'
        p['f'] = a['from']
    if a.get('to'):
        sql += ' AND txn_date <= :t'
        p['t'] = a['to']
    if a.get('q'):
        sql += ' AND (no LIKE :q OR ref LIKE :q OR narration LIKE :q)'
        p['q'] = f"%{a['q']}%"
    return jsonify(db.clean(db.q(sql + ' ORDER BY id DESC LIMIT 500', **p)))


@bp.get('/vouchers/<int:jid>')
@require('view', 'acc')
def voucher(jid):
    j = db.clean(db.one('SELECT * FROM journals WHERE id=:i', i=jid))
    if not j or j['company'] not in g.user['companies']:
        raise not_found('Voucher not found.', 'unknown_voucher')
    j['lines'] = db.clean(db.q('''SELECT l.*, a.name AS account_name FROM journal_lines l LEFT JOIN accounts a ON a.code=l.account AND a.company=:c
                                  WHERE l.journal_id=:i ORDER BY l.line_no''', i=jid, c=j['company']))
    return jsonify(j)
