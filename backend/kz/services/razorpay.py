"""Razorpay: orders, checkout signature, refunds, webhooks, settlements.

Each company has its own Razorpay account (RAZORPAY_KEY_ID_<CO>, RAZORPAY_KEY_SECRET_<CO>, RAZORPAY_WEBHOOK_SECRET_<CO>).
With no key configured for a company the store runs in SIMULATED mode: orders get ids order_SIM..., and the parent
confirms a test payment on screen. Simulated payments are marked env='Simulated' and are excluded from settlement.

Accounting
  payment captured -> the online invoice is paid by mode 'online' (Dr 1115 Razorpay clearing)
  settlement       -> Dr 1120 bank (net) + Dr 5850 gateway charges (fee) + Dr 1310 GST input (tax on fee) / Cr 1115 (gross)
  refund           -> credit note pays out through 1115 (Cr 1115)
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import secrets

import requests

from .. import db
from ..config import Config
from ..core import audit, notify
from ..core.security import setting
from ..errors import ApiError, bad
from . import ledger
from .common import money

API = 'https://api.razorpay.com/v1'
SYSTEM = {'id': None, 'name': 'System (Razorpay)', 'main_role': 'System'}


def keys(company):
    return Config.RAZORPAY.get(company) or {}


def simulated(company):
    return not (keys(company).get('key_id') and keys(company).get('key_secret'))


def env_label(company):
    return 'Simulated' if simulated(company) else (setting('razorpay', {}) or {}).get('env', 'Test')


def _call(company, method, path, payload=None):
    k = keys(company)
    auth = base64.b64encode(f"{k['key_id']}:{k['key_secret']}".encode()).decode()
    try:
        r = requests.request(method, API + path, json=payload, headers={'Authorization': f'Basic {auth}'}, timeout=20)
    except requests.RequestException as e:
        raise ApiError(502, 'gateway_unreachable', f'Razorpay could not be reached: {type(e).__name__}. Try again.')
    if r.status_code >= 400:
        try:
            msg = r.json().get('error', {}).get('description') or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        raise ApiError(502, 'gateway_error', f'Razorpay refused the request: {msg}')
    return r.json()


def create_order(company, amount, receipt, notes):
    paise = int(money(amount) * 100)
    if simulated(company):
        oid = 'order_SIM' + secrets.token_hex(7)
    else:
        oid = _call(company, 'POST', '/orders', {'amount': paise, 'currency': 'INR', 'receipt': receipt[:40], 'notes': notes})['id']
    return {'mode': 'simulated' if simulated(company) else 'razorpay', 'key_id': keys(company).get('key_id') or None, 'rzp_order_id': oid,
            'amount_paise': paise, 'currency': 'INR'}


def checkout_signature_ok(company, order_id, payment_id, signature):
    secret = keys(company).get('key_secret') or ''
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), f'{order_id}|{payment_id}'.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def webhook_signature_ok(company, raw, signature):
    secret = keys(company).get('webhook_secret') or ''
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def fetch_payment(company, payment_id):
    return _call(company, 'GET', f'/payments/{payment_id}')


def refund(company, payment_id, amount, notes=None):
    if simulated(company) or payment_id.startswith('pay_SIM'):
        return {'id': 'rfnd_SIM' + secrets.token_hex(6), 'status': 'processed'}
    return _call(company, 'POST', f'/payments/{payment_id}/refund', {'amount': int(money(amount) * 100), 'notes': notes or {}})


def record_payment(company, ent, ref, ref_type, verified):
    """Insert or update rzp_payments from a Razorpay payment entity (dict). Returns the row id."""
    pid = ent['id']
    status = {'captured': 'Captured', 'authorized': 'Authorised', 'failed': 'Failed', 'refunded': 'Refunded'}.get(ent.get('status'), 'Created')
    amount = money((ent.get('amount') or 0) / 100)
    detail = (ent.get('card') or {}).get('network') if isinstance(ent.get('card'), dict) else (ent.get('bank') or ent.get('wallet') or ent.get('vpa'))
    acq = ent.get('acquirer_data') or {}
    row = db.one('SELECT id, status FROM rzp_payments WHERE id=:i FOR UPDATE', i=pid)
    if row:
        if row['status'] != 'Captured' or status == 'Captured':
            db.run('''UPDATE rzp_payments SET status=:s, fee=:f, tax=:t, webhook_verified=GREATEST(webhook_verified,:w), error_reason=:e WHERE id=:i''',
                   s=status, f=money((ent.get('fee') or 0) / 100), t=money((ent.get('tax') or 0) / 100), w=1 if verified else 0,
                   e=ent.get('error_description'), i=pid)
    else:
        notes = ent.get('notes') or {}
        db.insert('rzp_payments', dict(id=pid, order_id=ent.get('order_id'), company=company, ref=ref or notes.get('ref') or ent.get('order_id') or pid,
                                       ref_type=ref_type or notes.get('ref_type') or 'online_order', adm_no=notes.get('adm_no'),
                                       payer_mobile=(ent.get('contact') or '')[-10:] or None, amount=amount, method=ent.get('method') or 'upi',
                                       method_detail=str(detail)[:60] if detail else None, status=status,
                                       bank_ref=acq.get('rrn') or acq.get('upi_transaction_id') or acq.get('bank_transaction_id'),
                                       fee=money((ent.get('fee') or 0) / 100), tax=money((ent.get('tax') or 0) / 100), webhook_verified=1 if verified else 0,
                                       error_reason=ent.get('error_description'), env=env_label(company),
                                       paid_at=dt.datetime.fromtimestamp(ent.get('created_at') or dt.datetime.now().timestamp())))
    return pid, status, amount


def handle_webhook(company, raw, signature, event_id):
    try:
        evt = json.loads(raw.decode('utf-8') or '{}')
    except ValueError:
        evt = {}
    ok = webhook_signature_ok(company, raw, signature)
    event_id = event_id or evt.get('id')
    if not ok:
        db.insert('rzp_webhook_events', dict(event_id=None, event=evt.get('event'), company=company, signature_ok=0, body=raw.decode('utf-8', 'replace')[:60000],
                                             error='Signature verification failed (event ' + str(event_id)[:40] + ')'))
        audit.security_event('Webhook rejected', f'Razorpay webhook with an invalid signature ({company})')
        notify.alert('Webhook rejected', f'A Razorpay webhook for {company} failed signature verification and was not processed.', company=company, severity='bad')
        return 'rejected'
    if event_id and db.val('SELECT COUNT(*) FROM rzp_webhook_events WHERE event_id=:e', e=event_id):
        return 'duplicate'
    eid = db.insert('rzp_webhook_events', dict(event_id=event_id, event=evt.get('event'), company=company, signature_ok=1, body=raw.decode('utf-8', 'replace')[:60000]))
    kind = evt.get('event', '')
    payload = evt.get('payload') or {}
    status = 'ignored'
    if kind.startswith('payment.'):
        ent = (payload.get('payment') or {}).get('entity') or {}
        if ent.get('id'):
            pid, st, amount = record_payment(company, ent, None, None, True)
            status = 'payment ' + st.lower()
            if st == 'Captured':
                from .online import confirm_by_gateway
                confirm_by_gateway(company, ent.get('order_id'), pid)
    elif kind == 'payment_link.paid':
        link = (payload.get('payment_link') or {}).get('entity') or {}
        ent = (payload.get('payment') or {}).get('entity') or {}
        if link.get('id') and ent.get('id'):
            record_payment(company, ent, link['id'], 'payment_link', True)
            from .paylinks import mark_paid
            mark_paid(link['id'], ent['id'], money((ent.get('amount') or 0) / 100))
            status = 'payment link paid'
    elif kind == 'refund.processed':
        r = (payload.get('refund') or {}).get('entity') or {}
        db.run("UPDATE refunds SET status='Refunded', completed_at=NOW(6) WHERE gateway_refund_id=:i", i=r.get('id'))
        status = 'refund processed'
    elif kind == 'settlement.processed':
        status = record_settlement(company, (payload.get('settlement') or {}).get('entity') or {})
    db.run('UPDATE rzp_webhook_events SET processed_at=NOW(6) WHERE id=:i', i=eid)
    return status


def record_settlement(company, s):
    if not s.get('id') or db.val('SELECT COUNT(*) FROM rzp_settlements WHERE id=:i', i=s['id']):
        return 'settlement duplicate'
    net = money((s.get('amount') or 0) / 100)
    fees = money((s.get('fees') or 0) / 100)
    tax = money((s.get('tax') or 0) / 100)
    fee_ex_tax = fees - tax
    gross = net + fees
    date = dt.date.today()
    j = ledger.post(company, date, 'Settlement', s['id'], f"Razorpay settlement {s['id']} UTR {s.get('utr', '')}",
                    [{'account': '1120', 'debit': net}, {'account': '5850', 'debit': fee_ex_tax}, {'account': '1310', 'debit': tax},
                     {'account': '1115', 'credit': gross}])
    db.insert('rzp_settlements', dict(id=s['id'], company=company, settled_on=date, gross=gross, fee=fee_ex_tax, tax=tax, net=net, utr=s.get('utr') or '',
                                      journal_no=j.no))
    audit.write('Razorpay settlement posted', s['id'], new=f'gross Rs {gross}, fee Rs {fees}, net Rs {net}', category='integration', company=company, user=SYSTEM)
    return 'settlement recorded'


def reconcile(company, date):
    """Daily match of gateway payments against the app. Returns a list of exceptions (also stored in rzp_exceptions)."""
    out = []
    for p in db.q('''SELECT * FROM rzp_payments WHERE company=:c AND DATE(paid_at)=:d AND status='Captured' AND env<>'Simulated' ''', c=company, d=date):
        if not p['app_ref']:
            out.append(('unposted', p['id'], p['amount'], f"Captured payment {p['id']} (Rs {p['amount']}) has no invoice in the app"))
    for o in db.q('''SELECT no, total, payment_id FROM online_orders WHERE company=:c AND DATE(placed_at)=:d AND payment_id IS NOT NULL''', c=company, d=date):
        p = db.one('SELECT amount, status FROM rzp_payments WHERE id=:i', i=o['payment_id'])
        if not p:
            out.append(('missing_payment', o['no'], o['total'], f"Order {o['no']} is paid in the app but the gateway payment is not recorded"))
        elif money(p['amount']) != money(o['total']):
            out.append(('amount_mismatch', o['no'], o['total'], f"Order {o['no']}: app Rs {o['total']} vs gateway Rs {p['amount']}"))
    for kind, ref, amt, detail in out:
        db.run('''INSERT IGNORE INTO rzp_exceptions (ex_key, ex_type, company, ref, amount, detail, ex_date, status)
                  VALUES (:k, :t, :c, :r, :a, :d, :dt, 'Open')''', k=f'{kind}:{ref}'[:80], t=kind, c=company, r=ref, a=amt, d=detail[:500], dt=date)
    return [{'type': k, 'ref': r, 'amount': float(a), 'detail': d} for k, r, a, d in out]


def check_amount(expected, got):
    if money(expected) != money(got):
        raise bad(f'Payment amount Rs {got} does not match the order total Rs {expected}.', 'amount_mismatch')
