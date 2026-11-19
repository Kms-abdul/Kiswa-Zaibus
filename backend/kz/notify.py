"""In-app alerts plus queued email / WhatsApp / SMS messages (sent by the Node gateway's outbox worker)."""
import json

from . import db


def alert(alert_type, message, ref=None, company=None, branch=None, severity='warn'):
    db.insert('alerts', dict(company=company, branch_id=branch, severity=severity, alert_type=alert_type, message=message, ref=ref))
    rule = db.one('SELECT * FROM alert_rules WHERE alert_type=:t', t=alert_type)
    if not rule:
        return
    roles = rule['roles']
    roles = json.loads(roles) if isinstance(roles, str) else (roles or [])
    if not roles:
        return
    people = db.q('''SELECT DISTINCT u.name, u.email, u.mobile FROM users u LEFT JOIN user_roles r ON r.user_id=u.id
                     WHERE u.status='Active' AND (u.main_role IN :r OR r.role IN :r)''', r=tuple(roles))
    for p in people:
        if rule['email'] and p['email']:
            queue('email', p['email'], f'[Kiswa-Zaibus] {alert_type}', message, p['name'], alert_type)
        if rule['whatsapp'] and p['mobile']:
            queue('whatsapp', p['mobile'], None, f'{alert_type}: {message}', p['name'], alert_type)


def queue(channel, address, subject, body, to_name=None, alert_type=None):
    return db.insert('outbox', dict(channel=channel, address=address, subject=subject, body=body, to_name=to_name, alert_type=alert_type))
