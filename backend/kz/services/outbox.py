"""Sends queued messages: email over SMTP, SMS and WhatsApp through the provider's HTTP API (POST JSON {to, sender, message}
with a Bearer key). Retries with back-off (2, 4, 8, 16 minutes); after 5 attempts the message is marked Failed.
One-time codes and temporary passwords are blanked from the database once sent."""
import smtplib
from email.message import EmailMessage

import requests

from .. import db
from ..config import Config

MAX_ATTEMPTS = 5
SENSITIVE = ('OTP',)


def _send(m):
    if m['channel'] == 'email':
        if not Config.SMTP_HOST:
            raise RuntimeError('SMTP is not configured (SMTP_HOST)')
        msg = EmailMessage()
        msg['From'] = Config.SMTP_FROM
        msg['To'] = m['address']
        msg['Subject'] = m['subject'] or 'Kiswa-Zaibus'
        msg.set_content(m['body'])
        if m.get('attachment'):
            msg.add_attachment(m['attachment'].encode('utf-8'), maintype='text', subtype='csv', filename=m.get('attachment_name') or 'report.csv')
        with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=20) as s:
            if Config.SMTP_TLS:
                s.starttls()
            if Config.SMTP_USER:
                s.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
            s.send_message(msg)
        return
    url, key = (Config.SMS_API_URL, Config.SMS_API_KEY) if m['channel'] == 'sms' else (Config.WHATSAPP_API_URL, Config.WHATSAPP_API_TOKEN)
    if not url:
        raise RuntimeError(f"{m['channel'].upper()} provider is not configured")
    to = ''.join(ch for ch in m['address'] if ch.isdigit())[-10:]
    r = requests.post(url, json={'to': '91' + to, 'sender': Config.SMS_SENDER_ID, 'message': m['body']},
                      headers={'Authorization': f'Bearer {key}'} if key else {}, timeout=20)
    if r.status_code >= 400:
        raise RuntimeError(f"{m['channel']} provider answered {r.status_code}")


def send_queued(limit=50):
    db.run("UPDATE outbox SET status='Queued' WHERE status='Sending' AND created_at < NOW() - INTERVAL 10 MINUTE")
    rows = db.q('''SELECT * FROM outbox WHERE status='Queued' AND attempts < :m AND (attempts = 0 OR created_at < NOW() - INTERVAL POW(2, attempts) MINUTE)
                   ORDER BY id LIMIT :l''', m=MAX_ATTEMPTS, l=limit)
    sent = failed = 0
    for m in rows:
        if db.run("UPDATE outbox SET status='Sending', attempts=attempts+1 WHERE id=:i AND status='Queued'", i=m['id']).rowcount != 1:
            continue
        try:
            _send(m)
            db.run("UPDATE outbox SET status='Sent', sent_at=NOW(6), last_error=NULL, body=IF(alert_type IN :s, '[sent]', body) WHERE id=:i", s=SENSITIVE, i=m['id'])
            sent += 1
        except Exception as e:  # noqa: BLE001 - any delivery failure is recorded and retried
            final = m['attempts'] + 1 >= MAX_ATTEMPTS
            db.run("UPDATE outbox SET status=:st, last_error=:e, body=IF(:f=1 AND alert_type IN :s, '[not delivered]', body) WHERE id=:i",
                   st='Failed' if final else 'Queued', e=str(e)[:300], f=1 if final else 0, s=SENSITIVE, i=m['id'])
            failed += 1
    return {'sent': sent, 'failed': failed}
