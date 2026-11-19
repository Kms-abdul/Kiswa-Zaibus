"""Create the setting rows an empty install is missing, so that saving on Setup / Settings really stores the change.

    python scripts\\create_settings.py             # from the backend folder, (.venv) active

Why: the app saves a setting with `UPDATE settings ... WHERE skey=<key>`, which changes nothing when the row does not exist
(the full seed creates these rows; migrations only create finance, pos and stock_audit). The screen then says "saved" and the
audit trail says "Setting changed", but nothing is stored.

It adds only the MISSING rows (INSERT IGNORE: an existing row is never overwritten):
  from seed/seed_data.json (policy and defaults): security, controls, maker_checker, escalation, student_categories, erp, razorpay
  empty (they would otherwise point at seed locations): counters [], closed_locations {}, online store pickup points / warehouses
  switches:    integrations_active = ERP off, Razorpay off
No business data: no items, stock, customers or users. Safe to run more than once.
"""
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from wsgi import app  # noqa: E402  (reads backend/.env -> DB_NAME)
from kz import db  # noqa: E402
from kz.config import Config  # noqa: E402
from kz.core import audit  # noqa: E402

SEED = BACKEND / 'seed' / 'seed_data.json'


def rows_from_seed():
    d = json.loads(SEED.read_text(encoding='utf-8'))
    sec = dict(d['security'])
    sec.pop('tempPw', None)
    intg = json.loads(json.dumps(d['razorpay_erp']))
    erp = intg.get('erp', {})
    for k in ('keyMask', 'keyFp'):
        erp.pop(k, None)
    erp['url'] = ''
    rzp = intg.get('rzp', {}) 
    rzp.pop('keys', None)
    rzp['env'] = 'Test'
    rzp['webhookUrl'] = ''
    controls = dict(d.get('controls') or {})
    controls.setdefault('returnDays', 7)
    online = dict(d.get('online') or {})
    online['points'] = []      # pickup locations: choose your own in Settings once the locations exist
    online['wh'] = {}          # central warehouse per company
    # Maker-checker (a second person approves) is OFF for role / user changes ('access') and settings changes ('cfg') during setup:
    # with only the Super Admin nobody else can approve, and nobody may approve their own request, so a role or user could never be
    # created. Switch both back on (Settings > Maker-checker) once a Director and the other staff exist.
    mc = dict(d.get('maker_checker') or {})
    mc.update({'access': False, 'cfg': False})
    return {
        'security': sec, 'controls': controls, 'maker_checker': mc, 'escalation': d.get('escalation') or {},
        'online': online, 'erp': erp, 'razorpay': rzp, 'student_categories': d.get('studentCats') or [],
        'counters': [], 'closed_locations': {}, 'integrations_active': {'erp': False, 'razorpay': False},
    }


def main():
    rows = rows_from_seed()
    with app.app_context():
        print(f'Database: {Config.DB_NAME} on {Config.DB_HOST}')
        have = {r['skey'] for r in db.q('SELECT skey FROM settings')}
        added = []
        try:
            for key, value in rows.items():
                if key in have:
                    print(f'  kept     {key}  (already exists)')
                    continue
                db.run('INSERT IGNORE INTO settings (skey, svalue) VALUES (:k, :v)', k=key, v=json.dumps(value, ensure_ascii=False))
                added.append(key)
                print(f'  created  {key}')
            if added:
                user = db.one("SELECT id, name, main_role FROM users WHERE main_role='Super Admin' ORDER BY id LIMIT 1") \
                    or {'id': None, 'name': 'System (CLI)', 'main_role': 'System'}
                audit.write('Settings created (command line)', 'settings', new=', '.join(added), category='setup', status='Done', user=user)
            db.finish(True)
        except Exception:
            db.finish(False)
            raise
    print(f'Done: {len(added)} setting row(s) created.')


if __name__ == '__main__':
    main()
