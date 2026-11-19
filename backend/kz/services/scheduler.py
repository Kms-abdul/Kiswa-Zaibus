"""The once-a-minute job (`flask kz tick`, run by the kz-scheduler systemd timer). Every step is idempotent."""
import datetime as dt
import json

from flask import g

from .. import db
from ..core import audit, notify
from ..core.security import policy, setting
from . import online, outbox

SYSTEM = {'id': None, 'name': 'System (scheduler)', 'main_role': 'System'}


def tick():
    g.user = None
    done = {}
    due = db.q("SELECT * FROM access_schedule WHERE status='Scheduled' AND effective_on <= CURDATE() ORDER BY id FOR UPDATE")
    for s in due:
        data = json.loads(s['data']) if isinstance(s['data'], str) else (s['data'] or {})
        if s['change_type'] in ('Resign', 'Suspend'):
            st = 'Resigned' if s['change_type'] == 'Resign' else 'Suspended'
            db.run('UPDATE users SET status=:s WHERE id=:u', s=st, u=s['user_id'])
            db.run('UPDATE sessions SET ended_at=NOW(6), end_reason=:r WHERE user_id=:u AND ended_at IS NULL', r=f'Account {st.lower()}', u=s['user_id'])
        elif s['change_type'] == 'Reinstate':
            db.run("UPDATE users SET status='Active' WHERE id=:u", u=s['user_id'])
        elif s['change_type'] == 'Transfer':
            db.run('DELETE FROM user_branches WHERE user_id=:u', u=s['user_id'])
            for b in data.get('branches') or []:
                db.run('INSERT IGNORE INTO user_branches (user_id, branch_id) VALUES (:u, :b)', u=s['user_id'], b=b)
        db.run("UPDATE access_schedule SET status='Applied', applied_at=NOW(6) WHERE id=:i", i=s['id'])
        audit.write(f"Scheduled access change applied ({s['change_type']})", s['user_id'], new=data, reason=s['reason'], category='security', user=SYSTEM)
    done['access_changes'] = len(due)

    exp = db.q("SELECT id, name FROM users WHERE status='Active' AND valid_to IS NOT NULL AND valid_to < CURDATE()")
    for u in exp:
        db.run("UPDATE users SET status='Inactive' WHERE id=:i", i=u['id'])
        db.run("UPDATE sessions SET ended_at=NOW(6), end_reason='Access expired' WHERE user_id=:u AND ended_at IS NULL", u=u['id'])
        audit.write('Access expired', u['name'], old='Active', new='Inactive', reason='Access end date passed', category='security', user=SYSTEM)
    done['expired_users'] = len(exp)

    esc = setting('escalation', {}) or {}
    hours = int(esc.get('hours') or 24)
    late = db.q("SELECT id, no, title, required_role FROM approvals WHERE status='Pending' AND escalated_at IS NULL AND requested_at < NOW() - INTERVAL :h HOUR", h=hours)
    for a in late:
        db.run('UPDATE approvals SET escalated_at=NOW(6) WHERE id=:i', i=a['id'])
        notify.alert('Approval escalated', f"{a['no']} has waited more than {hours} hours for {a['required_role']}: {a['title']}", ref=a['no'])
    done['escalated'] = len(late)

    idle = int(policy().get('idleMin') or 15)
    done['idle_sessions'] = db.run("UPDATE sessions SET ended_at=NOW(6), end_reason='Idle timeout' WHERE ended_at IS NULL AND last_seen < NOW() - INTERVAL :m MINUTE",
                                   m=idle).rowcount
    db.run("UPDATE login_challenges SET stage='failed' WHERE stage IN ('otp','change') AND expires_at < NOW()")
    db.run('UPDATE parent_sessions SET ended_at=NOW(6) WHERE ended_at IS NULL AND last_seen < NOW() - INTERVAL 12 HOUR')
    done['unpaid_orders_released'] = online.expire_unpaid(int((setting('online', {}) or {}).get('payHoldHours') or 2))
    from . import paylinks, report_schedules, stock_audit
    done['stock_audit_reminders'] = stock_audit.remind()
    done['payment_links_expired'] = paylinks.expire_due()
    done['reports_sent'] = report_schedules.run_due()
    from . import fin_ops
    done['finance'] = fin_ops.auto_run()                 # recurring journals, monthly depreciation, compliance alerts (release 2.3)
    r = outbox.send_queued()
    done['messages_sent'], done['messages_failed'] = r['sent'], r['failed']
    if dt.datetime.now().minute == 0:
        db.run('DELETE FROM parent_challenges WHERE created_at < NOW() - INTERVAL 2 DAY')
    return done
