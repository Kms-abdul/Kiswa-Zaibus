"""Parent-facing online store (PWA /shop): sign in with admission number + registered mobile (one-time code),
browse what the student may buy, place and pay orders, track them, request cancellation or return."""
import datetime as dt
import json
import secrets
from functools import wraps

from flask import Blueprint, g, jsonify, make_response, request

from .. import db
from ..config import Config
from ..core import audit, notify
from ..core.rbac import body
from ..core.security import new_otp, new_token, otp_hash, sha
from ..errors import ApiError, bad, denied, not_found
from ..models import OnlineOrder
from ..services import online as svc
from ..services import razorpay

bp = Blueprint('shop', __name__, url_prefix='/api/shop')


def parent_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        tok = request.cookies.get(Config.PARENT_COOKIE_NAME)
        s = db.one('SELECT * FROM parent_sessions WHERE token_hash=:h AND ended_at IS NULL', h=sha(tok or '-'))
        if not s or s['last_seen'] < dt.datetime.now() - dt.timedelta(hours=12):
            raise ApiError(401, 'not_signed_in', 'Please sign in with the admission number and your mobile number.')
        db.run('UPDATE parent_sessions SET last_seen=NOW(6) WHERE id=:i', i=s['id'])
        s['adm_nos'] = json.loads(s['adm_nos']) if isinstance(s['adm_nos'], str) else s['adm_nos']
        g.parent = s
        return fn(*a, **kw)
    return wrapper


@bp.post('/auth/start')
def start():
    b = body()
    adm = (b.get('adm_no') or '').strip().upper()
    mobile = ''.join(ch for ch in (b.get('mobile') or '') if ch.isdigit())[-10:]
    st = db.one('SELECT * FROM students WHERE adm_no=:a', a=adm)
    db.keep_on_error()
    if not st or (st['parent_mobile'] or '')[-10:] != mobile:
        audit.security_event('Parent sign-in failed', f'adm {adm[:20]} with an unregistered mobile')
        raise bad('The admission number and mobile number do not match our records. Use the mobile registered with the school.', 'parent_not_found')
    cid = secrets.token_hex(16)
    otp = new_otp()
    db.insert('parent_challenges', dict(id=cid, adm_no=adm, mobile=mobile, otp_hash=otp_hash(cid, otp), expires_at=dt.datetime.now() + dt.timedelta(minutes=5)))
    notify.queue('sms', mobile, None, f'Kiswa-Zaibus store: your sign-in code is {otp}. It expires in 5 minutes.', None, 'OTP')
    out = {'challenge': cid, 'mobile_hint': '******' + mobile[-4:]}
    if Config.OTP_DEMO:
        out['dev_otp'] = otp
    return jsonify(out)


@bp.post('/auth/verify')
def verify():
    b = body()
    c = db.one('SELECT * FROM parent_challenges WHERE id=:i FOR UPDATE', i=b.get('challenge') or '')
    if not c or c['used_at'] or c['expires_at'] < dt.datetime.now() or c['tries'] >= 3:
        raise ApiError(401, 'challenge_expired', 'This code has expired. Ask for a new one.')
    if otp_hash(c['id'], str(b.get('otp') or '').strip()) != c['otp_hash']:
        db.keep_on_error()
        db.run('UPDATE parent_challenges SET tries=tries+1 WHERE id=:i', i=c['id'])
        raise ApiError(401, 'bad_otp', 'The code is incorrect.')
    db.run('UPDATE parent_challenges SET used_at=NOW(6) WHERE id=:i', i=c['id'])
    kids = [r['adm_no'] for r in db.q("SELECT adm_no FROM students WHERE RIGHT(parent_mobile,10)=:m AND status='Active'", m=c['mobile'])]
    tok = new_token()
    db.insert('parent_sessions', dict(token_hash=sha(tok), mobile=c['mobile'], adm_nos=kids))
    resp = make_response(jsonify({'ok': True, 'children': kids}))
    resp.set_cookie(Config.PARENT_COOKIE_NAME, tok, httponly=True, secure=Config.COOKIE_SECURE, samesite='Lax', path='/api/shop', max_age=12 * 3600)
    return resp


@bp.post('/auth/logout')
def logout():
    tok = request.cookies.get(Config.PARENT_COOKIE_NAME)
    if tok:
        db.run('UPDATE parent_sessions SET ended_at=NOW(6) WHERE token_hash=:h', h=sha(tok))
    resp = make_response(jsonify({'ok': True}))
    resp.delete_cookie(Config.PARENT_COOKIE_NAME, path='/api/shop')
    return resp


@bp.get('/me')
@parent_required
def me():
    kids = db.clean(db.q('SELECT adm_no, name, class_name, section, school_id, category FROM students WHERE adm_no IN :a', a=tuple(g.parent['adm_nos']) or ('',)))
    return jsonify({'mobile_hint': '******' + g.parent['mobile'][-4:], 'children': kids})


def _child(adm):
    if adm not in (g.parent['adm_nos'] or []):
        raise denied('This student is not linked to your mobile number.', 'not_your_student')
    st = db.one('SELECT * FROM students WHERE adm_no=:a', a=adm)
    if not st:
        raise not_found('Student not found.', 'unknown_student')
    return st


@bp.get('/catalog/<adm>')
@parent_required
def catalog(adm):
    return jsonify(svc.catalogue(_child(adm)))


@bp.post('/orders')
@parent_required
def place():
    return jsonify(svc.place_order(g.parent, body())), 201


@bp.post('/orders/<int:oid>/pay')
@parent_required
def pay(oid):
    o = svc.confirm_from_checkout(g.parent, oid, body())
    return jsonify({'no': o.no, 'status': o.status, 'invoice_no': o.invoice_no})


@bp.get('/orders/<int:oid>/payment')
@parent_required
def payment_info(oid):
    o = db.session().get(OnlineOrder, oid)
    if not o or o.mobile != g.parent['mobile']:
        raise not_found('Order not found.', 'unknown_order')
    return jsonify({'mode': 'simulated' if razorpay.simulated(o.company) else 'razorpay', 'key_id': razorpay.keys(o.company).get('key_id') or None,
                    'rzp_order_id': o.rzp_order_id, 'amount_paise': int(o.total * 100), 'company': o.company, 'no': o.no})


@bp.get('/orders')
@parent_required
def orders():
    rows = db.clean(db.q('''SELECT o.id, o.no, o.company, o.adm_no, o.collect_at, o.total, o.status, o.invoice_no, o.placed_at, o.events, s.name AS student_name
                            FROM online_orders o JOIN students s ON s.adm_no=o.adm_no WHERE o.mobile=:m ORDER BY o.id DESC LIMIT 50''', m=g.parent['mobile']))
    for r in rows:
        r['lines'] = db.clean(db.q('''SELECT l.line_no, l.item_id, i.name, i.size, l.qty, l.rate, l.amount, l.collected_qty, l.returned_qty
                                      FROM online_order_lines l JOIN items i ON i.id=l.item_id WHERE l.order_id=:o ORDER BY l.line_no''', o=r['id']))
        r['requests'] = db.clean(db.q('SELECT no, type, status, reason, decision_note, created_at FROM order_requests WHERE order_id=:o ORDER BY id', o=r['id']))
    return jsonify(rows)


@bp.post('/orders/<int:oid>/request')
@parent_required
def request_change(oid):
    r = svc.create_request(g.parent, oid, body())
    return jsonify({'no': r.no, 'status': r.status}), 201
