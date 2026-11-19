"""Payment links and QR codes (Razorpay Payment Links).

Staff create a link for a student's or customer's dues (optionally one bill) or an advance. The parent opens the link
(SMS / WhatsApp, or scans the QR code at the counter) and pays. When Razorpay reports the link paid (webhook
`payment_link.paid`) a receipt is posted automatically: Dr 1115 Razorpay clearing / Cr the customer's receivable
(applied to the named bill, then oldest bills; the rest to the wallet 2200).

Simulated mode (no Razorpay keys for the company): the link points to this app's /pay/<id> page, which offers a
"test payment" button. Simulated payments go through the same receipt posting and are marked env='Simulated'.
"""
import datetime as dt
import secrets

from flask import g

from .. import db
from ..config import Config
from ..core import audit, notify
from ..errors import bad, conflict, not_found
from ..models import Customer, Invoice, PaymentLink
from . import razorpay
from .accounts import record_receipt
from .common import check_company, ho_branch, money, parse_date, today
from .sales import _student, student_customer

SYSTEM = {'id': None, 'name': 'System (payment link)', 'main_role': 'System'}


def _customer(co, d):
    if d.get('adm_no'):
        st = _student(d['adm_no'])
        return student_customer(co, st), st
    cust = db.session().get(Customer, d.get('customer_id'))
    if not cust or cust.company != co:
        raise bad('Choose the student or the customer.', 'unknown_customer')
    return cust, None


def create(d):
    u = g.user
    co = d.get('company')
    check_company(u, co)
    cust, st = _customer(co, d)
    amt = money(d.get('amount'))
    if amt <= 0:
        raise bad('Enter the amount to collect.', 'bad_amount')
    purpose = d.get('purpose') or 'dues'
    if purpose not in ('dues', 'invoice', 'advance'):
        raise bad('Purpose must be dues, invoice or advance.', 'invalid')
    inv_no = str(d.get('invoice_no') or '').strip() or None
    if purpose == 'invoice':
        inv = db.session().query(Invoice).filter_by(no=inv_no, company=co).first()
        if not inv or inv.customer_id != cust.id:
            raise bad('That bill does not belong to this customer.', 'unknown_invoice')
        if amt > money(inv.balance):
            raise bad(f'The bill has only Rs {inv.balance} outstanding.', 'overpayment')
    mobile = ''.join(ch for ch in (d.get('mobile') or (st or {}).get('parent_mobile') or cust.mobile or '') if ch.isdigit())[-10:] or None
    expires = parse_date(d.get('expires_on'), today() + dt.timedelta(days=7), 'Expires on')
    if expires < today():
        raise bad('The expiry date is in the past.', 'bad_date')
    desc = str(d.get('description') or '').strip() or {'dues': 'Dues', 'invoice': f'Bill {inv_no}', 'advance': 'Advance'}[purpose] + f' · {cust.name}'
    if razorpay.simulated(co):
        lid = 'plink_SIM' + secrets.token_hex(6)
        url = f"{Config.PUBLIC_URL.rstrip('/')}/pay/{lid}"
    else:
        r = razorpay._call(co, 'POST', '/payment_links', {
            'amount': int(amt * 100), 'currency': 'INR', 'description': desc[:200], 'expire_by': int(dt.datetime.combine(expires, dt.time(23, 59)).timestamp()),
            'reference_id': f'{co}-{secrets.token_hex(5)}', 'customer': {'name': cust.name[:60], **({'contact': '+91' + mobile} if mobile else {})},
            'notify': {'sms': bool(mobile), 'email': False}, 'reminder_enable': True,
            'notes': {'ref_type': 'payment_link', 'customer_id': cust.id, 'invoice_no': inv_no or ''}})
        lid, url = r['id'], r['short_url']
    pl = PaymentLink(id=lid, short_url=url, company=co, purpose=purpose, customer_id=cust.id, amount=amt, paid=0, status='Issued', expires_on=expires,
                     description=desc[:300], created_by=u['id'], adm_no=(st or {}).get('adm_no'), invoice_no=inv_no, mobile=mobile, env=razorpay.env_label(co))
    db.add(pl)
    if mobile and razorpay.simulated(co):
        notify.queue('sms', mobile, None, f'Kiswa-Zaibus: please pay Rs {amt} ({desc[:60]}) at {url} before {expires:%d %b}.', cust.name)
    audit.write('Payment link created', lid, new=f'Rs {amt} · {desc}', category='integration', company=co)
    return pl


def cancel(lid, reason):
    pl = _get(lid, lock=True)
    if pl.status != 'Issued':
        raise conflict(f'This link is {pl.status}.', 'wrong_status')
    if not (reason or '').strip():
        raise bad('Give the reason.', 'reason_required')
    if not razorpay.simulated(pl.company):
        razorpay._call(pl.company, 'POST', f'/payment_links/{lid}/cancel')
    pl.status = 'Cancelled'
    audit.write('Payment link cancelled', lid, reason=reason, category='integration', company=pl.company)
    return pl


def _get(lid, lock=False):
    pl = db.session().get(PaymentLink, lid, with_for_update=lock)
    if not pl:
        raise not_found('Payment link not found.', 'unknown_link')
    return pl


def public_view(lid):
    """What the parent sees on /pay/<id> (no sign-in; no internal ids beyond the link itself)."""
    pl = _get(lid)
    if pl.status == 'Issued' and pl.expires_on < today():
        pl.status = 'Expired'
    co = db.one('SELECT name, phone FROM companies WHERE code=:c', c=pl.company)
    return {'id': pl.id, 'company': co['name'], 'phone': co['phone'], 'description': pl.description, 'amount': float(pl.amount), 'status': pl.status,
            'expires_on': str(pl.expires_on), 'short_url': pl.short_url, 'simulated': razorpay.simulated(pl.company),
            'receipt_no': pl.receipt_no}


def mark_paid(lid, payment_id, amount, simulated=False):
    """Post the receipt for a paid link (idempotent: a second notification for the same link does nothing)."""
    pl = _get(lid, lock=True)
    if pl.status == 'Paid':
        return pl
    if pl.status != 'Issued':
        raise conflict(f'This link is {pl.status}.', 'wrong_status')
    amount = money(amount)
    razorpay.check_amount(pl.amount, amount)
    prev = g.get('user')
    g.user = prev or SYSTEM
    try:
        cust = db.session().get(Customer, pl.customer_id)
        if simulated:
            razorpay.record_payment(pl.company, {'id': payment_id, 'amount': int(amount * 100), 'status': 'captured', 'method': 'upi',
                                                 'notes': {'ref': lid, 'adm_no': pl.adm_no}, 'created_at': int(dt.datetime.now().timestamp())},
                                    lid, 'payment_link', False)
        r = record_receipt(pl.company, cust, amount, 'online', ho_branch(pl.company), today(), reference=lid, utr=payment_id,
                           against_invoice=pl.invoice_no, advance=pl.purpose == 'advance', created_by=(prev or {}).get('id') or 'system')
        db.run('UPDATE rzp_payments SET app_ref=:r WHERE id=:p', r=r.no, p=payment_id)
        pl.status, pl.paid, pl.payment_id, pl.receipt_no, pl.paid_at = 'Paid', amount, payment_id, r.no, dt.datetime.now()
        audit.write('Payment link paid', lid, new=f'Rs {amount} · receipt {r.no}', category='integration', company=pl.company)
        notify.alert('Payment link paid', f'{lid}: Rs {amount} from {cust.name} · receipt {r.no}', ref=lid, company=pl.company, severity='info')
    finally:
        g.user = prev
    return pl


def simulate_pay(lid):
    pl = _get(lid)
    if not razorpay.simulated(pl.company):
        raise bad('This link is live; pay it through Razorpay.', 'simulated_mode')
    return mark_paid(lid, 'pay_SIM' + secrets.token_hex(7), pl.amount, simulated=True)


def expire_due():
    return db.run("UPDATE payment_links SET status='Expired' WHERE status='Issued' AND expires_on < CURDATE()").rowcount
