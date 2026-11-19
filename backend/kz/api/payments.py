"""Razorpay: webhooks (public, signature-checked), payments list, settlements, reconciliation, integration status."""
from flask import Blueprint, g, jsonify, request

from .. import db
from ..core import audit
from ..core.approvals import handler, request as approval_request
from ..core.rbac import body, require
from ..core.security import setting
from ..errors import bad, conflict, not_found
from ..services import razorpay as svc
from ..services.common import parse_date

bp = Blueprint('payments', __name__)


@bp.post('/webhooks/razorpay/<company>')
def webhook(company):
    company = company.upper()
    if company not in ('KG', 'ZE'):
        raise not_found('Unknown company.', 'unknown_company')
    status = svc.handle_webhook(company, request.get_data(), request.headers.get('X-Razorpay-Signature'), request.headers.get('X-Razorpay-Event-Id'))
    if status == 'rejected':
        db.keep_on_error()
        raise bad('Signature verification failed.', 'bad_signature')
    return jsonify({'status': status})


@bp.get('/api/payments')
@require('view', 'ctl.pay')
def payments():
    a = request.args
    sql = 'SELECT * FROM rzp_payments WHERE company IN :c'
    p = {'c': tuple(g.user['companies']) or ('',)}
    if a.get('date'):
        sql += ' AND DATE(paid_at)=:d'
        p['d'] = a['date']
    if a.get('status'):
        sql += ' AND status=:s'
        p['s'] = a['status']
    return jsonify(db.clean(db.q(sql + ' ORDER BY paid_at DESC LIMIT 500', **p)))


@bp.get('/api/payments/settlements')
@require('view', 'ctl.pay')
def settlements():
    return jsonify(db.clean(db.q('SELECT * FROM rzp_settlements WHERE company IN :c ORDER BY settled_on DESC LIMIT 200', c=tuple(g.user['companies']) or ('',))))


@bp.get('/api/payments/reconciliation')
@require('view', 'ctl.pay')
def recon():
    date = parse_date(request.args.get('date'))
    out = {co: svc.reconcile(co, date) for co in g.user['companies']}
    open_ex = db.clean(db.q("SELECT * FROM rzp_exceptions WHERE status='Open' AND company IN :c ORDER BY ex_date DESC", c=tuple(g.user['companies']) or ('',)))
    return jsonify({'date': str(date), 'found': out, 'open_exceptions': open_ex})


@bp.post('/api/payments/exceptions/<path:key>/resolve')
@require('approve', 'ctl.pay')
def resolve(key):
    d = body()
    if not str(d.get('resolution') or '').strip():
        raise bad('Describe how it was resolved.', 'reason_required')
    n = db.run("UPDATE rzp_exceptions SET status='Resolved', resolution=:r, resolved_by=:u, resolved_at=NOW(6) WHERE ex_key=:k AND status='Open'",
               r=d['resolution'], u=g.user['id'], k=key).rowcount
    if not n:
        raise conflict('Exception not found or already resolved.', 'wrong_status')
    audit.write('Reconciliation exception resolved', key, reason=d['resolution'], category='integration')
    return jsonify({'ok': True})


@bp.get('/api/payments/status')
@require('view', 'ctl.pay')
def status():
    active = (setting('integrations_active', {}) or {}).get('razorpay')
    return jsonify({co: {'mode': 'simulated' if svc.simulated(co) else (setting('razorpay', {}) or {}).get('env', 'Test'),
                         'webhook_secret_set': bool(svc.keys(co).get('webhook_secret')), 'live_approved': bool(active)} for co in ('KG', 'ZE')})


@bp.post('/api/payments/go-live')
@require('edit', 'ctl.pay')
def go_live():
    """Switching Razorpay to Live needs a Super Admin's approval (and live keys in the server configuration)."""
    for co in g.user['companies']:
        if svc.simulated(co):
            raise conflict(f'No Razorpay keys are configured for {co}. Add RAZORPAY_KEY_ID_{co} / _SECRET_{co} on the server first.', 'gateway_not_configured')
    a = approval_request('mc', 'Switch Razorpay to Live mode', {'env': 'Live'}, 'Super Admin', sub_kind='intg_on', super_admin_only=True,
                         reason=body().get('reason'), signature='rzp-live')
    return jsonify(a), 202


@handler('mc:intg_on')
def _go_live(p, apr):
    rzp = setting('razorpay', {}) or {}
    rzp['env'] = p.get('env', 'Live')
    db.run("UPDATE settings SET svalue=:v, updated_by=:u WHERE skey='razorpay'", v=rzp, u=g.user['id'])
    act = setting('integrations_active', {}) or {}
    act['razorpay'] = True
    db.run("UPDATE settings SET svalue=:v WHERE skey='integrations_active'", v=act)
    return 'Razorpay is live'


# ---------------------------------------------------------------- payment links and QR codes
@bp.get('/api/payments/links')
@require('view', 'ctl.pay')
def links():
    a = request.args
    sql = '''SELECT l.*, c.name AS customer_name, u.name AS created_by_name FROM payment_links l LEFT JOIN customers c ON c.id=l.customer_id
             LEFT JOIN users u ON u.id=l.created_by WHERE l.company IN :c'''
    p = {'c': tuple(g.user['companies']) or ('',)}
    if a.get('status'):
        sql += ' AND l.status=:s'
        p['s'] = a['status']
    return jsonify(db.clean(db.q(sql + ' ORDER BY l.created_at DESC LIMIT 300', **p)))


@bp.post('/api/payments/links')
@require('add', 'ctl.pay')
def link_create():
    from ..services import paylinks
    return jsonify(paylinks.create(body()).as_dict()), 201


@bp.post('/api/payments/links/<lid>/cancel')
@require('cancel', 'ctl.pay')
def link_cancel(lid):
    from ..services import paylinks
    return jsonify(paylinks.cancel(lid, body().get('reason')).as_dict())


# Public: the page a parent opens from the SMS / QR code (no sign-in). Only the link id is needed.
@bp.get('/api/pay/<lid>')
def pay_public(lid):
    from ..services import paylinks
    return jsonify(paylinks.public_view(lid))


@bp.post('/api/pay/<lid>/simulate')
def pay_simulate(lid):
    """Test servers without Razorpay keys only: marks the link paid and posts the receipt."""
    from ..services import paylinks
    pl = paylinks.simulate_pay(lid)
    return jsonify({'status': pl.status, 'receipt_no': pl.receipt_no})
