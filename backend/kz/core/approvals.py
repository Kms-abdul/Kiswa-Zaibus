"""Generic maker-checker engine.

    request(kind, title, payload, required_role, ...)   create a pending approval
    decide(approval_id, 'approve' | 'reject' | 'return', remarks)
    resubmit(approval_id, remarks)                       maker sends a returned request again
    cancel(approval_id, reason)                          maker withdraws a pending request
    role_for_limit(user, limit_key, measure, default)    None when the user's own limit covers it, else the approver role

On approval the registered handler (@handler('kind') or @handler('kind:sub_kind')) applies the change inside the
same transaction, so the change and the approval commit together or not at all. @on_reject handlers undo
"pending" states on documents (e.g. a transfer goes to Rejected).

Rules enforced on every decision:
  * nobody decides their own request
  * the approver needs 'approve' on Approvals and a rank >= the required role's rank
  * Super-Admin-only requests need a Super Admin who is not the requester
  * the approver must have the request's branch in scope (Head Office requests need all locations)
  * where the controls say so, evidence must be attached before approval
  * read-only users never approve
"""
import json

from flask import g

from .. import db
from ..errors import bad, conflict, denied, not_found
from . import audit, notify
from .numbering import next_no
from .rbac import can, in_scope
from .routing import route_role
from .security import setting

HANDLERS = {}
REJECT_HANDLERS = {}
RETURN_HANDLERS = {}
APPROVER_LADDER = ['Store In-Charge', 'Branch Manager', 'Accounts Manager', 'CFO', 'Director']


def handler(key):
    def deco(fn):
        HANDLERS[key] = fn
        return fn
    return deco


def on_reject(key):
    def deco(fn):
        REJECT_HANDLERS[key] = fn
        return fn
    return deco


def on_return(key):
    """Optional handler run when an approver returns a request for correction (the requester's record goes back a step)."""
    def deco(fn):
        RETURN_HANDLERS[key] = fn
        return fn
    return deco


def role_rank(role):
    return float(db.val('SELECT approval_rank FROM roles WHERE name=:r', r=role) or 0)


def user_limit(user, key):
    if 'Super Admin' in user['roles']:       # 2.7: the highest limit of any role
        return float(db.val('SELECT MAX(amount) FROM approval_limits WHERE limit_key=:k', k=key) or 0)
    v = db.val('SELECT MAX(amount) FROM approval_limits WHERE role IN :r AND limit_key=:k', r=tuple(user['roles']), k=key)
    return float(v or 0)


def role_for_limit(user, key, measure, default='Director', txn_type=None, branch=None):
    """Who must approve `measure` (amount or %) for limit `key`? None = the user may do it themselves."""
    if user_limit(user, key) >= float(measure):
        return None
    routed = route_role(txn_type or key, branch, measure, None)
    if routed:
        return routed
    for r in APPROVER_LADDER:
        lim = db.val('SELECT amount FROM approval_limits WHERE role=:r AND limit_key=:k', r=r, k=key)
        if lim is not None and float(lim) >= float(measure):
            return r
    return default


def mc_on(sub_kind):
    """Is maker-checker switched on for this kind of master change? (Settings -> Maker-checker)"""
    return bool((setting('maker_checker', {}) or {}).get(sub_kind, True))


def second_approver_exists(required_role, super_admin_only=False):
    """Is there any active user, other than the one asking, who could approve a request routed to `required_role`?
    Uses the same rules as deciding (why_not): approve on Approvals, rank, read-only, all-location scope for Head Office requests."""
    from .rbac import load_user
    me = g.user['id'] if g.get('user') else None
    rank = role_rank(required_role)
    ids = db.q('''SELECT DISTINCT u.id FROM users u
                  LEFT JOIN user_roles ur ON ur.user_id=u.id
                  JOIN roles r ON r.active=1 AND (r.name=u.main_role OR r.name=ur.role)
                  WHERE u.status='Active' AND (:me IS NULL OR u.id<>:me) AND (r.approval_rank>=:rank OR r.name='Super Admin')''', me=me, rank=rank)
    probe = {'status': 'Pending', 'requested_by': me, 'super_admin_only': 1 if super_admin_only else 0, 'required_role': required_role,
             'branch_id': None, 'company': None}
    return any((u := load_user(r['id'])) and why_not(u, probe) is None for r in ids)


def needs_second_person(sub_kind, required_role='Director', super_admin_only=False):
    """Maker-checker for a master change (roles, users, settings, prices). It applies only when it can work: if it is switched on
    but nobody else can approve yet (e.g. a fresh install with only the Super Admin), the change applies at once and the audit
    trail records why. As soon as a second approver exists, changes go for approval again — no setting to remember."""
    if not mc_on(sub_kind):
        return False
    if second_approver_exists(required_role, super_admin_only):
        return True
    audit.write('Maker-checker skipped: no other approver yet', sub_kind, new=f'needs {required_role}; no other active user can approve',
                category='security')
    return False


def _key(a):
    return f"{a['kind']}:{a['sub_kind']}" if a['sub_kind'] else a['kind']


def request(kind, title, payload, required_role, *, sub_kind=None, company=None, branch=None, amount=0, detail=None, reason=None,
            super_admin_only=False, signature=None, ref=None):
    u = g.user
    if signature:
        dup = db.one("SELECT no FROM approvals WHERE signature=:s AND status IN ('Pending','Returned')", s=signature[:255])
        if dup:
            raise conflict(f'An identical request is already waiting ({dup["no"]}).', 'duplicate_request', no=dup['no'])
    no = next_no('HO', 'APR')
    aid = db.insert('approvals', dict(no=no, kind=kind, sub_kind=sub_kind, company=company, branch_id=branch, amount=amount, title=title[:300],
                                      detail=(detail or '')[:1000] or None, reason=reason, payload=payload, required_role=required_role,
                                      super_admin_only=1 if super_admin_only else 0, requested_by=u['id'], requested_role=u['main_role'],
                                      signature=signature[:255] if signature else None, ref=ref))
    db.insert('approval_log', dict(approval_id=aid, user_name=u['name'], role=u['main_role'], action='Submitted', remarks=reason))
    audit.write('Approval requested', no, new=title, reason=reason, category='approval', status='Pending', company=company, branch=branch)
    notify.alert('New access request' if kind == 'mc' and sub_kind in ('user', 'user_update') else 'Approval pending',
                 f'{no}: {title} (by {u["name"]}, needs {required_role})', ref=no, company=company, branch=branch, severity='info')
    return {'id': aid, 'no': no, 'status': 'Pending', 'required_role': required_role}


def why_not(u, a):
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


def _payload(a):
    p = a['payload']
    return json.loads(p) if isinstance(p, str) else p


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
    payload = _payload(a)
    result = None
    if action == 'approve':
        controls = setting('controls', {}) or {}
        if a['kind'] in (controls.get('evBeforeApprove') or []):
            n = db.val("SELECT COUNT(*) FROM evidence WHERE ref IN (:r1, :r2) AND status='Active'", r1=a['ref'] or a['no'], r2=a['no'])
            if not n:
                raise conflict('Attach evidence before approving this request.', 'evidence_required')
        fn = HANDLERS.get(_key(a)) or HANDLERS.get(a['kind'])
        if not fn:
            raise conflict(f'No handler is registered for {_key(a)}.', 'no_handler')
        result = fn(payload, a) or 'Applied'
        status = 'Approved'
    elif action == 'reject':
        fn = REJECT_HANDLERS.get(_key(a)) or REJECT_HANDLERS.get(a['kind'])
        if fn:
            fn(payload, a, remarks)
        status = 'Rejected'
    else:
        fn = RETURN_HANDLERS.get(_key(a)) or RETURN_HANDLERS.get(a['kind'])
        if fn:
            fn(payload, a, remarks)
        status = 'Returned'
    db.run('UPDATE approvals SET status=:s, decided_by=:u, decided_at=NOW(6), result=:r WHERE id=:i',
           s=status, u=u['id'], r=(result or remarks or '')[:500], i=a['id'])
    db.insert('approval_log', dict(approval_id=a['id'], user_name=u['name'], role=u['main_role'], action=status, remarks=remarks))
    audit.write(f'Approval {status.lower()}', a['no'], old='Pending', new=result or status, reason=remarks, category='approval', status=status,
                company=a['company'], branch=a['branch_id'])
    notify.alert('Access approved or rejected' if a['kind'] == 'mc' else 'Approval decided', f"{a['no']} {status.lower()} by {u['name']}: {a['title']}",
                 ref=a['no'], company=a['company'], branch=a['branch_id'], severity='info')
    return {'no': a['no'], 'status': status, 'result': result}


def _own(approval_id, statuses):
    a = db.one('SELECT * FROM approvals WHERE id=:i FOR UPDATE', i=approval_id)
    if not a:
        raise not_found('Approval not found.')
    if a['requested_by'] != g.user['id']:
        raise denied('Only the person who raised the request can do this.', 'not_requester')
    if a['status'] not in statuses:
        raise conflict(f'This request is {a["status"].lower()}.', 'wrong_status')
    return a


def resubmit(approval_id, remarks):
    a = _own(approval_id, ('Returned',))
    if not (remarks or '').strip():
        raise bad('Say what you changed.', 'reason_required')
    db.run("UPDATE approvals SET status='Pending', decided_by=NULL, decided_at=NULL WHERE id=:i", i=a['id'])
    db.insert('approval_log', dict(approval_id=a['id'], user_name=g.user['name'], role=g.user['main_role'], action='Resubmitted', remarks=remarks))
    audit.write('Approval resubmitted', a['no'], old='Returned', new='Pending', reason=remarks, category='approval')
    return {'no': a['no'], 'status': 'Pending'}


def cancel(approval_id, reason):
    a = _own(approval_id, ('Pending', 'Returned'))
    reason = (reason or '').strip() or 'Withdrawn by requester'
    fn = REJECT_HANDLERS.get(_key(a)) or REJECT_HANDLERS.get(a['kind'])
    if fn:
        fn(_payload(a), a, reason)
    db.run("UPDATE approvals SET status='Cancelled', decided_by=:u, decided_at=NOW(6), result=:r WHERE id=:i", u=g.user['id'], r=reason, i=a['id'])
    db.insert('approval_log', dict(approval_id=a['id'], user_name=g.user['name'], role=g.user['main_role'], action='Cancelled', remarks=reason))
    audit.write('Approval cancelled', a['no'], old=a['status'], new='Cancelled', reason=reason, category='approval', status='Cancelled')
    return {'no': a['no'], 'status': 'Cancelled'}
