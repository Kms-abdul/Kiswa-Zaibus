"""Approvals inbox: list, detail, approve / reject / return, resubmit, withdraw."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core import approvals
from ..core.rbac import body, high_risk, require
from ..errors import not_found

bp = Blueprint('approvals', __name__, url_prefix='/api/approvals')


@bp.get('')
@require('view', 'appr')
def inbox():
    st = request.args.get('status', 'Pending')
    rows = db.clean(db.q('''SELECT a.*, u.name AS requested_by_name FROM approvals a LEFT JOIN users u ON u.id=a.requested_by
                            WHERE (:s='All' OR a.status=:s) ORDER BY a.id DESC LIMIT 300''', s=st))
    u = g.user
    out = []
    for a in rows:
        mine = a['requested_by'] == u['id']
        if a['branch_id'] and u['branches'] != '*' and a['branch_id'] not in u['branches'] and not mine:
            continue
        a['blocked_reason'] = approvals.why_not(u, a)
        a['can_decide'] = a['blocked_reason'] is None
        a['mine'] = mine
        out.append(a)
    if request.args.get('mine') == '1':
        out = [a for a in out if a['can_decide']]
    return jsonify(out)


@bp.get('/<int:aid>')
@require('view', 'appr')
def detail(aid):
    a = db.one('SELECT a.*, u.name AS requested_by_name FROM approvals a LEFT JOIN users u ON u.id=a.requested_by WHERE a.id=:i', i=aid)
    if not a:
        raise not_found('Approval not found.')
    a = db.clean(a)
    a['log'] = db.clean(db.q('SELECT at, user_name, role, action, remarks FROM approval_log WHERE approval_id=:i ORDER BY id', i=aid))
    a['evidence'] = db.clean(db.q("SELECT id, kind, file_name, uploaded_at FROM evidence WHERE ref IN (:r1,:r2) AND status='Active'", r1=a['no'], r2=a['ref'] or a['no']))
    a['blocked_reason'] = approvals.why_not(g.user, a)
    a['can_decide'] = a['blocked_reason'] is None
    a['mine'] = a['requested_by'] == g.user['id']
    return jsonify(a)


@bp.post('/<int:aid>/decide')
@require('approve', 'appr')
@high_risk('approve')
def decide(aid):
    b = body()
    out = approvals.decide(aid, b.get('action'), b.get('remarks'))
    if g.get('dev_temp_password'):
        out['dev_temp_password'] = g.dev_temp_password
    return jsonify(out)


@bp.post('/<int:aid>/resubmit')
@require('view', 'appr')
def resubmit(aid):
    return jsonify(approvals.resubmit(aid, body().get('remarks')))


@bp.post('/<int:aid>/cancel')
@require('view', 'appr')
def cancel(aid):
    return jsonify(approvals.cancel(aid, body().get('reason')))
