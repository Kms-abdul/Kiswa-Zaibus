"""Scheduled report e-mails. Each schedule runs one report (any /api/reports/<key>) as CSV with the permissions of the
user who created it, and queues an e-mail with the CSV attached to every recipient. Called from scheduler.tick().

period (in params): today | yesterday | mtd (month to date) | last_month | last_7_days
"""
import datetime as dt
import inspect
import json
from urllib.parse import urlencode

from flask import current_app, g

from .. import db
from ..core import audit, notify
from ..core.rbac import can, load_user
from ..errors import bad

REPORTS = {
    'sales-register': 'Sales register', 'sales-summary': 'Sales summary', 'item-sales': 'Item-wise sales', 'stock-valuation': 'Stock valuation',
    'online-orders': 'Online orders', 'day-book': 'Day book', 'trial-balance': 'Trial balance', 'receivables-ageing': 'Receivables ageing',
    'payables-ageing': 'Payables ageing', 'ledger': 'Account ledger',
}
PERIODS = ('today', 'yesterday', 'mtd', 'last_month', 'last_7_days')
SYSTEM = {'id': None, 'name': 'System (report schedule)', 'main_role': 'System'}


def validate(d):
    if d.get('report') not in REPORTS:
        raise bad('Choose a report that can be scheduled.', 'invalid')
    if d.get('frequency') not in ('daily', 'weekly', 'monthly'):
        raise bad('Frequency must be daily, weekly or monthly.', 'invalid')
    t = str(d.get('run_at') or '21:30')
    try:
        dt.datetime.strptime(t, '%H:%M')
    except ValueError:
        raise bad('Time must be HH:MM (24-hour).', 'invalid') from None
    rec = [r.strip() for r in (d.get('recipients') or []) if r and '@' in r]
    if not rec:
        raise bad('Add at least one e-mail address.', 'invalid')
    if d['frequency'] == 'weekly' and d.get('weekday') not in range(7):
        raise bad('Choose the day of the week.', 'invalid')
    if d['frequency'] == 'monthly' and d.get('month_day') not in range(1, 29):
        raise bad('Choose a day of the month between 1 and 28.', 'invalid')
    params = dict(d.get('params') or {})
    if params.get('period', 'yesterday') not in PERIODS:
        raise bad('Unknown period.', 'invalid')
    return {'name': (d.get('name') or REPORTS[d['report']]).strip()[:120], 'report': d['report'], 'params': params, 'frequency': d['frequency'],
            'run_at': t, 'weekday': d.get('weekday'), 'month_day': d.get('month_day'), 'recipients': rec}


def _range(period, today):
    if period == 'today':
        return today, today
    if period == 'mtd':
        return today.replace(day=1), today
    if period == 'last_month':
        end = today.replace(day=1) - dt.timedelta(days=1)
        return end.replace(day=1), end
    if period == 'last_7_days':
        return today - dt.timedelta(days=7), today - dt.timedelta(days=1)
    y = today - dt.timedelta(days=1)
    return y, y


def is_due(s, now):
    hh, mm = map(int, s['run_at'].split(':'))
    slot = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if now < slot:
        return False
    if s['frequency'] == 'weekly' and now.weekday() != s['weekday']:
        return False
    if s['frequency'] == 'monthly' and now.day != s['month_day']:
        return False
    return not s['last_run_at'] or s['last_run_at'] < slot


def render_csv(s, now=None):
    """Run the report as the schedule's owner; returns (filename, csv_text, rows)."""
    now = now or dt.datetime.now()
    params = s['params'] if isinstance(s['params'], dict) else {}
    f, t = _range(params.get('period', 'yesterday'), now.date())
    q = {k: v for k, v in params.items() if k != 'period' and v not in (None, '')}
    q.update({'from': f, 'to': t, 'date': t, 'format': 'csv', 'detail': '1'})
    path = f"/api/reports/{s['report']}"
    owner = load_user(s['owner_id'])
    if not owner or owner['status'] != 'Active' or not can(owner, 'view', 'rep'):
        raise PermissionError('the owner of this schedule can no longer run reports')
    endpoint, _ = current_app.url_map.bind('localhost').match(path, method='GET')
    view = inspect.unwrap(current_app.view_functions[endpoint])
    prev = g.get('user')
    try:
        with current_app.test_request_context(path + '?' + urlencode(q)):
            g.user = owner
            resp = view()
    finally:
        g.user = prev
    text = resp.get_data(as_text=True)
    return f"{s['report']}-{t}.csv", text, max(0, text.count('\n') - 1)


def run_one(s, now=None):
    now = now or dt.datetime.now()
    try:
        name, text, rows = render_csv(s, now)
        rec = s['recipients'] if isinstance(s['recipients'], list) else []
        for r in rec:
            db.insert('outbox', dict(channel='email', address=r, subject=f"Kiswa-Zaibus report: {s['name']}", to_name=None,
                                     body=f"{s['name']} ({rows} rows) is attached as {name}.\n\nSent automatically by Kiswa-Zaibus Operations.",
                                     attachment_name=name, attachment=text))
        result = f'{rows} rows · e-mailed to {len(rec)} recipient(s)'
    except Exception as e:  # noqa: BLE001 - one bad schedule must not stop the others
        result = f'Failed: {e}'[:300]
        notify.alert('Scheduled report failed', f"{s['name']}: {result}", ref=f"SCH-{s['id']}", severity='warn')
    db.run('UPDATE report_schedules SET last_run_at=:n, last_result=:r WHERE id=:i', n=now, r=result, i=s['id'])
    audit.write('Scheduled report run', s['name'], new=result, category='settings', user=SYSTEM)
    return result


def load(s):
    s = dict(s)
    for k in ('params', 'recipients'):
        if isinstance(s.get(k), str):
            s[k] = json.loads(s[k])
    return s


def run_due(now=None):
    now = now or dt.datetime.now()
    n = 0
    for s in db.q('SELECT * FROM report_schedules WHERE active=1 ORDER BY id'):
        s = load(s)
        if is_due(s, now):
            run_one(s, now)
            n += 1
    return n
