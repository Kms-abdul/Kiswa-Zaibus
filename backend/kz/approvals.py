"""Generic maker-checker engine.

request(...) creates a pending approval; decide(...) approves / rejects / returns it.
On approval the registered handler applies the change inside the same transaction, so the change
and the approval either both commit or neither does.

Rules enforced on every decision (same as the prototype):
  * nobody approves their own request
  * the approver needs 'approve' on Approvals and a rank >= the required role's rank
  * Super-Admin-only requests need a Super Admin who is not the requester
  * the approver must have the request's branch in scope (Head Office requests need all locations)
  * where the controls say so, evidence must be attached before approval
  * read-only users never approve
"""
import json

from flask import g

from . import audit, db, notify
from .errors import bad, conflict, denied, not_found
from .numbering import next_no
from .rbac import can, in_scope
from .security import setting

HANDLERS = {}


def handler(key):
    def deco(fn):
        HANDLERS[key] = fn
        return fn
    return deco


def role_rank(role):
    return float(db.val('SELECT approval_rank FROM roles WHERE name=:r', r=role) or 0)


def mc_on(sub_kind):
    """Is maker-checker switched on for this kind of change? (Access & Control Center → Maker-checker)"""
    return bool((setting('maker_checker', {}) or {}).get(sub_kind, True))


def request(kind, title, payload, required_role, *, sub_kind=None, company=None, branch=None, amount=0, detail=None, reason=None,
            super_admin_only=False, signature=None):
    u = g.user
    if signature:
        dup = db.one("SELECT no FROM approvals WHERE signature=:s AND status='Pending'", s=signature)
        if dup:
            raise conflict(f'An identical request is already pending ({dup["no"]}).', 'duplicate_request', no=dup['no'])
    no = next_no('HO', 'APR')
    aid = db.insert('approvals', dict(no=no, kind=kind, sub_kind=sub_kind, company=company, branch_id=branch, amount=amount, title=title,
                                      detail=detail, reason=reason, payload=payload, required_role=required_role,
                                      super_admin_only=1 if super_admin_only else 0, requested_by=u['id'], requested_role=u['main_role'],
                                      signature=signature))
    db.insert('approval_log', dict(approval_id=aid, user_name=u['name'], role=u['main_role'], action='Submitted', remarks=reason))
    audit.write('Approval requested', no, new=title, reason=reason, category='approval', status='Pending', company=company, branch=branch)
    notify.alert('New access request' if kind == 'mc' and sub_kind in ('user', 'user_update') else 'Approval pending',
                 f'{no}: {title} (by {u["name"]}, needs {required_role})', ref=no, company=company, branch=branch, severity='info')
    return {'id': aid, 'no': no, 'status': 'Pending'}


def why_not(u, a):
    """Reason the user cannot decide this approval, or None if they can."""
    if a['status'] != 'Pending':
        return 'This request is no longer pending.'
    if a['requested_by'] == u['id']:
        return 'You cannot approve your own request.'
    if u['read_only'] or not can(u, 'approve', 'appr'):
        return 'Your role cannot approve requests.'
    if a['super_admin_only'] and 'Super Admin' not in u['roles']:
        return 'Only a Super Admin can approve this request.'
    if u['rank'] < role_rank(a['required_role']):
        return f'This request needs {a["required_role"]} or higher.'
    if not in_scope(u, a['branch_id']):
        return 'This request is outside your locations.'
    if a['company'] and a['company'] not in u['companies']:
        return 'This request is for a company you do not have.'
    return None


def decide(approval_id, action, remarks):
    a = db.one('SELECT * FROM approvals WHERE id=:i FOR UPDATE', i=approval_id)
    if not a:
        raise not_found('Approval not found.')
    u = g.user
    reason = why_not(u, a)
    if reason:
        raise denied(reason, 'cannot_decide')
    if action not in ('approve', 'reject', 'return'):
        raise bad('Action must be approve, reject or return.')
    if action in ('reject', 'return') and not (remarks or '').strip():
        raise bad('Please give a reason.', 'reason_required')
    payload = a['payload']
    payload = json.loads(payload) if isinstance(payload, str) else payload
    result = None
    if action == 'approve':
        controls = setting('controls', {}) or {}
        if a['kind'] in (controls.get('evBeforeApprove') or []):
            n = db.val("SELECT COUNT(*) FROM evidence WHERE ref=:r AND status='Active'", r=payload.get('ref') or a['no'])
            if not n:
                raise conflict('Attach evidence before approving this request.', 'evidence_required')
        key = f"{a['kind']}:{a['sub_kind']}" if a['sub_kind'] else a['kind']
        fn = HANDLERS.get(key)
        if not fn:
            raise conflict(f'No handler is registered for {key}.', 'no_handler')
        result = fn(payload, a) or 'Applied'
        status = 'Approved'
    else:
        status = 'Rejected' if action == 'reject' else 'Returned'
    db.run('UPDATE approvals SET status=:s, decided_by=:u, decided_at=NOW(6), result=:r WHERE id=:i', s=status, u=u['id'], r=result or remarks, i=a['id'])
    db.insert('approval_log', dict(approval_id=a['id'], user_name=u['name'], role=u['main_role'], action=status, remarks=remarks))
    audit.write(f'Approval {status.lower()}', a['no'], old='Pending', new=result or status, reason=remarks, category='approval', status=status,
                company=a['company'], branch=a['branch_id'])
    notify.alert('Access approved or rejected' if a['kind'] == 'mc' else 'Approval decided', f"{a['no']} {status.lower()} by {u['name']}: {a['title']}",
                 ref=a['no'], company=a['company'], branch=a['branch_id'], severity='info')
    return {'no': a['no'], 'status': status, 'result': result}
