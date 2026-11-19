"""Cash custody: handovers to the Outdoor Clerk, clerk balances, owner instructions."""
from flask import Blueprint, g, jsonify

from .. import db
from ..core.rbac import body, require, scope_sql
from ..services import cash as svc

bp = Blueprint('cash', __name__, url_prefix='/api/cash')


@bp.get('/clerks')
@require('view', 'cash')
def clerks():
    rows = db.clean(db.q("SELECT id, name, mobile FROM users WHERE main_role='Outdoor Clerk' AND status='Active' ORDER BY name"))
    for r in rows:
        r['holding'] = {k: float(v) for k, v in svc.clerk_balance(r['id']).items()}
    return jsonify(rows)


@bp.get('/handovers')
@require('view', 'cash')
def handovers():
    where, p = scope_sql(g.user, 'h.branch_id')
    return jsonify(db.clean(db.q(f'''SELECT h.*, u.name AS clerk_name, f.name AS from_name FROM cash_handovers h
                                     JOIN users u ON u.id=h.clerk_id JOIN users f ON f.id=h.from_user
                                     WHERE ({where} OR h.clerk_id=:me) ORDER BY h.id DESC LIMIT 300''', me=g.user['id'], **p)))


@bp.post('/handovers')
@require('add', 'cash')
def create_handover():
    return jsonify(svc.create_handover(body()).as_dict()), 201


@bp.post('/handovers/<int:hid>/accept')
@require('edit', 'cash')
def accept(hid):
    return jsonify(svc.accept_handover(hid, body()).as_dict())


@bp.post('/handovers/<int:hid>/reject')
@require('edit', 'cash')
def reject(hid):
    return jsonify(svc.reject_handover(hid, body().get('reason')).as_dict())


@bp.get('/instructions')
@require('view', 'cash')
def instructions():
    return jsonify(db.clean(db.q('''SELECT i.*, u.name AS clerk_name FROM owner_instructions i JOIN users u ON u.id=i.clerk_id
                                    WHERE i.company IN :c ORDER BY i.id DESC LIMIT 300''', c=tuple(g.user['companies']) or ('',))))


@bp.post('/instructions')
@require('add', 'cash')
def create_instruction():
    i, a = svc.create_instruction(body())
    return jsonify({'no': i.no, 'status': i.status, 'approval': a}), 201


@bp.post('/instructions/<int:iid>/execute')
@require('edit', 'cash')
def execute(iid):
    return jsonify(svc.execute_instruction(iid, body()).as_dict())


@bp.post('/instructions/<int:iid>/verify')
@require('approve', 'cash')
def verify(iid):
    return jsonify(svc.verify_instruction(iid, body()).as_dict())
